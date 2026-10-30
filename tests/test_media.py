"""Media shelf and real local video assembly; no engine or model is used."""
import hashlib
import json
import math
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import CanvasApp, make_server
from video_edit import clip_range, probe, run, tool


class MediaCheck(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.app = CanvasApp(self.root, self.root / 'source')

    def tearDown(self):
        self.temporary.cleanup()

    def test_shelf_keeps_removed_canvas_results_and_deduplicates_cached_outputs(self):
        image = self.app.store_asset('参考图.png', 'image/png', b'\x89PNG\r\n\x1a\nreference')
        original = '/api/comfy/view?filename=result.mp4&type=output&subfolder=clips'
        cached = self.app.store_asset('result.mp4', 'video/mp4', b'\0\0\0\x18ftypmp42cached',
                                      source='generated', sourceUrl=original)
        self.app.jobs['old'] = {'id': 'old', 'status': 'completed', 'outputs': [
            {'name': 'result.mp4', 'type': 'video', 'url': '/api/comfy/view?subfolder=clips&type=output&filename=result.mp4'}]}
        self.app.jobs['bad'] = {'id': 'bad', 'status': 'completed', 'outputs': [
            {'name': 'outside', 'type': 'video', 'url': 'https://example.com/movie.mp4'}]}
        items = self.app.media()
        self.assertEqual(len(items), 2)
        result = next(item for item in items if item['source'] == 'generated')
        self.assertEqual(result['key'], 'old:0')
        self.assertEqual(result['assetId'], cached['id'])
        self.assertEqual(result['url'], cached['url'])
        self.assertEqual(next(item for item in items if item['source'] == 'upload')['assetId'], image['id'])
        self.assertFalse(self.app.project()['cards'])

    def test_join_rejects_external_addresses_and_invalid_ranges(self):
        for bad in ('https://example.com/x.mp4', 'file:///C:/secret.mp4', '/assets/../../secret.mp4',
                    '/api/comfy/view?filename=../secret.mp4&type=output'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.app.join_videos({'clips': [{'url': bad}, {'url': bad}]})
        self.assertFalse(self.app.jobs)
        for clip in ({'start': -1}, {'start': 1, 'end': .5}, {'end': float('nan')}, {'start': 2.05, 'end': 2.1}):
            with self.subTest(clip=clip), self.assertRaises(ValueError):
                clip_range(clip, 2)

    def make_clip(self, name, color, size, audio=False):
        path = self.root / name
        arguments = [tool('ffmpeg'), '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i',
                     f'color={color}:s={size}:r=24:d=1']
        if audio:
            arguments += ['-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=1']
        arguments += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-threads', '1']
        if audio:
            arguments += ['-c:a', 'aac', '-shortest']
        run(arguments + [str(path)])
        return self.app.store_asset(name, 'video/mp4', path.read_bytes())

    def test_http_join_crops_preserves_order_adds_audio_and_retains_sources(self):
        try:
            tool('ffmpeg')
            tool('ffprobe')
        except ValueError:
            self.skipTest('FFmpeg is not installed')
        red = self.make_clip('红色带声音.mp4', 'red', '160x90', audio=True)
        blue = self.make_clip('蓝色静音.mp4', 'blue', '320x240')
        source_hashes = {entry['id']: hashlib.sha256((self.app.assets / entry['id']).read_bytes()).hexdigest()
                         for entry in (red, blue)}
        server = make_server(port=0, root=self.root, source=self.root / 'source')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            body = {'name': '动作拼接', 'clips': [
                {'assetId': red['id'], 'start': .2, 'end': .7},
                {'url': blue['url'], 'start': .1, 'end': .85}]}
            request = urllib.request.Request(base + '/api/videos/join', method='POST',
                                              data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
            with opener.open(request, timeout=5) as response:
                self.assertEqual(response.status, 202)
                job_id = json.load(response)['job_id']
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                with opener.open(base + '/api/jobs', timeout=5) as response:
                    job = next(job for job in json.load(response)['items'] if job['id'] == job_id)
                if job['status'] in ('completed', 'failed'):
                    break
                time.sleep(.05)
            self.assertEqual(job['status'], 'completed', job.get('error'))
            self.assertEqual(job['kind'], 'video-join')
            output = job['outputs'][0]
            path = self.app.assets / output['assetId']
            info = probe(path)
            self.assertAlmostEqual(info['duration'], 1.25, delta=.1)
            self.assertEqual((info['width'], info['height']), (160, 90))
            self.assertTrue(info['audio'])
            for moment, channel in ((.15, 0), (.9, 2)):
                pixels = subprocess.check_output([tool('ffmpeg'), '-v', 'error', '-ss', str(moment), '-i', str(path),
                                                   '-frames:v', '1', '-vf', 'scale=1:1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
                self.assertGreater(pixels[channel], 180, (moment, list(pixels)))
                self.assertLess(pixels[2 if channel == 0 else 0], 35, (moment, list(pixels)))
            volumes = []
            for moment in (.2, .9):
                pcm = subprocess.check_output([tool('ffmpeg'), '-v', 'error', '-ss', str(moment), '-i', str(path),
                                               '-t', '0.1', '-vn', '-ac', '1', '-ar', '48000', '-f', 's16le', '-'])
                samples = struct.unpack('<' + 'h' * (len(pcm) // 2), pcm)
                volumes.append(math.sqrt(sum(sample * sample for sample in samples) / len(samples)))
            self.assertGreater(volumes[0], 500, 'the first clip must retain its original tone')
            self.assertLess(volumes[1], 50, 'the silent second clip must receive silence')
            with opener.open(base + '/api/media', timeout=5) as response:
                items = json.load(response)['items']
            joined = [item for item in items if item['source'] == 'joined']
            self.assertEqual(len(joined), 1)
            self.assertEqual(joined[0]['jobId'], job_id)
            self.assertEqual(joined[0]['key'], job_id + ':0')
            for asset_id, digest in source_hashes.items():
                self.assertEqual(hashlib.sha256((self.app.assets / asset_id).read_bytes()).hexdigest(), digest)
            restarted = CanvasApp(self.root, self.root / 'source')
            self.assertEqual(restarted.jobs[job_id]['status'], 'completed')
            self.assertEqual(len([item for item in restarted.media() if item['source'] == 'joined']), 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
