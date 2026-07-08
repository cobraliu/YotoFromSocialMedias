# Yoto Auto-Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a one-click "⬆ Yoto" upload from a downloaded V2M track to an existing Yoto playlist, optimizing audio on the way up (cap bitrate at 96k, split tracks longer than 5 min at silence).

**Architecture:** A slim, self-contained `yoto/` package is vendored into V2M (ported from the `yoto-update` sibling repo) providing config/auth/audio/upload primitives. FastAPI gains 3 endpoints; an in-memory job manager tracks upload progress; the existing `scrape.html` gains a button + modal.

**Tech Stack:** Python 3.10, FastAPI, aiohttp, ffmpeg/ffprobe, vanilla-JS template.

## Global Constraints

- `BITRATE_CAP = 96` kbps; encode with `min(cap, source_kbps)` — never upscale.
- `SEG_MAX = 300` seconds; silence-aware equal split at `-30dB` / 0.35s.
- Append-only: add chapters to an existing card via `POST /content` with `cardId`. No new-card creation.
- State dir: `YOTO_STATE_DIR` env, default `<V2M>/.yoto/`; holds `.env` (client_id) + `.yoto_token.json`. Gitignored.
- Refresh tokens are single-use — always persist the rotated token after refresh.
- Working/encoded files under `data/<task_id>/yoto/`; `.sha.json` sidecar caches transcode result for resumability.
- All paths below are relative to `V2M/` (the FastAPI app root).

---

### Task 1: Vendor package foundation — endpoints, token, config

**Files:**
- Create: `yoto/__init__.py` (empty)
- Create: `yoto/endpoints.py`
- Create: `yoto/token.py`
- Create: `yoto/config.py`
- Test: `tests/test_yoto_config.py`

**Interfaces:**
- Produces: `endpoints.BASE_URL`, `endpoints.TOKEN_URL`, `endpoints.AUTHORIZE_URL`, `endpoints.CARDS_LIBRARY`; `token.Token` dataclass; `config.state_dir()`, `config.load_client_id()`, `config.load_token() -> Token|None`, `config.save_token(Token)`.

- [ ] **Step 1: Write `yoto/endpoints.py`**

```python
"""URL constants for the Yoto REST API."""

BASE_URL = "https://api.yotoplay.com"
TOKEN_URL = "https://login.yotoplay.com/oauth/token"
AUTHORIZE_URL = "https://login.yotoplay.com/authorize"
CARDS_LIBRARY = "/card/family/library"
```

- [ ] **Step 2: Write `yoto/token.py`** (copied from `yoto_api/Token.py`)

```python
"""OAuth token returned by the Auth0 flows."""

from dataclasses import dataclass, field
import datetime as dt


@dataclass
class Token:
    access_token: str | None = field(default=None, repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    id_token: str | None = field(default=None, repr=False)
    scope: str | None = None
    valid_until: dt.datetime = dt.datetime.min
    token_type: str | None = None
```

- [ ] **Step 3: Write `yoto/config.py`**

```python
"""Portable config + token cache for V2M's Yoto uploader.

STATE_DIR (default <V2M>/.yoto/, override with YOTO_STATE_DIR) holds:
    .env              -> client_id  (line "client_id: XXX" or "client_id=XXX")
    .yoto_token.json  -> cached OAuth token
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Optional

from .token import Token

# yoto/config.py -> <V2M>/yoto/ ; default state dir is <V2M>/.yoto/
_DEFAULT_STATE_DIR = Path(__file__).resolve().parent.parent / ".yoto"


def state_dir() -> Path:
    return Path(os.environ.get("YOTO_STATE_DIR", str(_DEFAULT_STATE_DIR))).resolve()


def env_path() -> Path:
    return state_dir() / ".env"


def token_path() -> Path:
    return state_dir() / ".yoto_token.json"


def load_client_id() -> str:
    p = env_path()
    if not p.exists():
        raise RuntimeError(f"No .env at {p} — put your Yoto client_id there.")
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        for sep in (":", "="):
            if sep in line:
                key, val = line.split(sep, 1)
                if key.strip().lower() in ("client_id", "yoto_client_id"):
                    val = val.strip().strip("'\"")
                    if val:
                        return val
    raise RuntimeError(f"client_id not found in {p}")


def save_token(token: Token) -> None:
    data = {
        "access_token": token.access_token,
        "refresh_token": token.refresh_token,
        "token_type": token.token_type,
        "scope": token.scope,
        "valid_until": token.valid_until.isoformat() if token.valid_until else None,
    }
    p = token_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))
    os.chmod(p, 0o600)


def load_token() -> Optional[Token]:
    p = token_path()
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    valid_until = data.get("valid_until")
    return Token(
        access_token=data.get("access_token"),
        refresh_token=data.get("refresh_token"),
        token_type=data.get("token_type", "Bearer"),
        scope=data.get("scope"),
        valid_until=(
            datetime.datetime.fromisoformat(valid_until)
            if valid_until else datetime.datetime.min
        ),
    )
```

- [ ] **Step 4: Write `tests/test_yoto_config.py`**

