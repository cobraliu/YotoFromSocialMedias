"""Dry-run: optimize a local audio file and print the append payload that
WOULD be POSTed, plus the playlist list. No upload, no card mutation.

Usage: python scripts/yoto_dryrun.py <audio_file> <playlist_id>
       python scripts/yoto_dryrun.py --list        # just list playlists

Run from the app root so the flat yoto_* modules import as top-level modules.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.yoto_pipeline as pipeline
from app.yoto_client import (authed_session, build_appended_payload, fetch_card,
                             list_playlists, part_titles)


async def _list_only() -> int:
    async with authed_session() as (session, get_token):
        pls = await list_playlists(session, get_token)
    print(json.dumps(pls, ensure_ascii=False, indent=2))
    return 0


async def main(audio_file: str, playlist_id: str) -> int:
    src = Path(audio_file)
    if not src.exists():
        raise SystemExit(f"no such file: {src}")
    parts = pipeline.optimize_track(src, src.parent / "yoto")
    titles = part_titles(src.stem, len(parts))
    print(f"optimized into {len(parts)} part(s): {[p.name for p in parts]}")
    fake_parts = [{"title": titles[i], "trackUrl": "yoto:#DRYRUN",
                   "duration": 0, "fileSize": 0, "channels": 2, "format": "mp3"}
                  for i in range(len(parts))]
    async with authed_session() as (session, get_token):
        pls = await list_playlists(session, get_token)
        print("playlists:", json.dumps(pls, ensure_ascii=False, indent=2))
        detail = await fetch_card(session, get_token, playlist_id)
    payload = build_appended_payload(detail, fake_parts)
    payload["content"]["chapters"] = payload["content"]["chapters"][-3:]
    print("would POST /content payload (last 3 chapters):")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--list":
        sys.exit(asyncio.run(_list_only()))
    if len(sys.argv) < 3:
        raise SystemExit("usage: yoto_dryrun.py <audio_file> <playlist_id> | --list")
    sys.exit(asyncio.run(main(sys.argv[1], sys.argv[2])))
