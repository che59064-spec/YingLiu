"""No models are run. Check lossless edits, local storage and trust boundaries."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import CanvasApp, HTTPProblem, make_server, validate_comfy_url
from workflows import apply_params, fields_for


DOCUMENT = {'last_node_id': 2, 'unknown': {'preserve': [1, 2, '你好']},
            'nodes': [{'id': 1, 'type': 'KSampler', 'title': '生成图片',
                       'widgets_values': [101, 'randomize', 40, 1.0, 'euler', 'simple', 1.0],
                       'inputs': [{'name': 'seed', 'widget': {'name': 'seed'}, 'link': None},
                                  {'name': 'steps', 'widget': {'name': 'steps'}, 'link': None}]},
                      {'id': 2, 'type': 'UnknownCustom', 'widgets_values': ['keep me'], 'custom': {'a': 3}}],
            'links': [[1, 1, 0, 2, 0, 'IMAGE']],
            'definitions': {'subgraphs': [{'id': 'opaque-subgraph', 'nodes': [], 'unknown': [9]}]}}


class BackendCheck(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.app = CanvasApp(self.root, self.source)

    def tearDown(self):
        self.temporary.cleanup()

    def test_import_preserves_unknown_fields_and_subgraphs(self):
        result = self.app.library.import_document(DOCUMENT, '../自定义/流程')
        self.assertEqual(result['workflow'], DOCUMENT)
        self.assertEqual(self.app.library.get(result['id'])['workflow'], DOCUMENT)
        self.assertFalse(list(self.source.iterdir()))

    def test_patch_changes_only_the_audited_widget(self):
        field = next(f for f in fields_for(DOCUMENT) if f['key'] == 'steps')
        edited = apply_params(DOCUMENT, {field['id']: 25})
        edited['nodes'][0]['widgets_values'][2] = 40
        self.assertEqual(edited, DOCUMENT)
        self.assertEqual(DOCUMENT['nodes'][0]['widgets_values'][2], 40)
        with self.assertRaises(ValueError):
            apply_params(DOCUMENT, {'nodes/1:unknown': 'overwrite'})
        with self.assertRaises(ValueError):
            apply_params(DOCUMENT, {field['id']: 0})

    def test_connected_input_is_not_overwritten(self):
        imported = json.loads(json.dumps(DOCUMENT))
        imported['nodes'][0]['inputs'][1]['link'] = 52
        field = next(f for f in fields_for(imported) if f['key'] == 'steps')
        with self.assertRaises(ValueError):
            apply_params(imported, {field['id']: 25})

    def test_project_conflict_keeps_first_save(self):
        saved = self.app.save_project({'revision': 0, 'cards': [{'id': 'a'}], 'custom': 42})
        self.assertEqual(saved['revision'], 1)
        with self.assertRaises(HTTPProblem) as raised:
            self.app.save_project({'revision': 0, 'cards': []})
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(self.app.project()['cards'], [{'id': 'a'}])

    def test_professional_snapshot_survives_app_restart(self):
        state = {'workflow': DOCUMENT, 'workflowId': '../opaque-card', 'revision': 7}
        filename = self.app.snapshot_bridge(state, 'load前备份')
        restarted = CanvasApp(self.root, self.source)
        saved = json.loads((restarted.data / 'snapshots' / filename).read_text(encoding='utf-8'))
        self.assertEqual(saved['workflow'], DOCUMENT)
        self.assertEqual(saved['workflowId'], '../opaque-card')
        self.assertEqual(saved['revision'], 7)

    def test_reference_allocation_reserves_explicit_slots_before_auto(self):
        document = {'nodes': [
            {'id': 1, 'type': 'LoadImage', 'widgets_values': ['old1.png', 'image']},
            {'id': 2, 'type': 'LoadImage', 'widgets_values': ['old2.png', 'image']}],
            'unknown': {'keep': ['原始文档']}}
        original = json.loads(json.dumps(document))
        uploads = []
        self.app.reference_asset = lambda ref: {'id': ref['assetId'], 'type': ref.get('type', 'image')}
        def upload(asset_id):
            uploads.append(asset_id)
            return asset_id + '.png'
        self.app.upload_to_comfy = upload
        prepared = self.app.prepare_workflow({'workflow': document, 'references': [
            {'assetId': 'auto-A'}, {'assetId': 'explicit-B', 'fieldId': 'nodes/0:image'}]})
        self.assertEqual([n['widgets_values'][0] for n in prepared['nodes']], ['explicit-B.png', 'auto-A.png'])
        self.assertEqual(document, original)
        self.assertEqual(set(uploads), {'auto-A', 'explicit-B'})
        invalid_cases = [
            ([{'assetId': 'A', 'fieldId': 'nodes/0:image'}, {'assetId': 'B', 'fieldId': 'nodes/0:image'}], '重复'),
            ([{'assetId': 'A', 'fieldId': 'nodes/404:image'}], '不存在'),
            ([{'assetId': 'A', 'fieldId': 'nodes/0:image', 'type': 'video'}], '类型'),
            ([{'assetId': 'A'}, {'assetId': 'B'}, {'assetId': 'C'}], '匹配输入'),
        ]
        for references, message in invalid_cases:
            with self.subTest(references=references):
                uploads.clear()
                with self.assertRaisesRegex(ValueError, message):
                    self.app.prepare_workflow({'workflow': document, 'references': references})
                self.assertEqual(uploads, [])
                self.assertEqual(document, original)

    def test_upload_opaque_path_and_content_validation(self):
        data = b'\x89PNG\r\n\x1a\n' + b'test-data'
        asset = self.app.store_asset('../../outside.png', 'image/png', data)
        path, metadata = self.app.asset(asset['id'])
        self.assertEqual(path.parent, self.app.assets)
        self.assertEqual(path.read_bytes(), data)
        self.assertEqual(metadata['name'], 'outside.png')
        with self.assertRaises(HTTPProblem):
            self.app.asset('../outside.png')
        with self.assertRaises(ValueError):
            self.app.store_asset('fake.png', 'image/png', b'<html>bad</html>')

    def test_only_loopback_engine_addresses(self):
        self.assertEqual(validate_comfy_url('http://127.0.0.1:8000/'), 'http://127.0.0.1:8000')
        for invalid in ('https://api.example.com:443', 'http://192.168.1.2:8000',
                        'http://127.0.0.1:8000/secret', 'http://user@localhost:8000'):
            with self.assertRaises(ValueError):
                validate_comfy_url(invalid)

    def test_http_roundtrip_range_and_conflict(self):
        (self.source / '测试工作流.json').write_text(json.dumps(DOCUMENT, ensure_ascii=False), encoding='utf-8')
        server = make_server(port=0, root=self.root, source=self.source)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        try:
            with urllib.request.urlopen(base + '/api/health') as response:
                self.assertEqual(json.load(response)['app'], 'creation-canvas')
            request = urllib.request.Request(base + '/api/project', data=b'{"revision":0,"cards":[]}',
                                             method='PUT', headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request) as response:
                self.assertEqual(json.load(response)['revision'], 1)
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            self.assertEqual(error.exception.code, 409)
            upload = urllib.request.Request(base + '/api/assets', data=b'\x89PNG\r\n\x1a\n123456', method='POST',
                                             headers={'Content-Type': 'image/png', 'X-Filename': '../../safe.png'})
            with urllib.request.urlopen(upload) as response:
                self.assertEqual(response.status, 201)
                asset = json.load(response)
            self.assertEqual(asset['name'], 'safe.png')
            range_request = urllib.request.Request(base + asset['url'], headers={'Range': 'bytes=8-10'})
            with urllib.request.urlopen(range_request) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.read(), b'123')
            with urllib.request.urlopen(base + '/api/catalog') as response:
                catalog = json.load(response)['items']
            self.assertEqual(len(catalog), 1)
            with urllib.request.urlopen(base + '/api/workflows/' + catalog[0]['id']) as response:
                detail = json.load(response)
            field = next(f for f in detail['fields'] if f['key'] == 'steps')
            prepare = urllib.request.Request(base + '/api/prepare-workflow', method='POST',
                                               data=json.dumps({'workflow_id': detail['id'], 'values': {field['id']: 25}}).encode(),
                                               headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(prepare) as response:
                prepared = json.load(response)['workflow']
            self.assertEqual(prepared['nodes'][0]['widgets_values'][2], 25)
            prepared['nodes'][0]['widgets_values'][2] = 40
            self.assertEqual(prepared, DOCUMENT)
            settings = urllib.request.Request(base + '/api/settings', method='PUT',
                                                data=b'{"comfy_url":"http://192.168.1.9:8000"}',
                                                headers={'Content-Type': 'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(settings)
            self.assertEqual(error.exception.code, 400)
            with urllib.request.urlopen(base + '/api/settings') as response:
                self.assertEqual(json.load(response)['comfy_url'], 'auto')
            malicious = urllib.request.Request(base + '/api/project', data=b'{"revision":1}', method='PUT',
                                               headers={'Content-Type': 'application/json', 'Origin': 'https://bad.example'})
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(malicious)
            self.assertEqual(error.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