```python
import json
import os
from yoto import config


def test_load_client_id_colon_and_equals(tmp_path, monkeypatch):
    monkeypatch.setenv("YOTO_STATE_DIR", str(tmp_path))
    (tmp_path / ".env").write_text("client_id: ABC123\n")
    assert config.load_client_id() == "ABC123"
    (tmp_path / ".env").write_text("YOTO_CLIENT_ID=XYZ\n")
    assert config.load_client_id() == "XYZ"


def test_token_roundtrip(tmp_path, monkeypatch):
    from yoto.token import Token
    import datetime
    monkeypatch.setenv("YOTO_STATE_DIR", str(tmp_path))
    t = Token(access_token="a", refresh_token="r",
              valid_until=datetime.datetime(2030, 1, 1))
    config.save_token(t)
    loaded = config.load_token()
    assert loaded.access_token == "a"
    assert loaded.refresh_token == "r"
    assert loaded.valid_until.year == 2030


def test_load_token_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("YOTO_STATE_DIR", str(tmp_path))
    assert config.load_token() is None
```

- [ ] **Step 5: Run tests**

Run: `cd V2M && python -m pytest tests/test_yoto_config.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add yoto/__init__.py yoto/endpoints.py yoto/token.py yoto/config.py tests/test_yoto_config.py
git commit -m "feat(yoto): vendor endpoints, token, config foundation"
```

---

### Task 2: Auth — authed_session with refresh + persist

**Files:**
- Create: `yoto/auth.py`
- Test: `tests/test_yoto_auth.py`

**Interfaces:**
- Consumes: `config.load_client_id/load_token/save_token`, `endpoints.TOKEN_URL`.
- Produces: `auth.authed_session()` async ctx mgr yielding `(session, access_token)`; `auth.auth_headers(token) -> dict`; `auth._build_token_from_body(body, scope) -> Token` (pure, tested).

- [ ] **Step 1: Write `yoto/auth.py`** (ported/inlined from `common.py` + `login.py`)

```python
"""Authenticated aiohttp session with token refresh + persistence.

Yoto refresh tokens are single-use, so the rotated token is written back to
disk after every refresh.
"""
from __future__ import annotations

import contextlib
import datetime

import aiohttp

from . import config, endpoints
from .token import Token

SCOPES = "family:library:view family:library:manage user:content:manage offline_access"
_REFRESH_MARGIN = datetime.timedelta(minutes=5)


def auth_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}


def _build_token_from_body(body: dict, scope: str) -> Token:
    valid_until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        seconds=int(body.get("expires_in", 3600))
    )
    return Token(
        access_token=body["access_token"],
        refresh_token=body.get("refresh_token"),
        id_token=body.get("id_token"),
        token_type=body.get("token_type", "Bearer"),
        scope=body.get("scope", scope),
        valid_until=valid_until,
    )


async def _refresh(session, client_id, token: Token) -> Token:
    data = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": token.refresh_token,
    }
    async with session.post(
        endpoints.TOKEN_URL, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    ) as resp:
        body = await resp.json(content_type=None)
        if not resp.ok or body.get("error"):
            raise RuntimeError(f"[auth] refresh failed: {resp.status} {body}")
        if not body.get("refresh_token"):
            body["refresh_token"] = token.refresh_token
        return _build_token_from_body(body, token.scope or SCOPES)


@contextlib.asynccontextmanager
async def authed_session():
    """Yield (session, access_token). Refresh + persist as needed.
    Raises RuntimeError with a user-facing message if no token is cached."""
    client_id = config.load_client_id()
    token = config.load_token()
    if token is None or not token.refresh_token:
        raise RuntimeError("请重新登录 Yoto（未找到缓存 token）。")

    now = datetime.datetime.now(datetime.timezone.utc)
    valid_until = token.valid_until
    if valid_until and valid_until.tzinfo is None:
        valid_until = valid_until.replace(tzinfo=datetime.timezone.utc)

    async with aiohttp.ClientSession() as session:
        if (token.access_token is None or valid_until is None
                or valid_until - _REFRESH_MARGIN <= now):
            token = await _refresh(session, client_id, token)
            config.save_token(token)
        yield session, token.access_token
```

- [ ] **Step 2: Write `tests/test_yoto_auth.py`** (pure helper only — no network)

```python
from yoto.auth import _build_token_from_body, auth_headers


def test_build_token_defaults_scope_and_expiry():
    tok = _build_token_from_body(
        {"access_token": "aa", "refresh_token": "rr", "expires_in": 100}, "sc"
    )
    assert tok.access_token == "aa"
    assert tok.refresh_token == "rr"
    assert tok.scope == "sc"
    assert tok.valid_until is not None


def test_auth_headers():
    h = auth_headers("tok")
    assert h["Authorization"] == "Bearer tok"
    assert h["Accept"] == "application/json"
```

- [ ] **Step 3: Run tests**

