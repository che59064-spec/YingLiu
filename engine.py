"""Start the existing local ComfyUI without changing its models or workflows."""
import argparse
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
LOCK = threading.Lock()
PROCESS = None
# Loopback traffic must not inherit a system proxy cached when the app started.
LOCAL_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def runtime_settings(root=None, overrides=None):
    """Local machine configuration is separate from distributable source files."""
    base = Path(root) if root is not None else ROOT
    values = {'comfy_url': 'auto', 'comfy_port': 8188, 'auto_start': True}
    for path in (base / 'config.json', base / 'data' / 'settings.json'):
        try:
            saved = json.loads(path.read_text(encoding='utf-8-sig'))
        except FileNotFoundError:
            continue
        if not isinstance(saved, dict):
            raise ValueError('本机配置需要使用 JSON 对象：' + path.name)
        values.update(saved)
    if overrides is not None:
        values.update(overrides)
    for name, key in (('YINGLIU_COMFY_ROOT', 'comfy_root'), ('YINGLIU_COMFY_PYTHON', 'comfy_python')):
        if os.environ.get(name):
            values[key] = os.environ[name]
    return values


def local_path(value):
    path = Path(os.path.expandvars(str(value))).expanduser()
    return path if path.is_absolute() else ROOT / path


def engine_address(settings):
    configured = settings.get('comfy_url', 'auto')
    if configured not in ('auto', '', None):
        parsed = urllib.parse.urlsplit(str(configured))
        if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1')
                or not parsed.port or parsed.path not in ('', '/') or parsed.query
                or parsed.fragment or parsed.username or parsed.password):
            raise ValueError('ComfyUI 地址仅支持本机 HTTP 地址和端口')
        return str(configured).rstrip('/'), parsed.port
    port = settings.get('comfy_port', 8188)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('ComfyUI 端口需要在 1–65535 之间')
    return 'http://127.0.0.1:%d' % port, port


def comfy_directory(settings):
    configured = settings.get('comfy_root', '')
    candidates = ([local_path(configured)] if configured else
                  [ROOT.parent / 'ComfyUI', ROOT.parent / 'ComfyUI' / 'ComfyUI',
                   ROOT.parent / 'ComfyUI_windows_portable' / 'ComfyUI',
                   Path.home() / 'ComfyUI', Path.home() / 'Documents' / 'ComfyUI'])
    for candidate in candidates:
        if (candidate / 'main.py').is_file():
            return candidate.resolve()
    raise RuntimeError('未找到 ComfyUI。请在连接设置中填写包含 main.py 的 ComfyUI 文件夹，或自行启动 ComfyUI 后连接。')


def comfy_python(settings, directory):
    configured = settings.get('comfy_python', '')
    candidates = ([local_path(configured)] if configured else
                  [directory / '.venv' / 'Scripts' / 'python.exe', directory / '.venv' / 'bin' / 'python',
                   directory / 'venv' / 'Scripts' / 'python.exe', directory / 'venv' / 'bin' / 'python',
                   directory.parent / 'python_embeded' / 'python.exe'])
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError('未找到 ComfyUI 的 Python 环境。请在连接设置中填写已有环境的 Python 路径；映流不会另装模型。')


def process_alive(pid):
    if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
        return False
    if os.name != 'nt':
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError, OSError):
            return False
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        # A protected process may still be the original engine.
        return kernel.GetLastError() == 5
    try:
        code = ctypes.c_ulong()
        return bool(kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))) and code.value == 259
    finally:
        kernel.CloseHandle(ctypes.c_void_p(handle))


def process_start_time(pid):
    """Return Windows process creation time as a Unix timestamp, if available."""
    if os.name != 'nt':
        return None
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        created, exited, kernel_time, user_time = (ctypes.c_ulonglong() for _ in range(4))
        if not kernel.GetProcessTimes(ctypes.c_void_p(handle), ctypes.byref(created), ctypes.byref(exited),
                                      ctypes.byref(kernel_time), ctypes.byref(user_time)):
            return None
        return created.value / 10000000 - 11644473600
    finally:
        kernel.CloseHandle(ctypes.c_void_p(handle))


