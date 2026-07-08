"""Audio probing + optimization (bitrate cap, silence-aware split, MP3 encode).
All heavy work shells out to ffmpeg/ffprobe."""
from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path

SEG_MAX = 300        # split threshold + max segment length (seconds)
BITRATE_CAP = 96     # kbps cap


def ffprobe_json(path: Path, args: list[str]) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", *args, "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    try:
        return json.loads(out.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def probe_duration(path: Path) -> float:
    j = ffprobe_json(path, ["-show_entries", "format=duration"])
    return float(j.get("format", {}).get("duration") or 0.0)


def src_kbps(path: Path) -> int | None:
    j = ffprobe_json(path, ["-select_streams", "a:0", "-show_entries", "stream=bit_rate"])
    streams = j.get("streams") or []
    br = int(streams[0].get("bit_rate") or 0) if streams else 0
    if not br:
        j2 = ffprobe_json(path, ["-show_entries", "format=bit_rate"])
        br = int(j2.get("format", {}).get("bit_rate") or 0)
    return round(br / 1000) if br else None


def target_kbps(path: Path, cap: int = BITRATE_CAP) -> int:
    k = src_kbps(path)
    return min(cap, k) if k else cap


def detect_silence_mids(path: Path, noise="-30dB", dur=0.35) -> list[float]:
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path),
         "-af", f"silencedetect=noise={noise}:d={dur}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    txt = out.stderr
    starts = [float(x) for x in re.findall(r"silence_start:\s*([\d.]+)", txt)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([\d.]+)", txt)]
    mids = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        mids.append((s + e) / 2 if e is not None else s)
    return sorted(mids)


def plan_cuts(duration: float, silences: list[float], seg_max: int = SEG_MAX) -> list[float]:
    """Boundaries [0, ..., duration] splitting into EQUAL ~<=seg_max parts,
    each snapped to nearest silence within a bounded window."""
    if duration <= seg_max:
        return [0.0, duration]
    n = math.ceil(duration / seg_max)
    seg = duration / n
    window = max(0.0, min(20.0, (seg_max - seg) * 0.9))
    bounds = [0.0]
    for i in range(1, n):
        ideal = i * seg
        cands = [t for t in silences
                 if abs(t - ideal) <= window and bounds[-1] + 5 < t < duration - 5]
        cut = min(cands, key=lambda t: abs(t - ideal)) if cands else ideal
        if cut <= bounds[-1]:
            cut = ideal
        bounds.append(cut)
    bounds.append(duration)
    return bounds


def encode_mp3(src: Path, dest: Path, kbps: int,
               start: float | None = None, end: float | None = None) -> Path:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src)]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if end is not None:
        cmd += ["-to", f"{end:.3f}"]
    cmd += ["-vn", "-c:a", "libmp3lame", "-b:a", f"{kbps}k", str(dest)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not dest.exists():
        raise RuntimeError(f"ffmpeg encode failed: {r.stderr[-500:]}")
    return dest


def readable_duration(sec: float) -> str:
    sec = int(sec)
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    parts = []
    if h:
        parts.append(f"{h}h")
    parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)
