"""No paid requests. Verify routing, privacy, reference order and review-only output."""
import copy
import io
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
from prompt_optimizer import PromptOptimizer, OptimizerProblem, credentials, profile_for, reference_context, normalize_output, full_chinese_translation, decode_output
from server import make_server

H3_TEXT = ('subject_definitions: <Subject 1> is the identity depicted in the supplied pictures.\n'
           'summary: Preserve every source person as an instance of <Subject 1>.\n'
           'retention_analysis: <Subject 1>: fully_preserved - preserve the depicted identity.\n'
           'detailed_description: [Shot 1] Every person follows the actual motion in <Video 1>.\n'
           'overall_soundscape: Unobtrusive natural ambience.\nnon_diegetic_music: N/A')
H3_ZH = ('主体定义：主体1是所提供参考图片中展示的同一个人物身份。\n'
         '概述：将原视频中的每一个人物都替换成主体1的独立实例，保持统一身份。\n'
         '保留关系：完整保留主体1在参考图片中展示的外貌特征。\n'
         '详细画面：镜头1，每一个人物都跟随参考视频1中的实际动作。\n'
         '整体声音：自然且不突兀的环境声。\n背景音乐：无。')


def graph():
    return {'nodes': [
        {'id': 20, 'type': 'LoadImage', 'widgets_values': [r'C:\private\front.png', 'image']},
        {'id': 10, 'type': 'LoadImage', 'widgets_values': ['private-back-name.png', 'image']},
        {'id': 30, 'type': 'VHS_LoadVideo', 'widgets_values': {'video': 'private-action.mp4'}},
        {'id': 40, 'type': 'MiniMaxH3ReferenceToVideo', 'widgets_values': ['', 864, 480, 124, 'match'],
         'inputs': [{'name': 'ref_images.ref_image_0', 'link': 1}, {'name': 'ref_images.ref_image_1', 'link': 2},
                    {'name': 'ref_videos.ref_video_0', 'link': 3}]}],
        'links': [[1, 10, 0, 40, 0, 'IMAGE'], [2, 20, 0, 40, 1, 'IMAGE'], [3, 30, 0, 40, 2, 'IMAGE']]}


