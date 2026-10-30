"""Offline import compatibility; never starts ComfyUI or submits a generation."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflows import WorkflowLibrary, apply_params, fields_for, unwrap_workflow, validate_import


GUI = {'nodes': [
    {'id': 1, 'type': 'CLIPTextEncode', 'widgets_values': ['a small boat'],
     'custom': {'keep': [1, '不改']}},
    {'id': 2, 'type': 'UnknownCustom', 'widgets_values': ['keep', 42]}],
    'links': [], 'definitions': {'subgraphs': [
        {'id': 'subgraph', 'nodes': [{'id': 1, 'type': 'LoadImage',
                                    'widgets_values': ['ref.png', 'image']}],
         'links': [], 'opaque': {'future': True}}]},
    'extra': {'unknown': ['keep me']}}
API = {'1': {'class_type': 'CustomNode', 'inputs': {
    'prompt': 'a boat', 'custom_strength': 0.75, 'enabled': True,
    'api_key': 'test-fixture-only', 'image': ['9', 0], 'options': {'untouched': 1}},
    '_meta': {'title': '自定义生成'}}}


class WorkflowImportCheck(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = WorkflowLibrary(self.root / 'empty', self.root / 'imports')

    def tearDown(self):
        self.temp.cleanup()

    def test_native_gui_and_subgraphs_remain_exact(self):
        result = self.library.import_document(GUI, '我的模板')
        self.assertEqual(result['workflow'], GUI)
        self.assertEqual(self.library.get(result['id'])['workflow'], GUI)
        self.assertEqual(result['name'], '我的模板')
        self.assertEqual(result['compatibility']['unmappedNodeTypes'], ['UnknownCustom'])
        self.assertTrue(result['warnings'])
        self.assertFalse(result['duplicate'])
        self.assertEqual(len(list((self.root / 'imports').glob('*.json'))), 1)

    def test_wrappers_select_editable_workflow_before_api(self):
        for envelope in ({'workflow': GUI, 'prompt': API, 'client_id': 'private'},
                         {'extra_data': {'extra_pnginfo': {'workflow': json.dumps(GUI)}}},
                         {'workflow': json.dumps(GUI)},
                         {'extra_pnginfo': {'workflow': GUI}}):
            with self.subTest(envelope=list(envelope)):
                result = self.library.import_document(envelope)
                self.assertEqual(result['workflow'], GUI)
        self.assertEqual(unwrap_workflow({'prompt': API, 'client_id': 'private'}), API)
        self.assertEqual(unwrap_workflow({'prompt': json.dumps(API)}), API)

    def test_identical_content_different_name_deduplicates(self):
        original = self.library.import_document(GUI, 'first')
        reordered = dict(reversed(list(GUI.items())))
        repeated = self.library.import_document({'workflow': reordered}, 'second')
        self.assertEqual(repeated['id'], original['id'])
        self.assertTrue(repeated['duplicate'])
        self.assertEqual(repeated['name'], 'first')
        self.assertEqual(len(self.library.catalog()), 1)

    def test_generic_api_inputs_edit_scalars_only_and_preserve_links(self):
        result = self.library.import_document(API, 'API 示例')
        self.assertEqual(result['format'], 'api')
        self.assertFalse(result['compatibility']['requiresBridge'])
        fields = {field['key']: field for field in fields_for(API)}
        self.assertEqual(fields['custom_strength']['label'], 'custom_strength')
        self.assertTrue(fields['custom_strength']['editable'])
        self.assertFalse(fields['api_key']['editable'])
        self.assertNotIn('image', fields)
        self.assertNotIn('options', fields)
        changed = apply_params(API, {fields['custom_strength']['id']: 0.5})
        self.assertEqual(changed['1']['inputs']['custom_strength'], 0.5)
        changed['1']['inputs']['custom_strength'] = 0.75
        self.assertEqual(changed, API)
        with self.assertRaises(ValueError):
            apply_params(API, {fields['api_key']['id']: 'replaced'})

    def test_standard_text_fields_are_readable_and_editable(self):
        field = next(field for field in fields_for(GUI) if field['nodeType'] == 'CLIPTextEncode')
        self.assertEqual(field['label'], '画面描述')
        self.assertEqual(field['type'], 'textarea')
        changed = apply_params(GUI, {field['id']: '静止的小船'})
        self.assertEqual(changed['nodes'][0]['widgets_values'], ['静止的小船'])
        self.assertEqual(changed['nodes'][1], GUI['nodes'][1])
        self.assertEqual(changed['definitions'], GUI['definitions'])

    def test_large_integer_seed_is_read_only_and_stays_exact(self):
        document = {'9': {'class_type': 'CustomNode', 'inputs': {'seed': 18446744073709551615}}}
        result = self.library.import_document(document)
        field = result['fields'][0]
        self.assertEqual(field['value'], 18446744073709551615)
        self.assertFalse(field['editable'])
        self.assertIn('精确保留', field['description'])
        self.assertEqual(apply_params(document, {field['id']: 18446744073709551615}), document)
        with self.assertRaises(ValueError):
            apply_params(document, {field['id']: 18446744073709551616})

    def test_named_custom_widgets_do_not_guess_array_layout(self):
        document = {'nodes': [{'id': 1, 'type': 'Unknown',
                    'widgets_values': {'creative_input': 'hello', 'some_scale': 2}}]}
        fields = fields_for(document)
        self.assertEqual({field['key'] for field in fields}, {'creative_input', 'some_scale'})
        self.assertEqual(len([f for f in fields_for(GUI) if f['nodeType'] == 'UnknownCustom']), 0)

    def test_video_loader_array_recognises_video_and_preserves_linked_widgets(self):
        for kind in ('H3ReferenceVideoLoader', 'VHS_LoadVideoFFmpeg'):
            document = {'nodes': [{'id': 202, 'type': kind,
                'widgets_values': ['original.mp4', 24, 0, 0, 0, 0, 'AnimateDiff', True],
                'inputs': [{'name': 'custom_height', 'link': 36}, {'name': 'frame_load_cap', 'link': 37}]}]}
            fields = {field['key']: field for field in fields_for(document)}
            self.assertEqual(fields['video']['type'], 'video')
            self.assertEqual(fields['video']['widgetIndex'], 0)
            self.assertFalse(fields['frame_load_cap']['editable'])
            self.assertFalse(fields['custom_height']['editable'])
            changed = apply_params(document, {fields['video']['id']: 'new.mp4'})
            self.assertEqual(changed['nodes'][0]['widgets_values'], ['new.mp4', 24, 0, 0, 0, 0, 'AnimateDiff', True])
            self.assertEqual(document['nodes'][0]['widgets_values'][0], 'original.mp4')

    def test_malformed_inputs_rejected_without_creating_files(self):
        bad = [[], {}, {'nodes': [None]}, {'nodes': [{'type': 'LoadImage'}]},
               {'nodes': [{'id': 1, 'type': 'LoadImage', 'inputs': {}}]},
               {'nodes': [{'id': 1, 'type': 'LoadImage', 'widgets_values': 'bad'}]},
               {'nodes': [{'id': 1, 'type': 'x'}, {'id': '1', 'type': 'y'}]},
               {'nodes': [], 'definitions': {'subgraphs': [None]}},
               {'1': {'class_type': 'Custom', 'inputs': {'v': float('nan')}}}]
        deep = {}
        target = deep
        for _ in range(70):
            target['child'] = {}
            target = target['child']
        bad.append({'nodes': [], 'extra': deep})
        for document in bad:
            with self.subTest(document=str(document)[:100]), self.assertRaises(ValueError):
                self.library.import_document(document)
        self.assertEqual(list((self.root / 'imports').iterdir()), [])

    def test_too_many_nodes_rejected(self):
        document = {'nodes': [{'id': i, 'type': 'x'} for i in range(10001)]}
        with self.assertRaisesRegex(ValueError, '10000'):
            validate_import(document)

    def test_filename_and_catalog_do_not_depend_on_personal_library(self):
        result = self.library.import_document(API, '../../我的:模板')
        self.assertEqual(result['workflow'], API)
        self.assertEqual(len(self.library.catalog()), 1)
        self.assertNotIn('千问', self.library.catalog()[0]['description'])
        for path in self.library.user_dir.iterdir():
            self.assertEqual(path.parent, self.root / 'imports')


if __name__ == '__main__':
    unittest.main()
