"""Avoid retaining an unused lazy audio value for app-owned silent clips."""
import re

from workflows import iter_nodes, node_at, workflow_format


def loader_uses_audio(document, path, node):
    api = workflow_format(document) == 'api'
    graph = document if api else node_at(document, path[:-2])
    node_id = str(path[0] if api else node['id'])
    if api:
        return any(isinstance(value, list) and len(value) == 2
                   and str(value[0]) == node_id and value[1] == 2
                   for other in graph.values() for value in other.get('inputs', {}).values())
    connected = any(isinstance(link, list) and len(link) >= 6
                    and str(link[1]) == node_id and link[2] == 2
                    for link in graph.get('links', []))
    outputs = node.get('outputs', [])
    return connected or (len(outputs) > 2 and bool(outputs[2].get('links')))


def prepare_loaders(document, available=False):
    api = workflow_format(document) == 'api'
    for path, node in iter_nodes(document):
        kind_key = 'class_type' if api else 'type'
        if node[kind_key] not in ('VHS_LoadVideoFFmpeg', 'H3ReferenceVideoLoader'):
            continue
        values = node.get('inputs' if api else 'widgets_values', {})
        if not isinstance(values, dict):
            continue
        audio_used = loader_uses_audio(document, path, node)
        silent_clip = re.fullmatch(r'canvas-segment-[0-9a-f]{64}\.mp4', str(values.get('video', '')))
        use_adapter = available and silent_clip and not audio_used
        node[kind_key] = 'H3ReferenceVideoLoader' if use_adapter else 'VHS_LoadVideoFFmpeg'
        if use_adapter:
            values['ignore_audio'] = True
        else:
            values.pop('ignore_audio', None)
        properties = node.get('properties', {})
        if properties.get('Node name for S&R') in ('VHS_LoadVideoFFmpeg', 'H3ReferenceVideoLoader'):
            properties['Node name for S&R'] = node[kind_key]
    return document
