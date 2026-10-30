"""Lossless ComfyUI documents and explicit, conservative friendly field adapters."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json
import math
import os
import re
import threading
from uuid import uuid4


# These are audited widget layouts from the local templates, not an API conversion.
LAYOUTS = {
    'CLIPTextEncode': ['text'],
    'CheckpointLoaderSimple': ['ckpt_name'],
    'CheckpointLoader': ['config_name', 'ckpt_name'],
    'LoraLoader': ['lora_name', 'strength_model', 'strength_clip'],
    'EmptySD3LatentImage': ['width', 'height', 'batch_size'],
    'EmptyHunyuanLatentVideo': ['width', 'height', 'length', 'batch_size'],
    'TextEncodeQwenImage21': ['prompt', 'negative_prompt', 'resolution'],
    'EmptyLatentImage': ['width', 'height', 'batch_size'],
    'KSampler': ['seed', '_seed_control', 'steps', 'cfg', 'sampler_name', 'scheduler', 'denoise'],
    'RandomNoise': ['noise_seed', '_seed_control'],
    'PrimitiveStringMultiline': ['value'], 'PrimitiveFloat': ['value'],
    'PrimitiveInt': ['value', '_seed_control'], 'PrimitiveBoolean': ['value'],
    'LoadImage': ['image', '_upload'],
    # VHS stores these six widgets in this order in both loader variants.
    'VHS_LoadVideoFFmpeg': ['video', 'force_rate', 'custom_width', 'custom_height', 'frame_load_cap', 'start_time'],
    'H3ReferenceVideoLoader': ['video', 'force_rate', 'custom_width', 'custom_height', 'frame_load_cap', 'start_time'],
    'ResolutionSelector': ['aspect_ratio', 'megapixels', 'multiple'],
    'MiniMaxH3ReferenceToVideo': ['prompt', 'width', 'height', 'length', 'ref_image_size'],
    'MiniMaxH3ImageToVideo': ['prompt', 'width', 'height', 'length'],
    'H3SegmentTiming': ['start_seconds', 'end_seconds'],
    'BasicScheduler': ['scheduler', 'steps', 'denoise'],
    'CreateVideo': ['fps', 'bit_depth', 'color_space', 'codec'],
    'KSamplerSelect': ['sampler_name'],
    'UNETLoader': ['unet_name', 'weight_dtype'], 'UnetLoaderGGUF': ['unet_name'],
    'CLIPLoader': ['clip_name', 'type', 'device'], 'CLIPLoaderGGUF': ['clip_name', 'type'],
    'VAELoader': ['vae_name'], 'LoraLoaderModelOnly': ['lora_name', 'strength_model'],
    'SaveImage': ['filename_prefix'],
    'SaveImageAdvanced': ['filename_prefix', 'format', 'format.bit_depth', 'format.input_color_space'],
    'SaveVideo': ['filename_prefix', 'format', 'format.codec', 'codec'],
    'MiniMaxChunkFeedForward': ['chunks', 'seq_threshold'],
    'MiniMaxLowVRAMAttention': ['head_chunks'],
}
LABELS = {
    'text': '画面描述', 'ckpt_name': '基础模型', 'config_name': '模型配置',
    'strength_clip': '附加模型文字强度', 'model_name': '模型',
    'prompt': '画面描述', 'negative_prompt': '不想出现的内容', 'width': '画面宽度',
    'height': '画面高度', 'batch_size': '生成数量', 'resolution': '参考处理尺寸',
    'seed': '变化编号', 'noise_seed': '变化编号', 'steps': '生成步数',
    'cfg': '描述遵循程度', 'denoise': '重新绘制程度', 'fps': '每秒画面数',
    'length': '视频画面总帧数', 'image': '参考图片', 'video': '动作参考视频',
    'start_seconds': '开始秒数', 'end_seconds': '结束秒数',
    'aspect_ratio': '画面比例', 'megapixels': '画面大小（百万像素）',
    'sampler_name': '绘制算法', 'scheduler': '变化计划', 'ref_image_size': '参考图适配方式',
    'unet_name': '生成模型', 'clip_name': '文字理解模型', 'vae_name': '画面还原模型',
    'lora_name': '附加模型', 'strength_model': '附加模型强度', 'filename_prefix': '结果文件名前缀',
    'force_rate': '读取帧率', 'frame_load_cap': '最多读取帧数', 'start_time': '视频开始秒数',
    'custom_width': '读取宽度', 'custom_height': '读取高度', 'frame_rate': '成片帧率',
    'save_output': '保存成片', 'format': '文件格式', 'device': '计算设备',
    'head_chunks': '节省显存分组数', 'chunks': '节省显存分块数',
}
MODEL_KEYS = {'unet_name', 'clip_name', 'vae_name', 'lora_name', 'ckpt_name', 'model_name'}
COMMON = {'prompt', 'text', 'image', 'video', 'width', 'height', 'batch_size', 'aspect_ratio',
          'start_seconds', 'end_seconds', 'noise_seed', 'seed'}
LIMITS = {'width': (64, 8192), 'height': (64, 8192), 'batch_size': (1, 32),
          'steps': (1, 1000), 'fps': (1, 120), 'length': (1, 4096),
          'start_seconds': (0, 86400), 'end_seconds': (0, 86400),
          'seed': (0, 2**53 - 1), 'noise_seed': (0, 2**53 - 1)}

MAX_WORKFLOW_BYTES = 32 * 1024 * 1024
MAX_WORKFLOW_NODES = 10000
SENSITIVE_FIELD = re.compile(r'(?:api[_-]?key|access[_-]?token|secret|password|authorization)', re.I)


def unwrap_workflow(document):
    """Select a saved graph, never queue fields such as client_id or API secrets."""
    if not isinstance(document, dict):
        raise ValueError('工作流必须是 JSON 对象')
    try:
        workflow_format(document)
        return document
    except ValueError:
        pass
    containers = [document]
    for name in ('extra_pnginfo', 'extra_data'):
        value = document.get(name)
        if isinstance(value, dict):
            containers.append(value)
            if isinstance(value.get('extra_pnginfo'), dict):
                containers.append(value['extra_pnginfo'])
    # Prefer the editable graph when an export contains both UI and API formats.
    for name in ('workflow', 'prompt'):
        for container in containers:
            candidate = container.get(name)
            if isinstance(candidate, str):
                if len(candidate.encode('utf-8')) > MAX_WORKFLOW_BYTES:
                    raise ValueError('工作流超过 32 MB，请拆分后再导入')
                try:
                    candidate = json.loads(candidate)
                except (ValueError, RecursionError):
                    continue
            if isinstance(candidate, dict):
                try:
                    workflow_format(candidate)
                    return candidate
                except ValueError:
                    continue
    raise ValueError('未识别的工作流：请选择 ComfyUI 导出的普通 JSON 或 API JSON')


def validate_import(document):
    """Bound imported structures while retaining unknown node data verbatim."""
    try:
        encoded = json.dumps(document, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise ValueError('工作流包含无效数值或过深结构') from None
    if len(encoded.encode('utf-8')) > MAX_WORKFLOW_BYTES:
        raise ValueError('工作流超过 32 MB，请拆分后再导入')
    count = 0
    pending = [(document, 0)]
    while pending:
        value, depth = pending.pop()
        if depth > 64:
            raise ValueError('工作流嵌套超过 64 层，请简化后再导入')
        if isinstance(value, dict):
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)
    form = workflow_format(document)
    if form == 'api':
        count = len(document)
    else:
        graphs = [document]
        while graphs:
            graph = graphs.pop()
            if not isinstance(graph, dict) or not isinstance(graph.get('nodes'), list):
                raise ValueError('子工作流必须包含 nodes 数组')
            ids = set()
            for node in graph['nodes']:
                if not isinstance(node, dict) or not isinstance(node.get('type'), str) or not node['type']:
                    raise ValueError('工作流节点必须包含有效的 type')
                identity = node.get('id')
                if isinstance(identity, bool) or not isinstance(identity, (str, int)) or str(identity) in ids:
                    raise ValueError('工作流节点编号缺失或重复')
                ids.add(str(identity))
                if not isinstance(node.get('inputs', []), list):
                    raise ValueError('普通工作流的 inputs 必须是数组')
                if not isinstance(node.get('widgets_values', []), (list, dict)):
                    raise ValueError('工作流的 widgets_values 必须是数组或对象')
                if any(not isinstance(item, dict) for item in node.get('inputs', [])):
                    raise ValueError('工作流输入项格式不正确')
            count += len(graph['nodes'])
            if not isinstance(graph.get('links', []), list):
                raise ValueError('工作流的 links 必须是数组')
            definitions = graph.get('definitions', {})
            if not isinstance(definitions, dict) or not isinstance(definitions.get('subgraphs', []), list):
                raise ValueError('工作流子图定义格式不正确')
            graphs.extend(definitions.get('subgraphs', []))
    if count > MAX_WORKFLOW_NODES:
        raise ValueError('工作流超过 10000 个节点，请拆分后再导入')
    return form


def workflow_format(document):
    if not isinstance(document, dict):
        raise ValueError('工作流必须是 JSON 对象')
    if isinstance(document.get('nodes'), list):
        if any(not isinstance(node, dict) or not isinstance(node.get('type'), str)
               for node in document['nodes']):
            raise ValueError('工作流节点必须包含有效的 type')
        return 'gui'
    if document and all(isinstance(n, dict) and isinstance(n.get('class_type'), str)
                        and isinstance(n.get('inputs'), dict) for n in document.values()):
        return 'api'
    raise ValueError('未识别的工作流：需要 ComfyUI GUI 或 API 格式')


def iter_nodes(document):
    """Yield JSON paths, including nested subgraphs without flattening them."""
    if workflow_format(document) == 'api':
        for key, node in document.items():
            yield [key], node
        return
    def visit(graph, base):
        for i, node in enumerate(graph.get('nodes', [])):
            if isinstance(node, dict):
                yield base + ['nodes', i], node
        subgraphs = graph.get('definitions', {}).get('subgraphs', [])
        if isinstance(subgraphs, list):
            for i, subgraph in enumerate(subgraphs):
                if isinstance(subgraph, dict):
                    yield from visit(subgraph, base + ['definitions', 'subgraphs', i])
    yield from visit(document, [])


def node_at(document, path):
    for segment in path:
        document = document[segment]
    return document


def fields_for(document):
    result = []
    is_api = workflow_format(document) == 'api'
    for path, node in iter_nodes(document):
        node_type = node.get('class_type') if is_api else node.get('type', '')
        metadata = node.get('_meta') if isinstance(node.get('_meta'), dict) else {}
        title = str(node.get('title') or metadata.get('title') or node_type)
        if not is_api and node.get('mode') in (2, 4):
            continue  # muted and bypassed nodes do not control this saved preset
        values = node.get('inputs', {}) if is_api else node.get('widgets_values', [])
        if isinstance(values, dict):
            pairs = [(name, name, value) for name, value in values.items()
                     if isinstance(name, str) and not isinstance(value, (dict, list))]
        elif node_type in LAYOUTS:
            pairs = [(name, i, values[i]) for i, name in enumerate(LAYOUTS[node_type])
                     if i < len(values) and not name.startswith('_')]
        else:
            continue  # unknown layouts stay intact and editable in professional mode
        for name, widget, value in pairs:
            if value is None or not isinstance(value, (str, bool, int, float)):
                continue
            input_info = next((item for item in node.get('inputs', [])
                               if isinstance(item, dict) and item.get('name') == name), {}) if not is_api else {}
            linked = input_info.get('link') is not None
            sensitive = bool(SENSITIVE_FIELD.search(name))
            unsafe_integer = isinstance(value, int) and not isinstance(value, bool) and abs(value) > 2**53 - 1
            label = LABELS.get(name, name)
            common = name in COMMON
            if name == 'value':
                label = title
                common = node_type in ('PrimitiveStringMultiline', 'PrimitiveBoolean') or ('时长' in title)
            kind = 'boolean' if isinstance(value, bool) else 'number' if isinstance(value, (int, float)) else 'text'
            if name in ('prompt', 'negative_prompt', 'text', 'positive_prompt') or node_type == 'PrimitiveStringMultiline':
                kind = 'textarea'
            if name == 'image':
                kind = 'image'
            elif name == 'video':
                kind = 'video'
            elif name in MODEL_KEYS:
                kind = 'model'
            field = {'id': '/'.join(map(str, path)) + ':' + name,
                     'nodeId': str(node.get('id', path[0])), 'nodeType': node_type,
                     'nodeTitle': title, 'nodePath': path, 'inputName': name,
                     'widgetIndex': widget, 'key': name, 'label': label, 'type': kind,
                     'value': value, 'advanced': not common, 'editable': not linked and not sensitive and not unsafe_integer,
                     'linked': linked, 'model': name in MODEL_KEYS,
                     'role': 'reference' if kind in ('image', 'video') else 'prompt' if kind == 'textarea' and name != 'negative_prompt' else 'setting'}
            if name in LIMITS:
                field['min'], field['max'] = LIMITS[name]
            if isinstance(value, int) and not isinstance(value, bool):
                field['step'] = 1
            if sensitive:
                field['sensitive'] = True
            if unsafe_integer:
                field['description'] = '此整数超出浏览器安全编辑范围，原值会精确保留；请在 ComfyUI 内修改。'
            elif sensitive:
                field['description'] = '此字段可能包含密钥，请在 ComfyUI 内配置，分享前检查原工作流。'
            elif linked:
                field['description'] = '由连线中的上游步骤控制，请编辑来源字段或打开专业模式。'
            elif name == 'value' and '时长' in title:
                field['description'] = '目标时长；实际帧数按原工作流规则计算。'
            result.append(field)
    return result


def compatibility_for(document):
    form = workflow_format(document)
    nodes = list(iter_nodes(document))
    types = sorted({node.get('class_type', node.get('type', '')) for _, node in nodes})
    unmapped = sorted({node.get('type', '') for _, node in nodes
                       if form == 'gui' and node.get('type') not in LAYOUTS
                       and isinstance(node.get('widgets_values'), list) and node['widgets_values']})
    warnings = []
    if not nodes:
        warnings.append('此工作流没有节点，请在专业模式中添加生成步骤。')
    if unmapped:
        warnings.append('部分自定义节点参数保留在专业模式中调整；原始节点、连线和子图均已保留。')
    fields = fields_for(document)
    if any(SENSITIVE_FIELD.search(field['inputName']) for field in fields):
        warnings.append('工作流中含疑似密钥字段，分享文件前请检查并清除个人密钥。')
    if form == 'api':
        warnings.append('API 工作流可直接执行；专业模式编辑完整节点布局时，请导入普通工作流 JSON。')
    return {'format': form, 'nodeTypes': types, 'unmappedNodeTypes': unmapped,
            'requiresBridge': form == 'gui', 'warnings': warnings}


def apply_params(document, params):
    """Copy first; accept only field IDs returned by this document's audited adapter."""
    if not isinstance(params, dict):
        raise ValueError('params 必须是字段 ID 到值的对象')
    copied = deepcopy(document)
    fields = {field['id']: field for field in fields_for(document)}
    for field_id, value in params.items():
        field = fields.get(field_id)
        if not field:
            raise ValueError('字段不存在或由上游控制：' + str(field_id))
        original = field['value']
        if not field['editable']:
            if value == original and type(value) is type(original):
                continue  # full form submissions include read-only values without editing them
            raise ValueError('字段不存在或由上游控制：' + str(field_id))
        if isinstance(original, bool):
            valid = isinstance(value, bool)
        elif isinstance(original, (float, int)):
            valid = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            if isinstance(original, int):
                valid = valid and int(value) == value
            if valid and 'min' in field:
                valid = field['min'] <= value <= field['max']
        else:
            valid = isinstance(value, str) and len(value) <= 200000
        if not valid:
            raise ValueError('字段值不合法：' + field['label'])
        target = node_at(copied, field['nodePath'])
        target['inputs' if workflow_format(copied) == 'api' else 'widgets_values'][field['widgetIndex']] = value
    return copied


