"""
so101.sound_helpers — 声音提示模块
====================================

纯 Python 正弦波 beep，无需额外音频文件。

使用方式：
    from so101.sound_helpers import (
        sound_start,        # episode 开始录制
        sound_episode_done, # 单个 episode 保存完毕
        sound_reset,        # 进入重置阶段
        sound_all_done,     # 全部采集完成
        sound_warn,         # 警告
    )
"""

import subprocess
import threading
import math
import tempfile
import wave


def _generate_beep(freq_hz: int, duration_ms: int) -> bytes:
    """生成正弦波 PCM 数据（16bit mono 22050Hz）。"""
    n = int(22050 * duration_ms / 1000)
    frames = []
    for i in range(n):
        t = i / 22050.0
        v = int(16000 * math.sin(2 * math.pi * freq_hz * t))
        frames.append(v.to_bytes(2, byteorder="little", signed=True))
    return b"".join(frames)


def _save_wav(data: bytes) -> str:
    """将 PCM 数据写入临时 wav 文件，返回路径。"""
    tmp = tempfile.mktemp(suffix=".wav")
    with wave.open(tmp, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(22050)
        wf.writeframes(data)
    return tmp


def _play_wav(path: str):
    """用系统播放器播放 wav 文件。"""
    try:
        # 优先 ffplay（静默，自动退出）
        subprocess.run(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path],
            timeout=5,
        )
        return
    except Exception:
        pass
    try:
        subprocess.run(["paplay", path], timeout=5)
        return
    except Exception:
        pass
    try:
        subprocess.run(["aplay", "-q", path], timeout=5)
        return
    except Exception:
        pass


def _beep(freq_hz: int, duration_ms: int, blocking: bool = False):
    """播放一个 beep 音。"""
    data = _generate_beep(freq_hz, duration_ms)
    path = _save_wav(data)

    def _play_and_cleanup():
        _play_wav(path)
        try:
            import os
            os.unlink(path)
        except Exception:
            pass

    if blocking:
        _play_and_cleanup()
    else:
        t = threading.Thread(target=_play_and_cleanup, daemon=True)
        t.start()


def sound_start():
    """录制开始（高音短 beep）。"""
    _beep(880, 150)


def sound_episode_done():
    """单个 episode 完成（两声短 beep）。"""
    _beep(660, 100, blocking=True)
    _beep(880, 100)


def sound_reset():
    """进入重置阶段（低音长 beep）。"""
    _beep(440, 300)


def sound_all_done():
    """全部完成（三声递增 beep）。"""
    _beep(523, 100, blocking=True)
    _beep(659, 100, blocking=True)
    _beep(784, 200)


def sound_warn():
    """警告（低音急促两声）。"""
    _beep(330, 80, blocking=True)
    _beep(330, 80)
