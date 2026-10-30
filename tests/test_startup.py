"""Startup/reopen regressions; no real engine is launched."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import CanvasApp


class StartupCheck(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.app = CanvasApp(self.temporary.name)
        self.app.comfy_url = Mock(return_value=None)

    def tearDown(self):
        self.temporary.cleanup()

    def saved(self):
        return json.loads((self.app.data / 'engine-start.json').read_text(encoding='utf-8'))

    def test_reopen_restarts_engine_once_and_records_ready(self):
        entered, release = threading.Event(), threading.Event()
        def delayed(settings=None):
            entered.set()
            release.wait(3)
            return {'status': 'ready', 'url': 'http://127.0.0.1:8188'}
        with patch('engine.ensure_engine', side_effect=delayed) as ensure:
            self.assertEqual(self.app.start_local_engine()['status'], 'starting')
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.app.start_local_engine()['status'], 'starting')
            release.set()
            self.app.engine_thread.join(3)
            self.assertEqual(ensure.call_count, 1)
        self.assertEqual(self.saved()['status'], 'ready')
        self.assertEqual(self.app.settings()['comfy_url'], 'auto')
        # A later launch rechecks even when the canvas background stays alive.
        with patch('engine.ensure_engine', return_value={'status': 'ready', 'url': 'http://127.0.0.1:8188'}) as ensure:
            self.app.start_local_engine()
            self.app.engine_thread.join(3)
            ensure.assert_called_once()

    def test_failed_start_can_retry_and_does_not_remain_starting(self):
        with patch('engine.ensure_engine', side_effect=RuntimeError('节点导入失败，请查看日志')):
            self.app.start_local_engine()
            self.app.engine_thread.join(3)
        self.assertEqual(self.app.status()['startup']['status'], 'error')
        self.assertIn('节点导入失败', self.saved()['message'])
        with patch('engine.ensure_engine', return_value={'status': 'ready', 'url': 'http://127.0.0.1:8188'}):
            self.app.start_local_engine()
            self.app.engine_thread.join(3)
        self.assertEqual(self.saved()['status'], 'ready')

    def test_stale_explicit_port_switches_to_started_engine(self):
        self.app.save_settings({'comfy_url': 'http://127.0.0.1:8000'})
        with patch('engine.ensure_engine', return_value={'status': 'ready', 'url': 'http://127.0.0.1:8188'}):
            self.app.start_local_engine()
            self.app.engine_thread.join(3)
        self.assertEqual(self.app.settings()['comfy_url'], 'http://127.0.0.1:8188')


if __name__ == '__main__':
    unittest.main()
