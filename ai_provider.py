"""User-owned prompt AI connections. No model discovery, retry or automatic requests."""
from __future__ import annotations

import base64
import ctypes
import ipaddress
import json
import os
from pathlib import Path
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4


PROVIDERS = {'agnes': 'Agnes', 'openai': '兼容接口', 'anthropic': 'Claude 接口', 'ollama': 'Ollama'}
DEFAULTS = {
    'agnes': {'baseUrl': 'https://apihub.agnes-ai.com', 'model': 'agnes-2.5-flash', 'vision': True, 'responseFormat': 'json_schema'},
    'openai': {'baseUrl': '', 'model': '', 'vision': False, 'responseFormat': 'prompt'},
    'anthropic': {'baseUrl': 'https://api.anthropic.com', 'model': '', 'vision': True, 'responseFormat': 'prompt'},
    'ollama': {'baseUrl': 'http://127.0.0.1:11434', 'model': '', 'vision': False, 'responseFormat': 'json_object'},
}


class ProviderProblem(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class _DataBlob(ctypes.Structure):
    _fields_ = [('size', ctypes.c_uint32), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def endpoint(provider, base):
    if not isinstance(base, str) or len(base) > 2048 or re.search(r'[\s\\\x00-\x1f\x7f]', base):
        raise ValueError('请填写有效的 AI 服务地址')
    parsed = urllib.parse.urlsplit(base)
    try:
        port = parsed.port
    except ValueError:
        raise ValueError('AI 服务端口无效') from None
    if (provider not in PROVIDERS or parsed.scheme not in ('https', 'http') or not parsed.hostname or
            parsed.username or parsed.password or parsed.query or parsed.fragment or (port is not None and not 0 < port < 65536)):
        raise ValueError('AI 服务地址只接受 HTTP(S)，不能包含账户、查询参数或密钥')
    local = parsed.hostname.lower() == 'localhost'
    try:
        local = local or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        pass
    if parsed.scheme == 'http' and not local:
        raise ValueError('云端 AI 服务请使用 HTTPS；本机服务可以使用 HTTP')
    path = parsed.path.rstrip('/')
    if provider in ('agnes', 'openai'):
        suffix = '' if path.endswith('/chat/completions') else ('/v1/chat/completions' if not path else '/chat/completions')
    elif provider == 'anthropic':
        suffix = '' if path.endswith('/messages') else ('/v1/messages' if not path else '/messages')
    else:
        suffix = '' if path.endswith('/chat') else ('/api/chat' if not path else '/chat')
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path + suffix, '', ''))


def _protect(data, decrypt=False):
    if os.name != 'nt':
        return data
    from ctypes import wintypes
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, target = _DataBlob(len(data), buffer), _DataBlob()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        ok = crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        ok = crypt32.CryptProtectData(ctypes.byref(source), 'Yingliu AI connection', None, None, None, 1, ctypes.byref(target))
    if not ok:
        raise OSError('本机密钥保护不可用')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel32.LocalFree(target.data)


