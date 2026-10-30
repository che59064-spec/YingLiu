"""Public endpoints and native API submission, entirely with temporary data/fake engines."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from prompt_optimizer import PromptOptimizer
from server import CanvasApp, HTTPProblem, make_server

API_GRAPH = {'1': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'A cat beside a window'}},
             '2': {'class_type': 'SaveImage', 'inputs': {'images': ['1', 0], 'filename_prefix': 'test'}}}


class PublicEndpointCheck(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copy2(ROOT / 'prompt_rules.json', self.root / 'prompt_rules.json')
        self.server = make_server(port=0, root=self.root, source=self.root / 'workflows')
        self.transport = Mock(return_value={'choices': [{'message': {'content': 'OK'}}]})
        self.server.app.prompt_optimizer = PromptOptimizer(self.root, credential_loader=lambda: {}, transport=self.transport)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def call(self, method, path, body=None, headers=None):
        supplied = {'Content-Type': 'application/json', **(headers or {})}
        data = None if body is None else json.dumps(body).encode('utf-8')
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=supplied)
        try:
            with self.http.open(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.load(error)

    def configure(self):
        status, body = self.call('PUT', '/api/ai-provider', {'provider': 'openai', 'baseUrl': 'https://provider.example/v1',
            'model': 'my-custom-model', 'apiKey': 'fake-release-test-key', 'vision': False, 'responseFormat': 'prompt'})
        self.assertEqual(status, 200, body)
        self.assertTrue(body['configured'])
        return body

    def test_provider_get_put_and_project_do_not_reveal_secret(self):
        result = self.configure()
        self.assertTrue(result['hasKey'])
        for path in ('/api/ai-provider', '/api/prompt-optimizer', '/api/settings', '/api/project', '/api/canvases'):
            status, body = self.call('GET', path)
            self.assertEqual(status, 200, body)
            text = json.dumps(body)
            self.assertNotIn('fake-release-test-key', text)
            self.assertNotIn('protectedKey', text)
        self.transport.assert_not_called()
        status, result = self.call('PUT', '/api/ai-provider', {'model': 'renamed-model', 'apiKey': ''})
        self.assertEqual(status, 200)
        self.assertTrue(result['hasKey'])
        status, result = self.call('PUT', '/api/ai-provider', {'baseUrl': 'https://different.example/v1', 'apiKey': ''})
        self.assertEqual(status, 200)
        self.assertFalse(result['hasKey'])
        self.transport.assert_not_called()

    def test_cross_origin_host_and_non_json_requests_are_rejected_before_ai(self):
        self.configure()
        for method, path, body in [('GET', '/api/ai-provider', None), ('PUT', '/api/ai-provider', {'clearKey': True}),
                                   ('POST', '/api/ai-provider/test', {})]:
            status, _ = self.call(method, path, body, {'Origin': 'https://untrusted.example'})
            self.assertEqual(status, 403)
        status, _ = self.call('GET', '/api/ai-provider', headers={'Host': 'untrusted.example'})
        self.assertEqual(status, 403)
        status, _ = self.call('PUT', '/api/ai-provider', {}, {'Content-Type': 'text/plain'})
        self.assertEqual(status, 415)
        self.assertTrue(self.server.app.prompt_optimizer.config_status()['hasKey'])
        self.transport.assert_not_called()

    def test_test_endpoint_accepts_no_user_content_and_makes_one_fixed_request(self):
        self.configure()
        status, _ = self.call('POST', '/api/ai-provider/test', {'prompt': 'private user content'})
        self.assertEqual(status, 400)
        self.transport.assert_not_called()
        status, body = self.call('POST', '/api/ai-provider/test', {})
        self.assertEqual(status, 200, body)
        self.assertTrue(body['ok'])
        self.transport.assert_called_once()
        payload = self.transport.call_args.args[2]
        self.assertEqual(payload['messages'], [{'role': 'user', 'content': 'Reply with OK.'}])
        self.assertNotIn('private', json.dumps(payload))

    def test_general_settings_cannot_store_api_keys_or_modify_project(self):
        previous = self.server.app.project()
        status, _ = self.call('PUT', '/api/settings', {'apiKey': 'fake-release-test-key'})
        self.assertEqual(status, 400)
        self.assertEqual(self.server.app.project(), previous)
        self.assertNotIn('apiKey', self.server.app.settings())
        self.assertFalse((self.server.app.data / 'settings.json').exists())

    def test_unknown_workflow_scalars_and_embedded_credentials_never_enter_ai_messages(self):
        self.configure()
        self.transport.return_value = {'choices': [{'message': {'content': json.dumps({
            'prompt': 'A small cat rests beside the window.',
            'translation': '一只小猫安静地待在窗户旁边，保留用户指定的主体与画面关系。',
            'summary': '整理描述', 'warnings': []})}}]}
        document = {'1': {'class_type': 'CustomRemoteImage', 'inputs': {
            'prompt': '小猫在窗边', 'api_key': 'workflow-private-credential',
            'custom_scalar': 'unknown-private-scalar', 'filename': 'private-filename.txt',
            'authorization': 'private-auth-token', 'extra': {'secret': 'private-nested-token'}}}}
        original = deepcopy(document)
        status, result = self.call('POST', '/api/prompt-optimizer', {'workflow': document, 'prompt': '小猫在窗边'})
        self.assertEqual(status, 200, result)
        self.assertEqual(result['profile'], 'generic')
        self.transport.assert_called_once()
        messages = json.dumps(self.transport.call_args.args[2]['messages'])
        for private in ('workflow-private-credential', 'unknown-private-scalar', 'private-filename.txt',
                        'private-auth-token', 'private-nested-token', 'fake-release-test-key'):
            self.assertNotIn(private, messages)
        self.assertEqual(document, original)
        self.assertEqual(self.server.app.jobs, {})


class NativeApiExecutionCheck(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = CanvasApp(self.temp.name, Path(self.temp.name) / 'workflows')
        self.canvas = self.app.project()

    def tearDown(self):
        self.temp.cleanup()

    def job(self):
        job = {'id': 'fake-job', 'canvas_id': self.canvas['id'], 'instance_id': 'fake-card',
               'status': 'submitting', 'outputs': []}
        self.app.jobs[job['id']] = job
        return job

    def test_api_execution_posts_once_and_never_accesses_bridge(self):
        job = self.job()
        original = deepcopy(API_GRAPH)
        self.app.comfy = Mock(side_effect=[{'prompt_id': 'fake-prompt'}, {'fake-prompt': {
            'status': {'status_str': 'success'}, 'outputs': {'2': {'images': [
                {'filename': 'unit.png', 'type': 'output', 'subfolder': ''}]}}}}])
        self.app._run_job(job['id'], API_GRAPH)
        self.assertEqual(job['status'], 'completed')
        self.assertEqual(job['outputs'][0]['name'], 'unit.png')
        calls = self.app.comfy.call_args_list
        self.assertEqual([call.args[0] for call in calls], ['/prompt', '/history/fake-prompt'])
        self.assertEqual(calls[0].args[1], 'POST')
        self.assertEqual(calls[0].args[2]['prompt'], API_GRAPH)
        self.assertEqual(API_GRAPH, original)

    def test_lost_submission_response_does_not_retry_or_write_bridge(self):
        job = self.job()
        self.app.comfy = Mock(side_effect=HTTPProblem(503, 'response lost'))
        self.app._run_job(job['id'], API_GRAPH)
        self.assertEqual(job['status'], 'failed')
        self.app.comfy.assert_called_once()
        self.assertEqual(self.app.comfy.call_args.args[0], '/prompt')

    def test_missing_prompt_confirmation_does_not_retry(self):
        job = self.job()
        self.app.comfy = Mock(return_value={'node_errors': {'2': {'error': 'fake invalid input'}}})
        self.app._run_job(job['id'], API_GRAPH)
        self.assertEqual(job['status'], 'failed')
        self.assertIn('未自动重提', job['error'])
        self.app.comfy.assert_called_once()

    def test_api_run_does_not_require_connected_browser_bridge(self):
        self.app.status = Mock(return_value={'connected': True, 'bridgeConnected': False})
        self.app.preflight = Mock(return_value={'ok': True, 'missingNodes': [], 'missingModels': []})
        self.app.prepare_workflow = Mock(return_value=deepcopy(API_GRAPH))
        self.app.missing_references = Mock(return_value=[])
        self.app.comfy = Mock(side_effect=AssertionError('no engine calls from this test'))
        with patch('server.threading.Thread') as worker:
            result = self.app.run({'workflow': API_GRAPH, 'canvas_id': self.canvas['id'], 'instance_id': 'fake-card'})
            worker.assert_called_once()
            worker.return_value.start.assert_called_once()
        self.assertEqual(result['canvas_id'], self.canvas['id'])
        self.assertEqual(result['status'], 'submitting')
        self.app.comfy.assert_not_called()


if __name__ == '__main__':
    unittest.main()