def recorded_process_alive(previous):
    pid = previous.get('pid')
    if not process_alive(pid):
        return False
    actual = process_start_time(pid)
    recorded = previous.get('processCreatedAt', previous.get('startedAt'))
    # Old metadata and inaccessible process details are treated conservatively.
    if actual is None or type(recorded) not in (int, float):
        return True
    return abs(actual - recorded) <= 10


def port_listening(port):
    # A busy listener can accept connections slowly; bind detects occupation
    # without mistaking a full backlog for a stopped engine.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(('127.0.0.1', port))
            return False
    except OSError:
        return True


@contextmanager
def engine_start_lock(port):
    """Serialize launch decisions across canvas processes on the same port."""
    if os.name != 'nt':
        import fcntl
        import tempfile
        with open(os.path.join(tempfile.gettempdir(), 'yingliu-comfy-%d.lock' % port), 'a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
        return
    kernel = ctypes.windll.kernel32
    kernel.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel.CreateMutexW(None, False, 'Local\\YingLiuComfyStart-%d' % port)
    if not handle:
        raise OSError('无法创建 ComfyUI 启动锁')
    acquired = False
    try:
        result = kernel.WaitForSingleObject(ctypes.c_void_p(handle), 30000)
        acquired = result in (0, 0x80)  # WAIT_OBJECT_0 or WAIT_ABANDONED
        if not acquired:
            raise RuntimeError('另一个窗口正在检查 ComfyUI，请稍后重试。')
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(ctypes.c_void_p(handle))
        kernel.CloseHandle(ctypes.c_void_p(handle))


def probe_engine(url):
    # Queue status stays inexpensive while large models are loading or running.
    for route in ('/queue', '/object_info'):
        try:
            with LOCAL_HTTP.open(url + route, timeout=2) as response:
                data = json.load(response)
            if isinstance(data, dict) and ((route == '/queue'
                    and isinstance(data.get('queue_running'), list)
                    and isinstance(data.get('queue_pending'), list))
                    or (route == '/object_info' and any(isinstance(node, dict)
                        and 'input' in node and 'output' in node for node in data.values()))):
                return True
        except (OSError, ValueError, urllib.error.URLError):
            continue
    return False


def find_engine(settings=None):
    settings = runtime_settings(overrides=settings)
    preferred, _ = engine_address(settings)
    candidates = [preferred]
    if settings.get('comfy_url', 'auto') in ('auto', '', None):
        candidates.extend(['http://127.0.0.1:8188', 'http://127.0.0.1:8000'])
    for url in dict.fromkeys(candidates):
        if probe_engine(url):
            return url
    return None


def install_bridge(settings=None):
    directory = comfy_directory(runtime_settings(overrides=settings))
    source = ROOT / 'bridge' / 'ComfyUI-CreationCanvas'
    target = directory / 'custom_nodes' / 'ComfyUI-CreationCanvas'
    if not (source / '__init__.py').is_file():
        raise RuntimeError('同步桥文件尚未就绪。')
    for relative in ('reference_video.py', 'reference_cache.py', 'web/creation_canvas_bridge.js', '__init__.py'):
        original = source / relative
        destination = target / relative
        data = original.read_bytes()
        if destination.exists() and destination.read_bytes() == data:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            backup = ROOT / 'data' / 'bridge-backups' / str(time.time_ns()) / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, backup)
        destination.write_bytes(data)
    return str(target)