Run: `cd V2M && python -m pytest tests/test_yoto_auth.py -v`
Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
git add yoto/auth.py tests/test_yoto_auth.py
git commit -m "feat(yoto): authed_session with refresh + persist"
```

---

### Task 3: Audio optimization helpers

**Files:**
- Create: `yoto/audio.py`
- Test: `tests/test_yoto_audio.py`

**Interfaces:**
- Produces: `SEG_MAX=300`, `BITRATE_CAP=96`; `probe_duration(Path)->float`, `src_kbps(Path)->int|None`, `target_kbps(Path,cap)->int`, `detect_silence_mids(Path,...)->list[float]`, `plan_cuts(duration,silences,seg_max=SEG_MAX)->list[float]`, `encode_mp3(src,dest,kbps,start=None,end=None)->Path`, `readable_duration(sec)->str`.

- [ ] **Step 1: Write `yoto/audio.py`** (ported from `forklib.py`)

```python
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
```

- [ ] **Step 2: Write `tests/test_yoto_audio.py`** (pure functions only)

```python
import math
from yoto.audio import plan_cuts, readable_duration, SEG_MAX


def test_plan_cuts_short_track_single_segment():
    assert plan_cuts(120.0, []) == [0.0, 120.0]


def test_plan_cuts_long_track_equal_parts_no_silence():
    # 660s -> ceil(660/300)=3 parts of 220s each, no silence -> ideal cuts.
    bounds = plan_cuts(660.0, [])
    assert bounds[0] == 0.0 and bounds[-1] == 660.0
    assert len(bounds) == 4
    for a, b in zip(bounds, bounds[1:]):
        assert (b - a) <= SEG_MAX + 0.001


def test_plan_cuts_snaps_to_silence():
    # 600s -> 2 parts, ideal cut 300; silence at 295 within window -> snap.
    bounds = plan_cuts(600.0, [295.0])
    assert abs(bounds[1] - 295.0) < 0.001


def test_readable_duration():
    assert readable_duration(65) == "1m 5s"
    assert readable_duration(3661) == "1h 1m 1s"
```

- [ ] **Step 3: Run tests**

Run: `cd V2M && python -m pytest tests/test_yoto_audio.py -v`
Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
git add yoto/audio.py tests/test_yoto_audio.py
git commit -m "feat(yoto): audio bitrate cap + silence-aware split helpers"
```

---

### Task 4: Uploader — upload primitives + append-to-playlist

**Files:**
- Create: `yoto/uploader.py`
- Test: `tests/test_yoto_uploader.py`

**Interfaces:**
- Consumes: `auth.auth_headers`, `endpoints.BASE_URL/CARDS_LIBRARY`, `audio.readable_duration`.
- Produces:
  - `list_playlists(session, token) -> list[dict]`  (each `{id, title, n_tracks}`)
  - `upload_file(session, token, path: Path) -> dict`  (`{sha,duration,fileSize,channels,format}`, sha-cached)
  - `fetch_card(session, token, card_id) -> dict`
  - `build_appended_payload(card_detail: dict, new_parts: list[dict]) -> dict`  (**pure**, tested)
  - `part_titles(base: str, n_parts: int) -> list[str]`  (**pure**, tested)

- [ ] **Step 1: Write `tests/test_yoto_uploader.py`** FIRST (pure functions)

```python
from yoto.uploader import build_appended_payload, part_titles


def test_part_titles_single_and_split():
    assert part_titles("song", 1) == ["song"]
    assert part_titles("song", 3) == ["song-1", "song-2", "song-3"]


def _fake_card():
    return {"card": {
        "cardId": "CARD1",
        "title": "My List",
        "metadata": {"author": "me", "cover": {"imageL": "u"}},
        "content": {
            "playbackType": "linear",
            "config": {"autoadvance": "next", "onlineOnly": False, "shuffle": []},
            "cover": {"imageL": "u"},
            "chapters": [
                {"key": "01", "overlayLabel": "1", "title": "old",
                 "tracks": [{"key": "01", "title": "old", "trackUrl": "yoto:#A",
                             "duration": 100, "fileSize": 1000, "channels": 2,
                             "format": "mp3", "type": "audio"}]},
            ],
        },
    }}


def test_build_appended_payload_appends_and_numbers():
    parts = [{"title": "new", "trackUrl": "yoto:#B", "duration": 60,
              "fileSize": 500, "channels": 2, "format": "mp3"}]
    p = build_appended_payload(_fake_card(), parts)
    chapters = p["content"]["chapters"]
    assert len(chapters) == 2                       # appended, existing kept
    assert chapters[0]["title"] == "old"            # existing preserved
    assert chapters[1]["key"] == "02"               # continued numbering
    assert chapters[1]["overlayLabel"] == "2"
    assert chapters[1]["tracks"][0]["trackUrl"] == "yoto:#B"
    assert p["cardId"] == "CARD1"                    # in-place update
    assert p["metadata"]["media"]["duration"] == 160  # 100 + 60 recomputed
    assert p["metadata"]["media"]["fileSize"] == 1500
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd V2M && python -m pytest tests/test_yoto_uploader.py -v`
Expected: FAIL (ImportError: cannot import name 'build_appended_payload').

- [ ] **Step 3: Write `yoto/uploader.py`**

