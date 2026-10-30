"""Session-only, lossless spill cache for the native H3 reference encoder.

ComfyUI supplies the complete input-signature hash. Only known CPU values are
serialized; unsupported graphs/data and every I/O error fall back to execution.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from uuid import uuid4


TARGET = 'MiniMaxH3ReferenceToVideo'
MAX_BYTES = 1024 ** 3
_provider = None


def _running_graph(prompt_id, node_id):
    from server import PromptServer
    queue = PromptServer.instance.prompt_queue
    getter = getattr(queue, 'get_current_queue_volatile', None) or queue.get_current_queue
    for item in getter()[0]:
        if (len(item) > 2 and item[1] == prompt_id and isinstance(item[2], dict)
                and item[2].get(str(node_id), {}).get('class_type') == TARGET):
            return item[2]
    return None


def _model_files(graph, node_id):
    """Only trace model inputs whose file-loading behavior is known here."""
    import folder_paths
    target = graph[str(node_id)]
    inputs = target['inputs']
    if 'embedding:' in json.dumps(graph, ensure_ascii=False).lower():
        raise ValueError('external embedding')
    files = set()
    visited = set()

    def weight(category, name):
        if not isinstance(name, str):
            raise ValueError('dynamic model name')
        path = Path(folder_paths.get_full_path_or_raise(category, name)).resolve()
        stat = path.stat()
        if not path.is_file():
            raise ValueError('model is not a file')
        files.add((str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino))

    def model(link, kind):
        if not (isinstance(link, list) and len(link) == 2
                and isinstance(link[0], str) and isinstance(link[1], int)):
            raise ValueError('unknown model connection')
        marker = (link[0], link[1], kind)
        if marker in visited:
            raise ValueError('cyclic model connection')
        visited.add(marker)
        node = graph[link[0]]
        name, values = node['class_type'], node['inputs']
        if kind == 'clip' and link[1] == 0 and name in ('CLIPLoader', 'DualCLIPLoader'):
            keys = ('clip_name',) if name == 'CLIPLoader' else ('clip_name1', 'clip_name2')
            for key in keys:
                weight('text_encoders', values[key])
        elif kind == 'clip' and link[1] == 0 and name == 'CLIPSetLastLayer':
            model(values['clip'], kind)
        elif kind == 'clip' and link[1] == 1 and name == 'LoraLoader':
            weight('loras', values['lora_name'])
            model(values['clip'], kind)
        elif kind == 'vae' and link[1] == 0 and name == 'VAELoader':
            # Composed/approximate VAEs have a separate loading contract.
            weight('vae', values['vae_name'])
        elif name == 'CheckpointLoaderSimple' and link[1] == {'clip': 1, 'vae': 2}[kind]:
            weight('checkpoints', values['ckpt_name'])
        else:
            raise ValueError('unrecognized model loader')
        visited.remove(marker)

    model(inputs['clip'], 'clip')
    for key in ('vae', 'audio_vae'):
        if inputs.get(key) is not None:
            model(inputs[key], 'vae')
    return sorted(files)


def _pack(value, torch, nested_type, budget):
    if type(value) in (str, int, float, bool, type(None)):
        return ['scalar', value]
    if type(value) is torch.Tensor:
        if (value.device.type != 'cpu' or value.layout != torch.strided
                or value.is_quantized or value.requires_grad):
            raise ValueError('unsupported tensor')
        budget[0] += value.numel() * value.element_size()
        if budget[0] > budget[1]:
            raise ValueError('entry exceeds disk limit')
        return ['tensor', value.detach().clone()]
    if type(value) is nested_type:
        return ['nested', [_pack(item, torch, nested_type, budget) for item in value.tensors]]
    if type(value) in (list, tuple):
        return [type(value).__name__, [_pack(item, torch, nested_type, budget) for item in value]]
    if type(value) is dict:
        if not all(type(key) in (str, int, float, bool, type(None)) for key in value):
            raise ValueError('unsupported dictionary key')
        return ['dict', [[key, _pack(item, torch, nested_type, budget)] for key, item in value.items()]]
    raise ValueError('unsupported output object')


def _unpack(value, torch, nested_type):
    tag, data = value
    if tag == 'scalar' and type(data) in (str, int, float, bool, type(None)):
        return data
    if tag == 'tensor' and type(data) is torch.Tensor and data.device.type == 'cpu':
        return data
    if tag in ('list', 'tuple', 'nested'):
        items = [_unpack(item, torch, nested_type) for item in data]
        return {'list': list, 'tuple': tuple, 'nested': nested_type}[tag](items)
    if tag == 'dict':
        return {key: _unpack(item, torch, nested_type) for key, item in data}
    raise ValueError('invalid cache value')


class ReferenceCache:
    def __init__(self, directory=None, max_bytes=MAX_BYTES):
        self.started_ns = time.time_ns()
        self._temporary = tempfile.TemporaryDirectory(prefix='creation-canvas-h3-') if directory is None else None
        self.directory = Path(self._temporary.name if self._temporary else directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.prompt_id = None
        self.lock = threading.Lock()
        self.stats = dict(enabled=True, hits=0, misses=0, stores=0, skipped=0, errors=0)
        self._known_models = {}
        self._disabled_models = set()
        self._lookup_stamps = {}

    def should_cache(self, context, value=None):
        return (context.class_type == TARGET and isinstance(context.cache_key_hash, str)
                and re.fullmatch(r'[a-f0-9]{64}', context.cache_key_hash) is not None)

    def on_prompt_start(self, prompt_id):
        self.prompt_id = prompt_id
        self._lookup_stamps.clear()

    def on_prompt_end(self, prompt_id):
        if self.prompt_id == prompt_id:
            self.prompt_id = None
        self._lookup_stamps.clear()

    def _key(self, context):
        graph = _running_graph(self.prompt_id, context.node_id)
        if graph is None:
            raise ValueError('no active graph')
        stamp = _model_files(graph, context.node_id)
        for path, *identity in stamp:
            if max(identity[1:3]) > self.started_ns:
                # A cached loader may predate a file changed before our first lookup.
                self._disabled_models.add(path)
            if path in self._known_models and self._known_models[path] != identity:
                # Once a file changes, loaded model objects may still hold old weights.
                self._disabled_models.add(path)
            self._known_models[path] = identity
            if path in self._disabled_models:
                raise ValueError('model changed within session')
        payload = json.dumps([context.cache_key_hash, stamp], separators=(',', ':')).encode()
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _types():
        import torch
        from comfy.nested_tensor import NestedTensor
        return torch, NestedTensor

    async def on_lookup(self, context):
        if not self.should_cache(context):
            return None
        try:
            key = self._key(context)
            self._lookup_stamps[(context.node_id, context.cache_key_hash)] = key
            result = await asyncio.to_thread(self._read, key)
            if result is None:
                self.stats['misses'] += 1
                return None
            if self._key(context) != key:
                raise ValueError('model changed during lookup')
            from comfy_api.latest._caching import CacheValue
            self.stats['hits'] += 1
            return CacheValue(outputs=result[0], ui=result[1])
        except Exception:
            self.stats['skipped'] += 1
            return None

    async def on_store(self, context, value):
        if not self.should_cache(context, value):
            return
        try:
            key = self._key(context)
            earlier = self._lookup_stamps.get((context.node_id, context.cache_key_hash))
            if earlier is not None and earlier != key:
                raise ValueError('weights changed during execution')
            torch, nested = self._types()
            packed = _pack((value.outputs, value.ui), torch, nested, [0, self.max_bytes])
            await asyncio.to_thread(self._write, key, packed)
        except Exception:
            self.stats['skipped'] += 1

    def _read(self, key):
        path = self.directory / (key + '.pt')
        with self.lock:
            if not path.exists():
                return None
            try:
                if path.stat().st_size > self.max_bytes:
                    raise ValueError('oversized cache')
                torch, nested = self._types()
                payload = torch.load(path, weights_only=True, map_location='cpu')
                if payload.get('version') != 1 or payload.get('key') != key:
                    raise ValueError('invalid cache envelope')
                result = _unpack(payload['value'], torch, nested)
                if not isinstance(result, tuple) or len(result) != 2 or type(result[0]) not in (list, tuple):
                    raise ValueError('invalid node outputs')
                os.utime(path, None)
                return result
            except Exception:
                self.stats['errors'] += 1
                path.unlink(missing_ok=True)
                return None

    def _write(self, key, packed):
        path = self.directory / (key + '.pt')
        temporary = self.directory / (uuid4().hex + '.tmp')
        with self.lock:
            try:
                torch, _ = self._types()
                torch.save({'version': 1, 'key': key, 'value': packed}, temporary)
                size = temporary.stat().st_size
                if size > self.max_bytes:
                    return
                entries = sorted(self.directory.glob('*.pt'), key=lambda p: p.stat().st_mtime_ns)
                total = sum(item.stat().st_size for item in entries if item != path)
                for entry in entries:
                    if total + size <= self.max_bytes:
                        break
                    if entry != path:
                        total -= entry.stat().st_size
                        entry.unlink(missing_ok=True)
                os.replace(temporary, path)
                self.stats['stores'] += 1
            finally:
                temporary.unlink(missing_ok=True)

    def get_stats(self):
        with self.lock:
            return {**self.stats, 'bytes': sum(p.stat().st_size for p in self.directory.glob('*.pt')),
                    'limitBytes': self.max_bytes}


def register():
    global _provider
    if _provider is None:
        from comfy_execution.cache_provider import register_cache_provider
        candidate = ReferenceCache()
        register_cache_provider(candidate)
        _provider = candidate
    return _provider


def get_stats():
    return _provider.get_stats() if _provider else {'enabled': False}
