"""Portable configuration and allowlist packaging; never launch an engine."""
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import engine

spec = importlib.util.spec_from_file_location('release_package', ROOT / 'scripts' / 'package_release.py')
release_package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_package)


class PortabilityCheck(unittest.TestCase):
    def test_configuration_priority_and_environment_override(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'data').mkdir()
            (root / 'config.json').write_text(json.dumps({'comfy_root': 'local', 'comfy_port': 8190}), encoding='utf-8-sig')
            (root / 'data' / 'settings.json').write_text(json.dumps({'comfy_port': 8191}), encoding='utf-8')
            with patch.dict(os.environ, {'YINGLIU_COMFY_ROOT': 'environment'}):
                options = engine.runtime_settings(root, {'auto_start': False})
            self.assertEqual(options['comfy_root'], 'environment')
            self.assertEqual(options['comfy_port'], 8191)
            self.assertFalse(options['auto_start'])

    def test_packaging_never_copies_private_configuration_or_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'app'
            root.mkdir()
            (root / 'server.py').write_text('print("canvas")', encoding='utf-8')
            (root / 'config.json').write_text('{"private":"keep"}', encoding='utf-8')
            (root / 'data').mkdir()
            (root / 'data' / 'settings.json').write_text('{"private":"keep"}', encoding='utf-8')
            result = release_package.package(Path(folder) / 'out', root=root)
            stage = Path(result['folder'])
            self.assertFalse((stage / 'config.json').exists())
            self.assertFalse((stage / 'data').exists())
            manifest = json.loads((stage / 'SHA256SUMS.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['server.py'], sha256((stage / 'server.py').read_bytes()).hexdigest())
            with zipfile.ZipFile(result['zip']) as archive:
                self.assertIsNone(archive.testzip())
                self.assertFalse(any('/data/' in name for name in archive.namelist()))
            self.assertEqual((root / 'config.json').read_text(), '{"private":"keep"}')

    def test_packaging_stops_on_secrets_and_missing_native_artifacts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            document = root / 'server.py'
            document.write_text('key = "' + 'sk-' + 'x' * 30 + '"', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '疑似密钥'):
                release_package.package(root / 'out', root=root)
            document.write_text('', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '缺少构建产物'):
                release_package.package(root / 'out', portable=True, root=root)
            self.assertFalse((root / 'out').exists())


if __name__ == '__main__':
    unittest.main()
