"""Connection loss/recovery checks; no engine or model is started."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import os

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import CanvasApp, HTTPProblem, atomic_json


class ConnectionCheck(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.app = CanvasApp(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_engine_that_returns_after_failed_probe_is_found_on_next_poll(self):
        with patch('server.time.monotonic', side_effect=[100, 100, 102, 102]), \
                patch('server.probe_engine', side_effect=[False, False, True]) as probe:
            self.assertIsNone(self.app.comfy_url())
            self.assertEqual(self.app.comfy_url(), 'http://127.0.0.1:8188')
        self.assertEqual(probe.call_count, 3)

    def test_custom_auto_port_survives_repeated_detection(self):
        self.app.save_settings({'comfy_port': 8199})
        with patch('server.probe_engine', side_effect=lambda url: url.endswith(':8199')) as probe:
            self.assertEqual(self.app.comfy_url(refresh=True), 'http://127.0.0.1:8199')
            self.assertEqual(self.app.comfy_url(refresh=True), 'http://127.0.0.1:8199')
        self.assertEqual(probe.call_count, 2)

    def test_disabled_auto_start_does_not_spawn_a_worker(self):
        self.app.save_settings({'auto_start': False})
        with patch('server.threading.Thread') as worker:
            self.assertEqual(self.app.start_local_engine()['status'], 'manual')
        worker.assert_not_called()

    def test_manual_start_uses_one_time_override_without_changing_preference(self):
        self.app.save_settings({'auto_start': False})
        self.app.comfy_url = Mock(return_value=None)
        with patch('engine.ensure_engine', return_value={'status': 'ready', 'url': 'http://127.0.0.1:8188'}) as ensure:
            self.app.start_local_engine(automatic=False)
            self.app.engine_thread.join(3)
        self.assertTrue(ensure.call_args.kwargs['settings']['auto_start'])
        self.assertFalse(self.app.settings()['auto_start'])

    def test_environment_root_has_precedence_over_saved_path(self):
        self.app.save_settings({'comfy_root': 'saved-directory'})
        with patch.dict(os.environ, {'YINGLIU_COMFY_ROOT': 'environment-directory'}):
            self.assertEqual(self.app.settings()['comfy_root'], 'environment-directory')

    def test_dropped_write_clears_address_without_replaying_job(self):
        self.app.cached_url, self.app.cached_at = 'http://127.0.0.1:8188', 100
        self.app.request = Mock(side_effect=ConnectionResetError('response lost'))
        with patch('server.time.monotonic', return_value=101):
            with self.assertRaises(HTTPProblem) as caught:
                self.app.comfy('/prompt', 'POST', {'prompt': {}})
        self.assertEqual(caught.exception.status, 503)
        self.app.request.assert_called_once()
        self.assertIsNone(self.app.cached_url)
        self.assertEqual(self.app.cached_at, 0)

    def test_status_does_not_reuse_ready_label_after_connection_lost(self):
        atomic_json(self.app.data / 'engine-start.json', {'status': 'ready', 'url': 'http://127.0.0.1:8188'})
        self.app.comfy_url = Mock(return_value='http://127.0.0.1:8188')
        self.app.comfy = Mock(side_effect=HTTPProblem(503, 'connection lost'))
        with patch('server.probe_engine', return_value=False):
            status = self.app.status()
        self.assertFalse(status['connected'])
        self.assertFalse(status['bridgeConnected'])
        self.assertEqual(status['startup']['status'], 'disconnected')

    def test_slow_bridge_does_not_disconnect_a_responsive_engine(self):
        url = 'http://127.0.0.1:8188'
        self.app.comfy_url = Mock(return_value=url)
        self.app.comfy = Mock(side_effect=HTTPProblem(503, 'bridge timed out'))
        with patch('server.probe_engine', return_value=True) as probe:
            status = self.app.status()
        probe.assert_called_once_with(url)
        self.assertTrue(status['connected'])
        self.assertFalse(status['bridgeConnected'])
        self.assertEqual(self.app.cached_url, url)

    def test_missing_bridge_does_not_mark_a_healthy_engine_offline(self):
        self.app.comfy_url = Mock(return_value='http://127.0.0.1:8188')
        self.app.comfy = Mock(side_effect=HTTPProblem(404, 'bridge missing'))
        status = self.app.status()
        self.assertTrue(status['connected'])
        self.assertFalse(status['bridgeInstalled'])

    def test_disconnect_recovery_waits_for_repeated_persistent_failures(self):
        atomic_json(self.app.data / 'engine-start.json', {'status': 'ready', 'url': 'http://127.0.0.1:8188'})
        self.app.comfy_url = Mock(return_value=None)
        self.app.start_local_engine = Mock(return_value={'status': 'starting'})
        with patch('server.time.monotonic', side_effect=[100, 106, 113]):
            self.assertEqual(self.app.status()['startup']['status'], 'disconnected')
            self.assertEqual(self.app.status()['startup']['status'], 'disconnected')
            self.app.start_local_engine.assert_not_called()
            self.assertEqual(self.app.status()['startup']['status'], 'starting')
        self.app.start_local_engine.assert_called_once_with()

    def test_transient_disconnect_resets_when_engine_recovers(self):
        atomic_json(self.app.data / 'engine-start.json', {'status': 'ready'})
        self.app.comfy_url = Mock(side_effect=[None, 'http://127.0.0.1:8188', None])
        self.app.comfy = Mock(return_value={'clientConnected': True})
        self.app.start_local_engine = Mock()
        with patch('server.time.monotonic', side_effect=[100, 120]):
            self.app.status()
            self.app.status()
            self.app.status()
        self.app.start_local_engine.assert_not_called()
        self.assertEqual(self.app.engine_disconnected_checks, 1)

    def test_startup_error_and_disabled_autostart_are_not_overwritten(self):
        self.app.comfy_url = Mock(return_value=None)
        self.app.start_local_engine = Mock()
        atomic_json(self.app.data / 'engine-start.json', {'status': 'error', 'message': '配置缺失'})
        for _ in range(5):
            self.assertEqual(self.app.status()['startup']['message'], '配置缺失')
        self.app.save_settings({'auto_start': False})
        atomic_json(self.app.data / 'engine-start.json', {'status': 'ready'})
        for _ in range(5):
            self.app.status()
        self.app.start_local_engine.assert_not_called()


if __name__ == '__main__':
    unittest.main()
