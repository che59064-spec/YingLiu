"""Prepare only connected references for Agnes image understanding, without public uploads."""
import base64
import json
import math
from pathlib import Path
import re
import tempfile

from prompt_optimizer import reference_context
from video_edit import tool, run, probe
from workflows import fields_for

MAX_REFERENCE_BYTES = 64 * 1024 * 1024


def prepare(app, document, raw_references, references):
    fields = {field['id']: field for field in fields_for(document)
              if field['editable'] and field['type'] in ('image', 'video')}
    assigned = {ref['fieldId'] for ref in raw_references if ref.get('fieldId')}
    bindings = {}
    for ref in raw_references:
        field_id = ref.get('fieldId') or next((key for key, field in fields.items()
                                               if key not in assigned and field['type'] == ref['type']), None)
        if field_id:
            assigned.add(field_id)
            bindings[field_id] = ref
    traced = reference_context(document, raw_references, include_fields=True)
    if len(traced) > 8:
        raise ValueError('参考素材超过 8 组，请减少连接后再优化。')
    values = {field['key']: field['value'] for field in fields_for(document)
              if field['key'] in ('start_seconds', 'end_seconds')}
    groups, summary, total_bytes = [], [], 0
    with tempfile.TemporaryDirectory(prefix='canvas-prompt-reference-') as folder:
        folder = Path(folder)
        for index, (source, target) in enumerate(zip(traced, references)):
            field_ids = source.get('fieldIds', [])
            if not field_ids or source['type'] not in ('image', 'video'):
                raise ValueError('参考素材无法读取，请上传或连接到具体的图片、视频输入。')
            if len(field_ids) > 1:
                raise ValueError('此参考经过了多素材合并，请先导出合并结果，再连接到工作流优化。')
            field = fields[field_ids[0]]
            reference = bindings.get(field['id'])
            segment = re.fullmatch(r'canvas-segment-([0-9a-f]{64})\.mp4(?: \[input\])?', str(field['value']))
            if source['type'] == 'video' and reference is None and segment:
                # Professional sync can return a prepared clip; timing still names the original range.
                metadata = app.data / 'segments' / (segment[1] + '.json')
                try:
                    original = json.loads(metadata.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    raise ValueError('截取片段的原视频记录无法读取，请重新连接原视频后优化。') from None
                if not isinstance(original, dict):
                    raise ValueError('截取片段的原视频记录无效，请重新连接原视频后优化。')
                if original.get('assetId'):
                    reference = {'assetId': original['assetId']}
                elif isinstance(original.get('value'), str) and original['value']:
                    field = {**field, 'value': original['value']}
                else:
                    raise ValueError('截取片段的原视频记录无效，请重新连接原视频后优化。')
            data, mime = app.read_optimizer_reference(reference, field)
            total_bytes += len(data)
            if total_bytes > MAX_REFERENCE_BYTES * 2:
                raise ValueError('参考素材总计超过 128 MB，请缩短视频后重试。')
            suffix = '.jpg' if source['type'] == 'image' else '.mp4'
            local = folder / ('source-' + str(index) + suffix)
            local.write_bytes(data)
            output = folder / ('reference-' + str(index) + '-%03d.jpg')
            command = [tool('ffmpeg'), '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                       '-threads', '1', '-filter_threads', '1', '-protocol_whitelist', 'file,pipe']
            entry = {'label': target['label'], 'type': source['type']}
            if source['type'] == 'video':
                info = probe(local)
                start = float(values.get('start_seconds', 0))
                end = float(values.get('end_seconds', info['duration']))
                if not all(math.isfinite(v) for v in (start, end)) or start < 0 or end <= start or start >= info['duration'] or end > info['duration'] + .1:
                    raise ValueError('参考视频所选时间超出素材范围，请调整开始和结束秒数。')
                end = min(end, info['duration'])
                span = end - start
                command += ['-ss', str(start), '-i', str(local), '-t', str(span),
                            '-vf', 'fps=%s,scale=960:960:force_original_aspect_ratio=decrease' % (12 / span),
                            '-frames:v', '12']
                entry.update(start=start, end=end, audio=False)
            else:
                command += ['-i', str(local), '-vf', 'scale=1536:1536:force_original_aspect_ratio=decrease', '-frames:v', '1']
            command += ['-an', '-q:v', '3', str(output)]
            run(command, timeout=45)
            images = sorted(folder.glob('reference-' + str(index) + '-*.jpg'))
            if not images:
                raise ValueError('参考画面读取失败，请检查素材后重试。')
            entry['frames'] = len(images)
            summary.append(entry)
            content = []
            for frame_index, image in enumerate(images):
                label = target['label']
                if source['type'] == 'video':
                    label += ' sampled frame %d/%d, approximately %.2f seconds in source; visual sample only, source audio not analyzed' % (
                        frame_index + 1, len(images), start + (frame_index + .5) * span / 12)
                content += [{'type': 'text', 'text': label},
                            {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(image.read_bytes()).decode('ascii')}}]
            groups.append((source['type'], content))
    # Keep identity images last so repeated motion frames cannot dominate appearance.
    content = []
    for kind in ('video', 'image'):
        for source_kind, blocks in groups:
            if source_kind == kind:
                content += [{'type': 'text', 'text': 'MOTION REFERENCE: keep only requested motion/layout, not original actor appearance.' if kind == 'video'
                             else 'TARGET IDENTITY REFERENCE: this picture defines replacement appearance. Video actors are not the identity reference.'}]
                content += blocks
    return content, summary
