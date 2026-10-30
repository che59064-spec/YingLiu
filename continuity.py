"""Create an optional, temporary H3 first-frame guide without changing a saved graph."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from uuid import uuid4

from video_edit import run, tool
from workflows import iter_nodes, workflow_format


def _graph(document):
    if workflow_format(document) != 'gui':
        raise ValueError('接续上一段目前需要普通 ComfyUI 工作流 JSON。')
    if any(len(path) > 2 and node.get('type') == 'MiniMaxH3ReferenceToVideo' for path, node in iter_nodes(document)):
        raise ValueError('工作流的 H3 节点位于子图内，暂无法安全添加接续。')
    nodes = document.get('nodes', [])
    targets = [node for node in nodes if node.get('type') == 'MiniMaxH3ReferenceToVideo' and node.get('mode', 0) not in (2, 4)]
    if len(targets) != 1:
        raise ValueError('接续需要一套明确的 H3 参考视频节点；此工作流没有或包含多个，请使用单一动作迁移工作流。')
    target = targets[0]
    if any(not isinstance(node.get('id'), int) for node in nodes):
        raise ValueError('工作流节点编号不兼容接续，请使用普通数字编号的工作流。')
    if len({node['id'] for node in nodes}) != len(nodes):
        raise ValueError('工作流存在重复的节点编号，无法安全接续。')
    links = document.get('links', [])
    if not isinstance(links, list) or any(not isinstance(link, list) or len(link) < 6 or not isinstance(link[0], int) for link in links):
        raise ValueError('工作流连线格式不兼容接续。')
    if len({link[0] for link in links}) != len(links):
        raise ValueError('工作流存在重复的连线编号，无法安全接续。')
    by_id = {node['id']: node for node in nodes}
    by_link = {link[0]: link for link in links}
    outputs = target.get('outputs', [])
    if len(outputs) < 2 or not isinstance(outputs[0].get('links'), list) or not isinstance(outputs[1].get('links'), list):
        raise ValueError('H3 输出连接不完整，无法安全接续。')
    matches = []
    for identity in outputs[0]['links']:
        link = by_link.get(identity)
        if not link or link[1:3] != [target['id'], 0]:
            raise ValueError('H3 条件连接不一致，无法安全接续。')
        destination = by_id.get(link[3])
        slot = link[4]
        if destination and destination.get('type') == 'BasicGuider' and isinstance(slot, int) and 0 <= slot < len(destination.get('inputs', [])):
            item = destination['inputs'][slot]
            if item.get('name') == 'conditioning' and item.get('link') == identity and destination.get('mode', 0) not in (2, 4):
                matches.append(link)
    if len(matches) != 1:
        raise ValueError('接续需要 H3 条件直接连接一个 BasicGuider；请检查工作流连接。')
    vae_inputs = [item for item in target.get('inputs', []) if item.get('name') == 'vae' and item.get('link') is not None]
    if len(vae_inputs) != 1:
        raise ValueError('H3 缺少已连接的视频 VAE，不能设置上一段的引导帧。')
    vae_link = by_link.get(vae_inputs[0]['link'])
    if not vae_link or vae_link[3] != target['id'] or not isinstance(vae_link[4], int) or vae_link[4] < 0 or vae_link[4] >= len(target['inputs']) or target['inputs'][vae_link[4]] is not vae_inputs[0]:
        raise ValueError('视频 VAE 连接不一致，不能安全接续。')
    vae_node = by_id.get(vae_link[1])
    if not vae_node or not isinstance(vae_link[2], int) or vae_link[2] < 0 or vae_link[2] >= len(vae_node.get('outputs', [])):
        raise ValueError('视频 VAE 的来源不存在。')
    if vae_link[0] not in (vae_node['outputs'][vae_link[2]].get('links') or []):
        raise ValueError('视频 VAE 输出连接不一致。')
    return target, matches[0], vae_node, vae_link[2]


def inject(document, filename):
    """Return a copied GUI document with the official guide slots and matching links."""
    copied = deepcopy(document)
    target, conditioning, vae, vae_slot = _graph(copied)
    nodes, links = copied['nodes'], copied['links']
    node_counter, link_counter = copied.get('last_node_id', 0), copied.get('last_link_id', 0)
    if not isinstance(node_counter, int) or not isinstance(link_counter, int):
        raise ValueError('工作流节点计数格式无效，无法安全接续。')
    next_node = max([node_counter] + [node['id'] for node in nodes])
    next_link = max([link_counter] + [link[0] for link in links])
    image_id, guide_id = next_node + 1, next_node + 2
    ids = list(range(next_link + 1, next_link + 5))
    original_link = conditioning[0]
    target['outputs'][0]['links'].remove(original_link)
    target['outputs'][0]['links'].append(ids[0])
    target['outputs'][1]['links'].append(ids[1])
    vae['outputs'][vae_slot].setdefault('links', [])
    if vae['outputs'][vae_slot]['links'] is None:
        vae['outputs'][vae_slot]['links'] = []
    vae['outputs'][vae_slot]['links'].append(ids[2])
    conditioning[1:3] = [guide_id, 0]
    position = target.get('pos', [0, 0])
    if not isinstance(position, list) or len(position) < 2 or not all(isinstance(x, (int, float)) for x in position[:2]):
        position = [0, 0]
    image = {'id': image_id, 'type': 'LoadImage', 'pos': [position[0], position[1] - 350], 'size': [290, 200],
             'flags': {}, 'order': 0, 'mode': 0, 'inputs': [],
             'outputs': [{'name': 'IMAGE', 'type': 'IMAGE', 'links': [ids[3]]}, {'name': 'MASK', 'type': 'MASK', 'links': None}],
             'title': '上一段末帧 · 自动接续', 'properties': {'Node name for S&R': 'LoadImage'},
             'widgets_values': [filename, 'image'], 'widgets_values_named': {'image': filename, 'upload': 'image'}}
    guide = {'id': guide_id, 'type': 'MiniMaxH3AddGuide', 'pos': [position[0] + 750, position[1] - 150], 'size': [310, 180],
             'flags': {}, 'order': 0, 'mode': 0,
             'inputs': [{'name': 'positive', 'type': 'CONDITIONING', 'link': ids[0]},
                        {'name': 'latent', 'type': 'LATENT', 'link': ids[1]},
                        {'name': 'vae', 'shape': 7, 'type': 'VAE', 'link': ids[2]},
                        {'name': 'audio_vae', 'shape': 7, 'type': 'VAE', 'link': None},
                        {'name': 'image', 'shape': 7, 'type': 'IMAGE', 'link': ids[3]},
                        {'name': 'audio', 'shape': 7, 'type': 'AUDIO', 'link': None},
                        {'name': 'frame_idx', 'type': 'INT', 'widget': {'name': 'frame_idx'}, 'link': None}],
             'outputs': [{'name': 'positive', 'type': 'CONDITIONING', 'links': [original_link]}],
             'title': '上一段末帧 → 新片段第 0 帧', 'properties': {'Node name for S&R': 'MiniMaxH3AddGuide'},
             'widgets_values': [0], 'widgets_values_named': {'frame_idx': 0}}
    nodes.extend([image, guide])
    links.extend([[ids[0], target['id'], 0, guide_id, 0, 'CONDITIONING'],
                  [ids[1], target['id'], 1, guide_id, 1, 'LATENT'],
                  [ids[2], vae['id'], vae_slot, guide_id, 2, 'VAE'],
                  [ids[3], image_id, 0, guide_id, 4, 'IMAGE']])
    copied['last_node_id'], copied['last_link_id'] = guide_id, ids[-1]
    return copied


def last_frame(path, output):
    """Count decoded frames and extract the last displayed one, including variable-rate clips."""
    information = json.loads(run([tool('ffprobe'), '-v', 'error', '-protocol_whitelist', 'file,pipe',
                                  '-select_streams', 'v:0', '-count_frames', '-show_entries', 'stream=nb_read_frames',
                                  '-of', 'json', str(path)], timeout=120))
    try:
        count = int(information['streams'][0]['nb_read_frames'])
    except (KeyError, IndexError, ValueError, TypeError):
        raise ValueError('无法读取上一段的视频帧，请选择可播放的视频。') from None
    if count < 1:
        raise ValueError('上一段没有可读取的视频画面。')
    run([tool('ffmpeg'), '-v', 'error', '-nostdin', '-y', '-protocol_whitelist', 'file,pipe', '-i', str(path),
         '-map', '0:v:0', '-vf', 'select=eq(n\\,%d)' % (count - 1), '-vsync', '0', '-frames:v', '1',
         '-an', '-c:v', 'png', '-threads', '1', str(output)], timeout=120)
    result = Path(output)
    if not result.is_file() or result.stat().st_size > 64 * 1024 * 1024 or result.stat().st_size < 20:
        raise ValueError('上一段末帧提取失败，请检查视频。')
    data = result.read_bytes()
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('上一段末帧不是有效图片。')
    return data


def prepare(app, document, reference):
    if not isinstance(reference, dict) or reference.get('type', 'video') != 'video':
        raise ValueError('请选择一段视频作为接续来源。')
    _graph(document)  # Validate before copying or uploading any media.
    for kind in ('MiniMaxH3AddGuide', 'LoadImage'):
        try:
            info = app.comfy('/object_info/' + kind, timeout=10)
        except Exception:
            raise ValueError('当前 ComfyUI 无法读取接续节点，请确认引擎已加载 MiniMax H3 节点。') from None
        if not isinstance(info, dict) or kind not in info:
            raise ValueError('当前 ComfyUI 缺少接续节点：' + kind)
    entry = app.reference_asset(reference)
    if entry.get('type') != 'video':
        raise ValueError('接续来源必须是视频，请重新选择。')
    source, _ = app.asset(entry['id'])
    with tempfile.TemporaryDirectory(prefix='canvas-continuity-') as folder:
        data = last_frame(source, Path(folder) / 'last.png')
    uploaded = app.upload_comfy_media('canvas-continuity-' + uuid4().hex + '.png', 'image/png', data)
    return inject(document, uploaded)
