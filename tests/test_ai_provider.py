"""Only temporary settings and loopback fake providers; no real AI calls."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ai_provider import ProviderProblem, ProviderStore, endpoint, protocol_payload, request
from prompt_optimizer import OptimizerProblem, PromptOptimizer, credentials, response_format


class ProviderCheck(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copy2(ROOT / 'prompt_rules.json', self.root / 'prompt_rules.json')
        self.store = ProviderStore(self.root, lambda: {})

    def tearDown(self):
        self.temp.cleanup()

    def save(self, **kwargs):
        return self.store.save({'provider': 'openai', 'baseUrl': 'https://example.com/v1', 'model': 'custom-model',
                                'apiKey': 'fake-private-test-key', 'vision': False, 'responseFormat': 'prompt', **kwargs})

    def test_settings_are_local_secret_is_bound_and_empty_does_not_erase(self):
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('no network on save')):
            self.save()
            self.assertEqual(self.store.save({'apiKey': ''})['apiKey'], 'fake-private-test-key')
            self.assertEqual(self.store.save({'model': 'second-model'})['apiKey'], 'fake-private-test-key')
            self.assertEqual(self.store.save({'baseUrl': 'https://different.example/v1'})['apiKey'], '')
            self.save()
            self.assertEqual(self.store.save({'clearKey': True})['apiKey'], '')
        if os.name == 'nt':
            self.save()
            self.assertNotIn('fake-private-test-key', self.store.path.read_text())
        else:
            self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)

    def test_editing_saved_host_does_not_reuse_old_credential(self):
        self.save()
        stored = json.loads(self.store.path.read_text())
        stored['baseUrl'] = 'https://changed.example/v1'
        self.store.path.write_text(json.dumps(stored))
        self.assertEqual(self.store.load()['apiKey'], '')

    def test_corrupt_settings_can_be_replaced_with_complete_valid_settings(self):
        self.save()
        self.store.path.write_text('{broken')
        with self.assertRaises(ProviderProblem):
            self.store.load()
        self.assertEqual(self.save()['model'], 'custom-model')

    def test_invalid_settings_never_replace_previous_configuration(self):
        original = self.save()
        for body in ({'provider': 'other'}, {'baseUrl': 'http://untrusted.example/v1'},
                     {'baseUrl': 'https://example.com/v1?api_key=secret'}, {'baseUrl': 'https://u:p@example.com'},
                     {'baseUrl': 'https://example.com/#key'}, {'model': ''}, {'vision': 'true'},
                     {'apiKey': 'bad\r\nheader'}, {'responseFormat': 'invalid'}, {'unknown': True}):
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    self.store.save(body)
                self.assertEqual(self.store.load(), original)

    def test_endpoints_accept_base_and_full_paths(self):
        cases = [('openai', 'https://example.com', 'https://example.com/v1/chat/completions'),
                 ('openai', 'https://example.com/custom/v1', 'https://example.com/custom/v1/chat/completions'),
                 ('openai', 'http://localhost:1234/v1/chat/completions', 'http://localhost:1234/v1/chat/completions'),
                 ('anthropic', 'https://api.anthropic.com/v1', 'https://api.anthropic.com/v1/messages'),
                 ('anthropic', 'https://api.anthropic.com', 'https://api.anthropic.com/v1/messages'),
                 ('ollama', 'http://[::1]:11434/api', 'http://[::1]:11434/api/chat')]
        for provider, base, expected in cases:
            self.assertEqual(endpoint(provider, base), expected)
        for base in ('file:///private', 'https://example.com\\@other', 'http://192.168.1.2', 'https://example.com:99999'):
            with self.assertRaises(ValueError):
                endpoint('openai', base)

    def test_status_never_exposes_key_and_provider_can_be_changed(self):
        optimizer = PromptOptimizer(self.root, credential_loader=lambda: {})
        status = optimizer.configure({'provider': 'openai', 'baseUrl': 'https://example.com/v1',
                                      'model': 'custom-name', 'apiKey': 'fake-private-test-key', 'vision': False})
        self.assertTrue(status['configured'])
        self.assertTrue(status['hasKey'])
        self.assertEqual(status['keySource'], 'local')
        self.assertNotIn('private-test', json.dumps(status))
        self.assertNotIn('protectedKey', json.dumps(optimizer.status()))
        self.assertIn('generic', optimizer.status()['profiles'])
        optimizer.configure({'provider': 'ollama', 'model': 'local-model'})
        self.assertFalse(optimizer.config_status()['hasKey'])
        self.assertTrue(optimizer.config_status()['configured'])

    def test_credentials_only_read_explicit_application_file(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(Path, 'read_text', side_effect=AssertionError('no implicit sibling file')):
            self.assertEqual(credentials(), {})

    def test_generic_workflow_uses_bilingual_output_and_text_mode_never_reads_images(self):
        calls = []
        def transport(base, key, payload):
            calls.append(deepcopy(payload))
            return {'choices': [{'message': {'content': json.dumps({'prompt': 'A small cat sits beside the window in soft daylight.',
                'translation': '一只小猫坐在窗户旁边，柔和的自然日光照亮它的身体。', 'summary': '整理画面描述', 'warnings': []})}}]}
        media = Mock(side_effect=AssertionError('text-only must not read media'))
        optimizer = PromptOptimizer(self.root, credential_loader=lambda: {}, transport=transport, media_loader=media)
        optimizer.configure({'provider': 'ollama', 'model': 'my-local-model', 'vision': False})
        doc = {'nodes': [{'id': 1, 'type': 'LoadImage', 'widgets_values': ['private-reference.png', 'image']},
                         {'id': 2, 'type': 'MyCustomGenerator', 'widgets_values': []}]}
        result = optimizer.optimize(doc, {'prompt': '一只小猫在窗边', 'references': [{'type': 'image', 'role': 'cat identity'}]})
        self.assertEqual(result['profile'], 'generic')
        self.assertEqual(result['model'], 'my-local-model')
        self.assertEqual(result['referencePolicy'], 'text_only')
        self.assertEqual(result['sources'], [])
        self.assertTrue(any('没有发送' in warning for warning in result['warnings']))
        self.assertEqual(len(calls), 1)
        self.assertIn('JSON output schema', calls[0]['messages'][0]['content'])
        media.assert_not_called()

    def test_manual_test_sends_only_one_short_fixed_message(self):
        transport = Mock(return_value={'choices': [{'message': {'content': 'OK'}}]})
        optimizer = PromptOptimizer(self.root, credential_loader=lambda: {}, transport=transport)
        optimizer.configure({'provider': 'ollama', 'model': 'local-model'})
        self.assertTrue(optimizer.test_connection()['ok'])
        transport.assert_called_once()
        self.assertEqual(transport.call_args.args[2]['messages'], [{'role': 'user', 'content': 'Reply with OK.'}])
        self.assertEqual(transport.call_args.args[2]['max_tokens'], 32)

    def test_limited_reasoning_connection_check_does_not_retry_or_increase_budget(self):
        transport = Mock(return_value={'choices': [{'finish_reason': 'length', 'message': {'content': None}}]})
        optimizer = PromptOptimizer(self.root, credential_loader=lambda: {}, transport=transport)
        optimizer.configure({'provider': 'openai', 'baseUrl': 'https://provider.example/v1', 'model': 'o3', 'apiKey': 'fake-key'})
        result = optimizer.test_connection()
        self.assertTrue(result['ok'])
        self.assertIn('尚未验证完整文字输出', result['message'])
        transport.assert_called_once()
        self.assertEqual(transport.call_args.args[2]['max_tokens'], 32)
        transport.reset_mock()
        transport.return_value = {'choices': [{'finish_reason': 'stop', 'message': {'content': ''}}]}
        with self.assertRaises(OptimizerProblem):
            optimizer.test_connection()
        transport.assert_called_once()

    def test_protocol_visuals_and_response_options(self):
        payload = {'model': 'custom', 'messages': [{'role': 'system', 'content': 'system instructions'},
                    {'role': 'user', 'content': [{'type': 'text', 'text': 'reference image'},
                    {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,aGVsbG8='}}]}],
                   'max_tokens': 8192, 'temperature': 0.3, 'stream': False, 'response_format': response_format('generic')}
        a = protocol_payload({'provider': 'anthropic', 'responseFormat': 'prompt'}, payload)
        self.assertEqual(a['system'], 'system instructions')
        self.assertEqual(a['messages'][0]['content'][1]['source'], {'type': 'base64', 'media_type': 'image/jpeg', 'data': 'aGVsbG8='})
        self.assertNotIn('response_format', a)
        a = protocol_payload({'provider': 'anthropic', 'responseFormat': 'json_schema'}, payload)
        self.assertIn('schema', a['output_config']['format'])
        o = protocol_payload({'provider': 'ollama', 'responseFormat': 'json_object'}, payload)
        self.assertEqual(o['messages'][1]['images'], ['aGVsbG8='])
        self.assertIn('[Attached image 1', o['messages'][1]['content'])
        self.assertEqual(o['format'], 'json')
        self.assertEqual(o['options']['num_predict'], 8192)
        self.assertNotIn('response_format', protocol_payload({'provider': 'openai', 'responseFormat': 'prompt'}, payload))
        self.assertEqual(protocol_payload({'provider': 'openai', 'responseFormat': 'json_object'}, payload)['response_format'], {'type': 'json_object'})
        self.assertEqual(protocol_payload({'provider': 'openai', 'responseFormat': 'json_schema'}, payload)['response_format']['type'], 'json_schema')

    def test_known_reasoning_names_use_supported_parameters_without_removing_output_cap(self):
        for model in ('o1', 'o1-2024-12-17', 'o3', 'o3-mini', 'o4-mini', 'gpt-5', 'gpt-5-mini', 'gpt-5.2', 'gpt-6-astra'):
            for limit in (32, 8192):
                with self.subTest(model=model, limit=limit):
                    payload = {'model': model, 'messages': [{'role': 'user', 'content': 'hello'}],
                               'max_tokens': limit, 'temperature': 0.3, 'stream': False}
                    original = deepcopy(payload)
                    result = protocol_payload({'provider': 'openai', 'responseFormat': 'prompt'}, payload)
                    self.assertEqual(result['max_completion_tokens'], limit)
                    self.assertNotIn('max_tokens', result)
                    self.assertNotIn('temperature', result)
                    self.assertEqual(payload, original)
        for provider, model in (('agnes', 'gpt-5'), ('openai', 'gpt-4o'), ('openai', 'qwen-plus'),
                                ('openai', 'deepseek-reasoner'), ('openai', 'custom-reasoning-alias')):
            with self.subTest(provider=provider, model=model):
                payload = {'model': model, 'messages': [], 'max_tokens': 8192, 'temperature': 0.3}
                result = protocol_payload({'provider': provider, 'responseFormat': 'prompt'}, payload)
                self.assertEqual(result['max_tokens'], 8192)
                self.assertEqual(result['temperature'], 0.3)
                self.assertNotIn('max_completion_tokens', result)

    def test_real_loopback_protocols_never_redirect_or_retry(self):
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append((self.path, dict(self.headers), body))
                if self.path == '/redirect/chat/completions':
                    self.send_response(307)
                    self.send_header('Location', '/leak')
                    self.end_headers()
                    return
                if self.path.endswith('/messages'):
                    result = {'content': [{'type': 'text', 'text': 'anthropic OK'}], 'stop_reason': 'end_turn'}
                elif self.path.endswith('/api/chat'):
                    result = {'message': {'content': 'ollama OK'}, 'done_reason': 'stop'}
                else:
                    result = {'choices': [{'message': {'content': 'compatible OK'}}]}
                raw = json.dumps(result).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        payload = {'model': 'custom', 'messages': [{'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'hello'}], 'max_tokens': 32}
        try:
            for provider, expected in [('openai', 'compatible OK'), ('anthropic', 'anthropic OK'), ('ollama', 'ollama OK')]:
                result = request({'provider': provider, 'baseUrl': base, 'apiKey': 'fake-test-key', 'responseFormat': 'prompt'}, payload)
                self.assertEqual(result['choices'][0]['message']['content'], expected)
            self.assertEqual(calls[0][0], '/v1/chat/completions')
            self.assertEqual(calls[0][1]['Authorization'], 'Bearer fake-test-key')
            self.assertEqual(calls[1][1]['X-Api-Key'], 'fake-test-key')
            self.assertEqual(calls[1][1]['Anthropic-Version'], '2023-06-01')
            self.assertEqual(calls[2][0], '/api/chat')
            with self.assertRaises(ProviderProblem) as caught:
                request({'provider': 'openai', 'baseUrl': base + '/redirect', 'apiKey': 'fake-test-key', 'responseFormat': 'prompt'}, payload)
            self.assertIn('重定向', str(caught.exception))
            self.assertEqual(len(calls), 4)
            self.assertNotIn('/leak', [call[0] for call in calls])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