```python
"""Yoto media upload + playlist append."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from . import endpoints
from .auth import auth_headers
from .audio import readable_duration


# ─── pure helpers (unit-tested) ───────────────────────────────────────


def part_titles(base: str, n_parts: int) -> list[str]:
    if n_parts <= 1:
        return [base]
    return [f"{base}-{i + 1}" for i in range(n_parts)]


def _next_base_seq(chapters: list[dict]) -> int:
    """Highest existing chapter number, robust to non-integer keys."""
    mx = len(chapters)
    for ch in chapters:
        try:
            mx = max(mx, int(ch.get("key")))
        except (TypeError, ValueError):
            pass
    return mx


def build_appended_payload(card_detail: dict, new_parts: list[dict]) -> dict:
    """Append new_parts (each: title, trackUrl, duration, fileSize, channels,
    format) as chapters to an existing card; return POST /content payload that
    updates the card in place (cardId included)."""
    card = card_detail["card"]
    card_id = card.get("cardId")
    content = card.get("content", {}) or {}
    meta = card.get("metadata", {}) or {}
    title = card.get("title")
    chapters = list(content.get("chapters", []) or [])
    cover = (content.get("cover") or {}).get("imageL") or (meta.get("cover") or {}).get("imageL")

    base = _next_base_seq(chapters)
    for i, part in enumerate(new_parts):
        seq = base + i + 1
        track = {
            "key": "01",
            "overlayLabel": "1",
            "title": part["title"],
            "trackUrl": part["trackUrl"],
            "duration": part["duration"],
            "fileSize": part["fileSize"],
            "channels": part["channels"],
            "format": part["format"],
            "type": "audio",
        }
        chapters.append({
            "key": f"{seq:02d}",
            "overlayLabel": str(seq),
            "title": part["title"],
            "tracks": [track],
        })

    total_dur = sum((tr.get("duration") or 0)
                    for ch in chapters for tr in (ch.get("tracks") or []))
    total_size = sum((tr.get("fileSize") or 0)
                     for ch in chapters for tr in (ch.get("tracks") or []))

    return {
        "cardId": card_id,
        "title": title,
        "content": {
            "playbackType": content.get("playbackType", "linear"),
            "config": content.get("config") or {"autoadvance": "next", "onlineOnly": False, "shuffle": []},
            "cover": {"imageL": cover} if cover else {},
            "chapters": chapters,
        },
        "metadata": {
            "author": meta.get("author", ""),
            "cover": {"imageL": cover} if cover else {},
            "media": {
                "duration": total_dur,
                "readableDuration": readable_duration(total_dur),
                "fileSize": total_size,
                "readableFileSize": f"{round(total_size / 1024 / 1024, 1)}MB",
            },
        },
    }


# ─── network primitives (ported from forklib.py) ──────────────────────


async def list_playlists(session, token) -> list[dict]:
    async with session.get(
        endpoints.BASE_URL + endpoints.CARDS_LIBRARY, headers=auth_headers(token)
    ) as r:
        txt = await r.text()
        if not r.ok:
            raise RuntimeError(f"library -> {r.status}: {txt[:200]}")
        cards = json.loads(txt).get("cards", []) or []
    out = []
    for item in cards:
        card = item.get("card", {}) or {}
        cid = item.get("cardId") or card.get("cardId")
        title = card.get("title") or item.get("title") or cid
        chapters = (card.get("content", {}) or {}).get("chapters", []) or []
        n = sum(len(ch.get("tracks", []) or []) for ch in chapters)
        if cid:
            out.append({"id": cid, "title": title, "n_tracks": n})
    return out


async def fetch_card(session, token, card_id: str) -> dict:
    async with session.get(
        endpoints.BASE_URL + f"/card/{card_id}", headers=auth_headers(token)
    ) as r:
        txt = await r.text()
        if not r.ok:
            raise RuntimeError(f"GET /card/{card_id} -> {r.status}: {txt[:200]}")
        return json.loads(txt)


async def _get_upload_url(session, token) -> tuple[str, str]:
    async with session.get(
        endpoints.BASE_URL + "/media/transcode/audio/uploadUrl",
        headers=auth_headers(token),
    ) as r:
        txt = await r.text()
        if not r.ok:
            raise RuntimeError(f"uploadUrl -> {r.status}: {txt[:200]}")
        up = json.loads(txt)["upload"]
        return up["uploadUrl"], up["uploadId"]


async def _put_upload(session, upload_url: str, path: Path) -> None:
    data = path.read_bytes()
    async with session.put(
        upload_url, data=data, headers={"Content-Type": "audio/mpeg"}
    ) as r:
        if r.status not in (200, 201, 204):
            txt = await r.text()
            raise RuntimeError(f"PUT upload -> {r.status}: {txt[:200]}")


async def _poll_transcode(session, token, upload_id: str,
                          tries: int = 90, delay: float = 1.0) -> dict:
    url = endpoints.BASE_URL + f"/media/upload/{upload_id}/transcoded?loudnorm=false"
    for _ in range(tries):
        async with session.get(url, headers=auth_headers(token)) as r:
            if r.ok:
                tc = (json.loads(await r.text())).get("transcode", {})
                if tc.get("transcodedSha256"):
                    return tc
        await asyncio.sleep(delay)
    raise TimeoutError(f"transcode timed out for upload {upload_id}")


async def upload_file(session, token, path: Path) -> dict:
    """Upload + transcode one mp3; return {sha,duration,fileSize,channels,format}.
    Sibling .sha.json short-circuits re-uploads on resume."""
    cache = path.with_suffix(path.suffix + ".sha.json")
    if cache.exists():
        return json.loads(cache.read_text())
    upload_url, upload_id = await _get_upload_url(session, token)
    await _put_upload(session, upload_url, path)
    tc = await _poll_transcode(session, token, upload_id)
    info = tc.get("transcodedInfo", {}) or {}
    result = {
        "sha": tc["transcodedSha256"],
        "duration": info.get("duration"),
        "fileSize": info.get("fileSize"),
        "channels": info.get("channels"),
        "format": info.get("format"),
    }
    cache.write_text(json.dumps(result))
    return result


async def create_content(session, token, payload: dict) -> dict:
    async with session.post(
        endpoints.BASE_URL + "/content",
        headers={**auth_headers(token), "Content-Type": "application/json"},
        json=payload,
    ) as r:
        txt = await r.text()
        if not r.ok:
            raise RuntimeError(f"POST /content -> {r.status}: {txt[:400]}")
        return json.loads(txt)
```

