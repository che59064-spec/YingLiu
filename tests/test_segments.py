"""A physical clip goes to the loader; timing/output naming keeps the original range."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import CanvasApp
from video_edit import tool, run, probe


class SegmentCheck(unittest.TestCase):
    def test_h3_detached_start_uses_chosen_range_with_nonzero_old_offset(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source.bin'
            source.write_bytes(b'original reference')
            for node_type in ('H3ReferenceVideoLoader', 'VHS_LoadVideoFFmpeg'):
                with self.subTest(node_type=node_type):
                    app = CanvasApp(root / node_type, root / 'source')
                    app.reference_asset = Mock(return_value={'id': 'selected', 'type': 'video'})
                    app.asset = Mock(return_value=(source, {}))
                    app.upload_comfy_media = Mock(return_value='selected-5-to-10.mp4')
                    graph = {'nodes': [
                        {'id': 201, 'type': 'H3SegmentTiming', 'widgets_values': [5, 10]},
                        {'id': 202, 'type': node_type,
                         'widgets_values': ['original.mp4', 0, 0, 0, 0, 0.017, 'AnimateDiff'],
                         'inputs': [{'name': 'video', 'link': None}, {'name': 'frame_load_cap', 'link': 37},
                                    {'name': 'start_time', 'link': None}], 'outputs': []},
                        {'id': 136, 'type': 'MiniMaxH3ReferenceToVideo', 'widgets_values': []}],
                        'links': [[37, 201, 1, 202, 1, 'INT']]}
                    original = copy.deepcopy(graph)
                    def cut(path, dest, start, end):
                        self.assertEqual(path, source)
                        self.assertEqual((start, end), (5., 10.))
                        dest.write_bytes(b'five-to-ten')
                    with patch('segment_reference.extract_segment', side_effect=cut) as extract:
                        result = app.prepare_workflow({'workflow': graph, 'references': [{'type': 'video'}]})
                    extract.assert_called_once()
                    self.assertEqual(result['nodes'][1]['widgets_values'][0], 'selected-5-to-10.mp4')
                    self.assertEqual(result['nodes'][1]['widgets_values'][5], 0.)
                    self.assertEqual(graph, original)

    def test_array_loader_selected_input_uses_current_timing_and_cached_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app = CanvasApp(root, root / 'source')
            asset = root / 'original.bin'
            asset.write_bytes(b'new selected input')
            app.reference_asset = Mock(return_value={'id': 'new-asset', 'type': 'video'})
            app.asset = Mock(return_value=(asset, {}))
            app.upload_comfy_media = Mock(side_effect=lambda name, _mime, _data: name)
            # Simulate an app-owned graph whose start link was removed when a prior
            # short segment was sent to the professional editor.
            old = 'canvas-segment-' + 'a' * 64 + '.mp4'
            document = {'nodes': [
                {'id': 201, 'type': 'H3SegmentTiming', 'widgets_values': [5, 10],
                 'inputs': [], 'outputs': [{'links': []}, {'links': [37]}]},
                {'id': 202, 'type': 'H3ReferenceVideoLoader',
                 'widgets_values': [old, 24, 0, 0, 0, 0, 'AnimateDiff', True],
                 'inputs': [{'name': 'video', 'link': None}, {'name': 'frame_load_cap', 'link': 37},
                            {'name': 'start_time', 'link': None}], 'outputs': []}],
                'links': [[37, 201, 1, 202, 1, 'INT']]}
            original = copy.deepcopy(document)
            def extract(source, result, start, end):
                self.assertEqual(source.read_bytes(), b'new selected input')
                self.assertEqual((start, end), (5., 10.))
                result.write_bytes(b'mocked segment bytes')
            with patch('segment_reference.extract_segment', side_effect=extract) as cutting:
                prepared = app.prepare_workflow({'workflow': document, 'references': [{'type': 'video'}]})
                self.assertEqual(cutting.call_count, 1)
                self.assertEqual(document, original)
                self.assertNotEqual(prepared['nodes'][1]['widgets_values'][0], old)
                self.assertEqual(prepared['nodes'][1]['widgets_values'][5:], [0., 'AnimateDiff', True])
                self.assertEqual(prepared['links'], original['links'])
                # A repeated run recovers the source from metadata and reuses the cache.
                repeated = app.prepare_workflow({'workflow': prepared, 'references': []})
                self.assertEqual(cutting.call_count, 1)
                self.assertEqual(repeated['nodes'][1]['widgets_values'][0], prepared['nodes'][1]['widgets_values'][0])

    def test_plain_vhs_disconnected_start_does_not_infer_time(self):
        from segment_reference import materialize
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app = CanvasApp(root, root / 'source')
            document = {'nodes': [
                {'id': 201, 'type': 'H3SegmentTiming', 'widgets_values': [5, 10]},
                {'id': 202, 'type': 'VHS_LoadVideoFFmpeg', 'widgets_values': ['original.mp4', 24, 0, 0, 0, 0],
                 'inputs': [{'name': 'start_time', 'link': None}, {'name': 'frame_load_cap', 'link': 37}]}],
                'links': [[37, 201, 1, 202, 1, 'INT']]}
            original = copy.deepcopy(document)
            with patch('segment_reference.extract_segment') as cutting:
                self.assertEqual(materialize(app, document, {'nodes/1:video': 'asset'}), {'nodes/1:video': 'asset'})
                cutting.assert_not_called()
            self.assertEqual(document, original)

    def test_gui_cut_repeat_and_changed_range_preserve_original_and_use_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'original.mp4'
            run([tool('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=64x64:r=24', '-t', '3', '-c:v', 'libx264', str(source)], timeout=15)
            app = CanvasApp(root, root / 'source')
            entry = app.store_asset('original.mp4', 'video/mp4', source.read_bytes())
            app.upload_comfy_media = Mock(side_effect=lambda name, _mime, _data: name)
            document = {'nodes': [
                {'id': 1, 'type': 'H3SegmentTiming', 'widgets_values': [1.0, 2.0], 'outputs': [{'links': [1]}, {'links': [2]}]},
                {'id': 2, 'type': 'VHS_LoadVideoFFmpeg', 'widgets_values': {'video': 'original.mp4', 'start_time': 1, 'frame_load_cap': 24},
                 'inputs': [{'name': 'start_time', 'link': 1}, {'name': 'frame_load_cap', 'link': 2}]}],
                'links': [[1, 1, 0, 2, 0, 'FLOAT'], [2, 1, 1, 2, 1, 'INT']]}
            original = copy.deepcopy(document)
            prepared = app.prepare_workflow({'workflow': document, 'references': [{'assetId': entry['id'], 'type': 'video'}]})
            self.assertEqual(document, original)
            self.assertEqual(prepared['nodes'][0]['widgets_values'], [1., 2.])
            self.assertEqual(prepared['nodes'][1]['widgets_values']['start_time'], 0)
            self.assertEqual(prepared['nodes'][1]['inputs'][0]['link'], None)
            self.assertEqual(prepared['links'], [[2, 1, 1, 2, 1, 'INT']])
            self.assertEqual(app.upload_comfy_media.call_count, 1)
            clips = list((root / 'data' / 'segments').glob('*.mp4'))
            self.assertEqual(len(clips), 1)
            self.assertAlmostEqual(probe(clips[0])['duration'], 1, delta=.05)
            # Reusing a prepared professional workflow must not trim its short clip again.
            repeated = app.prepare_workflow({'workflow': prepared, 'references': []})
            self.assertEqual(repeated['nodes'][1]['widgets_values']['video'], prepared['nodes'][1]['widgets_values']['video'])
            self.assertEqual(len(list((root / 'data' / 'segments').glob('*.mp4'))), 1)
            prepared['nodes'][0]['widgets_values'] = [2., 3.]
            updated = app.prepare_workflow({'workflow': prepared, 'references': []})
            self.assertNotEqual(updated['nodes'][1]['widgets_values']['video'], repeated['nodes'][1]['widgets_values']['video'])
            self.assertEqual(len(list((root / 'data' / 'segments').glob('*.mp4'))), 2)

    def test_api_loader_uses_zero_start_with_original_timing(self):
        from segment_reference import materialize
        with tempfile.TemporaryDirectory() as folder:
            app = CanvasApp(Path(folder), Path(folder) / 'source')
            app.read_optimizer_reference = Mock(return_value=(b'local source', 'video/mp4'))
            app.upload_comfy_media = Mock(return_value='selected.mp4')
            document = {'timing': {'class_type': 'H3SegmentTiming', 'inputs': {'start_seconds': 10, 'end_seconds': 15}},
                        'loader': {'class_type': 'VHS_LoadVideoFFmpeg', 'inputs': {'video': 'original.mp4', 'start_time': ['timing', 0], 'frame_load_cap': ['timing', 1]}}}
            from unittest.mock import patch
            def extract(_source, destination, _start, _end):
                destination.write_bytes(b'only selected frames')
            with patch('segment_reference.extract_segment', side_effect=extract) as cutting:
                materialize(app, document, {})
            self.assertEqual(cutting.call_args.args[2:], (10., 15.))
            self.assertEqual(document['loader']['inputs']['start_time'], 0)
            self.assertEqual(document['loader']['inputs']['video'], 'selected.mp4')
            self.assertEqual(document['timing']['inputs'], {'start_seconds': 10, 'end_seconds': 15})


if __name__ == '__main__':
    unittest.main()
