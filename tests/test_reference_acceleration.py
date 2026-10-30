"""Only silent app clips with unconnected audio use the pass-through adapter."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reference_acceleration import prepare_loaders


class ReferenceAccelerationCheck(unittest.TestCase):
    def graph(self):
        return {'nodes': [{'id': 1, 'type': 'VHS_LoadVideoFFmpeg',
                          'widgets_values': {'video': 'canvas-segment-' + 'a' * 64 + '.mp4',
                                             'force_rate': 24, 'start_time': 0, 'frame_load_cap': 120},
                          'outputs': [{'links': [1]}, {}, {'links': []}]}],
                'links': [[1, 1, 0, 2, 0, 'IMAGE']]}

    def test_adapter_is_gated_and_does_not_change_visual_inputs(self):
        graph = self.graph()
        original = copy.deepcopy(graph)
        self.assertEqual(prepare_loaders(graph), original)
        prepare_loaders(graph, available=True)
        node = graph['nodes'][0]
        self.assertEqual(node['type'], 'H3ReferenceVideoLoader')
        self.assertTrue(node['widgets_values'].pop('ignore_audio'))
        node['type'] = 'VHS_LoadVideoFFmpeg'
        self.assertEqual(graph, original)

    def test_connected_audio_and_user_videos_stay_original(self):
        for use_link, use_metadata in ((True, False), (False, True)):
            graph = self.graph()
            if use_link:
                graph['links'].append([2, 1, 2, 3, 0, 'AUDIO'])
            if use_metadata:
                graph['nodes'][0]['outputs'][2]['links'] = [2]
            self.assertEqual(prepare_loaders(copy.deepcopy(graph), True), graph)
        graph = self.graph()
        graph['nodes'][0]['widgets_values']['video'] = 'original.mp4'
        self.assertEqual(prepare_loaders(copy.deepcopy(graph), True), graph)

    def test_api_audio_link_reverses_adapter_safely(self):
        graph = {'video': {'class_type': 'VHS_LoadVideoFFmpeg',
                           'inputs': self.graph()['nodes'][0]['widgets_values']},
                 'ref': {'class_type': 'MiniMaxH3ReferenceToVideo', 'inputs': {'ref_videos': ['video', 0]}}}
        prepare_loaders(graph, True)
        self.assertEqual(graph['video']['class_type'], 'H3ReferenceVideoLoader')
        graph['ref']['inputs']['ref_video_audios'] = ['video', 2]
        prepare_loaders(graph, True)
        self.assertEqual(graph['video']['class_type'], 'VHS_LoadVideoFFmpeg')
        self.assertNotIn('ignore_audio', graph['video']['inputs'])

    def test_audio_used_skips_silent_physical_crop(self):
        from segment_reference import materialize
        graph = {'timing': {'class_type': 'H3SegmentTiming', 'inputs': {'start_seconds': 5, 'end_seconds': 10}},
                 'video': {'class_type': 'VHS_LoadVideoFFmpeg', 'inputs': {'video': 'original.mp4',
                            'start_time': ['timing', 0], 'frame_load_cap': ['timing', 1]}},
                 'ref': {'class_type': 'MiniMaxH3ReferenceToVideo', 'inputs': {'ref_video_audios': ['video', 2]}}}
        original = copy.deepcopy(graph)
        app = Mock()
        self.assertEqual(materialize(app, graph, {}), {})
        self.assertEqual(graph, original)
        app.read_optimizer_reference.assert_not_called()

    def test_preflight_accepts_saved_adapter_on_older_engine(self):
        from server import CanvasApp
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            app = CanvasApp(folder)
            app.comfy = Mock(return_value={'VHS_LoadVideoFFmpeg': {}})
            graph = self.graph()
            graph['nodes'][0]['type'] = 'H3ReferenceVideoLoader'
            graph['nodes'][0]['widgets_values']['ignore_audio'] = True
            self.assertTrue(app.preflight(graph, check_references=False)['ok'])
            self.assertEqual(graph['nodes'][0]['type'], 'H3ReferenceVideoLoader')


if __name__ == '__main__':
    unittest.main()
