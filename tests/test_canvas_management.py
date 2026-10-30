"""Canvas tab management uses only disposable files and fake engine calls."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import CanvasApp, HTTPProblem, atomic_json, make_server


class CanvasManagementCheck(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = CanvasApp(self.root, self.root / 'workflows')
        self.first = self.app.project()
        self.second = self.app.create_canvas({'name': '第二张'})
        self.third = self.app.create_canvas({'name': '第三张'})

    def tearDown(self):
        self.temp.cleanup()

    def test_order_survives_restart_without_touching_canvas_or_active_selection(self):
        self.app.select_canvas(self.second['id'])
        before = {doc['id']: self.app.canvas_path(doc['id']).read_bytes()
                  for doc in (self.first, self.second, self.third)}
        order = [self.third['id'], self.first['id'], self.second['id']]
        result = self.app.order_canvases({'ids': order})
        self.assertEqual([item['id'] for item in result['items']], order)
        self.assertEqual(result['active_id'], self.second['id'])
        for identity, data in before.items():
            self.assertEqual(self.app.canvas_path(identity).read_bytes(), data)
        restarted = CanvasApp(self.root, self.root / 'workflows')
        self.assertEqual(restarted.canvases(), result)

    def test_order_rejects_incomplete_duplicate_and_invalid_ids(self):
        original = self.app.canvases()
        for invalid in (None, [], {}, {'ids': None}, {'ids': [self.first['id']]},
                        {'ids': [self.first['id']] * 3}, {'ids': ['../outside']},
                        {'ids': [self.first['id'], self.second['id'], 'f' * 32]}):
            with self.subTest(body=invalid), self.assertRaises((HTTPProblem, ValueError)):
                self.app.order_canvases(invalid)
            self.assertEqual(self.app.canvases(), original)

    def test_rename_changes_only_name_and_revision_and_blocks_stale_save(self):
        doc = self.app.save_canvas(self.second['id'], {
            **self.second, 'cards': [{'id': 'a', 'unknown': [3]}], 'custom': {'keep': True}})
        renamed = self.app.rename_canvas(doc['id'], {'name': '  视频分镜  ', 'expectedRevision': doc['revision']})
        self.assertEqual(renamed['name'], '视频分镜')
        self.assertEqual(renamed['revision'], doc['revision'] + 1)
        for key in ('cards', 'edges', 'viewport', 'custom', 'id'):
            self.assertEqual(renamed[key], doc[key])
        with self.assertRaises(HTTPProblem) as caught:
            self.app.save_canvas(doc['id'], doc)
        self.assertEqual(caught.exception.status, 409)
        for invalid in ({'name': 'bad', 'expectedRevision': doc['revision']},
                        {'name': 'bad'}, {'name': 'bad', 'expectedRevision': True}):
            with self.subTest(body=invalid), self.assertRaises(HTTPProblem):
                self.app.rename_canvas(doc['id'], invalid)
        with self.assertRaises(ValueError):
            self.app.rename_canvas(doc['id'], {'name': 'bad', 'expectedRevision': renamed['revision'], 'cards': []})

    def test_delete_archives_full_document_and_preserves_assets_jobs_and_other_canvases(self):
        document = self.app.save_canvas(self.second['id'], {
            **self.second, 'cards': [{'id': 'source', 'workflow': {'private': 'kept'}}],
            'custom': ['keep'], 'viewport': {'x': 35, 'y': -21, 'zoom': .9}})
        asset = self.app.store_asset('sample.png', 'image/png', b'\x89PNG\r\n\x1a\n')
        self.app.jobs['done'] = {'id': 'done', 'canvas_id': document['id'], 'status': 'completed',
                                 'outputs': [{'assetId': asset['id']}]}
        self.app.select_canvas(document['id'])
        first_bytes = self.app.canvas_path(self.first['id']).read_bytes()
        result = self.app.delete_canvas(document['id'], {'expectedRevision': document['revision']})
        self.assertEqual(result['deleted_id'], document['id'])
        self.assertEqual(result['active_id'], self.third['id'])
        self.assertEqual(result['active_canvas'], self.third)
        self.assertEqual(self.app.canvas_path(self.first['id']).read_bytes(), first_bytes)
        self.assertTrue(self.app.asset(asset['id'])[0].is_file())
        self.assertIn('done', self.app.jobs)
        archives = list((self.app.data / 'canvases' / 'trash').glob('*.json'))
        self.assertEqual(len(archives), 1)
        self.assertEqual(json.loads(archives[0].read_text(encoding='utf-8'))['canvas'], document)
        with self.assertRaises(HTTPProblem):
            self.app.canvas(document['id'])
        restarted = CanvasApp(self.root, self.root / 'workflows')
        self.assertEqual(restarted.project()['id'], self.third['id'])
        self.assertEqual(len(restarted.canvases()['items']), 2)

    def test_delete_rejects_last_canvas_active_tasks_and_stale_revision(self):
        for status in ('pending', 'submitting', 'submitted', 'queued', 'running'):
            self.app.jobs = {'busy': {'canvas_id': self.second['id'], 'status': status}}
            with self.subTest(status=status), self.assertRaises(HTTPProblem) as caught:
                self.app.delete_canvas(self.second['id'])
            self.assertEqual(caught.exception.status, 409)
        self.app.jobs = {}
        with self.assertRaises(HTTPProblem):
            self.app.delete_canvas(self.second['id'], {'expectedRevision': 25})
        self.app.delete_canvas(self.second['id'])
        self.app.delete_canvas(self.third['id'])
        with self.assertRaises(HTTPProblem) as caught:
            self.app.delete_canvas(self.first['id'])
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.app.project(), self.first)

    def test_failed_trash_or_index_write_leaves_original_canvas_accessible(self):
        real_write = atomic_json
        for target in ('trash', 'index.json'):
            def fail(path, value):
                if target in Path(path).parts:
                    raise OSError('simulated disk failure')
                return real_write(path, value)
            with self.subTest(target=target), patch('server.atomic_json', side_effect=fail):
                with self.assertRaises(OSError):
                    self.app.delete_canvas(self.second['id'])
            self.assertEqual(self.app.canvas(self.second['id']), self.second)
            self.assertIn(self.second['id'], self.app.canvas_index()['ids'])

    def test_legacy_task_ownership_does_not_follow_first_tab_after_reorder(self):
        self.app.jobs = {'old': {'id': 'old', 'status': 'completed'}}
        self.app.order_canvases({'ids': [self.third['id'], self.second['id'], self.first['id']]})
        self.assertEqual(self.app.jobs['old']['canvas_id'], self.first['id'])
        # A later older-format record still belongs to the original canvas.
        self.app.jobs['later-legacy'] = {'id': 'later-legacy', 'status': 'queued'}
        self.app.jobs_list()
        self.assertEqual(self.app.jobs['later-legacy']['canvas_id'], self.first['id'])
        with self.assertRaises(HTTPProblem):
            self.app.delete_canvas(self.first['id'])

    def test_deleted_during_preflight_never_submits_an_orphan_job(self):
        graph = {'1': {'class_type': 'NoOp', 'inputs': {'text': 'fake'}}}
        self.app.resolve_workflow = lambda body: deepcopy(graph)
        self.app.status = lambda: {'connected': True, 'bridgeConnected': False}
        def preflight(*args, **kwargs):
            self.app.delete_canvas(self.second['id'])
            return {'ok': True}
        self.app.preflight = preflight
        self.app.prepare_workflow = lambda body: deepcopy(graph)
        self.app.missing_references = lambda graph: []
        with patch('server.threading.Thread') as worker:
            with self.assertRaises(HTTPProblem):
                self.app.run({'canvas_id': self.second['id']})
        worker.assert_not_called()
        self.assertFalse(self.app.jobs)

    def test_http_routes_and_cross_origin_delete_protection(self):
        server = make_server(port=0, root=self.root, source=self.root / 'workflows')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def request(path, method='GET', body=None, origin=None):
            headers = {'Content-Type': 'application/json'}
            if origin:
                headers['Origin'] = origin
            req = urllib.request.Request(base + path, method=method,
                data=None if body is None else json.dumps(body).encode(), headers=headers)
            with http.open(req, timeout=5) as reply:
                return json.load(reply)
        try:
            order = [self.third['id'], self.second['id'], self.first['id']]
            result = request('/api/canvases/order', 'PUT', {'ids': order})
            self.assertEqual([item['id'] for item in result['items']], order)
            renamed = request('/api/canvases/' + self.second['id'] + '/name', 'PUT', {'name': 'API改名', 'expectedRevision': 0})
            self.assertEqual(renamed['name'], 'API改名')
            with self.assertRaises(urllib.error.HTTPError) as caught:
                request('/api/canvases/' + self.second['id'], 'DELETE', {'expectedRevision': 1}, 'https://outside.example')
            self.assertEqual(caught.exception.code, 403)
            result = request('/api/canvases/' + self.second['id'], 'DELETE', {'expectedRevision': 1})
            self.assertEqual(result['deleted_id'], self.second['id'])
            self.assertEqual(len(result['items']), 2)
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