class ProviderStore:
    def __init__(self, root, legacy_loader):
        self.path = Path(root) / 'data' / 'ai-provider.json'
        self.legacy_loader = legacy_loader
        self.lock = threading.RLock()

    def load(self):
        with self.lock:
            try:
                document = json.loads(self.path.read_text(encoding='utf-8-sig'))
            except FileNotFoundError:
                values = self.legacy_loader()
                return {'provider': 'agnes', **DEFAULTS['agnes'],
                        'baseUrl': values.get('AGNES_API_BASE') or DEFAULTS['agnes']['baseUrl'],
                        'model': values.get('AGNES_CHAT_MODEL') or DEFAULTS['agnes']['model'],
                        'apiKey': values.get('AGNES_API_KEY', ''),
                        'keySource': 'environment' if values.get('AGNES_API_KEY') else 'none'}
            except (OSError, ValueError):
                raise ProviderProblem(503, 'AI 连接配置无法读取，请重新保存设置；原文件仍保留') from None
            try:
                provider = document['provider']
                config = {key: document[key] for key in ('provider', 'baseUrl', 'model', 'vision', 'responseFormat')}
                endpoint(provider, config['baseUrl'])
                if not isinstance(config['model'], str) or not isinstance(config['vision'], bool) or config['responseFormat'] not in ('prompt', 'json_object', 'json_schema'):
                    raise ValueError()
                encrypted = document.get('protectedKey', '')
                key = ''
                if encrypted:
                    raw = base64.b64decode(encrypted, validate=True)
                    if document.get('keyProtection') == 'windows-dpapi':
                        if os.name != 'nt':
                            raise ValueError()
                        raw = _protect(raw, decrypt=True)
                    elif document.get('keyProtection') != 'owner-file':
                        raise ValueError()
                    secret = json.loads(raw.decode('utf-8'))
                    # A moved/edited connection never sends another service's credential.
                    if secret.get('provider') == provider and secret.get('endpoint') == endpoint(provider, config['baseUrl']):
                        key = secret['apiKey']
                        if not isinstance(key, str) or len(key) > 8192 or re.search(r'[\x00-\x20\x7f]', key):
                            raise ValueError()
                return {**config, 'apiKey': key, 'keySource': 'local' if key else 'none'}
            except (KeyError, TypeError, ValueError, OSError):
                raise ProviderProblem(503, 'AI 连接配置或本机密钥无法读取，请重新保存设置') from None

    def save(self, body):
        if not isinstance(body, dict):
            raise ValueError('AI 连接设置格式无效')
        allowed = {'provider', 'baseUrl', 'model', 'vision', 'responseFormat', 'apiKey', 'clearKey'}
        if set(body) - allowed:
            raise ValueError('AI 连接设置包含未知选项')
        with self.lock:
            try:
                current = self.load()
            except ProviderProblem:
                if not all(key in body for key in ('provider', 'baseUrl', 'model')):
                    raise
                current = {'provider': 'agnes', **DEFAULTS['agnes'], 'apiKey': '', 'keySource': 'none'}
            provider = body.get('provider', current['provider'])
            if provider not in PROVIDERS:
                raise ValueError('请选择支持的 AI 接口类型')
            defaults = current if provider == current['provider'] else DEFAULTS[provider]
            config = {'provider': provider, **{key: body.get(key, defaults[key]) for key in ('baseUrl', 'model', 'vision', 'responseFormat')}}
            for name in ('baseUrl', 'model'):
                if not isinstance(config[name], str):
                    raise ValueError('AI 地址和模型名称必须是文字')
                config[name] = config[name].strip()
            target = endpoint(provider, config['baseUrl'])
            if not config['model'] or len(config['model']) > 200 or re.search(r'[\x00-\x1f\x7f]', config['model']):
                raise ValueError('请填写模型名称，最多 200 字符')
            if not isinstance(config['vision'], bool) or config['responseFormat'] not in ('prompt', 'json_object', 'json_schema'):
                raise ValueError('请选择有效的图片能力和返回格式')
            key, clear = body.get('apiKey', ''), body.get('clearKey', False)
            if not isinstance(key, str) or len(key) > 8192 or re.search(r'[\x00-\x20\x7f]', key) or not isinstance(clear, bool):
                raise ValueError('API 密钥格式无效')
            if clear:
                key = ''
            elif not key and provider == current['provider']:
                try:
                    same_service = target == endpoint(current['provider'], current['baseUrl'])
                except ValueError:
                    same_service = False
                if same_service:
                    key = current['apiKey']
            document = dict(config)
            if key:
                raw = json.dumps({'provider': provider, 'endpoint': target, 'apiKey': key}).encode('utf-8')
                document['protectedKey'] = base64.b64encode(_protect(raw)).decode('ascii')
                document['keyProtection'] = 'windows-dpapi' if os.name == 'nt' else 'owner-file'
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + '.' + uuid4().hex + '.tmp')
            try:
                with temporary.open('x', encoding='utf-8') as stream:
                    if os.name != 'nt':
                        os.chmod(temporary, 0o600)
                    json.dump(document, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            except OSError:
                raise ProviderProblem(503, 'AI 设置保存失败，请检查软件目录的写入权限') from None
            finally:
                temporary.unlink(missing_ok=True)
            return self.load()


def configured(config):
    try:
        endpoint(config['provider'], config['baseUrl'])
    except (ValueError, KeyError):
        return False
    host = urllib.parse.urlsplit(config['baseUrl']).hostname
    local = host == 'localhost'
    try:
        local = local or ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    return bool(config.get('model')) and (bool(config.get('apiKey')) or (local and config['provider'] != 'anthropic'))


def _image(part):
    match = re.fullmatch(r'data:(image/(?:jpeg|png|webp|gif));base64,([A-Za-z0-9+/=]+)', part.get('image_url', {}).get('url', ''))
    if not match:
        raise ValueError('参考图片必须是本机准备的图片数据')
    return match.groups()


def protocol_payload(config, payload):
    """Translate the existing internal chat container; no external image URL is fetched."""
    provider = config['provider']
    payload = dict(payload)
    output = payload.pop('response_format', None)
    mode = config['responseFormat']
    if provider in ('agnes', 'openai'):
        # shortcut: custom gateway aliases need their real model name to select reasoning parameters.
        if provider == 'openai' and re.match(r'^(?:o[134](?:-|$)|gpt-[56](?:[.-]|$))', str(payload.get('model', '')).lower()):
            payload.pop('temperature', None)
            if 'max_tokens' in payload:
                payload['max_completion_tokens'] = payload.pop('max_tokens')
        if mode == 'json_schema' and output:
            payload['response_format'] = output
        elif mode == 'json_object':
            payload['response_format'] = {'type': 'json_object'}
        return payload
    messages = []
    for message in payload['messages']:
        content = message['content']
        if provider == 'anthropic':
            if message['role'] == 'system':
                continue
            if isinstance(content, list):
                blocks = []
                for part in content:
                    if part['type'] == 'text':
                        blocks.append(part)
                    else:
                        mime, data = _image(part)
                        blocks.append({'type': 'image', 'source': {'type': 'base64', 'media_type': mime, 'data': data}})
                content = blocks
            messages.append({'role': message['role'], 'content': content})
        else:
            images = []
            if isinstance(content, list):
                texts = []
                for part in content:
                    if part['type'] == 'text':
                        texts.append(part['text'])
                    else:
                        images.append(_image(part)[1])
                        texts.append('[Attached image %d, in images-array order]' % len(images))
                content = '\n'.join(texts)
            messages.append({'role': message['role'], 'content': content, **({'images': images} if images else {})})
    if provider == 'anthropic':
        result = {'model': payload['model'], 'messages': messages, 'max_tokens': payload.get('max_tokens', 8192),
                  'system': '\n'.join(item['content'] for item in payload['messages'] if item['role'] == 'system'), 'stream': False}
        if mode == 'json_schema' and output:
            result['output_config'] = {'format': {'type': 'json_schema', 'schema': output['json_schema']['schema']}}
        return result
    result = {'model': payload['model'], 'messages': messages, 'stream': False,
              'options': {'num_predict': payload.get('max_tokens', 8192), 'temperature': payload.get('temperature', 0.3)}}
    if mode == 'json_schema' and output:
        result['format'] = output['json_schema']['schema']
    elif mode == 'json_object':
        result['format'] = 'json'
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        # Never forward credentials, reference images or paid POSTs to a redirect target.
        return None


def request(config, payload):
    url = endpoint(config['provider'], config['baseUrl'])
    data = protocol_payload(config, payload)
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if config['provider'] == 'anthropic':
        headers['x-api-key'] = config.get('apiKey', '')
        headers['anthropic-version'] = '2023-06-01'
    elif config.get('apiKey'):
        headers['Authorization'] = 'Bearer ' + config['apiKey']
    req = urllib.request.Request(url, data=json.dumps(data, ensure_ascii=False).encode('utf-8'), headers=headers, method='POST')
    try:
        with urllib.request.build_opener(_NoRedirect()).open(req, timeout=120) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError('oversized response')
            result = json.loads(raw)
        if config['provider'] == 'anthropic':
            return {'choices': [{'finish_reason': 'length' if result.get('stop_reason') == 'max_tokens' else 'stop',
                                 'message': {'content': ''.join(part['text'] for part in result['content'] if part.get('type') == 'text')}}]}
        if config['provider'] == 'ollama':
            return {'choices': [{'finish_reason': 'length' if result.get('done_reason') == 'length' else 'stop',
                                 'message': {'content': result['message']['content']}}]}
        return result
    except urllib.error.HTTPError as error:
        error.close()
        message = ('AI 请求过于频繁，请稍后重试' if error.code == 429 else
                   'AI 密钥或访问权限无效，请检查连接设置' if error.code in (401, 403) else
                   'AI 服务地址或模型不存在，请检查连接设置' if error.code == 404 else
                   'AI 服务不接受当前参数，请检查接口类型、返回格式和图片能力' if error.code in (400, 422) else
                   'AI 服务返回重定向，请填写最终的服务地址' if 300 <= error.code < 400 else
                   'AI 服务暂时无法完成请求，请稍后重试')
        raise ProviderProblem(429 if error.code == 429 else 502, message) from None
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
        raise ProviderProblem(504, 'AI 响应未确认；请稍后再试，软件不会自动重复请求') from None
