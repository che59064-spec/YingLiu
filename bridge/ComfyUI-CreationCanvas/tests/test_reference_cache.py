"""Offline CPU checks. No Comfy server, model loading, CUDA or generation."""
import asyncio
from dataclasses import dataclass
import importlib.util
from pathlib import Path
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import torch


BASE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('canvas_reference_cache', BASE / 'reference_cache.py')
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)


class NestedTensor:
    def __init__(self, tensors):
        self.tensors = list(tensors)


@dataclass
class CacheValue:
    outputs: list
    ui: dict = None


class ReferenceCacheCheck(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.weight = self.root / 'clip.safetensors'
        self.weight.write_bytes(b'not-a-model-only-statted')
        self.vae = self.root / 'vae.safetensors'
        self.vae.write_bytes(b'another-fake-file')
        self.graph = {
            '136': {'class_type': cache.TARGET, 'inputs': {
                'clip': ['128', 0], 'vae': ['120', 0], 'prompt': 'same input',
                'width': 864, 'height': 480, 'length': 124}},
            '128': {'class_type': 'CLIPLoader', 'inputs': {'clip_name': 'clip.safetensors'}},
            '120': {'class_type': 'VAELoader', 'inputs': {'vae_name': 'vae.safetensors'}}}
        def full_path(category, name):
            return str(self.root / name)
        self.modules = patch.dict(sys.modules, {
            'folder_paths': types.SimpleNamespace(get_full_path_or_raise=full_path),
            'comfy_api.latest._caching': types.SimpleNamespace(CacheValue=CacheValue),
            'comfy.nested_tensor': types.SimpleNamespace(NestedTensor=NestedTensor)})
        self.modules.start()
        self.graph_patch = patch.object(cache, '_running_graph', side_effect=lambda *_: self.graph)
        self.graph_patch.start()
        self.provider = cache.ReferenceCache(self.root / 'cache')
        self.provider.on_prompt_start('test-prompt')
        self.context = types.SimpleNamespace(node_id='136', class_type=cache.TARGET, cache_key_hash='a' * 64)

    def tearDown(self):
        self.graph_patch.stop()
        self.modules.stop()
        self.temporary.cleanup()

    def sample(self):
        image_latent = torch.arange(24, dtype=torch.bfloat16).reshape(2, 3, 4).transpose(0, 1)
        condition = [[torch.tensor([[0.1, -0.3]], dtype=torch.float32), {
            'pooled_output': torch.tensor([1, 2], dtype=torch.float16),
            'minimax_refs': [{'kind': 'video', 'latent': image_latent, 'audio_latent': None}]}]]
        latent = {'samples': NestedTensor((torch.zeros((1, 16, 2, 2, 2)), torch.zeros((1, 8, 3))))}
        return CacheValue(outputs=[[condition], [latent]], ui={'text': ['preserved']})

    async def test_original_dtype_values_shape_nested_and_ui_round_trip(self):
        original = self.sample()
        self.assertIsNone(await self.provider.on_lookup(self.context))
        await self.provider.on_store(self.context, original)
        restored = await self.provider.on_lookup(self.context)
        self.assertIsNotNone(restored)
        def same(left, right):
            self.assertIs(type(left), type(right))
            if type(left) is torch.Tensor:
                self.assertEqual(left.dtype, right.dtype)
                self.assertEqual(left.shape, right.shape)
                self.assertTrue(torch.equal(left.view(torch.uint8), right.view(torch.uint8)))
            elif type(left) is NestedTensor:
                same(left.tensors, right.tensors)
            elif type(left) in (list, tuple):
                self.assertEqual(len(left), len(right))
                for a, b in zip(left, right): same(a, b)
            elif type(left) is dict:
                self.assertEqual(left.keys(), right.keys())
                for key in left: same(left[key], right[key])
            else:
                self.assertEqual(left, right)
        same(original.outputs, restored.outputs)
        same(original.ui, restored.ui)
        self.assertEqual(self.provider.get_stats()['hits'], 1)
        self.assertGreater(self.provider.get_stats()['bytes'], 0)

    async def test_native_key_change_is_miss_but_same_input_different_downstream_seed_reuses(self):
        await self.provider.on_store(self.context, self.sample())
        self.graph['125'] = {'class_type': 'SamplerCustomAdvanced', 'inputs': {'noise_seed': 2}}
        self.assertIsNotNone(await self.provider.on_lookup(self.context))
        self.context.cache_key_hash = 'b' * 64  # Native hash changes with reference/prompt/range.
        self.assertIsNone(await self.provider.on_lookup(self.context))

    async def test_weight_replacement_disables_affected_session_even_if_restored(self):
        await self.provider.on_store(self.context, self.sample())
        old_stat = self.weight.stat()
        self.weight.write_bytes(b'replaced-weight')
        self.assertIsNone(await self.provider.on_lookup(self.context))
        self.weight.write_bytes(b'not-a-model-only-statted')
        os.utime(self.weight, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        self.assertIsNone(await self.provider.on_lookup(self.context))

    async def test_weight_changed_before_first_lookup_is_never_cached_this_session(self):
        self.assertEqual(self.provider._known_models, {})
        old_stat = self.weight.stat()
        os.utime(self.weight, ns=(old_stat.st_atime_ns, self.provider.started_ns + 1_000_000))
        await self.provider.on_store(self.context, self.sample())
        self.assertIsNone(await self.provider.on_lookup(self.context))
        self.assertEqual(list(self.provider.directory.glob('*.pt')), [])
        os.utime(self.weight, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        await self.provider.on_store(self.context, self.sample())
        self.assertEqual(list(self.provider.directory.glob('*.pt')), [])

    async def test_newer_ctime_before_first_lookup_also_disables_cache(self):
        stat = self.weight.stat()
        stamp = [(str(self.weight.resolve()), stat.st_size, self.provider.started_ns - 1,
                  self.provider.started_ns + 1, stat.st_ino)]
        with patch.object(cache, '_model_files', return_value=stamp):
            self.assertIsNone(await self.provider.on_lookup(self.context))
            await self.provider.on_store(self.context, self.sample())
        self.assertEqual(list(self.provider.directory.glob('*.pt')), [])

    async def test_unknown_model_no_graph_unknown_outputs_or_non_cpu_tensor_skip(self):
        self.graph['128']['class_type'] = 'CustomUnknownCLIPLoader'
        await self.provider.on_store(self.context, self.sample())
        self.assertEqual(list(self.provider.directory.glob('*.pt')), [])
        self.graph['128']['class_type'] = 'CLIPLoader'
        await self.provider.on_store(self.context, CacheValue([object()]))
        await self.provider.on_store(self.context, CacheValue([torch.empty(2, device='meta')]))
        self.assertEqual(list(self.provider.directory.glob('*.pt')), [])
        with patch.object(cache, '_running_graph', return_value=None):
            await self.provider.on_store(self.context, self.sample())
        self.assertEqual(list(self.provider.directory.glob('*.pt')), [])

    async def test_corrupt_file_is_deleted_and_execution_can_repopulate(self):
        await self.provider.on_store(self.context, self.sample())
        path = next(self.provider.directory.glob('*.pt'))
        path.write_bytes(b'broken')
        self.assertIsNone(await self.provider.on_lookup(self.context))
        self.assertFalse(path.exists())
        await self.provider.on_store(self.context, self.sample())
        self.assertIsNotNone(await self.provider.on_lookup(self.context))

    async def test_io_failure_is_a_miss_not_generation_failure(self):
        with patch.object(self.provider, '_read', side_effect=OSError('busy')):
            self.assertIsNone(await self.provider.on_lookup(self.context))
        with patch.object(self.provider, '_write', side_effect=OSError('disk full')):
            await self.provider.on_store(self.context, self.sample())

    async def test_disk_budget_evicts_old_values_and_rejects_oversize(self):
        value = self.sample()
        await self.provider.on_store(self.context, value)
        size = self.provider.get_stats()['bytes']
        self.provider.max_bytes = size + 32
        self.context.cache_key_hash = 'b' * 64
        await self.provider.on_store(self.context, value)
        self.assertLessEqual(self.provider.get_stats()['bytes'], self.provider.max_bytes)
        self.assertEqual(len(list(self.provider.directory.glob('*.pt'))), 1)
        self.context.cache_key_hash = 'c' * 64
        self.provider.max_bytes = 8
        await self.provider.on_store(self.context, value)
        self.assertEqual(len(list(self.provider.directory.glob('*.pt'))), 1)
        self.assertEqual(list(self.provider.directory.glob('*.tmp')), [])

    async def test_session_directories_are_unique_and_wrong_nodes_ignored(self):
        a, b = cache.ReferenceCache(), cache.ReferenceCache()
        self.assertNotEqual(a.directory, b.directory)
        try:
            self.context.class_type = 'SamplerCustomAdvanced'
            self.assertFalse(a.should_cache(self.context))
            await a.on_store(self.context, self.sample())
            self.assertIsNone(await a.on_lookup(self.context))
            self.assertEqual(a.get_stats()['stores'], 0)
        finally:
            a._temporary.cleanup()
            b._temporary.cleanup()

    async def test_shared_vae_model_connection_can_be_stamped(self):
        self.graph['136']['inputs']['audio_vae'] = ['120', 0]
        await self.provider.on_store(self.context, self.sample())
        self.assertIsNotNone(await self.provider.on_lookup(self.context))

    async def test_linked_prompt_with_external_embedding_skips_cache(self):
        self.graph['136']['inputs']['prompt'] = ['text', 0]
        self.graph['text'] = {'class_type': 'PrimitiveStringMultiline',
                              'inputs': {'value': 'portrait embedding:custom'}}
        await self.provider.on_store(self.context, self.sample())
        self.assertEqual(list(self.provider.directory.glob('*.pt')), [])

    async def test_running_graph_uses_only_matching_active_prompt(self):
        queue = types.SimpleNamespace(get_current_queue_volatile=lambda: (
            [(0, 'active-id', self.graph, {}, ['136'])], []))
        module = types.SimpleNamespace(PromptServer=types.SimpleNamespace(
            instance=types.SimpleNamespace(prompt_queue=queue)))
        with patch.dict(sys.modules, {'server': module}):
            self.graph_patch.stop()
            try:
                self.assertIs(cache._running_graph('active-id', '136'), self.graph)
                self.assertIsNone(cache._running_graph('other-id', '136'))
                self.assertIsNone(cache._running_graph('active-id', '999'))
            finally:
                self.graph_patch.start()

    async def test_registration_uses_public_provider_hook_and_is_idempotent(self):
        providers = []
        with patch.dict(sys.modules, {'comfy_execution.cache_provider': types.SimpleNamespace(
                register_cache_provider=providers.append)}), patch.object(cache, '_provider', None):
            first = cache.register()
            try:
                self.assertIs(cache.register(), first)
                self.assertEqual(providers, [first])
            finally:
                first._temporary.cleanup()


if __name__ == '__main__':
    unittest.main()
