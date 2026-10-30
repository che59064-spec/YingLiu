"""Prepare a short reference file before ComfyUI sees a long source video."""
from hashlib import sha256
import json
import math
from pathlib import Path
import tempfile

from video_edit import extract_segment, probe
from workflows import fields_for, iter_nodes, node_at, workflow_format
from reference_acceleration import loader_uses_audio


def materialize(app, document, planned):
    api = workflow_format(document) == 'api'
    media_fields = {tuple(field['nodePath']): field for field in fields_for(document) if field['type'] == 'video' and field['editable']}
    for path, loader in iter_nodes(document):
        if loader.get('class_type', loader.get('type')) not in ('VHS_LoadVideoFFmpeg', 'H3ReferenceVideoLoader'):
            continue
        if loader_uses_audio(document, path, loader):
            continue  # Silent physical clips cannot replace an input whose audio is used.
        field = media_fields.get(tuple(path))
        if not field:
            continue
        graph = document if api else node_at(document, path[:-2])
        nodes = {str(key): value for key, value in graph.items()} if api else {str(node['id']): node for node in graph['nodes']}
        start_link = loader['inputs'].get('start_time') if api else next((x.get('link') for x in loader.get('inputs', []) if x['name'] == 'start_time'), None)
        cap_link = loader['inputs'].get('frame_load_cap') if api else next((x.get('link') for x in loader.get('inputs', []) if x['name'] == 'frame_load_cap'), None)
        links = {} if api else {link[0]: link for link in graph.get('links', []) if isinstance(link, list)}
        origin = start_link if api else links.get(start_link, [None, None, None])[1:3]
        cap_origin = cap_link if api else links.get(cap_link, [None, None, None])[1:3]
        # App H3 graphs can be saved after the temporary start link was removed.
        values = loader['inputs'] if api else loader.get('widgets_values', {})
        if not api and isinstance(values, list) and len(values) < 6:
            continue  # Cannot safely set the time widget in an incomplete layout.
        saved_segment = str(field['value']).startswith('canvas-segment-')
        app_loader = loader.get('class_type', loader.get('type')) == 'H3ReferenceVideoLoader'
        start_detached = not isinstance(start_link, (list, tuple)) if api else start_link is None
        h3_workflow = any(n.get('class_type', n.get('type')) == 'MiniMaxH3ReferenceToVideo' for n in nodes.values())
        if start_detached and (saved_segment or app_loader or h3_workflow) and isinstance(cap_origin, list) and len(cap_origin) == 2 and cap_origin[1] == 1:
            origin = [cap_origin[0], 0]
        if not isinstance(origin, (list, tuple)) or len(origin) != 2 or origin[1] != 0 or cap_origin != [origin[0], 1]:
            continue
        timing = nodes.get(str(origin[0]))
        if not timing or timing.get('class_type', timing.get('type')) != 'H3SegmentTiming':
            continue
        values = timing['inputs'] if api else timing['widgets_values']
        if not isinstance(values, dict):
            values = dict(zip(('start_seconds', 'end_seconds'), values))
        start, end = float(values['start_seconds']), float(values['end_seconds'])
        if not all(math.isfinite(x) for x in (start, end)) or start < 0 or end <= start:
            raise ValueError('片段起止时间无效。')
        cache = app.data / 'segments'
        cache.mkdir(exist_ok=True)
        source_info = {'assetId': planned.get(field['id']), 'value': field['value']}
        previous = str(field['value']).removeprefix('canvas-segment-').removesuffix('.mp4')
        if not source_info['assetId'] and len(previous) == 64 and all(c in '0123456789abcdef' for c in previous):
            metadata = cache / (previous + '.json')
            if metadata.is_file():
                source_info = json.loads(metadata.read_text(encoding='utf-8'))
        if source_info.get('assetId'):
            original, _ = app.asset(source_info['assetId'])
            data = None
            digest_builder = sha256()
            with original.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest_builder.update(block)
        else:
            data, _ = app.read_optimizer_reference(None, {**field, 'value': source_info['value']})
            digest_builder = sha256(data)
        digest_builder.update(('|24fps|%.9f|%.9f|v1' % (start, end)).encode())
        digest = digest_builder.hexdigest()
        destination = cache / (digest + '.mp4')
        with app.video_lock:
            if not destination.is_file():
                with tempfile.TemporaryDirectory(prefix='canvas-selected-segment-') as temporary:
                    source = Path(temporary) / 'source.mp4'
                    result = Path(temporary) / 'selected.mp4'
                    if data is not None:
                        source.write_bytes(data)
                    else:
                        source = original
                    extract_segment(source, result, start, end)
                    result.replace(destination)
                    (cache / (digest + '.json')).write_text(json.dumps(source_info), encoding='utf-8')
            # Cache only the local physical segment; it does not enter the media shelf.
            name = 'canvas-segment-' + digest + '.mp4'
            uploaded = app.upload_comfy_media(name, 'video/mp4', destination.read_bytes())
        if api:
            loader['inputs']['video'] = uploaded
            loader['inputs']['start_time'] = 0.0
        else:
            widgets = loader['widgets_values']
            widgets[field['widgetIndex']] = uploaded
            widgets['start_time' if isinstance(widgets, dict) else 5] = 0.0
            for item in loader['inputs']:
                if item['name'] == 'start_time':
                    item['link'] = None
            if start_link is not None:
                graph['links'] = [link for link in graph['links'] if link[0] != start_link]
                for node in graph['nodes']:
                    for output in node.get('outputs', []):
                        if output.get('links'):
                            output['links'] = [link for link in output['links'] if link != start_link]
        planned.pop(field['id'], None)
    return planned