- [ ] **Step 4: Run tests**

Run: `cd V2M && python -m pytest tests/test_yoto_uploader.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add yoto/uploader.py tests/test_yoto_uploader.py
git commit -m "feat(yoto): upload primitives + append-to-playlist payload"
```

---

### Task 5: Upload pipeline + in-memory job manager

**Files:**
- Create: `yoto/pipeline.py`
- Create: `yoto_jobs.py`
- Test: `tests/test_yoto_jobs.py`

**Interfaces:**
- Consumes: everything in `yoto/` above.
- Produces:
  - `yoto_jobs.UploadJob` (attrs: `job_id, progress, status, success, error_message, log, done`); `yoto_jobs.jobManager` singleton with `create() -> UploadJob`, `get(job_id) -> dict|None`.
  - `pipeline.optimize_track(src: Path, workdir: Path, cap=BITRATE_CAP) -> list[Path]` (encode; returns ordered part paths — sync, off-loop).
  - `pipeline.run_upload(audio_path: str, filename: str, playlist_id: str, job) -> None` (async orchestration; updates `job`).

- [ ] **Step 1: Write `tests/test_yoto_jobs.py`**

```python
from yoto_jobs import JobManager


def test_job_lifecycle():
    mgr = JobManager()
    job = mgr.create()
    assert mgr.get(job.job_id)["progress"] == 0
    job.update(40, "上传中")
    job.add_log("hi")
    assert mgr.get(job.job_id)["progress"] == 40
    assert mgr.get(job.job_id)["status"] == "上传中"
    assert "hi" in mgr.get(job.job_id)["log"]
    job.finish(True, "完成")
    d = mgr.get(job.job_id)
    assert d["success"] is True and d["done"] is True


def test_get_unknown():
    assert JobManager().get("nope") is None
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd V2M && python -m pytest tests/test_yoto_jobs.py -v`
Expected: FAIL (ModuleNotFoundError: yoto_jobs).

- [ ] **Step 3: Write `yoto_jobs.py`**

```python
"""In-memory Yoto upload job tracking (short-lived; no disk persistence)."""
from __future__ import annotations

import uuid
from typing import Optional


class UploadJob:
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.progress = 0
        self.status = "初始化"
        self.success: Optional[bool] = None
        self.error_message = ""
        self.log: list[str] = []
        self.done = False

    def update(self, progress: int, status: str):
        self.progress = progress
        self.status = status

    def add_log(self, msg: str):
        self.log.append(msg)
        print(f"[yoto:{self.job_id}] {msg}")

    def finish(self, success: bool, message: str):
        self.success = success
        self.done = True
        self.progress = 100 if success else self.progress
        self.status = message
        if not success:
            self.error_message = message

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "progress": self.progress,
            "status": self.status,
            "success": self.success,
            "error_message": self.error_message,
            "log": self.log[-15:],
            "done": self.done,
        }


class JobManager:
    def __init__(self):
        self.jobs: dict[str, UploadJob] = {}

    def create(self) -> UploadJob:
        job = UploadJob(uuid.uuid4().hex[:12])
        self.jobs[job.job_id] = job
        return job

    def get(self, job_id: str):
        job = self.jobs.get(job_id)
        return job.to_dict() if job else None


jobManager = JobManager()
```

- [ ] **Step 4: Write `yoto/pipeline.py`**

```python
"""Orchestrate: optimize a downloaded track -> upload -> append to playlist."""
from __future__ import annotations

import asyncio
from pathlib import Path

from . import audio
from .auth import authed_session
from .uploader import (build_appended_payload, create_content, fetch_card,
                       part_titles, upload_file)


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


async def run_upload(audio_path: str, filename: str, playlist_id: str, job) -> None:
    """Full async pipeline, updating `job` as it goes. Never raises — failures
    are recorded on the job."""
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

        async with authed_session() as (session, token):
            new_parts = []
            for i, part in enumerate(parts):
                job.update(20 + int(60 * i / len(parts)),
                           f"上传 {i + 1}/{len(parts)} …")
                info = await upload_file(session, token, part)
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
            detail = await fetch_card(session, token, playlist_id)
            payload = build_appended_payload(detail, new_parts)
            await create_content(session, token, payload)

        job.finish(True, f"完成：已添加 {len(parts)} 段到 playlist")
    except Exception as e:  # surfaced to the UI via job status
        job.finish(False, str(e))
```

