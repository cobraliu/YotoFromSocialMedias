import os
import sys
import uuid
from pathlib import Path
from typing import Optional


def resource_path(relative_path: str) -> str:
    """获取资源的绝对路径"""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


def generate_task_id() -> str:
    """生成唯一的任务ID"""
    return str(uuid.uuid4())


def get_file_size(file_path: str) -> Optional[float]:
    """获取文件大小（MB）"""
    try:
        if os.path.exists(file_path):
            return os.path.getsize(file_path) / (1024 * 1024)  # MB
        return None
    except:
        return None


def get_file_extension(file_path: str) -> str:
    """获取文件扩展名"""
    return os.path.splitext(file_path)[1].lower()


def get_content_type(file_path: str) -> str:
    """根据文件扩展名获取内容类型"""
    ext = get_file_extension(file_path)
    content_types = {
        '.mp4': 'video/mp4',
        '.mkv': 'video/x-matroska',
        '.webm': 'video/webm',
        '.mp3': 'audio/mpeg',
        '.m4a': 'audio/mp4',
        '.wav': 'audio/wav',
        '.flac': 'audio/flac',
        '.opus': 'audio/opus',
        '.aac': 'audio/aac'
    }
    return content_types.get(ext, 'application/octet-stream')

import subprocess
import json
import shutil
import re
import unicodedata

def sanitize_filename(name: str, max_bytes: int = 120) -> str:
    """生成 shell / URL / 跨平台文件系统都安全的文件名。

    只保留字母、数字、Unicode 文字（含中日韩）、'.'、'-'、'_'。
    空格、#、!、&、(、)、引号、emoji、控制字符等一律替换为 '_'，
    避免后续 ffmpeg / scp / URL 编码出现 shell 解析、路径截断、符号被吞等问题。
    最后按 UTF-8 字节数截断，确保不超过文件系统 255 字节限制。
    """
    name = unicodedata.normalize('NFC', str(name))
    # 一刀切：非 [\w . -] 的字符全部替换为 _
    # （\w 在 Python3 默认含 Unicode 字母、汉字、数字、下划线）
    name = re.sub(r'[^\w.\-]+', '_', name, flags=re.UNICODE)
    name = re.sub(r'_+', '_', name)         # 压缩连续 _
    name = name.strip('._-')                # 去掉首尾的 . - _
    encoded = name.encode('utf-8')
    if len(encoded) > max_bytes:
        name = encoded[:max_bytes].decode('utf-8', errors='ignore').strip('._-')
    return name or 'unnamed'


def normalize_loudness(input_file: str, output_file: str, target_lufs: float = -16,
                       true_peak: float = -1.5, lra: float = 11) -> None:
    """EBU R128 两遍响度归一化，使用 ffmpeg loudnorm 滤镜。
    默认目标 -16 LUFS（与 Spotify 一致），适合 Yoto 等固定音量设备。
    第一遍测量实际响度，第二遍用线性增益精确对齐，不引入动态压缩。
    失败时回退到复制原文件。
    """
    if not os.path.exists(input_file):
        raise FileNotFoundError(f"输入文件不存在: {input_file}")

    try:
        # ── 第一遍：测量响度 ──────────────────────────────────────
        r1 = subprocess.run(
            ['ffmpeg', '-i', input_file,
             '-af', f'loudnorm=I={target_lufs}:TP={true_peak}:LRA={lra}:print_format=json',
             '-f', 'null', '/dev/null'],
            capture_output=True, text=True, timeout=180
        )
        # loudnorm 将 JSON 统计输出到 stderr
        stderr = r1.stderr
        j_start = stderr.rfind('{')
        j_end   = stderr.rfind('}') + 1
        if j_start == -1 or j_end <= j_start:
            raise RuntimeError(f"无法解析 loudnorm 测量结果:\n{stderr[-300:]}")
        stats = json.loads(stderr[j_start:j_end])

        # ── 第二遍：线性增益归一化 ────────────────────────────────
        af = (
            f"loudnorm=I={target_lufs}:TP={true_peak}:LRA={lra}"
            f":measured_I={stats['input_i']}"
            f":measured_LRA={stats['input_lra']}"
            f":measured_TP={stats['input_tp']}"
            f":measured_thresh={stats['input_thresh']}"
            f":offset={stats['target_offset']}"
            f":linear=true:print_format=summary"
        )
        r2 = subprocess.run(
            ['ffmpeg', '-i', input_file, '-af', af,
             '-ar', '44100', '-b:a', '192k', '-y', output_file],
            capture_output=True, text=True, timeout=180
        )
        if r2.returncode != 0:
            raise RuntimeError(f"第二遍失败: {r2.stderr[-300:]}")

        print(f"响度归一化完成 ({target_lufs} LUFS): {output_file}")

    except Exception as e:
        # 归一化失败时保留原文件，不中断下载流程
        shutil.copy2(input_file, output_file)
        print(f"响度归一化失败，使用原文件: {e}")
