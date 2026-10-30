"""Reuse VHS video loading without retaining an unused lazy audio object.

Only substitute this node when the reference workflow does not connect AUDIO.
Image, mask and video-info outputs are the original VHS objects, without copying
or converting their data. Set ignore_audio=False to retain the complete output.
"""
from copy import deepcopy


def _vhs_loader():
    # VHS may be registered after this bridge during custom-node discovery.
    # Resolve its already-loaded class at use time rather than importing it twice.
    import nodes
    loader = nodes.NODE_CLASS_MAPPINGS.get('VHS_LoadVideoFFmpeg')
    if loader is None:
        raise RuntimeError('缺少 Video Helper Suite 的视频读取节点，请先安装或启用它。')
    return loader


class _VHSAttribute:
    def __init__(self, name):
        self.name = name

    def __get__(self, instance, owner):
        return getattr(_vhs_loader(), self.name)


class H3ReferenceVideoLoader:
    CATEGORY = '创作画布/动作参考'
    FUNCTION = 'load_video'
    RETURN_TYPES = _VHSAttribute('RETURN_TYPES')
    RETURN_NAMES = _VHSAttribute('RETURN_NAMES')
    DESCRIPTION = '动作参考仅使用视频画面时，跳过未使用的音频缓存；不改变视频像素、帧数、尺寸或遮罩。'

    @classmethod
    def INPUT_TYPES(cls):
        schema = deepcopy(_vhs_loader().INPUT_TYPES())
        schema.setdefault('optional', {})['ignore_audio'] = (
            'BOOLEAN', {'default': True, 'tooltip': '仅在音频输出未连接时开启；关闭后保留原始音频输出。'})
        return schema

    @classmethod
    def IS_CHANGED(cls, video, ignore_audio=True, **kwargs):
        return _vhs_loader().IS_CHANGED(video, **kwargs)

    @classmethod
    def VALIDATE_INPUTS(cls, video):
        return _vhs_loader().VALIDATE_INPUTS(video)

    def load_video(self, ignore_audio=True, **kwargs):
        if not hasattr(self, '_loader'):
            self._loader = _vhs_loader()()
        result = self._loader.load_video(**kwargs)
        if not ignore_audio:
            return result
        # Index 1 is VHS's mask, not a frame count. Never iterate lazy AUDIO.
        return (*result[:2], None, *result[3:])


NODE_CLASS_MAPPINGS = {'H3ReferenceVideoLoader': H3ReferenceVideoLoader}
NODE_DISPLAY_NAME_MAPPINGS = {'H3ReferenceVideoLoader': '动作参考画面 · 不读取未使用音轨'}
