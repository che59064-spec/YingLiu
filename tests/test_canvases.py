"""Multi-canvas storage and task ownership; no engine or encoder is started."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import CanvasApp, HTTPProblem, atomic_json, make_server


class CanvasCheck(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.app = CanvasApp(self.root, self.root / 'source')

    def tearDown(self):
        self.temporary.cleanup()

    def test_migration_preserves_full_project_and_legacy_file(self):
        original = {'revision': 12, 'name': '动作迁移', 'cards': [{'id': 'a', 'workflow': {'opaque': [4]}}],
                    'edges': [{'from': 'a', 'to': 'b'}], 'viewport': {'x': -912, 'y': 55, 'zoom': .8},
                    'unknown': {'keep': ['原样保留']}}
        legacy = self.app.data / 'project.json'
        atomic_json(legacy, original)
        original_bytes = legacy.read_bytes()
        first = self.app.project()
        self.assertEqual({key: value for key, value in first.items() if key != 'id'}, original)
        self.assertEqual(legacy.read_bytes(), original_bytes)
        restarted = CanvasApp(self.root, self.root / 'source')
        self.assertEqual(restarted.project(), first)
        self.assertEqual(len(restarted.canvases()['items']), 1)

    def test_independent_revisions_rename_and_selected_canvas_survive_restart(self):
        first = self.app.project()
        second = self.app.create_canvas({'name': '第二个画布'})
        self.assertEqual(self.app.project()['id'], first['id'])
        self.assertEqual(second['cards'], [])
        first.update(cards=[{'id': 'first-card'}], edges=[{'from': 'first-card', 'to': 'x'}])
        saved_first = self.app.save_canvas(first['id'], first)
        second.update(name='  改名后的画布  ', cards=[{'id': 'second-card'}], viewport={'x': 10, 'y': 20, 'zoom': 1.2})
        saved_second = self.app.save_canvas(second['id'], second)
        self.assertEqual(saved_second['name'], '改名后的画布')
        self.assertEqual((saved_first['revision'], saved_second['revision']), (1, 1))
        with self.assertRaises(HTTPProblem) as raised:
            self.app.save_canvas(first['id'], first)
        self.assertEqual(raised.exception.status, 409)
        self.app.select_canvas(second['id'])
        restarted = CanvasApp(self.root, self.root / 'source')
        self.assertEqual(restarted.project(), saved_second)
        self.assertEqual(restarted.canvas(first['id']), saved_first)
        compatibility_save = restarted.save_project({**saved_first, 'name': '在原画布保存'})
        self.assertEqual(compatibility_save['id'], first['id'])
        self.assertEqual(restarted.project()['id'], second['id'])

    def test_invalid_ids_names_and_document_identity_cannot_overwrite(self):
        first = self.app.project()
        second = self.app.create_canvas({})
        for invalid in ('../project', '..\\project', 'index', '', None, 'a' * 31, 'A' * 32, 'f' * 32):
            with self.subTest(canvas_id=invalid), self.assertRaises(HTTPProblem):
                self.app.canvas(invalid)
            with self.subTest(select=invalid), self.assertRaises(HTTPProblem):
                self.app.select_canvas(invalid)
        for invalid in ('', '   ', '字' * 81, '换\n行', 12, None):
            with self.subTest(name=invalid), self.assertRaises(ValueError):
                self.app.create_canvas({'name': invalid})
        with self.assertRaises(ValueError):
            self.app.save_canvas(first['id'], second)
        self.assertEqual(self.app.canvas(first['id']), first)
        self.assertEqual(len(self.app.canvases()['items']), 2)

    def test_jobs_keep_original_canvas_after_switch_and_persistence(self):
        first = self.app.project()
        second = self.app.create_canvas({})
        document = {'nodes': [{'id': 1, 'type': 'NoOp', 'widgets_values': []}], 'links': []}
        self.app.resolve_workflow = lambda body: deepcopy(document)
        self.app.status = lambda: {'connected': True, 'bridgeConnected': True}
        self.app.preflight = lambda *args, **kwargs: {'ok': True}
        self.app.prepare_workflow = lambda body: deepcopy(document)
        self.app.missing_references = lambda doc: []
        clips = [{'url': '/api/comfy/view?filename=a.mp4&type=output'},
                 {'url': '/api/comfy/view?filename=b.mp4&type=output'}]
        with patch('server.threading.Thread') as worker:
            run = self.app.run({'canvas_id': first['id'], 'instance_id': 'same-card'})
            self.app.select_canvas(second['id'])
            joined = self.app.join_videos({'canvas_id': first['id'], 'instance_id': 'same-card', 'clips': clips})
            legacy = self.app.run({'instance_id': 'second-card'})
            self.assertEqual(worker.call_count, 3)
            for invalid in ('../project', None, 'f' * 32):
                with self.assertRaises(HTTPProblem):
                    self.app.run({'canvas_id': invalid})
                with self.assertRaises(HTTPProblem):
                    self.app.join_videos({'canvas_id': invalid, 'clips': clips})
            self.assertEqual(worker.call_count, 3)
        self.assertEqual(run['canvas_id'], first['id'])
        self.assertEqual(joined['canvas_id'], first['id'])
        self.assertEqual(legacy['canvas_id'], second['id'])
        for result in (run, joined, legacy):
            self.app.jobs[result['job_id']]['status'] = 'completed'
        self.app.store_terminal_jobs()
        restarted = CanvasApp(self.root, self.root / 'source')
        self.assertEqual(restarted.jobs[run['job_id']]['canvas_id'], first['id'])
        self.assertEqual(restarted.jobs[joined['job_id']]['canvas_id'], first['id'])
        self.assertEqual(restarted.jobs[legacy['job_id']]['canvas_id'], second['id'])

    def test_legacy_jobs_keep_migrated_canvas_with_duplicate_node_ids_after_restart(self):
        legacy_project = {**self.app.empty_canvas('原来的画布'), 'cards': [{'id': 'same-card'}]}
        atomic_json(self.app.data / 'project.json', legacy_project)
        original = self.app.project()
        second = self.app.create_canvas({'name': '导入的副本'})
        self.app.save_canvas(second['id'], {**second, 'cards': deepcopy(original['cards'])})
        self.app.select_canvas(second['id'])
        self.app.jobs = {
            'old-video': {'id': 'old-video', 'instance_id': 'same-card', 'status': 'completed',
                          'outputs': [{'url': '/assets/old.mp4', 'type': 'video'}]},
            'new-video': {'id': 'new-video', 'instance_id': 'same-card', 'status': 'completed',
                          'canvas_id': second['id'], 'outputs': []},
        }
        jobs = {job['id']: job for job in self.app.jobs_list()}
        self.assertEqual(jobs['old-video']['canvas_id'], original['id'])
        self.assertEqual(jobs['new-video']['canvas_id'], second['id'])
        self.assertEqual(self.app.project()['id'], second['id'])
        restarted = CanvasApp(self.root, self.root / 'source')
        self.assertEqual(restarted.jobs['old-video']['canvas_id'], original['id'])
        self.assertEqual(restarted.jobs['new-video']['canvas_id'], second['id'])
        with patch('server.atomic_json') as write:
            restarted.jobs_list()
        write.assert_not_called()

    def test_legacy_job_migration_does_not_eagerly_create_canvas(self):
        atomic_json(self.app.data / 'jobs.json', {'legacy': {'id': 'legacy', 'status': 'completed'}})
        restarted = CanvasApp(self.root, self.root / 'source')
        self.assertFalse((restarted.data / 'canvases' / 'index.json').exists())
        original = {**restarted.empty_canvas('迟写入的原画布'), 'cards': [{'id': 'source'}]}
        atomic_json(restarted.data / 'project.json', original)
        jobs = restarted.jobs_list()
        self.assertEqual(restarted.project()['name'], original['name'])
        self.assertEqual(restarted.project()['cards'], original['cards'])
        self.assertEqual(jobs[0]['canvas_id'], restarted.project()['id'])

    def test_http_create_select_save_conflict_and_legacy_endpoint(self):
        server = make_server(port=0, root=self.root, source=self.root / 'source')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def request(path, method='GET', value=None):
            body = None if value is None else json.dumps(value).encode()
            req = urllib.request.Request(base + path, method=method, data=body,
                                         headers={'Content-Type': 'application/json'})
            with opener.open(req, timeout=5) as response:
                return response.status, json.load(response)

        try:
            _, index = request('/api/canvases')
            first_id = index['active_id']
            status, second = request('/api/canvases', 'POST', {'name': '视频分镜'})
            self.assertEqual(status, 201)
            _, selected = request('/api/canvases/active', 'PUT', {'id': second['id']})
            self.assertEqual(selected['active_id'], second['id'])
            self.assertEqual(request('/api/project')[1], second)
            second['cards'] = [{'id': 'my-card'}]
            _, saved = request('/api/canvases/' + second['id'], 'PUT', second)
            self.assertEqual(saved['revision'], 1)
            with self.assertRaises(urllib.error.HTTPError) as raised:
                request('/api/canvases/' + second['id'], 'PUT', second)
            self.assertEqual(raised.exception.code, 409)
            self.assertEqual(request('/api/canvases/' + first_id)[1]['cards'], [])
            self.assertEqual(request('/api/project')[1], saved)
            server.app.jobs['legacy'] = {'id': 'legacy', 'instance_id': 'my-card', 'status': 'completed'}
            _, job_list = request('/api/jobs')
            self.assertEqual(job_list['items'][0]['canvas_id'], first_id)
            with self.assertRaises(urllib.error.HTTPError) as raised:
                request('/api/canvases/%2e%2e%2fproject')
            self.assertEqual(raised.exception.code, 404)
            with self.assertRaises(urllib.error.HTTPError) as raised:
                request('/api/canvases/active', 'PUT', [])
            self.assertEqual(raised.exception.code, 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