- [ ] **Step 5: Add `authed_session` import note** — verify `yoto/pipeline.py` imports `authed_session` from `.auth` (already in Step 4). Run tests.

Run: `cd V2M && python -m pytest tests/test_yoto_jobs.py -v`
Expected: 2 passed.

- [ ] **Step 6: Smoke-import the pipeline** (catches syntax/import errors)

Run: `cd V2M && python -c "import yoto.pipeline, yoto_jobs; print('ok')"`
Expected: `ok`

- [ ] **Step 7: Commit**

```bash
git add yoto/pipeline.py yoto_jobs.py tests/test_yoto_jobs.py
git commit -m "feat(yoto): upload pipeline + in-memory job manager"
```

---

### Task 6: FastAPI endpoints

**Files:**
- Modify: `video2mp3.py` (imports near top; new routes after existing `/api/...` routes)
- Test: `tests/test_yoto_api.py`

**Interfaces:**
- Consumes: `taskManager.get_file_path`, `yoto_jobs.jobManager`, `yoto.pipeline.run_upload`, `yoto.uploader.list_playlists`, `yoto.auth.authed_session`.
- Produces routes: `GET /api/yoto/playlists`, `POST /api/yoto/upload`, `GET /api/yoto/upload-status/{job_id}`.

- [ ] **Step 1: Add imports to `video2mp3.py`** (after the existing `from taskmanager import ...` line)

```python
import asyncio
from yoto import pipeline as yoto_pipeline
from yoto.auth import authed_session
from yoto.uploader import list_playlists as yoto_list_playlists
from yoto_jobs import jobManager as yotoJobManager
```

- [ ] **Step 2: Add the request model + routes** (append before `@app.on_event("shutdown")`)

```python
class YotoUploadRequest(BaseModel):
    task_id: str
    filename: str
    playlist_id: str


@app.get("/api/yoto/playlists")
async def yoto_playlists():
    """List the user's Yoto playlists for the upload dropdown."""
    try:
        async with authed_session() as (session, token):
            items = await yoto_list_playlists(session, token)
        return {"playlists": items}
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


@app.post("/api/yoto/upload")
async def yoto_upload(req: YotoUploadRequest):
    """Start an optimize+upload+append job; return its job_id."""
    audio_path = taskManager.get_file_path(req.task_id, "audio")
    if not audio_path or not os.path.exists(audio_path):
        raise HTTPException(status_code=404, detail="音频文件不存在")
    filename = (req.filename or "").strip() or os.path.splitext(
        os.path.basename(audio_path))[0]
    if not req.playlist_id:
        raise HTTPException(status_code=400, detail="未选择 playlist")
    job = yotoJobManager.create()
    asyncio.create_task(
        yoto_pipeline.run_upload(audio_path, filename, req.playlist_id, job)
    )
    return {"job_id": job.job_id}


@app.get("/api/yoto/upload-status/{job_id}")
async def yoto_upload_status(job_id: str):
    status = yotoJobManager.get(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return status
```

- [ ] **Step 3: Write `tests/test_yoto_api.py`** (uses FastAPI TestClient; monkeypatches the pipeline so no network/ffmpeg runs)

```python
import os
from fastapi.testclient import TestClient
import video2mp3
from video2mp3 import app

client = TestClient(app)


def test_upload_missing_file_404(monkeypatch):
    monkeypatch.setattr(video2mp3.taskManager, "get_file_path",
                        lambda tid, ft: None)
    r = client.post("/api/yoto/upload",
                    json={"task_id": "x", "filename": "n", "playlist_id": "p"})
    assert r.status_code == 404


def test_upload_starts_job(monkeypatch, tmp_path):
    f = tmp_path / "audio.mp3"
    f.write_bytes(b"x")
    monkeypatch.setattr(video2mp3.taskManager, "get_file_path",
                        lambda tid, ft: str(f))

    async def fake_run(audio_path, filename, playlist_id, job):
        job.finish(True, "完成")
    monkeypatch.setattr(video2mp3.yoto_pipeline, "run_upload", fake_run)

    r = client.post("/api/yoto/upload",
                    json={"task_id": "t", "filename": "song", "playlist_id": "p"})
    assert r.status_code == 200
    jid = r.json()["job_id"]
    s = client.get(f"/api/yoto/upload-status/{jid}")
    assert s.status_code == 200


def test_status_unknown_404():
    assert client.get("/api/yoto/upload-status/nope").status_code == 404
```

- [ ] **Step 4: Run tests**

Run: `cd V2M && python -m pytest tests/test_yoto_api.py -v`
Expected: 3 passed. (If `httpx`/`starlette` TestClient dep is missing, install `httpx`.)

- [ ] **Step 5: Commit**

```bash
git add video2mp3.py tests/test_yoto_api.py
git commit -m "feat(api): Yoto playlists + upload + status endpoints"
```

---

### Task 7: UI — button + upload modal in scrape.html

**Files:**
- Modify: `templates/scrape.html` (add button in list item render; add modal markup; add JS)

**Interfaces:**
- Consumes: `GET /api/yoto/playlists`, `POST /api/yoto/upload`, `GET /api/yoto/upload-status/{job_id}`.

- [ ] **Step 1: Add the "⬆ Yoto" button** in the history-item action block near `templates/scrape.html:508-511` (the `t.has_audio` action buttons). Insert after the download-audio anchor:

