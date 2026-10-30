"""No model, real server, or real media is used."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import continuity
from server import CanvasApp


def workflow():
    return {'last_node_id': 8, 'last_link_id': 4, 'custom': {'unchanged': True}, 'nodes': [
        {'id': 1, 'type': 'VAELoader', 'inputs': [], 'outputs': [{'name': 'VAE', 'type': 'VAE', 'links': [1]}]},
        {'id': 2, 'type': 'MiniMaxH3ReferenceToVideo', 'pos': [100, 100], 'mode': 0,
         'inputs': [{'name': 'vae', 'type': 'VAE', 'link': 1}, {'name': 'ref_videos.ref_video_0', 'type': 'IMAGE', 'link': 4}],
         'outputs': [{'name': 'positive', 'type': 'CONDITIONING', 'links': [2]}, {'name': 'LATENT', 'type': 'LATENT', 'links': [3]}]},
        {'id': 3, 'type': 'BasicGuider', 'inputs': [{'name': 'conditioning', 'type': 'CONDITIONING', 'link': 2}], 'outputs': []},
        {'id': 4, 'type': 'SamplerCustomAdvanced', 'inputs': [{'name': 'latent_image', 'type': 'LATENT', 'link': 3}], 'outputs': []},
        {'id': 8, 'type': 'VHS_LoadVideoFFmpeg', 'inputs': [], 'outputs': [{'name': 'IMAGE', 'type': 'IMAGE', 'links': [4]}]}],
        'links': [[1, 1, 0, 2, 0, 'VAE'], [2, 2, 0, 3, 0, 'CONDITIONING'],
                  [3, 2, 1, 4, 0, 'LATENT'], [4, 8, 0, 2, 1, 'IMAGE']]}


class ContinuityCheck(unittest.TestCase):
    def test_graph_is_copied_and_official_guide_ports_are_connected(self):
        original = workflow()
        before = deepcopy(original)
        result = continuity.inject(original, 'previous-last.png')
        self.assertEqual(original, before)
        nodes = {node['id']: node for node in result['nodes']}
        guide = next(node for node in nodes.values() if node['type'] == 'MiniMaxH3AddGuide')
        load = next(node for node in nodes.values() if node['type'] == 'LoadImage')
        self.assertEqual([item['name'] for item in guide['inputs']], ['positive', 'latent', 'vae', 'audio_vae', 'image', 'audio', 'frame_idx'])
        self.assertEqual(guide['widgets_values'], [0])
        self.assertEqual(load['widgets_values'], ['previous-last.png', 'image'])
        self.assertEqual(next(link for link in result['links'] if link[0] == 2), [2, guide['id'], 0, 3, 0, 'CONDITIONING'])
        self.assertEqual(next(link for link in result['links'] if link[0] == 3), before['links'][2])
        self.assertEqual(next(link for link in result['links'] if link[0] == 4), before['links'][3])
        for link in result['links']:
            identity, source, source_slot, dest, dest_slot, _ = link
            self.assertIn(identity, nodes[source]['outputs'][source_slot]['links'])
            self.assertEqual(nodes[dest]['inputs'][dest_slot]['link'], identity)
        self.assertEqual(result['custom'], before['custom'])

    def test_ambiguous_or_missing_topology_is_rejected_before_media(self):
        for edit in (lambda graph: graph['nodes'].append(deepcopy(graph['nodes'][1])),
                     lambda graph: graph['nodes'][1]['inputs'][0].update(link=None),
                     lambda graph: graph['nodes'][2]['inputs'][0].update(link=None)):
            graph = workflow(); edit(graph)
            app = Mock()
            with self.assertRaises(ValueError):
                continuity.prepare(app, graph, {'type': 'video', 'assetId': 'a'})
            app.reference_asset.assert_not_called()
            app.upload_comfy_media.assert_not_called()

    def test_absent_guide_fails_before_video_is_read(self):
        app = Mock(); app.comfy.return_value = {}
        with self.assertRaisesRegex(ValueError, '缺少接续节点'):
            continuity.prepare(app, workflow(), {'type': 'video', 'assetId': 'a'})
        app.reference_asset.assert_not_called()

    def test_exact_last_frame_is_selected_after_decoded_frame_count(self):
        calls = []
        def fake_run(args, timeout):
            calls.append(args)
            if args[0] == 'ffprobe':
                return json.dumps({'streams': [{'nb_read_frames': '121'}]})
            Path(args[-1]).write_bytes(b'\x89PNG\r\n\x1a\n' + b'content-data-for-test')
            return ''
        with tempfile.TemporaryDirectory() as folder, patch('continuity.tool', side_effect=lambda name: name), patch('continuity.run', side_effect=fake_run):
            image = continuity.last_frame(Path(folder) / 'source.mp4', Path(folder) / 'last.png')
        self.assertTrue(image.startswith(b'\x89PNG'))
        self.assertIn('-count_frames', calls[0])
        self.assertIn('select=eq(n\\,120)', calls[1])
        self.assertIn('png', calls[1])

    def test_user_selected_video_only_is_extracted_and_uploaded(self):
        app = Mock()
        app.comfy.side_effect = lambda route, **kw: {route.rsplit('/', 1)[1]: {}}
        app.reference_asset.return_value = {'id': 'selected', 'type': 'video'}
        app.asset.return_value = (Path('selected.mp4'), {})
        app.upload_comfy_media.return_value = 'input/last.png'
        with patch('continuity.last_frame', return_value=b'png') as frame:
            updated = continuity.prepare(app, workflow(), {'type': 'video', 'assetId': 'selected', 'name': '上段'})
        app.reference_asset.assert_called_once_with({'type': 'video', 'assetId': 'selected', 'name': '上段'})
        frame.assert_called_once()
        self.assertEqual(next(node for node in updated['nodes'] if node['type'] == 'LoadImage')['widgets_values'][0], 'input/last.png')

    def test_foreign_url_is_rejected_by_existing_local_media_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            app = CanvasApp(folder)
            app.comfy = lambda route, **kw: {route.rsplit('/', 1)[1]: {}}
            app.comfy_url = lambda: 'http://127.0.0.1:8188'
            with self.assertRaisesRegex(ValueError, '禁止任意外部地址'):
                continuity.prepare(app, workflow(), {'type': 'video', 'url': 'https://example.org/private.mp4'})

    def test_execution_bridge_uses_temporary_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            app = CanvasApp(folder)
            app.jobs['abc'] = {'id': 'abc', 'instance_id': 'original-card', 'continuation': True, 'status': 'submitting'}
            sent = []
            def comfy(path, method='GET', body=None, **kwargs):
                if path == '/creation-canvas/command':
                    sent.append(body); return {}
                if path == '/creation-canvas/state':
                    if sent:
                        return {'revision': 1, 'pending': [{'opId': sent[0]['opId'], 'status': 'completed', 'result': {'promptId': 'p1'}}]}
                    return {'revision': 0}
                if path == '/history/p1':
                    return {'p1': {'status': {'status_str': 'success'}, 'outputs': {}}}
                raise AssertionError(path)
            app.comfy = comfy
            app._run_job('abc', workflow())
            self.assertEqual(sent[0]['workflowId'], 'continuation-abc')
            self.assertEqual(app.jobs['abc']['instance_id'], 'original-card')
            self.assertEqual(app.jobs['abc']['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