def stable_id(relative_path):
    return sha256(relative_path.replace('\\', '/').encode('utf-8')).hexdigest()[:24]


class WorkflowLibrary:
    def __init__(self, source, user_dir):
        self.source = Path(source)
        self.user_dir = Path(user_dir)
        self.user_dir.mkdir(parents=True, exist_ok=True)
        self.paths = {}
        self.import_lock = threading.RLock()

    def catalog(self):
        with self.import_lock:
            return self._catalog()

    def _catalog(self):
        items = []
        self.paths = {}
        paths = [(p, p.relative_to(self.source).as_posix(), False)
                 for p in sorted(self.source.rglob('*.json'))]
        paths += [(p, '我的导入/' + p.name, True) for p in sorted(self.user_dir.glob('*.json'))]
        for path, relative, imported in paths:
            try:
                if path.stat().st_size > MAX_WORKFLOW_BYTES:
                    continue
                document = json.loads(path.read_text(encoding='utf-8-sig'))
                form = validate_import(document)
                fields = fields_for(document)
            except (ValueError, OSError):
                continue
            workflow_id = stable_id(relative)
            self.paths[workflow_id] = path
            name = self.display_name(path, document)
            category = relative.split('/')[0]
            types = [n.get('type', n.get('class_type', '')) for _, n in iter_nodes(document)]
            models = sorted({str(f['value']) for f in fields if f['model']})
            video = any('Video' in t or 'H3' in t or 'VHS' in t for t in types)
            recommended = not imported and category in ('图片', '视频') and (
                ('快速8步' in name) or name == 'H3_多图参考生视频_均衡_960x544' or ('动作迁移_按秒' in name))
            items.append({'id': workflow_id, 'name': name, 'category': category,
                          'kind': 'video' if video else 'image', 'format': form,
                          'relativePath': relative, 'nodeCount': len(types),
                          'node_count': len(types), 'fields': fields,
                          'description': ('视频工作流' if video else '图片与自定义工作流') + ' · ' + str(len(types)) + ' 个专业步骤',
                          'fieldCount': sum(f['editable'] for f in fields),
                          'models': models, 'recommended': recommended,
                          'imported': imported, 'historical': category == '旧版归档',
                          'compatibility': compatibility_for(document)})
        return items

    def display_name(self, path, document):
        try:
            metadata = json.loads(path.with_suffix('.meta').read_text(encoding='utf-8'))
            name = metadata.get('name') if isinstance(metadata, dict) else None
            if isinstance(name, str) and name.strip():
                return name
        except (ValueError, OSError):
            pass
        title = document.get('_canvas_title')
        return title if isinstance(title, str) and title.strip() else path.stem

    def get(self, workflow_id):
        with self.import_lock:
            return self._get(workflow_id)

    def _get(self, workflow_id):
        self.catalog()
        path = self.paths.get(workflow_id)
        if path is None:
            raise KeyError(workflow_id)
        document = json.loads(path.read_text(encoding='utf-8-sig'))
        compatibility = compatibility_for(document)
        return {'id': workflow_id, 'workflow': document, 'fields': fields_for(document),
                'format': workflow_format(document), 'name': self.display_name(path, document),
                'category': '我的导入' if path.parent == self.user_dir else path.relative_to(self.source).parts[0],
                'compatibility': compatibility,
                'warnings': compatibility['warnings']}

    def import_document(self, document, name='导入工作流'):
        with self.import_lock:
            return self._import_document(document, name)

    def _import_document(self, document, name):
        document = unwrap_workflow(document)
        validate_import(document)
        # Name is metadata beside the exact original document, never an inserted node field.
        canonical = json.dumps(document, ensure_ascii=False, sort_keys=True, allow_nan=False)
        digest = sha256(canonical.encode()).hexdigest()[:16]
        for item in self.catalog():
            path = self.paths[item['id']]
            existing_document = json.loads(path.read_text(encoding='utf-8-sig'))
            if json.dumps(existing_document, ensure_ascii=False, sort_keys=True, allow_nan=False) == canonical:
                return {**self.get(item['id']), 'duplicate': True}
        safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(name))[:80].strip('. ') or '导入工作流'
        path = self.user_dir / (safe_name + '_' + digest + '.json')
        if not path.exists():
            temporary = path.with_suffix('.' + uuid4().hex + '.tmp')
            try:
                temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding='utf-8')
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        metadata = path.with_suffix('.meta')
        temporary = metadata.with_suffix('.' + uuid4().hex + '.tmp')
        try:
            temporary.write_text(json.dumps({'name': safe_name}, ensure_ascii=False), encoding='utf-8')
            os.replace(temporary, metadata)
        finally:
            temporary.unlink(missing_ok=True)
        workflow_id = stable_id('我的导入/' + path.name)
        return {**self.get(workflow_id), 'duplicate': False}