```javascript
        ${t.has_audio ? `<button class="btn-s" onclick="openYotoModal('${t.task_id}', '${esc(t.audio_filename || '')}')" title="上传到 Yoto">⬆</button>` : ''}
```

Also add the same button to the active download list actions near `scrape.html:387-389` (after the audio download anchor):

```javascript
    if (s.has_audio) acts += `<button class="btn-s" onclick="openYotoModal('${id}', '${(s.audio_filename||'').replace(/'/g,"\\'")}')" title="上传到 Yoto">⬆ Yoto</button>`;
```

- [ ] **Step 2: Add modal markup** just before the closing `</body>` (alongside the other modals):

```html
<div id="yoto-modal" class="modal" style="display:none">
  <div class="lm-bd">
    <div class="lm-hd">上传到 Yoto</div>
    <label style="display:block;margin:10px 0 4px">文件名</label>
    <input id="yoto-filename" type="text" style="width:100%;padding:8px"/>
    <label style="display:block;margin:10px 0 4px">Playlist</label>
    <select id="yoto-playlist" style="width:100%;padding:8px"><option>加载中…</option></select>
    <div id="yoto-progress" style="margin-top:10px;font-size:13px;color:var(--fg2)"></div>
    <div style="margin-top:14px;display:flex;gap:8px;justify-content:flex-end">
      <button class="btn-s" onclick="closeYotoModal()">取消</button>
      <button id="yoto-go" class="btn-dl" onclick="submitYoto()">上传</button>
    </div>
  </div>
</div>
```

- [ ] **Step 3: Add JS** (in the main `<script>` block):

```javascript
let yotoTaskId = null, yotoPoll = null;

async function openYotoModal(taskId, filename) {
  yotoTaskId = taskId;
  document.getElementById('yoto-filename').value =
    (filename || '').replace(/\.[^.]+$/, '');
  document.getElementById('yoto-progress').textContent = '';
  document.getElementById('yoto-go').disabled = false;
  const sel = document.getElementById('yoto-playlist');
  sel.innerHTML = '<option>加载中…</option>';
  document.getElementById('yoto-modal').style.display = 'flex';
  try {
    const r = await fetch('/api/yoto/playlists');
    const d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || '加载失败');
    sel.innerHTML = d.playlists.map(p =>
      `<option value="${p.id}">${esc(p.title)} (${p.n_tracks})</option>`).join('');
  } catch (e) {
    sel.innerHTML = '';
    document.getElementById('yoto-progress').textContent = '无法加载 playlist：' + e.message;
  }
}

function closeYotoModal() {
  document.getElementById('yoto-modal').style.display = 'none';
  if (yotoPoll) { clearInterval(yotoPoll); yotoPoll = null; }
}

async function submitYoto() {
  const filename = document.getElementById('yoto-filename').value.trim();
  const playlist_id = document.getElementById('yoto-playlist').value;
  if (!playlist_id) { showToast('请选择 playlist', 'er'); return; }
  document.getElementById('yoto-go').disabled = true;
  const prog = document.getElementById('yoto-progress');
  prog.textContent = '提交中…';
  try {
    const r = await fetch('/api/yoto/upload', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({task_id: yotoTaskId, filename, playlist_id}),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || '提交失败');
    yotoPoll = setInterval(() => pollYoto(d.job_id), 1200);
  } catch (e) {
    prog.textContent = '错误：' + e.message;
    document.getElementById('yoto-go').disabled = false;
  }
}

