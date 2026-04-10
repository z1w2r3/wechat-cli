"""语音消息 silk → wav 转码。"""

import os
import sqlite3
import subprocess
import shutil


def _ensure_audio_dir(cfg):
    """返回语音输出目录，不存在则创建。"""
    state_dir = os.path.dirname(os.path.abspath(cfg.get("keys_file", "")))
    audio_dir = os.path.join(state_dir, "audio")
    os.makedirs(audio_dir, exist_ok=True)
    return audio_dir


def extract_voice_from_db(local_id, create_time, cache, cfg):
    """从 media_0.db 的 VoiceInfo 表提取语音数据并转码为 wav。

    Args:
        local_id: 消息 local_id
        create_time: 消息时间戳
        cache: DBCache 实例
        cfg: 配置 dict

    Returns:
        (wav_path, warning) 元组
    """
    media_path = cache.get("message/media_0.db")
    if not media_path:
        return None, "media_0.db 不可用"

    try:
        conn = sqlite3.connect(media_path)
        row = conn.execute(
            "SELECT voice_data FROM VoiceInfo WHERE local_id=? AND create_time=?",
            (local_id, create_time)
        ).fetchone()
        conn.close()
    except Exception as e:
        return None, f"查询 VoiceInfo 失败: {e}"

    if not row or not row[0]:
        return None, None  # 无语音数据，不报错

    voice_data = row[0]
    audio_dir = _ensure_audio_dir(cfg)
    silk_path = os.path.join(audio_dir, f"voice_{local_id}_{create_time}.silk")
    with open(silk_path, 'wb') as f:
        f.write(voice_data)

    wav_path, warning = decode_silk_to_wav(silk_path, cfg)

    # 清理临时 silk 文件
    if os.path.isfile(silk_path) and wav_path:
        os.remove(silk_path)

    return wav_path, warning


def _has_ffmpeg():
    return shutil.which("ffmpeg") is not None


def _has_pilk():
    try:
        import pilk  # noqa: F401
        return True
    except ImportError:
        return False


def decode_silk_to_wav(silk_path, cfg):
    """将 silk 语音文件转码为 wav。

    Args:
        silk_path: .aud.silk 文件路径
        cfg: wechat-cli 配置 dict

    Returns:
        (wav_path, warning) 元组。
        成功时 wav_path 为输出文件路径，warning 为 None。
        失败时 wav_path 为 None，warning 为原因描述。
    """
    if not os.path.isfile(silk_path):
        return None, f"silk 文件不存在: {silk_path}"

    if not _has_pilk():
        return None, "pilk 未安装，跳过语音转码 (pip install pilk)"

    if not _has_ffmpeg():
        return None, "ffmpeg 未安装，跳过语音转码 (brew install ffmpeg)"

    audio_dir = _ensure_audio_dir(cfg)
    base_name = os.path.splitext(os.path.basename(silk_path))[0]
    # 去掉 .aud 后缀（文件名形如 xxx.aud.silk）
    if base_name.endswith(".aud"):
        base_name = base_name[:-4]
    wav_path = os.path.join(audio_dir, base_name + ".wav")

    # 缓存：wav 已存在且不早于 silk 文件，跳过转码
    if os.path.isfile(wav_path):
        if os.path.getmtime(wav_path) >= os.path.getmtime(silk_path):
            return wav_path, None

    # silk → pcm
    import pilk
    pcm_path = os.path.join(audio_dir, base_name + ".pcm")
    try:
        pilk.decode(silk_path, pcm_path)
    except Exception as e:
        return None, f"silk 解码失败: {e}"

    # pcm → wav (s16le, 24000Hz, mono)
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "s16le", "-ar", "24000", "-ac", "1",
                "-i", pcm_path,
                wav_path,
            ],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        return None, f"ffmpeg 转码失败: {e.stderr.decode(errors='replace')}"
    finally:
        # 清理 pcm 临时文件
        if os.path.isfile(pcm_path):
            os.remove(pcm_path)

    return wav_path, None
