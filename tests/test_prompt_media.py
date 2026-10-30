"""Local-only checks: real frame extraction, port order, scope and path boundaries."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from prompt_media import prepare
from prompt_optimizer import reference_context
from server import make_server, atomic_json
from video_edit import tool, run
from test_prompt_optimizer import graph


class PromptMediaCheck(unittest.TestCase):
    def test_selected_video_frames_and_only_connected_identity_images(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image, video = root / 'private-image.jpg', root / 'private-video.mp4'
            run([tool('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'color=c=yellow:s=64x64', '-frames:v', '1', str(image)], timeout=15)
            run([tool('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=64x64:r=12', '-t', '2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)], timeout=15)
            app = Mock()
            app.read_optimizer_reference.side_effect = lambda _ref, field: (video.read_bytes(), 'video/mp4') if field['type'] == 'video' else (image.read_bytes(), 'image/jpeg')
            document = graph()
            document['nodes'] += [{'id': 100, 'type': 'H3SegmentTiming', 'widgets_values': [1, 2]},
                                  {'id': 101, 'type': 'LoadImage', 'widgets_values': ['unconnected-private.jpg', 'image']}]
            refs = [{'type': 'image', 'fieldId': 'nodes/0:image', 'assetId': 'front'},
                    {'type': 'image', 'fieldId': 'nodes/1:image', 'assetId': 'back'},
                    {'type': 'video', 'assetId': 'motion'}]
            blocks, inspected = prepare(app, document, refs, reference_context(document, refs))
            self.assertEqual([x['label'] for x in inspected], ['<Picture 1>', '<Picture 2>', '<Video 1>'])
            self.assertEqual(inspected[-1], {'label': '<Video 1>', 'type': 'video', 'start': 1.0, 'end': 2.0, 'audio': False, 'frames': 12})
            self.assertEqual(len([x for x in blocks if x['type'] == 'image_url']), 14)
            self.assertTrue(all(x['image_url']['url'].startswith('data:image/jpeg;base64,') for x in blocks if x['type'] == 'image_url'))
            self.assertNotIn('private', json.dumps(blocks))
            self.assertEqual([c.args[0]['assetId'] for c in app.read_optimizer_reference.call_args_list], ['back', 'front', 'motion'])
            document['nodes'][-2]['widgets_values'] = [2, 3]
            with self.assertRaisesRegex(ValueError, '时间超出'):
                prepare(app, document, refs, reference_context(document, refs))

    def test_imported_reference_never_reads_arbitrary_path_or_external_url(self):
        with tempfile.TemporaryDirectory() as folder:
            app_server = make_server(port=0, root=Path(folder), source=Path(folder) / 'source')
            app = app_server.app
            app.comfy_url = lambda: 'http://127.0.0.1:8188'
            try:
                with self.assertRaisesRegex(ValueError, '路径越界'):
                    app.read_optimizer_reference(None, {'type': 'image', 'value': 'C:/private/a.jpg'})
                with self.assertRaisesRegex(ValueError, '禁止任意外部'):
                    app.read_optimizer_reference({'url': 'https://example.org/private.jpg'}, {'type': 'image'})
                self.assertEqual(app.project()['cards'], [])
                self.assertEqual(app.jobs, {})
            finally:
                app_server.server_close()

    def test_prepared_clip_restores_original_for_nonzero_selected_range(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image, original, clipped = root / 'identity.jpg', root / 'original.mp4', root / 'clipped.mp4'
            run([tool('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'color=c=yellow:s=64x64', '-frames:v', '1', str(image)], timeout=15)
            run([tool('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=64x64:r=12', '-t', '10', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(original)], timeout=15)
            run([tool('ffmpeg'), '-v', 'error', '-ss', '5', '-i', str(original), '-t', '5', '-c', 'copy', str(clipped)], timeout=15)
            cache = root / 'segments'
            cache.mkdir()
            digest = 'a' * 64
            document = graph()
            document['nodes'][2].update(type='VHS_LoadVideoFFmpeg', widgets_values={'video': 'canvas-segment-' + digest + '.mp4', 'start_time': 0.0})
            document['nodes'].append({'id': 100, 'type': 'H3SegmentTiming', 'widgets_values': [5, 10]})
            for mapping in ({'assetId': 'original', 'value': 'old-source.mp4'}, {'assetId': None, 'value': 'original.mp4'}):
                with self.subTest(mapping=mapping):
                    (cache / (digest + '.json')).write_text(json.dumps(mapping), encoding='utf-8')
                    app = Mock(data=root)
                    def read_reference(reference, field):
                        if field['type'] == 'image':
                            return image.read_bytes(), 'image/jpeg'
                        restored = reference == {'assetId': 'original'} or field['value'] == 'original.mp4'
                        return (original if restored else clipped).read_bytes(), 'video/mp4'
                    app.read_optimizer_reference.side_effect = read_reference
                    blocks, inspected = prepare(app, document, [], reference_context(document, []))
                    self.assertEqual(inspected[-1], {'label': '<Video 1>', 'type': 'video', 'start': 5.0, 'end': 10.0, 'audio': False, 'frames': 12})
                    self.assertEqual(sum(block['type'] == 'image_url' for block in blocks), 14)
                    video_call = app.read_optimizer_reference.call_args_list[-1]
                    self.assertTrue(video_call.args[0] == {'assetId': 'original'} or video_call.args[1]['value'] == 'original.mp4')
                    self.assertTrue(document['nodes'][2]['widgets_values']['video'].startswith('canvas-segment-'))

    def test_prepared_clip_metadata_cannot_bypass_reference_path_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app_server = make_server(port=0, root=root, source=root / 'source')
            app = app_server.app
            app.comfy_url = lambda: 'http://127.0.0.1:8188'
            try:
                digest = 'b' * 64
                cache = app.data / 'segments'
                cache.mkdir()
                (cache / (digest + '.json')).write_text(json.dumps({'value': 'C:/private/a.mp4'}), encoding='utf-8')
                document = {'1': {'class_type': 'VHS_LoadVideoFFmpeg', 'inputs': {'video': 'canvas-segment-' + digest + '.mp4'}},
                            '2': {'class_type': 'MiniMaxH3ReferenceToVideo', 'inputs': {'ref_videos.ref_video_0': ['1', 0]}}}
                with self.assertRaisesRegex(ValueError, '路径越界'):
                    prepare(app, document, [], reference_context(document, []))
                (cache / (digest + '.json')).unlink()
                with self.assertRaisesRegex(ValueError, '原视频记录无法读取'):
                    prepare(app, document, [], reference_context(document, []))
            finally:
                app_server.server_close()


if __name__ == '__main__':
    unittest.main()