async function pollYoto(jobId) {
  try {
    const r = await fetch('/api/yoto/upload-status/' + jobId);
    const s = await r.json();
    document.getElementById('yoto-progress').textContent =
      `${s.progress}% · ${s.status}`;
    if (s.done) {
      clearInterval(yotoPoll); yotoPoll = null;
      if (s.success) { showToast('已上传到 Yoto', 'ok'); closeYotoModal(); }
      else { showToast('上传失败：' + s.status, 'er');
             document.getElementById('yoto-go').disabled = false; }
    }
  } catch (e) { /* keep polling */ }
}
```

- [ ] **Step 4: Manual UI check** — confirm the CSS classes used (`modal`, `lm-bd`, `lm-hd`, `btn-s`, `btn-dl`, `showToast`, `esc`) exist in `scrape.html`. Grep:

Run: `cd V2M && grep -nE "class=\"modal\"|lm-bd|function esc|function showToast" templates/scrape.html | head`
Expected: matches for each (adjust the modal wrapper class names in Steps 2–3 to whatever the existing link/download modals use if they differ).

- [ ] **Step 5: Commit**

```bash
git add templates/scrape.html
git commit -m "feat(ui): Yoto upload button + modal"
```

---

### Task 8: Credentials seeding, deps, gitignore, dry-run E2E

**Files:**
- Create: `V2M/.yoto/.env`, `V2M/.yoto/.yoto_token.json` (copied, NOT committed)
- Modify: `V2M/.gitignore`
- Create: `V2M/requirements.txt` (if absent) — add `aiohttp`
- Create: `scripts/yoto_dryrun.py` (manual E2E without card mutation)

- [ ] **Step 1: Seed credentials** from the sibling repo

```bash
cd V2M
mkdir -p .yoto
cp ../../yoto-update/.env .yoto/.env
cp ../../yoto-update/.yoto_token.json .yoto/.yoto_token.json
chmod 600 .yoto/.yoto_token.json
```

- [ ] **Step 2: Gitignore secrets + work artifacts** — append to `V2M/.gitignore`:

```
.yoto/
data/*/yoto/
```

- [ ] **Step 3: Ensure `requirements.txt` lists aiohttp** — create/append `V2M/requirements.txt`:

```
fastapi
uvicorn
aiohttp
pydantic
```

- [ ] **Step 4: Write `scripts/yoto_dryrun.py`** — optimize + build payload, print, NO upload/POST

```python
"""Dry-run: optimize a local audio file and print the append payload that
WOULD be POSTed, plus playlist list. No upload, no card mutation.

Usage: python scripts/yoto_dryrun.py <audio_file> <playlist_id>
"""
import asyncio
import json
import sys
from pathlib import Path

from yoto import pipeline
from yoto.auth import authed_session
from yoto.uploader import build_appended_payload, fetch_card, list_playlists, part_titles


async def main(audio_file: str, playlist_id: str) -> int:
    src = Path(audio_file)
    parts = pipeline.optimize_track(src, src.parent / "yoto")
    titles = part_titles(src.stem, len(parts))
    print(f"optimized into {len(parts)} part(s): {[p.name for p in parts]}")
    fake_parts = [{"title": titles[i], "trackUrl": "yoto:#DRYRUN",
                   "duration": 0, "fileSize": 0, "channels": 2, "format": "mp3"}
                  for i in range(len(parts))]
    async with authed_session() as (session, token):
        pls = await list_playlists(session, token)
        print("playlists:", json.dumps(pls, ensure_ascii=False, indent=2))
        detail = await fetch_card(session, token, playlist_id)
    payload = build_appended_payload(detail, fake_parts)
    print("would POST /content payload (truncated chapters):")
    payload["content"]["chapters"] = payload["content"]["chapters"][-3:]
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit("usage: yoto_dryrun.py <audio_file> <playlist_id>")
    sys.exit(asyncio.run(main(sys.argv[1], sys.argv[2])))
```

- [ ] **Step 5: Run the full unit suite**

Run: `cd V2M && python -m pytest tests/ -v`
Expected: all pass.

- [ ] **Step 6: Run dry-run against a real playlist id** (pick one from `GET /api/yoto/playlists` or `list_playlists`)

Run: `cd V2M && python scripts/yoto_dryrun.py data/<some_task>/<audio>.mp3 <playlist_id>`
Expected: prints part list, playlists, and an append payload whose last chapter is the new track with continued numbering.

- [ ] **Step 7: Real end-to-end** — start the app, use the UI to upload one short track to a **throwaway** playlist, confirm it appears in the Yoto app.

Run: `cd V2M && bash restart.sh` (then browse to the app, click ⬆ Yoto)
Expected: job reaches 100% 完成; new chapter visible in the Yoto library.

- [ ] **Step 8: Commit** (config files excluded by gitignore)

```bash
git add .gitignore requirements.txt scripts/yoto_dryrun.py
git commit -m "chore(yoto): deps, gitignore, dry-run E2E script"
```

---

## Self-Review

**Spec coverage:**
- Cap bitrate → Task 3 (`target_kbps`), applied in Task 5 (`optimize_track`). ✓
- Cap length / split → Task 3 (`plan_cuts`/`detect_silence_mids`), Task 5. ✓
- Slim vendored `yoto/` package → Tasks 1–5. ✓
- Filename default = track name → Task 6 (`/api/yoto/upload` fallback) + Task 7 (modal prefill, extension stripped). ✓
- Select playlist from list → Task 4 (`list_playlists`), Task 6 (route), Task 7 (dropdown). ✓
- Append to existing playlist → Task 4 (`build_appended_payload`), Task 5. ✓
- Per-item button entry point → Task 7. ✓
- Resumable sha cache / work dir → Task 4 (`upload_file`), Task 5 (`optimize_track`). ✓
- Credentials in `.yoto/` + gitignore → Task 8. ✓
- Error handling (auth/ffmpeg/upload/fetch) → Task 5 (`run_upload` try/except → job), Task 6 (routes). ✓
- Tests (unit pure fns + dry-run E2E) → Tasks 1–6 unit, Task 8 dry-run/E2E. ✓

**Placeholder scan:** No TBD/TODO; all code blocks complete. ✓

**Type consistency:** `part_titles`, `build_appended_payload`, `upload_file` result keys (`sha/duration/fileSize/channels/format`), `UploadJob.update/add_log/finish/to_dict`, `jobManager.create/get` are consistent across Tasks 4–7. `optimize_track`/`run_upload` signatures match Task 5 and their callers in Tasks 6/8. ✓

## Execution notes
- `tests/` may need an `__init__.py`-free layout; run pytest from `V2M/` so `yoto` + `video2mp3` import as top-level modules. Add `V2M/conftest.py` (empty) if import paths misbehave.
- Yoto tracks preserved verbatim from `fetch_card` should round-trip through `POST /content`; the dry-run (Task 8 Step 6) verifies payload shape before any real mutation.
