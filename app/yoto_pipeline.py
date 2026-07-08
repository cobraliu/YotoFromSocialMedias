"""Orchestrate: optimize a downloaded track -> upload -> append to playlist."""
from __future__ import annotations

import asyncio
from pathlib import Path

import app.yoto_audio as audio
from app.yoto_client import (authed_session, build_appended_payload, create_content,
                         fetch_card, part_titles, upload_file)


def optimize_track(src: Path, workdir: Path, cap: int = audio.BITRATE_CAP) -> list[Path]:
    """Bitrate-cap + (if >SEG_MAX) silence-split into MP3 parts. Returns ordered
    part paths. Skips encode when the part file already exists (resumable)."""
    workdir.mkdir(parents=True, exist_ok=True)
    dur = audio.probe_duration(src)
    kbps = audio.target_kbps(src, cap)
    if dur > audio.SEG_MAX:
        sil = audio.detect_silence_mids(src)
        bounds = audio.plan_cuts(dur, sil)
    else:
        bounds = [0.0, dur]
    n = len(bounds) - 1
    parts = []
    for p in range(n):
        start, end = bounds[p], bounds[p + 1]
        dest = workdir / f"enc_{p + 1:02d}.mp3"
        if not (dest.exists() and dest.stat().st_size > 0):
            if n == 1:
                audio.encode_mp3(src, dest, kbps)
            else:
                audio.encode_mp3(src, dest, kbps, start, end)
        parts.append(dest)
    return parts


async def run_upload(audio_path: str, filename: str, playlist_id: str, job,
                     icon_media_id: str | None = None,
                     uid: str | None = None) -> None:
    """Full async pipeline, updating `job` as it goes. Never raises — failures
    are recorded on the job so the UI can surface them."""
    try:
        src = Path(audio_path)
        if not src.exists():
            job.finish(False, "源音频文件不存在")
            return
        workdir = src.parent / "yoto"

        job.update(10, "优化音频（限码率/切分）…")
        parts = await asyncio.to_thread(optimize_track, src, workdir)
        titles = part_titles(filename, len(parts))
        job.add_log(f"优化完成：{len(parts)} 段")

        async with authed_session(uid) as (session, get_token):
            new_parts = []
            for i, part in enumerate(parts):
                job.update(20 + int(60 * i / len(parts)),
                           f"上传 {i + 1}/{len(parts)} …")
                info = await upload_file(session, get_token, part)
                new_parts.append({
                    "title": titles[i],
                    "trackUrl": f"yoto:#{info['sha']}",
                    "duration": info.get("duration"),
                    "fileSize": info.get("fileSize"),
                    "channels": info.get("channels"),
                    "format": info.get("format"),
                })
                job.add_log(f"已上传 {titles[i]}")

            job.update(85, "更新 playlist …")
            detail = await fetch_card(session, get_token, playlist_id)
            icon_ref = f"yoto:#{icon_media_id}" if icon_media_id else None
            payload = build_appended_payload(detail, new_parts, icon_ref=icon_ref)
            await create_content(session, get_token, payload)

        job.finish(True, f"完成：已添加 {len(parts)} 段到 playlist")
    except Exception as e:  # surfaced to the UI via job status
        job.finish(False, str(e))
