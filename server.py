"""Local creation canvas; external prompt rewriting is explicitly requested."""
from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4

from engine import probe_engine
from prompt_optimizer import PromptOptimizer, OptimizerProblem
from workflows import WorkflowLibrary, apply_params, fields_for, iter_nodes, workflow_format

ROOT = Path(__file__).resolve().parent
LOCAL_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
MAX_JSON = 32 * 1024 * 1024
MAX_ASSET = 512 * 1024 * 1024
MEDIA_EXTENSIONS = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                    '.webp': 'image/webp', '.gif': 'image/gif', '.mp4': 'video/mp4',
                    '.webm': 'video/webm', '.mov': 'video/quicktime', '.mkv': 'video/x-matroska'}


class HTTPProblem(Exception):
    def __init__(self, status, message, **detail):
        super().__init__(message)
        self.status, self.message, self.detail = status, message, detail


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return deepcopy(default)


def validate_comfy_url(value):
    if value in ('', 'auto', None):
        return 'auto'
    parsed = urllib.parse.urlsplit(str(value))
    try:
        port = parsed.port
    except ValueError:
        raise ValueError('ComfyUI 端口无效')
    if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1')
            or parsed.username or parsed.password or parsed.path not in ('', '/')
            or parsed.query or parsed.fragment or not port):
        raise ValueError('ComfyUI 地址仅支持本机 http://127.0.0.1:端口')
    return str(value).rstrip('/')