def start_engine(settings=None):
    global PROCESS
    options = runtime_settings(overrides=settings)
    address, port = engine_address(options)
    with LOCK, engine_start_lock(port):
        url = find_engine(options)
        if url:
            return {'status': 'ready', 'url': url}
        if not options.get('auto_start', True):
            raise RuntimeError('自动启动已关闭。请自行启动 ComfyUI，再点击重新连接。')
        DATA.mkdir(parents=True, exist_ok=True)
        if PROCESS is not None and PROCESS.poll() is None:
            return {'status': 'starting', 'pid': PROCESS.pid, 'url': address}
        if port_listening(port):
            return {'status': 'starting', 'url': address}
        pid_file = DATA / 'engine-process.json'
        try:
            previous = json.loads(pid_file.read_text(encoding='utf-8'))
        except (OSError, ValueError, UnicodeError):
            previous = None
        if isinstance(previous, dict):
            # A second app process must not launch another engine during startup.
            if recorded_process_alive(previous):
                return {**previous, 'status': 'starting', 'url': address}
        directory = comfy_directory(options)
        python = comfy_python(options, directory)
        install_bridge(options)
        extra = options.get('comfy_args', [])
        if not isinstance(extra, list) or len(extra) > 100 or any(not isinstance(arg, str) or '\x00' in arg for arg in extra):
            raise ValueError('ComfyUI 附加参数需要使用字符串列表')
        if any(arg.split('=')[0] in ('--listen', '--port') for arg in extra):
            raise ValueError('请通过 ComfyUI 地址或端口设置连接，不要在附加参数中设置 listen/port')
        args = [str(python), str(directory / 'main.py'), '--listen', '127.0.0.1', '--port', str(port),
                '--disable-auto-launch', *extra]
        configs = options.get('extra_model_paths', [])
        if not isinstance(configs, list) or any(not isinstance(path, str) for path in configs):
            raise ValueError('额外模型路径配置需要使用路径列表')
        if configs:
            resolved = [local_path(path) for path in configs]
            missing = [str(path) for path in resolved if not path.is_file()]
            if missing:
                raise RuntimeError('额外模型路径配置文件不存在：' + '；'.join(missing))
            args += ['--extra-model-paths-config', *map(str, resolved)]
        for key, flag in (('input_dir', '--input-directory'), ('output_dir', '--output-directory')):
            if options.get(key):
                path = local_path(options[key])
                if not path.is_dir():
                    raise RuntimeError('配置的素材或输出文件夹不存在：' + key)
                args += [flag, str(path)]
        log_path = DATA / 'comfy-engine.log'
        with log_path.open('ab', buffering=0) as log:
            PROCESS = subprocess.Popen(args, cwd=str(directory), stdout=log, stderr=log,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        result = {'status': 'starting', 'pid': PROCESS.pid, 'startedAt': time.time(),
                  'url': address, 'log': str(log_path)}
        created = process_start_time(PROCESS.pid)
        if created is not None:
            result['processCreatedAt'] = created
        pid_file.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        return result


def ensure_engine(timeout=180, settings=None):
    """Reuse or start ComfyUI, then wait until its HTTP API is ready."""
    deadline = time.monotonic() + max(0, timeout)
    result = start_engine(settings) if settings is not None else start_engine()
    if result['status'] == 'ready':
        return result
    log_path = result.get('log', str(DATA / 'comfy-engine.log'))
    while True:
        url = find_engine(settings) if settings is not None else find_engine()
        if url:
            return {**result, 'status': 'ready', 'url': url}
        pid = result.get('pid')
        if PROCESS is not None and PROCESS.pid == pid:
            code = PROCESS.poll()
            if code is not None:
                raise RuntimeError(f'ComfyUI 启动失败，进程已退出（退出码 {code}）。请查看日志：{log_path}')
        elif pid is not None and not process_alive(pid):
            raise RuntimeError(f'ComfyUI 启动失败，启动进程已退出。请查看日志：{log_path}')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(f'等待 ComfyUI 启动超时（{timeout} 秒）。引擎仍可能在加载，请稍后重试；日志：{log_path}')
        time.sleep(min(1, remaining))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', action='store_true')
    parser.add_argument('--install-bridge', action='store_true')
    args = parser.parse_args()
    try:
        if args.install_bridge:
            print(json.dumps({'installed': install_bridge()}, ensure_ascii=False))
        elif args.start:
            print(json.dumps(start_engine(), ensure_ascii=False))
        else:
            print(json.dumps({'url': find_engine()}, ensure_ascii=False))
    except (OSError, RuntimeError, ValueError) as error:
        raise SystemExit(str(error))
