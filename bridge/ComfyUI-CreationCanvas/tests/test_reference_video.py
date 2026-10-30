"""No ComfyUI/Torch import or decoding: test exact pass-through and lazy-audio isolation."""
import copy
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('canvas_reference_video', Path(__file__).resolve().parents[1] / 'reference_video.py')
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)


class NeverReadAudio(dict):
    def __iter__(self):
        raise AssertionError('lazy audio must not be read')

    def values(self):
        raise AssertionError('lazy audio must not be sized')


class VHSLoader:
    RETURN_TYPES = (object(), 'MASK', 'AUDIO', 'VHS_VIDEOINFO')
    RETURN_NAMES = ('IMAGE', 'mask', 'audio', 'video_info')
    schema = {'required': {'video': (['source.mp4'],), 'force_rate': ('FLOAT', {'default': 24})},
              'optional': {'format': (['None'],)}, 'hidden': {'unique_id': 'UNIQUE_ID'}}
    output = (object(), object(), NeverReadAudio(), {'loaded_frame_count': 120, 'loaded_fps': 24})
    calls = []

    @classmethod
    def INPUT_TYPES(cls):
        return cls.schema

    @classmethod
    def IS_CHANGED(cls, video, **kwargs):
        return 'original-fingerprint:' + video

    @classmethod
    def VALIDATE_INPUTS(cls, video):
        return True if video == 'source.mp4' else 'Invalid video file'

    def load_video(self, **kwargs):
        self.calls.append(kwargs)
        return self.output


class ReferenceVideoCheck(unittest.TestCase):
    def setUp(self):
        self.patched = patch.dict(sys.modules, {'nodes': types.SimpleNamespace(NODE_CLASS_MAPPINGS={'VHS_LoadVideoFFmpeg': VHSLoader})})
        self.patched.start()
        VHSLoader.calls = []

    def tearDown(self):
        self.patched.stop()

    def test_schema_and_metadata_reuse_without_mutating_vhs(self):
        before = copy.deepcopy(VHSLoader.schema)
        schema = reference.H3ReferenceVideoLoader.INPUT_TYPES()
        self.assertEqual(VHSLoader.schema, before)
        self.assertEqual(schema['required'], before['required'])
        self.assertEqual(schema['hidden'], before['hidden'])
        self.assertEqual(schema['optional']['format'], before['optional']['format'])
        self.assertEqual(schema['optional']['ignore_audio'][1]['default'], True)
        self.assertIs(reference.H3ReferenceVideoLoader.RETURN_TYPES, VHSLoader.RETURN_TYPES)
        self.assertIs(reference.H3ReferenceVideoLoader.RETURN_NAMES, VHSLoader.RETURN_NAMES)

    def test_default_ignores_audio_and_preserves_exact_visual_objects(self):
        result = reference.H3ReferenceVideoLoader().load_video(video='source.mp4', force_rate=24, frame_load_cap=120, format='None')
        self.assertIs(result[0], VHSLoader.output[0])
        self.assertIs(result[1], VHSLoader.output[1])
        self.assertIsNone(result[2])
        self.assertIs(result[3], VHSLoader.output[3])
        self.assertEqual(VHSLoader.calls, [{'video': 'source.mp4', 'force_rate': 24, 'frame_load_cap': 120, 'format': 'None'}])

    def test_audio_enabled_returns_the_unchanged_original_output(self):
        result = reference.H3ReferenceVideoLoader().load_video(ignore_audio=False, video='source.mp4')
        self.assertIs(result, VHSLoader.output)
        self.assertIs(result[2], VHSLoader.output[2])

    def test_fingerprint_validation_and_late_dependency_resolution(self):
        node = reference.H3ReferenceVideoLoader
        self.assertEqual(node.IS_CHANGED('source.mp4', ignore_audio=True), VHSLoader.IS_CHANGED('source.mp4'))
        self.assertTrue(node.VALIDATE_INPUTS('source.mp4'))
        self.assertEqual(node.VALIDATE_INPUTS('missing.mp4'), 'Invalid video file')
        with patch.dict(sys.modules, {'nodes': types.SimpleNamespace(NODE_CLASS_MAPPINGS={})}):
            with self.assertRaisesRegex(RuntimeError, 'Video Helper Suite'):
                node.INPUT_TYPES()


if __name__ == '__main__':
    unittest.main()