class CanvasApp:
    def __init__(self, root=ROOT, source=None):
        self.root = Path(root)
        self.data = self.root / 'data'
        self.data.mkdir(parents=True, exist_ok=True)
        self.assets = self.data / 'assets'
        self.assets.mkdir(exist_ok=True)
        library_source = Path(source or self.settings().get('workflow_source') or self.root / 'workflows').expanduser()
        if not library_source.is_absolute():
            library_source = self.root / library_source
        self.library = WorkflowLibrary(library_source, self.data / 'workflows')
        # One lock keeps document writes and the selected-canvas index consistent.
        self.lock = threading.RLock()
        self.submit_lock = threading.Lock()
        self.video_lock = threading.Lock()
        self.jobs = read_json(self.data / 'jobs.json', {})
        self.cached_url = None
        self.cached_at = 0
        self.engine_thread = None
        self.engine_disconnected_at = None
        self.engine_disconnected_checks = 0
        self.reference_loader_ready = False
        self.prompt_optimizer = PromptOptimizer(self.root, media_loader=self.optimizer_media)

    def settings(self):
        from engine import runtime_settings
        return {'comfy_url': 'auto', 'cloud_enabled': False,
                **runtime_settings(self.root)}

    def save_settings(self, settings):
        if not isinstance(settings, dict):
            raise ValueError('设置必须是对象')
        allowed = {'comfy_url', 'comfyUrl', 'comfy_root', 'comfy_python', 'comfy_port', 'auto_start',
                   'comfy_args', 'extra_model_paths', 'input_dir', 'output_dir', 'workflow_source', 'app_python'}
        if set(settings) - allowed - {'cloud_enabled'}:
            raise ValueError('包含不支持的设置，请在 AI 接口页面配置密钥。')
        current = self.settings()
        url = validate_comfy_url(settings.get('comfy_url', settings.get('comfyUrl', current.get('comfy_url', 'auto'))))
        for name in ('comfy_root', 'comfy_python', 'input_dir', 'output_dir', 'workflow_source', 'app_python'):
            if name in settings and (not isinstance(settings[name], str) or '\x00' in settings[name] or len(settings[name]) > 2048):
                raise ValueError('路径格式无效：' + name)
        if 'comfy_port' in settings and (isinstance(settings['comfy_port'], bool) or not isinstance(settings['comfy_port'], int) or not 1024 <= settings['comfy_port'] <= 65535):
            raise ValueError('ComfyUI 启动端口应为 1024–65535。')
        if 'auto_start' in settings and not isinstance(settings['auto_start'], bool):
            raise ValueError('自动启动设置必须为开关值。')
        for name in ('comfy_args', 'extra_model_paths'):
            if name in settings and (not isinstance(settings[name], list) or len(settings[name]) > 100 or any(not isinstance(value, str) or '\x00' in value for value in settings[name])):
                raise ValueError('启动参数或模型路径格式无效。')
        saved = {**current, **settings, 'comfy_url': url, 'cloud_enabled': False}
        saved.pop('comfyUrl', None)
        with self.lock:
            atomic_json(self.data / 'settings.json', saved)
            self.cached_url = None
            self.cached_at = 0
        return saved

    @staticmethod
    def empty_canvas(name):
        return {'revision': 0, 'name': name, 'cards': [], 'edges': [],
                'viewport': {'x': 0, 'y': 0, 'zoom': 1}}

    def canvas_path(self, canvas_id):
        if not isinstance(canvas_id, str) or not re.fullmatch(r'[a-f0-9]{32}', canvas_id):
            raise HTTPProblem(404, '画布不存在')
        return self.data / 'canvases' / (canvas_id + '.json')

    def canvas_index(self):
        """Called under the writer lock; retain the legacy file during migration."""
        path = self.data / 'canvases' / 'index.json'
        if path.exists():
            return read_json(path, {})
        document = read_json(self.data / 'project.json', self.empty_canvas('我的创作画布'))
        canvas_id = uuid4().hex
        document['id'] = canvas_id
        document.setdefault('revision', 0)
        document.setdefault('name', '我的创作画布')
        index = {'ids': [canvas_id], 'active_id': canvas_id, 'legacy_id': canvas_id}
        atomic_json(self.canvas_path(canvas_id), document)
        atomic_json(path, index)
        return index

    def canvases(self):
        with self.lock:
            index = self.canvas_index()
            items = []
            for canvas_id in index['ids']:
                document = self.canvas(canvas_id)
                items.append({key: document.get(key) for key in ('id', 'name', 'revision', 'savedAt')})
            return {'items': items, 'active_id': index['active_id']}

    def canvas(self, canvas_id):
        with self.lock:
            path = self.canvas_path(canvas_id)
            if canvas_id not in self.canvas_index()['ids'] or not path.is_file():
                raise HTTPProblem(404, '画布不存在')
            return read_json(path, {})

    @staticmethod
    def canvas_name(name):
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80 or any(ord(c) < 32 for c in name):
            raise ValueError('画布名称请输入 1 到 80 个字符。')
        return name.strip()

    def create_canvas(self, body):
        if not isinstance(body, dict):
            raise ValueError('画布参数必须是对象')
        with self.lock:
            index = self.canvas_index()
            name = self.canvas_name(body.get('name', '画布 ' + str(len(index['ids']) + 1)))
            document = {**self.empty_canvas(name), 'id': uuid4().hex}
            atomic_json(self.canvas_path(document['id']), document)
            index['ids'].append(document['id'])
            atomic_json(self.data / 'canvases' / 'index.json', index)
            return document

    def select_canvas(self, canvas_id):
        with self.lock:
            self.canvas(canvas_id)
            index = self.canvas_index()
            index['active_id'] = canvas_id
            atomic_json(self.data / 'canvases' / 'index.json', index)
            return {'active_id': canvas_id}

    def order_canvases(self, body):
        if not isinstance(body, dict) or set(body) != {'ids'} or not isinstance(body['ids'], list):
            raise ValueError('排序需要包含全部画布编号。')
        ids = body['ids']
        if any(not isinstance(item, str) or not re.fullmatch(r'[a-f0-9]{32}', item) for item in ids) or len(set(ids)) != len(ids):
            raise ValueError('画布排序编号无效或重复。')
        with self.lock:
            index = self.canvas_index()
            if set(ids) != set(index['ids']):
                raise HTTPProblem(409, '画布列表已变化，请刷新后重新排序。', canvases=self.canvases())
            index.setdefault('legacy_id', index['ids'][0])
            self.jobs_list()
            index['ids'] = list(ids)
            atomic_json(self.data / 'canvases' / 'index.json', index)
            return self.canvases()

    @staticmethod
    def check_canvas_revision(current, body, required=True):
        expected = body.get('expectedRevision', body.get('revision'))
        if expected is None and not required:
            return
        if type(expected) is not int or expected != current['revision']:
            raise HTTPProblem(409, '另一个窗口已保存画布，请重新读取后再操作。', project=current)

    def rename_canvas(self, canvas_id, body):
        if not isinstance(body, dict) or set(body) - {'name', 'expectedRevision', 'revision'}:
            raise ValueError('画布名称参数无效。')
        name = self.canvas_name(body.get('name'))
        with self.lock:
            current = self.canvas(canvas_id)
            self.check_canvas_revision(current, body)
            return self.save_canvas(canvas_id, {**current, 'name': name})

    def delete_canvas(self, canvas_id, body=None):
        body = {} if body is None else body
        if not isinstance(body, dict) or set(body) - {'expectedRevision', 'revision'}:
            raise ValueError('删除画布参数无效。')
        with self.lock:
            current = self.canvas(canvas_id)
            self.check_canvas_revision(current, body, required=False)
            index = self.canvas_index()
            if len(index['ids']) == 1:
                raise HTTPProblem(409, '至少保留一个画布，请先新建画布再删除。')
            index.setdefault('legacy_id', index['ids'][0])
            jobs = self.jobs_list()
            if any(job.get('canvas_id') == canvas_id and job.get('status') in
                   ('pending', 'submitting', 'submitted', 'queued', 'running') for job in jobs):
                raise HTTPProblem(409, '这个画布仍有生成或拼接任务，请等待完成后再删除。')
            archive = self.data / 'canvases' / 'trash' / (canvas_id + '-' + uuid4().hex + '.json')
            atomic_json(archive, {'canvas': current, 'deletedAt': datetime.now(timezone.utc).isoformat()})
            position = index['ids'].index(canvas_id)
            index['ids'].remove(canvas_id)
            if index['active_id'] == canvas_id:
                index['active_id'] = index['ids'][min(position, len(index['ids']) - 1)]
            active = self.canvas(index['active_id'])
            items = [item for item in self.canvases()['items'] if item['id'] != canvas_id]
            # Commit visibility only after the recovery copy is safely saved.
            atomic_json(self.data / 'canvases' / 'index.json', index)
            try:
                self.canvas_path(canvas_id).unlink()
            except OSError:
                pass  # An unlisted original is harmless when Windows still has it open.
            return {'items': items, 'active_id': index['active_id'], 'deleted_id': canvas_id,
                    'active_canvas': active}

    def project(self):
        with self.lock:
            return self.canvas(self.canvas_index()['active_id'])

    def save_canvas(self, canvas_id, document):
        if not isinstance(document, dict):
            raise ValueError('项目必须是 JSON 对象')
        with self.lock:
            current = self.canvas(canvas_id)
            expected = document.get('expectedRevision', document.get('revision'))
            if expected != current['revision']:
                raise HTTPProblem(409, '另一个窗口已保存画布，请重新读取后再保存。', project=current)
            if document.get('id', canvas_id) != canvas_id:
                raise ValueError('画布编号与保存地址不一致')
            saved = deepcopy(document)
            saved.pop('expectedRevision', None)
            saved['id'] = canvas_id
            saved['name'] = self.canvas_name(saved.get('name', current['name']))
            saved['revision'] = current['revision'] + 1
            saved['savedAt'] = datetime.now(timezone.utc).isoformat()
            atomic_json(self.canvas_path(canvas_id), saved)
            return saved

    def save_project(self, document):
        with self.lock:
            canvas_id = document.get('id') if isinstance(document, dict) else None
            return self.save_canvas(canvas_id or self.canvas_index()['active_id'], document)

    def job_canvas_id(self, body):
        with self.lock:
            canvas_id = body.get('canvas_id', self.canvas_index()['active_id'])
            self.canvas(canvas_id)
            return canvas_id

    def snapshot_bridge(self, state, reason):
        if not isinstance(state, dict) or not isinstance(state.get('workflow'), dict):
            return None
        stamp = datetime.now(timezone.utc)
        filename = stamp.strftime('%Y%m%dT%H%M%S.%fZ') + '-' + uuid4().hex[:12] + '.json'
        snapshot = {'workflow': state['workflow'], 'workflowId': state.get('workflowId'),
                    'revision': state.get('revision'), 'savedAt': stamp.isoformat(), 'reason': reason}
        path = self.data / 'snapshots' / filename
        with self.lock:
            atomic_json(path, snapshot)
        return filename

    @staticmethod
    def request(url, method='GET', body=None, headers=None, timeout=10, raw=False):
        data = body
        headers = dict(headers or {})
        if body is not None and not isinstance(body, bytes):
            data = json.dumps(body, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with LOCAL_HTTP.open(req, timeout=timeout) as response:
                content = response.read()
                return content if raw else json.loads(content.decode('utf-8'))
        except urllib.error.HTTPError as error:
            payload = error.read()
            try:
                detail = json.loads(payload)
            except (ValueError, UnicodeDecodeError):
                detail = {'message': payload[:300].decode('utf-8', 'replace')}
            extra = {'upstream': detail}
            if isinstance(detail, dict) and 'state' in detail:
                extra['state'] = detail['state']
            raise HTTPProblem(error.code, 'ComfyUI 返回错误', **extra)

    def comfy_url(self, refresh=False):
        if not refresh and time.monotonic() - self.cached_at < (5 if self.cached_url else 1):
            return self.cached_url
        settings = self.settings()
        configured = settings.get('comfy_url', 'auto')
        candidates = list(dict.fromkeys(['http://127.0.0.1:' + str(settings.get('comfy_port', 8188)),
                                        'http://127.0.0.1:8188', 'http://127.0.0.1:8000'])) if configured == 'auto' else [validate_comfy_url(configured)]
        found = None
        for url in candidates:
            if probe_engine(url):
                found = url
                break
        self.cached_url, self.cached_at = found, time.monotonic()
        return found

    def comfy(self, route, method='GET', body=None, **kwargs):
        url = self.comfy_url()
        if not url:
            raise HTTPProblem(503, '本地 ComfyUI 尚未连接，请启动专业引擎。')
        try:
            return self.request(url + route, method=method, body=body, **kwargs)
        except (OSError, urllib.error.URLError, TimeoutError, socket.timeout) as error:
            self.cached_url, self.cached_at = None, 0
            # Never replay a write: a dropped response may already have queued a job.
            raise HTTPProblem(503, 'ComfyUI 连接中断：' + str(error))

    def start_local_engine(self, automatic=True):
        with self.lock:
            if self.engine_thread is not None and self.engine_thread.is_alive():
                return read_json(self.data / 'engine-start.json', {'status': 'starting'})
            if automatic and self.settings().get('auto_start') is False:
                result = {'status': 'manual', 'message': '自动启动已关闭，请手动启动 ComfyUI 后重新连接。'}
                atomic_json(self.data / 'engine-start.json', result)
                return result
            result = {'status': 'starting', 'message': '正在启动并等待本地 ComfyUI 就绪。',
                      'log': str(self.data / 'comfy-engine.log'),
                      'checkedAt': datetime.now(timezone.utc).isoformat()}
            atomic_json(self.data / 'engine-start.json', result)
            self.engine_thread = threading.Thread(target=self._ensure_local_engine, args=(not automatic,), daemon=True)
            self.engine_thread.start()
            return result

    def _ensure_local_engine(self, explicit=False):
        try:
            from engine import ensure_engine
            url = self.comfy_url(refresh=True)
            settings = self.settings()
            if explicit:
                settings = {**settings, 'auto_start': True}
            result = {'status': 'ready', 'url': url} if url else ensure_engine(settings=settings)
            with self.lock:
                settings = self.settings()
                # A stale explicit port must not hide the engine we just started.
                if settings['comfy_url'] != 'auto' and settings['comfy_url'] != result['url']:
                    self.save_settings({'comfy_url': result['url']})
                self.cached_url = result['url']
                self.cached_at = time.monotonic()
        except Exception as error:
            result = {'status': 'error', 'message': str(error), 'log': str(self.data / 'comfy-engine.log')}
            print('本地引擎自动启动失败：' + str(error), flush=True)
        result['checkedAt'] = datetime.now(timezone.utc).isoformat()
        with self.lock:
            atomic_json(self.data / 'engine-start.json', result)

    def recover_disconnected_engine(self, startup):
        # Wait through transient probe failures; preserve actual startup errors for the user.
        with self.lock:
            if not startup or startup.get('status') not in ('ready', 'disconnected') or not self.settings().get('auto_start', True):
                self.engine_disconnected_at = None
                self.engine_disconnected_checks = 0
                return startup
            now = time.monotonic()
            if self.engine_disconnected_at is None:
                self.engine_disconnected_at = now
            self.engine_disconnected_checks += 1
            if self.engine_disconnected_checks < 3 or now - self.engine_disconnected_at < 12:
                return startup
            self.engine_disconnected_at = None
            self.engine_disconnected_checks = 0
            return self.start_local_engine()

    def status(self):
        url = self.comfy_url()
        startup = read_json(self.data / 'engine-start.json', None)
        if url:
            startup = {**(startup or {}), 'status': 'ready', 'url': url}
        state = None
        if url:
            try:
                state = self.comfy('/creation-canvas/state', timeout=2)
            except HTTPProblem as error:
                if error.status == 503:
                    # A slow bridge must not disconnect a responsive generation engine.
                    if probe_engine(url):
                        self.cached_url, self.cached_at = url, time.monotonic()
                    else:
                        url = None
            except ValueError:
                pass
        if not url and startup and startup.get('status') == 'ready':
            startup = {**startup, 'status': 'disconnected', 'message': '本地引擎暂时无法响应，正在重新检测连接。'}
        if url:
            with self.lock:
                self.engine_disconnected_at = None
                self.engine_disconnected_checks = 0
        else:
            startup = self.recover_disconnected_engine(startup)
        return {'connected': bool(url), 'url': url, 'bridgeConnected': bool(state and state.get('clientConnected')),
                'bridgeInstalled': state is not None, 'revision': state.get('revision', 0) if state else 0,
                'message': '本地引擎已连接' if url else (startup or {}).get('message', '请启动本地 ComfyUI'), 'cloudEnabled': False,
                'startup': startup}

    def asset_metadata(self):
        return read_json(self.data / 'assets.json', {})

    def store_asset(self, name, content_type, data, **attributes):
        name = Path(urllib.parse.unquote(name).replace('\\', '/')).name
        suffix = Path(name).suffix.lower()
        if suffix not in MEDIA_EXTENSIONS or not data:
            raise ValueError('仅支持 PNG、JPG、WebP、GIF、MP4、WebM、MOV、MKV 素材')
        if len(data) > MAX_ASSET:
            raise HTTPProblem(413, '素材过大，单个文件最多 512 MB')
        media_type = MEDIA_EXTENSIONS[suffix]
        # Validate signatures before a file is served with an active media MIME type.
        valid = ((suffix == '.png' and data.startswith(b'\x89PNG\r\n\x1a\n'))
                 or (suffix in ('.jpg', '.jpeg') and data.startswith(b'\xff\xd8\xff'))
                 or (suffix == '.gif' and data[:6] in (b'GIF87a', b'GIF89a'))
                 or (suffix == '.webp' and data[:4] == b'RIFF' and data[8:12] == b'WEBP')
                 or (suffix in ('.mp4', '.mov') and b'ftyp' in data[:64])
                 or (suffix in ('.webm', '.mkv') and data[:4] == b'\x1aE\xdf\xa3'))
        if not valid:
            raise ValueError('素材内容与文件类型不符，请选择有效图片或视频')
        asset_id = uuid4().hex + suffix
        (self.assets / asset_id).write_bytes(data)
        entry = {'id': asset_id, 'url': '/assets/' + asset_id, 'name': name,
                 'type': media_type.split('/')[0], 'mime': media_type, 'size': len(data),
                 'source': 'upload', 'createdAt': datetime.now(timezone.utc).isoformat(), **attributes}
        with self.lock:
            metadata = self.asset_metadata()
            metadata[asset_id] = entry
            atomic_json(self.data / 'assets.json', metadata)
        return entry

    @staticmethod
    def media_identity(url):
        parsed = urllib.parse.urlsplit(str(url))
        if parsed.scheme or parsed.netloc or parsed.fragment:
            raise ValueError('仅支持本画布素材和生成结果')
        if parsed.path.startswith('/assets/'):
            asset_id = parsed.path.removeprefix('/assets/')
            if not re.fullmatch(r'[a-f0-9]{32}\.[a-z0-9]+', asset_id):
                raise ValueError('素材地址无效')
            return 'asset:' + asset_id
        if parsed.path == '/api/comfy/view':
            query = CanvasApp.safe_view_query(parsed.query)
            return 'comfy:' + urllib.parse.urlencode(sorted(urllib.parse.parse_qsl(query)))
        raise ValueError('仅支持本画布素材和生成结果')

    def media(self):
        with self.lock:
            assets = self.asset_metadata()
            jobs = deepcopy(list(self.jobs.values()))
        items = {}
        for asset_id, entry in assets.items():
            if entry.get('type') not in ('image', 'video') or not (self.assets / asset_id).is_file():
                continue
            try:
                identity = self.media_identity(entry.get('sourceUrl') or entry['url'])
            except ValueError:
                continue
            items[identity] = {**entry, 'key': 'asset:' + asset_id, 'assetId': asset_id,
                               'source': entry.get('source', 'upload'), 'status': 'completed'}
        for job in jobs:
            if job.get('status') != 'completed':
                continue
            for index, output in enumerate(job.get('outputs', [])):
                if output.get('type') not in ('image', 'video'):
                    continue
                try:
                    identity = self.media_identity(output.get('url'))
                except ValueError:
                    continue
                cached = items.get(identity, {})
                items[identity] = {**output, **cached, 'key': job['id'] + ':' + str(index),
                                   'jobId': job['id'], 'status': 'completed',
                                   'source': 'joined' if job.get('kind') == 'video-join' else 'generated',
                                   'createdAt': job.get('createdAt', cached.get('createdAt'))}
        return sorted(items.values(), key=lambda item: item.get('createdAt') or '', reverse=True)

    def join_videos(self, body):
        if not isinstance(body, dict):
            raise ValueError('拼接参数必须是对象')
        canvas_id = self.job_canvas_id(body)
        clips = body.get('clips')
        if not isinstance(clips, list) or not 2 <= len(clips) <= 20 or any(not isinstance(c, dict) for c in clips):
            raise ValueError('请选择 2 到 20 段视频进行拼接。')
        for clip in clips:
            if clip.get('assetId'):
                _, entry = self.asset(clip['assetId'])
                if entry['type'] != 'video':
                    raise ValueError('拼接仅支持视频素材。')
            else:
                self.media_identity(clip.get('url'))
        name = str(body.get('name') or '拼接视频').replace('\\', '/').rsplit('/', 1)[-1][:120]
        if not name.lower().endswith('.mp4'):
            name += '.mp4'
        job_id = uuid4().hex
        job = {'id': job_id, 'job_id': job_id, 'kind': 'video-join', 'name': name,
               'instance_id': body.get('instance_id'), 'canvas_id': canvas_id, 'status': 'queued', 'progress': 0,
               'outputs': [], 'createdAt': datetime.now(timezone.utc).isoformat(), 'error': None}
        with self.lock:
            if sum(j.get('kind') == 'video-join' and j.get('status') in ('queued', 'running') for j in self.jobs.values()) >= 3:
                raise HTTPProblem(409, '已有视频正在拼接，请等待完成后再添加。')
            self.canvas(canvas_id)
            self.jobs[job_id] = job
        threading.Thread(target=self._join_video_job, args=(job_id, deepcopy(clips)), daemon=True).start()
        return {'job_id': job_id, 'canvas_id': canvas_id, 'status': 'queued'}

    def _join_video_job(self, job_id, clips):
        from video_edit import join
        job = self.jobs[job_id]
        try:
            # ponytail: one local encoder; add a worker queue only if concurrent editing is needed.
            with self.video_lock:
                job['status'] = 'running'
                paths = []
                for clip in clips:
                    entry = self.reference_asset(clip)
                    if entry['type'] != 'video':
                        raise ValueError('拼接仅支持视频素材。')
                    path, _ = self.asset(entry['id'])
                    paths.append(path)
                with tempfile.TemporaryDirectory(prefix='canvas-join-') as folder:
                    output = Path(folder) / 'joined.mp4'
                    info = join(paths, clips, output, lambda progress: job.update(progress=progress))
                    if output.stat().st_size > MAX_ASSET:
                        raise ValueError('拼接结果超过 512 MB，请缩短片段后重试。')
                    entry = self.store_asset(job['name'], 'video/mp4', output.read_bytes(), source='joined',
                                             jobId=job_id, duration=info['duration'], width=info['width'], height=info['height'])
                job.update(status='completed', progress=1, outputs=[{**entry, 'assetId': entry['id']}])
        except Exception as error:
            job.update(status='failed', error=str(error), progress=0)
        finally:
            self.store_terminal_jobs()

    def asset(self, asset_id):
        if not re.fullmatch(r'[a-f0-9]{32}\.[a-z0-9]+', asset_id):
            raise HTTPProblem(404, '素材不存在')
        entry = self.asset_metadata().get(asset_id)
        if not entry or not (self.assets / asset_id).is_file():
            raise HTTPProblem(404, '素材不存在')
        return self.assets / asset_id, entry

    def upload_to_comfy(self, asset_id):
        path, entry = self.asset(asset_id)
        return self.upload_comfy_media(asset_id, entry['mime'], path.read_bytes())

    def upload_comfy_media(self, filename, mime, data):
        boundary = '----Canvas' + uuid4().hex
        body = ('--' + boundary + '\r\nContent-Disposition: form-data; name="image"; filename="' + filename + '"\r\nContent-Type: ' + mime + '\r\n\r\n').encode()
        body += data + ('\r\n--' + boundary + '\r\nContent-Disposition: form-data; name="type"\r\n\r\ninput\r\n--' + boundary + '--\r\n').encode()
        uploaded = self.comfy('/upload/image', 'POST', body, headers={'Content-Type': 'multipart/form-data; boundary=' + boundary}, timeout=120)
        subfolder = uploaded.get('subfolder', '')
        return (subfolder + '/' if subfolder else '') + uploaded['name']

    @staticmethod
    def safe_view_query(query):
        params = urllib.parse.parse_qs(query, keep_blank_values=True)
        allowed = {'filename', 'subfolder', 'type', 'format', 'preview', 'channel'}
        safe = {key: values[0] for key, values in params.items() if key in allowed}
        if not safe.get('filename') or safe.get('type', 'output') not in ('input', 'output', 'temp'):
            raise ValueError('无效的结果素材路径')
        for key in ('filename', 'subfolder'):
            value = safe.get(key, '').replace('\\', '/')
            if value.startswith('/') or '..' in value.split('/') or ':' in value:
                raise ValueError('素材路径越界')
        return urllib.parse.urlencode(safe)

    def reference_asset(self, reference):
        if reference.get('assetId'):
            _, entry = self.asset(reference['assetId'])
            return entry
        value = reference.get('url', '')
        if not isinstance(value, str):
            raise ValueError('参考素材 URL 无效')
        parsed = urllib.parse.urlsplit(value)
        if not parsed.scheme and not parsed.netloc and parsed.path.startswith('/assets/'):
            _, entry = self.asset(parsed.path.removeprefix('/assets/'))
            return entry
        is_relative_view = not parsed.scheme and not parsed.netloc and parsed.path == '/api/comfy/view'
        current_url = self.comfy_url()
        is_comfy_view = current_url and parsed.path == '/view' and (parsed.scheme + '://' + parsed.netloc) == current_url
        if not (is_relative_view or is_comfy_view):
            raise ValueError('仅支持本画布素材或当前本地 ComfyUI 生成结果，禁止任意外部地址')
        if not current_url:
            raise HTTPProblem(503, '本地 ComfyUI 尚未连接，暂时无法复用此结果')
        query = self.safe_view_query(parsed.query)
        parameters = urllib.parse.parse_qs(query)
        source_url = '/api/comfy/view?' + query
        for entry in self.asset_metadata().values():
            if entry.get('sourceUrl') and self.media_identity(entry['sourceUrl']) == self.media_identity(source_url):
                if (self.assets / entry['id']).is_file():
                    return entry
        # Download a result only when it is explicitly reused, never during history polling.
        request = urllib.request.Request(current_url + '/view?' + query)
        with LOCAL_HTTP.open(request, timeout=60) as response:
            data = response.read(MAX_ASSET + 1)
            return self.store_asset(parameters['filename'][0], response.headers.get_content_type(), data,
                                    source='generated', sourceUrl=source_url)

    def resolve_workflow(self, body):
        document = body.get('workflow')
        if document is None:
            document = self.library.get(body.get('workflow_id', body.get('workflowId')))['workflow']
        workflow_format(document)
        return apply_params(document, body.get('values', body.get('params', {})))

    def optimizer_status(self):
        try:
            return self.prompt_optimizer.status()
        except OptimizerProblem as error:
            raise HTTPProblem(error.status, str(error)) from None

    def configure_optimizer(self, body):
        try:
            return self.prompt_optimizer.configure(body)
        except OptimizerProblem as error:
            raise HTTPProblem(error.status, str(error)) from None

    def test_optimizer(self):
        try:
            return self.prompt_optimizer.test_connection()
        except OptimizerProblem as error:
            raise HTTPProblem(error.status, str(error)) from None

    def optimizer_media(self, document, references, context):
        from prompt_media import prepare
        return prepare(self, document, references, context)

    def read_optimizer_reference(self, reference, field):
        from prompt_media import MAX_REFERENCE_BYTES
        if reference:
            entry = self.reference_asset(reference)
            if entry['type'] != field['type']:
                raise ValueError('参考素材类型与工作流输入不符。')
            path, _ = self.asset(entry['id'])
            if path.stat().st_size > MAX_REFERENCE_BYTES:
                raise ValueError('单份参考超过 64 MB，请缩短或压缩后重试。')
            return path.read_bytes(), entry['mime']
        filename, storage_type = str(field['value']), 'input'
        annotated = re.fullmatch(r'(.*) \[(input|output|temp)\]', filename)
        if annotated:
            filename, storage_type = annotated.groups()
        query = self.safe_view_query(urllib.parse.urlencode({'filename': filename, 'type': storage_type}))
        url = self.comfy_url()
        if not url:
            raise ValueError('ComfyUI 尚未连接，无法读取工作流内的参考素材。')
        try:
            with LOCAL_HTTP.open(url + '/view?' + query, timeout=30) as response:
                data = response.read(MAX_REFERENCE_BYTES + 1)
                if len(data) > MAX_REFERENCE_BYTES:
                    raise ValueError('单份参考超过 64 MB，请缩短或压缩后重试。')
                return data, response.headers.get_content_type()
        except urllib.error.HTTPError:
            raise ValueError('工作流内的参考素材已找不到，请重新上传或连接素材。') from None

    def optimize_prompt(self, body):
        if not isinstance(body, dict):
            raise ValueError('提示词优化参数必须是对象')
        document = self.resolve_workflow(body)
        try:
            return self.prompt_optimizer.optimize(document, body)
        except OptimizerProblem as error:
            raise HTTPProblem(error.status, str(error)) from None

    def prepare_workflow(self, body):
        document = self.resolve_workflow(body)
        references = body.get('references', [])
        if not isinstance(references, list):
            raise ValueError('references 必须是列表')
        media_fields = [f for f in fields_for(document) if f['type'] in ('image', 'video') and f['editable']]
        by_id = {field['id']: field for field in media_fields}
        reserved = set()
        for reference in references:
            if not isinstance(reference, dict):
                raise ValueError('参考素材格式不正确')
            field_id = reference.get('fieldId')
            if field_id is not None:
                if not isinstance(field_id, str) or field_id not in by_id:
                    raise ValueError('指定的参考输入不存在或不可编辑：' + str(field_id))
                if field_id in reserved:
                    raise ValueError('同一个参考输入不能重复分配素材：' + by_id[field_id]['nodeTitle'])
                reserved.add(field_id)
        entries = [(reference, self.reference_asset(reference)) for reference in references]
        for reference, entry in entries:
            field_id = reference.get('fieldId')
            if field_id is not None and by_id[field_id]['type'] != entry['type']:
                raise ValueError('参考素材类型与工作流输入不符：' + by_id[field_id]['label'])
        assigned = set(reserved)
        planned = []
        for reference, entry in entries:
            field_id = reference.get('fieldId')
            target = (by_id[field_id] if field_id is not None else
                      next((f for f in media_fields if f['id'] not in assigned and f['type'] == entry['type']), None))
            if target is None:
                raise ValueError('参考素材找不到匹配输入，请选择具体参考图位置。')
            assigned.add(target['id'])
            planned.append((target['id'], entry['id']))
        from segment_reference import materialize
        planned = materialize(self, document, dict(planned))
        for field_id, asset_id in planned.items():
            document = apply_params(document, {field_id: self.upload_to_comfy(asset_id)})
        from reference_acceleration import prepare_loaders
        return prepare_loaders(document, self.reference_loader_ready)

    def missing_references(self, document):
        missing = []
        url = self.comfy_url()
        if not url:
            raise HTTPProblem(503, '本地 ComfyUI 尚未连接')
        for field in fields_for(document):
            if field['type'] not in ('image', 'video') or not field['editable']:
                continue
            filename, storage_type = str(field['value']), 'input'
            annotated = re.fullmatch(r'(.*) \[(input|output|temp)\]', filename)
            if annotated:
                filename, storage_type = annotated.groups()
            query = urllib.parse.urlencode({'filename': filename, 'type': storage_type})
            try:
                with LOCAL_HTTP.open(url + '/view?' + query, timeout=10):
                    pass
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    missing.append({'nodeId': field['nodeId'], 'fieldId': field['id'],
                                    'name': field['value'], 'label': field['nodeTitle'], 'type': field['type']})
                else:
                    raise HTTPProblem(error.code, '参考素材读取失败：' + str(field['value']))
        return missing

    def preflight(self, document, check_references=True):
        info = self.comfy('/object_info', timeout=60)
        self.reference_loader_ready = 'H3ReferenceVideoLoader' in info
        from reference_acceleration import prepare_loaders
        document = prepare_loaders(deepcopy(document), self.reference_loader_ready)
        is_api = workflow_format(document) == 'api'
        definitions = set()
        def gather(graph):
            for sub in graph.get('definitions', {}).get('subgraphs', []):
                if isinstance(sub, dict):
                    definitions.add(sub.get('id'))
                    gather(sub)
        if not is_api:
            gather(document)
        missing_nodes = set()
        for _, node in iter_nodes(document):
            node_type = node.get('class_type') if is_api else node.get('type')
            if not is_api and (node_type in ('MarkdownNote', 'Note', 'PrimitiveNode', 'Reroute') or node.get('mode') in (2, 4)):
                continue
            if node_type not in info and node_type not in definitions:
                missing_nodes.add(node_type)
        missing_models = []
        for field in fields_for(document):
            if not field['model'] or not field['editable']:
                continue
            node_info = info.get(field['nodeType'], {})
            schema = node_info.get('input', {})
            spec = schema.get('required', {}).get(field['key'], schema.get('optional', {}).get(field['key']))
            choices = spec[0] if isinstance(spec, list) and spec else None
            if isinstance(choices, list) and field['value'] not in choices:
                missing_models.append({'nodeId': field['nodeId'], 'fieldId': field['id'],
                                       'name': field['value'], 'label': field['label']})
        warnings = []
        if not is_api:
            warnings.append('普通工作流需安装同步桥，并打开专业模式后执行。')
        references = self.missing_references(document) if check_references else []
        return {'ok': not missing_nodes and not missing_models and not references, 'missingNodes': sorted(missing_nodes),
                'missingModels': missing_models, 'missingReferences': references, 'warnings': warnings}

    def run(self, body):
        if not isinstance(body, dict):
            raise ValueError('生成参数必须是对象')
        canvas_id = self.job_canvas_id(body)
        document = self.resolve_workflow(body)
        is_api = workflow_format(document) == 'api'
        status = self.status()
        if not status['connected']:
            raise HTTPProblem(503, 'ComfyUI 尚未连接，请先设置本地引擎地址。')
        if not is_api and not status['bridgeConnected']:
            raise HTTPProblem(503, '请先打开专业引擎窗口，等待“同步已连接”后生成。', status=status)
        check = self.preflight(document, check_references=False)
        if not check['ok']:
            missing_names = check['missingNodes'] + [item['name'] for item in check['missingModels']]
            raise HTTPProblem(422, '工作流缺少节点或模型：' + '、'.join(missing_names) + '。请在专业引擎补齐后再生成。', preflight=check)
        document = self.prepare_workflow(body)
        missing_references = self.missing_references(document)
        if missing_references:
            raise HTTPProblem(422, '缺少参考素材：' + '、'.join(item['name'] for item in missing_references) + '。请上传并连接到对应输入。',
                              preflight={**check, 'ok': False, 'missingReferences': missing_references})
        continuation = body.get('continuation')
        if continuation is not None:
            from continuity import prepare
            document = prepare(self, document, continuation)
        job_id = uuid4().hex
        job = {'id': job_id, 'job_id': job_id, 'instance_id': body.get('instance_id'),
               'canvas_id': canvas_id,
               'workflow_id': body.get('workflow_id'), 'status': 'submitting', 'progress': 0,
               'outputs': [], 'createdAt': datetime.now(timezone.utc).isoformat(), 'error': None,
               'continuation': continuation is not None}
        with self.lock:
            self.canvas(canvas_id)
            self.jobs[job_id] = job
        threading.Thread(target=self._run_job, args=(job_id, document), daemon=True).start()
        return {'job_id': job_id, 'canvas_id': canvas_id, 'status': 'submitting'}

    def store_terminal_jobs(self):
        with self.lock:
            atomic_json(self.data / 'jobs.json', {key: job for key, job in self.jobs.items()
                        if job.get('status') in ('completed', 'failed')})

    def jobs_list(self):
        with self.lock:
            legacy = [job for job in self.jobs.values() if not job.get('canvas_id')]
            if legacy:
                # Old jobs belong to the original single canvas, never the selected copy.
                index = self.canvas_index()
                original_id = index.get('legacy_id', index['ids'][0])
                for job in legacy:
                    job['canvas_id'] = original_id
                self.store_terminal_jobs()
            return deepcopy(list(self.jobs.values()))

    def _run_job(self, job_id, document):
        job = self.jobs[job_id]
        try:
            with self.submit_lock:
                is_api = workflow_format(document) == 'api'
                if is_api:
                    # A submission is sent once; a lost response must never queue a duplicate.
                    result = self.comfy('/prompt', 'POST', {'prompt': document, 'client_id': 'yingliu-' + job_id}, timeout=60)
                    prompt_id = result.get('prompt_id')
                    if not isinstance(prompt_id, str) or not prompt_id:
                        raise ValueError('ComfyUI 未确认任务编号，请检查引擎队列后再尝试；未自动重提。')
                else:
                    state = self.comfy('/creation-canvas/state')
                    self.snapshot_bridge(state, '生成任务载入前保留专业图')
                    op_id = uuid4().hex
                    self.comfy('/creation-canvas/command', 'POST', {'opId': op_id, 'type': 'execute',
                               'revision': state['revision'], 'workflow': document,
                               'workflowId': ('continuation-' + job_id) if job.get('continuation') else (job.get('instance_id') or job.get('workflow_id'))})
                    deadline = time.monotonic() + 120
                    prompt_id = None
                    while time.monotonic() < deadline:
                        state = self.comfy('/creation-canvas/state')
                        operation = next((op for op in state.get('pending', []) if op.get('opId') == op_id), None)
                        if operation and operation.get('status') == 'failed':
                            raise ValueError(json.dumps(operation.get('error'), ensure_ascii=False))
                        if operation and operation.get('status') == 'uncertain':
                            raise ValueError('专业引擎不确定是否已提交，请先检查队列与历史；画布未自动重新提交。')
                        if operation and operation.get('status') == 'completed':
                            prompt_id = (operation.get('result') or {}).get('promptId')
                            if not prompt_id:
                                raise ValueError('专业引擎未返回任务编号')
                            break
                        time.sleep(0.6)
                    if not prompt_id:
                        raise ValueError('专业引擎同步超时，请保持专业引擎窗口开启；未自动重试提交。')
                job.update(prompt_id=prompt_id, status='queued')
            absent = 0
            while True:
                history = self.comfy('/history/' + urllib.parse.quote(prompt_id, safe=''), timeout=20)
                entry = history.get(prompt_id)
                if entry:
                    status = entry.get('status', {})
                    if status.get('status_str') == 'error':
                        raise ValueError(json.dumps(status.get('messages', []), ensure_ascii=False))
                    outputs = []
                    for node_outputs in entry.get('outputs', {}).values():
                        for key in ('images', 'gifs', 'videos', 'audio'):
                            for item in node_outputs.get(key, []):
                                if not isinstance(item, dict) or not item.get('filename'):
                                    continue
                                query = urllib.parse.urlencode({k: item.get(k, '') for k in ('filename', 'subfolder', 'type')})
                                suffix = Path(item['filename']).suffix.lower()
                                media_type = 'video' if suffix in ('.mp4', '.webm', '.mov', '.mkv') else 'audio' if suffix in ('.mp3', '.wav', '.flac') else 'image'
                                outputs.append({'url': '/api/comfy/view?' + query, 'name': item['filename'], 'type': media_type,
                                                'filename': item['filename'], 'subfolder': item.get('subfolder', ''), 'storageType': item.get('type', 'output')})
                    job.update(status='completed', progress=1, outputs=outputs)
                    return
                if is_api:
                    queue = self.comfy('/queue')
                    running = any(isinstance(item, list) and len(item) > 1 and item[1] == prompt_id for item in queue.get('queue_running', []))
                    pending = any(isinstance(item, list) and len(item) > 1 and item[1] == prompt_id for item in queue.get('queue_pending', []))
                    job['status'] = 'running' if running else 'queued'
                    if not running and not pending:
                        # History can become visible just after a queue item leaves.
                        absent += 1
                        if absent >= 4:
                            raise ValueError('任务已离开引擎队列且没有完成记录，可能已被取消；未自动重提。')
                    else:
                        absent = 0
                else:
                    state = self.comfy('/creation-canvas/state')
                    execution = state.get('execution', {})
                    if execution.get('promptId') == prompt_id:
                        if execution.get('status') in ('error', 'failed', 'interrupted'):
                            raise ValueError(str(execution.get('error') or '任务已中断'))
                        if execution.get('status') == 'running':
                            job['status'] = 'running'
                        progress = execution.get('progress')
                        if isinstance(progress, (int, float)):
                            job['progress'] = max(0, min(1, progress))
                        elif isinstance(progress, dict) and progress.get('max'):
                            job['progress'] = progress.get('value', 0) / progress['max']
                time.sleep(1.5)
        except Exception as error:
            job.update(status='failed', error=str(error), progress=0)
        finally:
            self.store_terminal_jobs()


class Handler(BaseHTTPRequestHandler):
    server_version = 'CreationCanvas/1.7.5'

    @property
    def app(self):
        return self.server.app

    def log_message(self, pattern, *args):
        print('%s %s' % (self.log_date_time_string(), pattern % args), flush=True)

    def json_response(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(data)

    def body(self, limit=MAX_JSON):
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise ValueError('无效的请求长度')
        if length <= 0 or length > limit:
            raise HTTPProblem(413, '请求为空或超过大小限制')
        data = self.rfile.read(length)
        if len(data) != length:
            raise ValueError('请求未完整传输')
        return data

    def json_body(self):
        if self.headers.get_content_type() != 'application/json':
            raise HTTPProblem(415, '请使用 application/json')
        return json.loads(self.body().decode('utf-8-sig'))

    def check_local(self):
        host = self.headers.get('Host', '')
        parsed = urllib.parse.urlsplit('http://' + host)
        if parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise HTTPProblem(403, '仅允许本机访问')
        origin = self.headers.get('Origin')
        if origin and origin not in ('http://127.0.0.1:' + str(self.server.server_port), 'http://localhost:' + str(self.server.server_port)):
            raise HTTPProblem(403, '不允许来自其他页面的写入请求')

    def do_GET(self):
        self.dispatch('GET')

    def do_POST(self):
        self.dispatch('POST')

    def do_PUT(self):
        self.dispatch('PUT')

    def do_DELETE(self):
        self.dispatch('DELETE')

    def dispatch(self, method):
        try:
            self.check_local()
            parsed = urllib.parse.urlsplit(self.path)
            path = urllib.parse.unquote(parsed.path)
            if method == 'GET' and path == '/api/health':
                return self.json_response({'app': 'creation-canvas', 'version': '1.7.5', 'startupProtocol': 1, 'ready': True})
            if method == 'GET' and path == '/api/catalog':
                return self.json_response({'items': self.app.library.catalog()})
            if method == 'GET' and path.startswith('/api/workflows/'):
                return self.json_response(self.app.library.get(path.rsplit('/', 1)[1]))
            if path == '/api/project' and method in ('GET', 'PUT'):
                return self.json_response(self.app.project() if method == 'GET' else self.app.save_project(self.json_body()))
            if path == '/api/canvases' and method in ('GET', 'POST'):
                return self.json_response(self.app.canvases() if method == 'GET' else self.app.create_canvas(self.json_body()),
                                          200 if method == 'GET' else 201)
            if path == '/api/canvases/active' and method == 'PUT':
                body = self.json_body()
                if not isinstance(body, dict):
                    raise ValueError('画布参数必须是对象')
                return self.json_response(self.app.select_canvas(body.get('id')))
            if path == '/api/canvases/order' and method == 'PUT':
                return self.json_response(self.app.order_canvases(self.json_body()))
            if path.startswith('/api/canvases/') and path.endswith('/name') and method == 'PUT':
                canvas_id = path.removeprefix('/api/canvases/').removesuffix('/name')
                return self.json_response(self.app.rename_canvas(canvas_id, self.json_body()))
            if path.startswith('/api/canvases/') and method == 'DELETE':
                body = self.json_body() if int(self.headers.get('Content-Length', '0')) > 0 else {}
                return self.json_response(self.app.delete_canvas(path.removeprefix('/api/canvases/'), body))
            if path.startswith('/api/canvases/') and method in ('GET', 'PUT'):
                canvas_id = path.removeprefix('/api/canvases/')
                return self.json_response(self.app.canvas(canvas_id) if method == 'GET'
                                          else self.app.save_canvas(canvas_id, self.json_body()))
            if path == '/api/settings' and method in ('GET', 'PUT'):
                return self.json_response(self.app.settings() if method == 'GET' else self.app.save_settings(self.json_body()))
            if method == 'GET' and path == '/api/prompt-optimizer':
                return self.json_response(self.app.optimizer_status())
            if path == '/api/ai-provider' and method in ('GET', 'PUT'):
                return self.json_response(self.app.optimizer_status() if method == 'GET' else self.app.configure_optimizer(self.json_body()))
            if method == 'POST' and path == '/api/ai-provider/test':
                body = self.json_body()
                if body != {}:
                    raise ValueError('接口测试只接受空参数，不发送画布或素材。')
                return self.json_response(self.app.test_optimizer())
            if method == 'POST' and path == '/api/prompt-optimizer':
                return self.json_response(self.app.optimize_prompt(self.json_body()))
            if method == 'POST' and path == '/api/import-workflow':
                body = self.json_body()
                return self.json_response(self.app.library.import_document(body, urllib.parse.unquote(self.headers.get('X-Workflow-Name', '')) or (body.get('name', '导入工作流') if isinstance(body, dict) else '导入工作流')), 201)
            if method == 'POST' and path == '/api/assets':
                return self.json_response(self.app.store_asset(self.headers.get('X-Filename', ''), self.headers.get_content_type(), self.body(MAX_ASSET)), 201)
            if method == 'GET' and path == '/api/assets':
                return self.json_response({'items': list(self.app.asset_metadata().values())})
            if method == 'GET' and path == '/api/media':
                return self.json_response({'items': self.app.media()})
            if method == 'POST' and path == '/api/videos/join':
                return self.json_response(self.app.join_videos(self.json_body()), 202)
            if method == 'GET' and path.startswith('/assets/'):
                asset, entry = self.app.asset(path.removeprefix('/assets/'))
                return self.serve_file(asset, entry['mime'])
            if method == 'GET' and path == '/api/comfy/status':
                return self.json_response(self.app.status())
            if method == 'POST' and path == '/api/comfy/start':
                body = self.json_body()
                if not isinstance(body, dict) or not isinstance(body.get('automatic', True), bool):
                    raise ValueError('引擎启动参数无效')
                result = self.app.start_local_engine(body.get('automatic', True))
                self.app.cached_at = 0
                return self.json_response(result, 202 if result.get('status') == 'starting' else 200)
            if path == '/api/comfy/bridge' and method in ('GET', 'POST'):
                route = '/creation-canvas/state' if method == 'GET' else '/creation-canvas/command'
                body = None if method == 'GET' else self.json_body()
                if body is not None and body.get('type') in ('load', 'execute'):
                    self.app.snapshot_bridge(self.app.comfy('/creation-canvas/state'), '工作流载入前保留专业图')
                    body = {**body, 'workflow': self.app.prepare_workflow(body),
                            'workflowId': body.get('workflowId', body.get('workflow_id'))}
                return self.json_response(self.app.comfy(route, method, body), 200 if method == 'GET' else 202)
            if method == 'POST' and path == '/api/preflight':
                body = self.json_body()
                if not isinstance(body, dict) or not isinstance(body.get('checkReferences', True), bool):
                    raise ValueError('工作流检查参数无效')
                return self.json_response(self.app.preflight(self.app.resolve_workflow(body), body.get('checkReferences', True)))
            if method == 'POST' and path == '/api/workflow-fields':
                document = self.app.resolve_workflow(self.json_body())
                return self.json_response({'fields': fields_for(document)})
            if method == 'POST' and path in ('/api/workflows/apply', '/api/prepare-workflow'):
                body = self.json_body()
                document = self.app.prepare_workflow(body)
                return self.json_response({'workflow': document, 'fields': fields_for(document)})
            if method == 'POST' and path == '/api/run':
                return self.json_response(self.app.run(self.json_body()), 202)
            if method == 'GET' and path == '/api/jobs':
                return self.json_response({'items': self.app.jobs_list()})
            if method == 'GET' and path in ('/api/comfy/queue', '/api/comfy/history'):
                return self.json_response(self.app.comfy(path.removeprefix('/api/comfy')))
            if method == 'POST' and path == '/api/comfy/interrupt':
                return self.json_response(self.app.comfy('/interrupt', 'POST', {}))
            if method == 'GET' and path == '/api/comfy/view':
                return self.proxy_media(parsed.query)
            if method == 'GET' and not path.startswith('/api/'):
                relative = path.lstrip('/') or 'index.html'
                web = (self.app.root / 'web').resolve()
                target = (web / relative).resolve()
                if target != web and web in target.parents and target.is_file():
                    return self.serve_file(target)
            raise HTTPProblem(404, '未找到此页面或接口')
        except HTTPProblem as error:
            self.json_response({'error': error.message, **error.detail}, error.status)
        except KeyError:
            self.json_response({'error': '工作流不存在'}, 404)
        except (ValueError, TypeError) as error:
            self.json_response({'error': str(error)}, 400)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as error:
            self.log_error('request failed: %s', str(error))
            self.json_response({'error': '本地服务错误：' + str(error)}, 500)

    def serve_file(self, path, mime=None):
        size = path.stat().st_size
        first, last, status = 0, size - 1, 200
        value = self.headers.get('Range')
        if value:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', value)
            if not match or not any(match.groups()):
                raise HTTPProblem(416, '不支持的媒体读取范围')
            start, end = match.groups()
            if start:
                first, last = int(start), min(int(end), size - 1) if end else size - 1
            else:
                first = max(0, size - int(end))
            if first > last or first >= size:
                self.send_response(416)
                self.send_header('Content-Range', 'bytes */' + str(size))
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            status = 206
        count = last - first + 1
        self.send_response(status)
        self.send_header('Content-Type', mime or mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(count))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if status == 206:
            self.send_header('Content-Range', 'bytes %s-%s/%s' % (first, last, size))
        self.end_headers()
        with path.open('rb') as stream:
            stream.seek(first)
            while count > 0:
                data = stream.read(min(65536, count))
                if not data:
                    break
                self.wfile.write(data)
                count -= len(data)

    def proxy_media(self, query):
        safe_query = self.app.safe_view_query(query)
        url = self.app.comfy_url()
        if not url:
            raise HTTPProblem(503, '本地 ComfyUI 尚未连接')
        headers = {'Range': self.headers['Range']} if self.headers.get('Range') else {}
        request = urllib.request.Request(url + '/view?' + safe_query, headers=headers)
        try:
            with LOCAL_HTTP.open(request, timeout=30) as response:
                self.send_response(response.status)
                for key in ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges'):
                    if response.headers.get(key):
                        self.send_header(key, response.headers[key])
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                while chunk := response.read(65536):
                    self.wfile.write(chunk)
        except urllib.error.HTTPError as error:
            raise HTTPProblem(error.code, '结果素材读取失败')


def make_server(host='127.0.0.1', port=8766, root=ROOT, source=None):
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.app = CanvasApp(root, source)
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    server = make_server(port=args.port)
    print('创作画布已启动：http://127.0.0.1:%s' % server.server_port, flush=True)
    server.app.start_local_engine()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