class PromptOptimizerCheck(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        shutil.copy2(ROOT / 'prompt_rules.json', self.root / 'prompt_rules.json')
        self.calls = []
        def transport(base, key, payload):
            self.calls.append((base, key, copy.deepcopy(payload)))
            return {'choices': [{'message': {'content': json.dumps({'prompt': H3_TEXT, 'translation': H3_ZH, 'summary': '参考分工已整理', 'warnings': []})}}]}
        self.optimizer = PromptOptimizer(self.root, credential_loader=lambda: {'AGNES_API_KEY': 'unit-private-key'}, transport=transport)

    def tearDown(self):
        self.temporary.cleanup()

    def test_reference_labels_follow_real_ports_not_ui_or_loader_order(self):
        document = graph()
        original = copy.deepcopy(document)
        refs = [{'type': 'image', 'name': r'C:\private\named.png', 'role': 'front view'},
                {'type': 'image', 'fieldId': 'nodes/1:image', 'role': 'back view'},
                {'type': 'video', 'role': 'motion only'}]
        context = reference_context(document, refs)
        self.assertEqual([r['label'] for r in context], ['<Picture 1>', '<Picture 2>', '<Video 1>'])
        self.assertEqual([r['role'] for r in context], ['back view', 'front view', 'motion only'])
        body = {'prompt': '用参考角色跟随动作', 'references': refs}
        result = self.optimizer.optimize(document, body)
        outbound = self.calls[0][2]['messages'][1]['content']
        for private in ('private', 'named.png', 'front.png', 'back-name.png', 'action.mp4', 'nodes/1:image'):
            self.assertNotIn(private, outbound)
        self.assertEqual(result['model'], 'agnes-2.5-flash')
        self.assertEqual(result['profile'], 'h3_ref')
        self.assertEqual(document, original)
        self.assertEqual(len(self.calls), 1)

    def test_api_and_first_last_reference_mapping(self):
        document = {'a': {'class_type': 'LoadImage', 'inputs': {'image': 'a.png'}},
                    'b': {'class_type': 'LoadImage', 'inputs': {'image': 'b.png'}},
                    'h3': {'class_type': 'MiniMaxH3ReferenceToVideo', 'inputs': {'ref_images': {'ref_image_0': ['b', 0], 'ref_image_1': ['a', 0]}}}}
        refs = [{'type': 'image', 'fieldId': 'a:image', 'role': 'front'}, {'type': 'image', 'fieldId': 'b:image', 'role': 'back'}]
        self.assertEqual([r['role'] for r in reference_context(document, refs)], ['back', 'front'])
        document['h3'] = {'class_type': 'MiniMaxH3ImageToVideo', 'inputs': {'first_frame': ['a', 0], 'last_frame': ['b', 0]}}
        context = reference_context(document, refs)
        self.assertEqual(context[0]['role'], 'first_frame: front')
        self.assertEqual(context[1]['role'], 'last_frame: back')

    def test_profile_and_credentials_boundaries(self):
        qwen = {'nodes': [{'id': 1, 'type': 'TextEncodeQwenImage21', 'widgets_values': ['猫', '', 1024]}]}
        self.assertEqual(profile_for(qwen, []), 'qwen_image')
        self.assertEqual(profile_for(qwen, [{'type': 'image'}]), 'qwen_edit')
        self.assertEqual(profile_for({'nodes': [{'id': 1, 'type': 'WanAnimateToVideo'}]}, []), 'generic')
        missing = PromptOptimizer(self.root, credential_loader=lambda: {}, transport=Mock())
        self.assertFalse(missing.status()['configured'])
        with self.assertRaises(OptimizerProblem):
            missing.optimize(graph(), {'prompt': 'test'})
        missing.transport.assert_not_called()
        self.assertNotIn('unit-private-key', json.dumps(self.optimizer.status()))
        with self.assertRaisesRegex(ValueError, '本地路径'):
            self.optimizer.optimize(graph(), {'prompt': r'使用C:\private\file.png'})
        self.assertEqual(len(self.calls), 0)

    def test_qwen_ignores_orphan_image_loader_and_uses_encoder_port_order(self):
        document = {'nodes': [
            {'id': 1, 'type': 'LoadImage', 'widgets_values': ['unused.png', 'image']},
            {'id': 2, 'type': 'LoadImage', 'widgets_values': ['used.png', 'image']},
            {'id': 3, 'type': 'TextEncodeQwenImage21', 'widgets_values': ['test', '', 1024], 'inputs': []}], 'links': []}
        self.assertEqual(reference_context(document, []), [])
        self.assertEqual(profile_for(document, reference_context(document, [])), 'qwen_image')
        document['nodes'][2]['inputs'] = [{'name': 'images.image_1', 'link': 1}]
        document['links'] = [[1, 2, 0, 3, 0, 'IMAGE']]
        context = reference_context(document, [{'type': 'image', 'fieldId': 'nodes/1:image', 'role': 'edit this'}])
        self.assertEqual(len(context), 1)
        self.assertEqual(context[0]['role'], 'edit this')
        self.assertEqual(profile_for(document, context), 'qwen_edit')

    def test_busy_or_invalid_response_never_replays(self):
        self.optimizer.lock.acquire()
        try:
            with self.assertRaises(OptimizerProblem) as caught:
                self.optimizer.optimize(graph(), {'prompt': 'test'})
            self.assertEqual(caught.exception.status, 409)
            self.assertEqual(self.calls, [])
        finally:
            self.optimizer.lock.release()
        self.optimizer.transport = Mock(return_value={'choices': [{'message': {'content': '{"prompt":"wrong structure"}'}}]})
        with self.assertRaises(OptimizerProblem):
            self.optimizer.optimize(graph(), {'prompt': 'test'})
        self.optimizer.transport.assert_called_once()
        self.assertFalse(self.optimizer.lock.locked())
        error = urllib.error.HTTPError('https://apihub.agnes-ai.com', 401, 'private-key', {}, io.BytesIO(b'private-key'))
        with patch('ai_provider.urllib.request.OpenerDirector.open', side_effect=error) as opening:
            with self.assertRaises(OptimizerProblem) as caught:
                PromptOptimizer._request('https://apihub.agnes-ai.com', 'private-key', {})
        opening.assert_called_once()
        self.assertNotIn('private-key', str(caught.exception))

    def test_rewrite_scope_and_h3_format_are_guarded_without_guessing_media(self):
        self.optimizer.optimize(graph(), {'prompt': '两张正背视图是同一角色，替换视频里的所有人物'})
        system = self.calls[0][2]['messages'][0]['content']
        for guard in ('ONE definite executable plan', 'ONLY in warnings', 'ONE Subject identity',
                      'do not invent hair', 'do not impose silence', 'fully_preserved', '[Shot 1]'):
            self.assertIn(guard, system)
        invalid = [H3_TEXT.replace('Every person', 'The primary actor'),
                   H3_TEXT.replace('[Shot 1]', 'The character enters the frame'),
                   H3_TEXT.replace('fully_preserved', 'maybe unchanged'),
                   H3_TEXT.replace('Every person', 'A generic placeholder')]
        for text in invalid:
            self.optimizer.transport = Mock(return_value={'choices': [{'message': {'content': json.dumps({'prompt': text})}}]})
            with self.subTest(text=text):
                with self.assertRaises(OptimizerProblem):
                    self.optimizer.optimize(graph(), {'prompt': '替换所有人物'})
                self.optimizer.transport.assert_called_once()

    def test_mechanical_h3_normalization_keeps_content_and_real_warnings(self):
        text = ('subject_definitions: Subject A comes from <Picture 1>. Subject B is separate.\n'
                'retention_analysis: Subject A is full_preserved.\n'
                'detailed_description: [Shot 1] Subject B stays beside Subject A. A sign says "Subject A". '
                '<d>[English] Subject B.</d>\nnon_diegetic_music: None.')
        warnings = ['未提供图片1和图片2，无法确认外观。', '未提供视频1，无法确认动作。',
                    '舞台灯光尚未明确指定。', '未提供图片3。', '舞台灯光尚未明确指定。']
        context = [{'type': 'image'}, {'type': 'image'}, {'type': 'video'}]
        normalized, actual = normalize_output(text, warnings, context, 'h3_ref')
        self.assertIn('<Subject 1> comes from <Picture 1>. <Subject 2> is separate.', normalized)
        self.assertIn('<Subject 2> stays beside <Subject 1>', normalized)
        self.assertIn('fully_preserved', normalized)
        self.assertTrue(normalized.endswith('non_diegetic_music: N/A'))
        self.assertIn('"Subject A"', normalized)
        self.assertIn('<d>[English] Subject B.</d>', normalized)
        self.assertEqual(actual, ['本次文字优化未分析图片或视频内容。', '舞台灯光尚未明确指定。', '未提供图片3。'])
        self.assertEqual(normalize_output(text, warnings, [], 'qwen_image')[0], text)

    def test_existing_provider_env_only_and_environment_priority(self):
        path = self.root / '.env.local'
        path.write_text('AGNES_API_KEY=file-key\nAGNES_API_BASE=https://apihub.agnes-ai.com\nOTHER_SECRET=do-not-load\n', encoding='utf-8')
        with patch.dict('os.environ', {'AGNES_API_KEY': 'environment-key'}, clear=True):
            config = credentials(path)
        self.assertEqual(config['AGNES_API_KEY'], 'environment-key')
        self.assertNotIn('OTHER_SECRET', config)

    def test_complete_chinese_counterpart_in_same_request(self):
        result = self.optimizer.optimize(graph(), {'prompt': '替换所有人物'})
        self.assertEqual(result['translation'], H3_ZH)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][2]['max_tokens'], 8192)
        output_format = self.calls[0][2]['response_format']
        self.assertEqual(output_format['type'], 'json_schema')
        schema = output_format['json_schema']['schema']
        self.assertEqual(schema['properties']['prompt']['required'], list(dict(line.split(': ', 1) for line in H3_TEXT.splitlines())))
        self.assertEqual(schema['properties']['translation']['required'], schema['properties']['prompt']['required'])
        for bad in (None, '', 'Just English', '参考分工已整理', '主体定义：主体1。'):
            with self.assertRaises(ValueError):
                full_chinese_translation(H3_TEXT, H3_TEXT, bad, '参考分工已整理', 'h3_ref')
        self.assertEqual(full_chinese_translation('一只黄色的小猫坐在舞台上。', '一只黄色的小猫坐在舞台上。', None, '', 'qwen_edit'), '一只黄色的小猫坐在舞台上。')
        self.assertIn('属性迁移动作', full_chinese_translation(H3_TEXT, H3_TEXT, H3_ZH.replace('完整保留', 'attribute_transfer动作，完整保留'), '', 'h3_ref'))

    def test_complete_visible_figure_scope_and_truncated_response(self):
        text = H3_TEXT.replace('every source person', 'every visible figure').replace('Every person', 'Every visible figure')
        self.optimizer.transport = Mock(return_value={'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps({'prompt': text, 'translation': H3_ZH, 'warnings': []})}}]})
        self.assertEqual(self.optimizer.optimize(graph(), {'prompt': '替换所有人物'})['prompt'], text)
        self.optimizer.transport = Mock(return_value={'choices': [{'finish_reason': 'length', 'message': {'content': '{'}}]})
        with self.assertRaisesRegex(OptimizerProblem, '长度上限'):
            self.optimizer.optimize(graph(), {'prompt': '替换所有人物'})
        self.optimizer.transport.assert_called_once()

    def test_multimodal_payload_contains_actual_images_and_keeps_generation_text_separate(self):
        data_url = 'data:image/jpeg;base64,YWJj'
        self.optimizer.media_loader = Mock(return_value=([
            {'type': 'text', 'text': '<Picture 1>'}, {'type': 'image_url', 'image_url': {'url': data_url}}],
            [{'label': '<Picture 1>', 'type': 'image', 'frames': 1}]))
        result = self.optimizer.optimize(graph(), {'prompt': '替换所有人物'})
        blocks = self.calls[0][2]['messages'][1]['content']
        self.assertIsInstance(blocks, list)
        self.assertEqual([block for block in blocks if block['type'] == 'image_url'][-1]['image_url']['url'], data_url)
        self.assertIn('简体中文', blocks[-1]['text'])
        self.assertNotIn('private-back-name.png', json.dumps(blocks))
        self.assertEqual(result['referencePolicy'], 'images_video_frames')
        self.assertEqual(result['prompt'], H3_TEXT)
        self.assertEqual(result['translation'], H3_ZH)

    def test_two_complete_bilingual_json_blocks_are_normalized_without_new_model_request(self):
        english = dict(line.split(': ', 1) for line in H3_TEXT.splitlines())
        chinese = dict(line.split('：', 1) for line in H3_ZH.splitlines())
        chinese['详细画面'] = chinese.pop('详细画面')
        response = '```json\n' + json.dumps(english) + '\n```\n```json\n' + json.dumps(chinese) + '\n```'
        parsed = decode_output(response, 'h3_ref')
        self.assertIn('subject_definitions:', parsed['prompt'])
        self.assertIn('详细画面:', parsed['translation'])
        self.assertEqual(full_chinese_translation(parsed['prompt'], parsed['prompt'], parsed['translation'], '', 'h3_ref'), parsed['translation'])

    def test_http_review_does_not_modify_project_or_submit_generation(self):
        server = make_server(port=0, root=self.root, source=self.root / 'source')
        server.app.prompt_optimizer = self.optimizer
        before = server.app.project()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(base + '/api/prompt-optimizer') as response:
                self.assertTrue(json.load(response)['configured'])
            request = urllib.request.Request(base + '/api/prompt-optimizer', method='POST',
                headers={'Content-Type': 'application/json'}, data=json.dumps({'workflow': graph(), 'prompt': '测试'}).encode())
            with opener.open(request) as response:
                self.assertEqual(json.load(response)['prompt'], H3_TEXT)
            self.assertEqual(server.app.project(), before)
            self.assertEqual(server.app.jobs, {})
            self.assertNotIn('AGNES_API_KEY', json.dumps(server.app.settings()))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
