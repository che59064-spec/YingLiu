"""Check startup readiness without launching ComfyUI or loading models."""
from pathlib import Path
import io
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine


class EngineStartupCheck(unittest.TestCase):
    def setUp(self):
        self.settings = patch.object(engine, 'runtime_settings', side_effect=lambda root=None, overrides=None:
            {'comfy_url': 'auto', 'auto_start': True, **(overrides or {})})
        self.settings.start()
        self.addCleanup(self.settings.stop)
        listening = patch.object(engine, 'port_listening', return_value=False)
        listening.start()
        self.addCleanup(listening.stop)

    def test_probe_uses_direct_loopback_client(self):
        with patch.object(engine.LOCAL_HTTP, 'open', return_value=io.BytesIO(b'{"queue_running": [], "queue_pending": []}')) as request:
            self.assertEqual(engine.find_engine(), 'http://127.0.0.1:8188')
        request.assert_called_once_with('http://127.0.0.1:8188/queue', timeout=2)

    def test_probe_falls_back_and_rejects_unrelated_http_services(self):
        with patch.object(engine.LOCAL_HTTP, 'open', side_effect=[TimeoutError(), io.BytesIO(b'{"LoadImage":{"input":{},"output":["IMAGE"]}}')]) as request:
            self.assertTrue(engine.probe_engine('http://127.0.0.1:8188'))
        self.assertEqual(request.call_args_list[-1].args[0], 'http://127.0.0.1:8188/object_info')
        with patch.object(engine.LOCAL_HTTP, 'open', side_effect=[io.BytesIO(b'{"queue_running": true}'), io.BytesIO(b'{"ready": true}')]):
            self.assertFalse(engine.probe_engine('http://127.0.0.1:8188'))

    def test_existing_engine_is_reused(self):
        ready = {'status': 'ready', 'url': 'http://127.0.0.1:8188'}
        with patch.object(engine, 'start_engine', return_value=ready), patch.object(engine, 'find_engine') as find:
            self.assertEqual(engine.ensure_engine(), ready)
            find.assert_not_called()

    def test_delayed_start_waits_for_http_api(self):
        starting = {'status': 'starting', 'pid': 42, 'log': 'test.log'}
        with patch.object(engine, 'PROCESS', None), \
                patch.object(engine, 'start_engine', return_value=starting), \
                patch.object(engine, 'find_engine', side_effect=[None, 'http://127.0.0.1:8188']), \
                patch.object(engine, 'process_alive', return_value=True), \
                patch.object(engine.time, 'sleep') as sleep:
            result = engine.ensure_engine()
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['url'], 'http://127.0.0.1:8188')
        sleep.assert_called_once()

    def test_early_process_exit_reports_log(self):
        process = Mock(pid=42)
        process.poll.return_value = 1
        with patch.object(engine, 'PROCESS', process), \
                patch.object(engine, 'start_engine', return_value={'status': 'starting', 'pid': 42, 'log': 'test.log'}), \
                patch.object(engine, 'find_engine', return_value=None):
            with self.assertRaisesRegex(RuntimeError, '退出码 1.*test.log'):
                engine.ensure_engine()

    def test_timeout_does_not_kill_loading_engine(self):
        process = Mock(pid=42)
        process.poll.return_value = None
        with patch.object(engine, 'PROCESS', process), \
                patch.object(engine, 'start_engine', return_value={'status': 'starting', 'pid': 42, 'log': 'test.log'}), \
                patch.object(engine, 'find_engine', return_value=None), \
                patch.object(engine.time, 'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, '超时.*test.log'):
                engine.ensure_engine(timeout=0)
        sleep.assert_not_called()
        process.terminate.assert_not_called()

    def test_bad_pid_metadata_does_not_block_start(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            process = Mock(pid=42)
            for saved in ('{broken', '[]', '{"pid": "invalid"}'):
                with self.subTest(saved=saved):
                    (data / 'engine-process.json').write_text(saved, encoding='utf-8')
                    with patch.object(engine, 'DATA', data), patch.object(engine, 'PROCESS', None), \
                            patch.object(engine, 'find_engine', return_value=None), \
                            patch.object(engine, 'port_listening', return_value=False), \
                            patch.object(engine, 'install_bridge'), \
                            patch.object(engine, 'process_start_time', return_value=None), \
                            patch.object(engine, 'comfy_directory', return_value=data), \
                            patch.object(engine, 'comfy_python', return_value=data / 'python'), \
                            patch.object(engine.subprocess, 'Popen', return_value=process) as popen:
                        result = engine.start_engine()
                    self.assertEqual(result['pid'], 42)
                    popen.assert_called_once()
                    arguments = popen.call_args.args[0]
                    self.assertNotIn('--lowvram', arguments)
                    self.assertNotIn('--extra-model-paths-config', arguments)
                    self.assertEqual(arguments[arguments.index('--listen') + 1], '127.0.0.1')

    def test_live_listener_is_never_replaced_even_when_http_probe_fails(self):
        with patch.object(engine, 'find_engine', return_value=None), \
                patch.object(engine, 'port_listening', return_value=True), \
                patch.object(engine, 'PROCESS', None), \
                patch.object(engine.subprocess, 'Popen') as popen:
            result = engine.start_engine()
        self.assertEqual(result['status'], 'starting')
        popen.assert_not_called()

    def test_reused_pid_does_not_hold_up_engine_start(self):
        record = {'pid': 42, 'startedAt': 1000}
        with patch.object(engine, 'process_alive', return_value=True), \
                patch.object(engine, 'process_start_time', return_value=2000):
            self.assertFalse(engine.recorded_process_alive(record))
        with patch.object(engine, 'process_alive', return_value=True), \
                patch.object(engine, 'process_start_time', return_value=None):
            self.assertTrue(engine.recorded_process_alive(record))
        with patch.object(engine, 'process_alive', return_value=True), \
                patch.object(engine, 'process_start_time', return_value=1001):
            self.assertTrue(engine.recorded_process_alive(record))

    def test_start_lock_is_shared_across_processes(self):
        acquired = threading.Event()
        command = ('import engine; print("waiting", flush=True); '
                   'lock = engine.engine_start_lock(54321); lock.__enter__(); '
                   'print("acquired", flush=True); lock.__exit__(None, None, None)')
        with engine.engine_start_lock(54321):
            child = subprocess.Popen([sys.executable, '-c', command], cwd=str(engine.ROOT),
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), 'waiting')
                reader = threading.Thread(target=lambda: (child.stdout.readline(), acquired.set()), daemon=True)
                reader.start()
                self.assertFalse(acquired.wait(0.2))
            except BaseException:
                child.kill()
                child.wait()
                raise
        self.assertTrue(acquired.wait(5))
        self.assertEqual(child.wait(timeout=5), 0)
        child.stdout.close()
        child.stderr.close()

    def test_missing_model_path_is_named_and_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            missing = data / 'missing.yaml'
            with patch.object(engine, 'DATA', data), patch.object(engine, 'PROCESS', None), \
                    patch.object(engine, 'find_engine', return_value=None), \
                    patch.object(engine, 'port_listening', return_value=False), \
                    patch.object(engine, 'install_bridge'), \
                    patch.object(engine, 'comfy_directory', return_value=data), \
                    patch.object(engine, 'comfy_python', return_value=data / 'python'), \
                    patch.object(engine.subprocess, 'Popen') as popen:
                with self.assertRaises(RuntimeError) as caught:
                    engine.start_engine({'extra_model_paths': [str(missing)]})
            self.assertIn(str(missing), str(caught.exception))
            popen.assert_not_called()

    def test_custom_engine_address_is_not_replaced_with_other_installation(self):
        with patch.object(engine, 'probe_engine', return_value=False) as probe:
            self.assertIsNone(engine.find_engine({'comfy_url': 'http://localhost:8199'}))
        probe.assert_called_once_with('http://localhost:8199')

    def test_disabled_autostart_reuses_online_engine_and_never_spawns(self):
        with patch.object(engine, 'find_engine', return_value='http://localhost:8199'), patch.object(engine.subprocess, 'Popen') as popen:
            self.assertEqual(engine.start_engine({'auto_start': False})['status'], 'ready')
        popen.assert_not_called()
        with patch.object(engine, 'find_engine', return_value=None), patch.object(engine.subprocess, 'Popen') as popen:
            with self.assertRaisesRegex(RuntimeError, '自动启动已关闭'):
                engine.start_engine({'auto_start': False})
        popen.assert_not_called()

    def test_comfy_directory_requires_main_and_python_is_local(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            with self.assertRaisesRegex(RuntimeError, '未找到 ComfyUI'):
                engine.comfy_directory({'comfy_root': folder})
            (directory / 'main.py').write_text('', encoding='utf-8')
            self.assertEqual(engine.comfy_directory({'comfy_root': folder}), directory.resolve())
            binary = directory / '.venv' / 'Scripts' / 'python.exe'
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b'fake')
            self.assertEqual(engine.comfy_python({}, directory), binary)

    def test_local_address_boundary(self):
        for value in ('https://localhost:8188', 'http://example.com:8188', 'http://127.0.0.1:8188/path', 'http://user@localhost:8188'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                engine.engine_address({'comfy_url': value})

    def test_invalid_pids_are_not_processes(self):
        for pid in (None, 'oops', '42', 0, -1, True, 1.5, 2 ** 40):
            with self.subTest(pid=pid):
                self.assertFalse(engine.process_alive(pid))


if __name__ == '__main__':
    unittest.main()
