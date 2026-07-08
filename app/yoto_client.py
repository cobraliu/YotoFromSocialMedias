"""Core Yoto REST client for YotoFromSocialMedias — one file that owns the whole
write path against the Yoto API: OAuth (Authorization Code + PKCE) login, per-user
token storage + lazy refresh, media upload/transcode, and playlist create/append.

────────────────────────────────────────────────────────────────────────────────
FORK NOTICE
────────────────────────────────────────────────────────────────────────────────
This project forks https://github.com/cdnninja/yoto_api (vendored as a git
submodule at ``vendor/yoto_api``; our fork lives at
https://github.com/cobraliu/yoto_api).

Upstream ``yoto_api`` is a *read / playback* library: it covers device-code auth,
reading the library, player state and MQTT control. It has **no** content upload,
no playlist create/append, no Authorization-Code + PKCE web login, and no icon
management — everything this app needs to push social-media audio into Yoto cards.

So the whole write path below is our own code. The *one* thing we take from the
fork is its ``Token`` dataclass: we load ``vendor/yoto_api/yoto_api/Token.py``
directly by file path (to avoid pulling the fork's ``aiohttp``/``aiomqtt`` package
init just for a dataclass) and re-export ``Token`` here. Loading it from the
submodule keeps us honest — if upstream changes the token shape, we inherit it.

This module consolidates what used to be the ``yoto/`` package's ``endpoints``,
``config``, ``auth``, ``login`` and ``uploader`` submodules. Icons, audio and the
upload pipeline stay in their own sibling modules (``yoto_icons``, ``yoto_audio``,
``yoto_pipeline``).
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import copy
import datetime
import hashlib
import importlib.util
import json
import os
import secrets
import urllib.parse
from pathlib import Path
from typing import Optional

import aiohttp

from app.yoto_audio import readable_duration


# ─── Token model (loaded directly from the cdnninja/yoto_api fork submodule) ──
#
# `from yoto_api import Token` would execute the package __init__ and import its
# MQTT/aiohttp client stack; we only want the dataclass, so load the module file
# directly. Falls back to an identical local definition if the submodule hasn't
# been checked out (git submodule update --init).

# This module lives at <repo>/app/yoto_client.py; the submodule, state dir and
# data dir all sit at the repo root, one level up.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOKEN_PY = os.path.join(_REPO_ROOT, "vendor", "yoto_api", "yoto_api", "Token.py")


def _load_fork_token():
    spec = importlib.util.spec_from_file_location("yoto_fork_token", _TOKEN_PY)
    if spec is None or spec.loader is None:
        raise ImportError(_TOKEN_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Token


try:
    Token = _load_fork_token()
except (ImportError, FileNotFoundError, OSError):
    # Submodule not initialised — mirror upstream's dataclass so the app still
    # runs. Keep in sync with vendor/yoto_api/yoto_api/Token.py.
    from dataclasses import dataclass, field

    @dataclass
    class Token:  # type: ignore[no-redef]
        access_token: Optional[str] = field(default=None, repr=False)
        refresh_token: Optional[str] = field(default=None, repr=False)
        id_token: Optional[str] = field(default=None, repr=False)
        scope: Optional[str] = None
        valid_until: datetime.datetime = datetime.datetime.min
        token_type: Optional[str] = None


# ─── endpoints (URL constants for the Yoto REST API) ──────────────────────────

BASE_URL = "https://api.yotoplay.com"
TOKEN_URL = "https://login.yotoplay.com/oauth/token"
AUTHORIZE_URL = "https://login.yotoplay.com/authorize"
CARDS_LIBRARY = "/card/family/library"
DISPLAY_ICONS_YOTO = "/media/displayIcons/user/yoto"
DISPLAY_ICONS_ME = "/media/displayIcons/user/me"
DISPLAY_ICONS_UPLOAD = "/media/displayIcons/user/me/upload"
YOTOICONS_BASE = "https://www.yotoicons.com"


# ─── config: portable per-user config + token cache ───────────────────────────
#
# STATE_DIR (default <app>/.yoto/, override with YOTO_STATE_DIR) holds per user:
#     users/<uid>/.env              -> client_id ("client_id: XXX" or "client_id=XXX")
#     users/<uid>/.yoto_token.json  -> cached OAuth token

# yoto_client.py lives at the app root; default state dir is <app>/.yoto/
_DEFAULT_STATE_DIR = Path(_REPO_ROOT) / ".yoto"


def state_dir() -> Path:
    return Path(os.environ.get("YOTO_STATE_DIR", str(_DEFAULT_STATE_DIR))).resolve()


def user_dir(uid: str) -> Path:
    """Per-user Yoto state dir: <state_dir>/users/<uid>/."""
    return state_dir() / "users" / uid


def _base(uid: Optional[str]) -> Path:
    """Legacy flat layout when uid is None, else the user's dir."""
    return user_dir(uid) if uid else state_dir()


def env_path(uid: Optional[str] = None) -> Path:
    return _base(uid) / ".env"


def token_path(uid: Optional[str] = None) -> Path:
    return _base(uid) / ".yoto_token.json"


def pkce_path(uid: Optional[str] = None) -> Path:
    return _base(uid) / ".yoto_pkce.json"


def yoto_icons_path(uid: Optional[str] = None) -> Path:
    return _base(uid) / "yoto.icons.json"


def me_icons_path(uid: Optional[str] = None) -> Path:
    return _base(uid) / "me.icons.json"


def yotoicons_cache_path(uid: Optional[str] = None) -> Path:
    return _base(uid) / "yotoicons.cache.json"


def data_dir() -> Path:
    """Downloaded-media root (shared with the rest of the app). Honors
    V2M_DATA_DIR, else <app>/data."""
    d = os.environ.get("V2M_DATA_DIR")
    if not d:
        d = str(Path(_REPO_ROOT) / "data")
    return Path(d).resolve()


def icons_cache_dir() -> Path:
    """Where downloaded icon image files (PNG) are cached, under data/."""
    return data_dir() / "yoto_icons"


def load_client_id(uid: Optional[str] = None) -> str:
    p = env_path(uid)
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


def save_token(token: Token, uid: Optional[str] = None) -> None:
    data = {
        "access_token": token.access_token,
        "refresh_token": token.refresh_token,
        "token_type": token.token_type,
        "scope": token.scope,
        "valid_until": token.valid_until.isoformat() if token.valid_until else None,
    }
    p = token_path(uid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))
    os.chmod(p, 0o600)


def load_token(uid: Optional[str] = None) -> Optional[Token]:
    p = token_path(uid)
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


# ─── auth: authenticated aiohttp session with lazy per-request refresh ────────
#
# Mirrors the token-refresh idea from cdnninja/yoto_api's check_and_refresh_token
# (see FORK NOTICE): rather than grabbing one access token for the whole
# operation, callers resolve a *token provider* before each request. The provider
# refreshes when within _REFRESH_MARGIN of expiry and always persists the rotated
# token (Yoto refresh tokens are single-use). Concurrent resolves are serialised
# by a lock so a burst of parallel requests triggers at most one refresh.

SCOPES = "family:library:view family:library:manage user:content:manage user:icons:manage offline_access"
# Refresh when this close to expiry. Comfortably larger than any single request
# (the transcode poll runs up to ~90s), so a token resolved for a request never
# expires mid-request.
_REFRESH_MARGIN = datetime.timedelta(minutes=10)


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
        TOKEN_URL, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    ) as resp:
        body = await resp.json(content_type=None)
        if not resp.ok or body.get("error"):
            raise RuntimeError(f"[auth] refresh failed: {resp.status} {body}")
        if not body.get("refresh_token"):
            body["refresh_token"] = token.refresh_token
        return _build_token_from_body(body, token.scope or SCOPES)


def _needs_refresh(token: Token, now: datetime.datetime) -> bool:
    """True if the access token is missing or within the refresh margin."""
    if token.access_token is None:
        return True
    vu = token.valid_until
    if vu is None or vu == datetime.datetime.min:
        return True
    if vu.tzinfo is None:
        vu = vu.replace(tzinfo=datetime.timezone.utc)
    return vu - _REFRESH_MARGIN <= now


class TokenProvider:
    """Callable holder yielding a fresh access token, refreshing on demand."""

    def __init__(self, session: aiohttp.ClientSession, client_id: str, token: Token,
                 uid: str | None = None):
        self._session = session
        self._client_id = client_id
        self._token = token
        self._uid = uid
        self._lock = asyncio.Lock()

    async def __call__(self) -> str:
        async with self._lock:
            now = datetime.datetime.now(datetime.timezone.utc)
            if _needs_refresh(self._token, now):
                self._token = await _refresh(self._session, self._client_id, self._token)
                save_token(self._token, self._uid)
            return self._token.access_token


@contextlib.asynccontextmanager
async def authed_session(uid: str | None = None):
    """Yield (session, get_token) for a specific user. `get_token` is an async
    callable returning a valid access token; call it before each request. Raises
    RuntimeError with a user-facing message if the user is unbound/untokened."""
    client_id = load_client_id(uid)
    token = load_token(uid)
    if token is None or not token.refresh_token:
        raise RuntimeError("请先绑定并登录 Yoto 账号。")

    async with aiohttp.ClientSession() as session:
        yield session, TokenProvider(session, client_id, token, uid)


# ─── login: Yoto OAuth (Authorization Code + PKCE), paste-callback style ───────
#
# Flow:
#   1. authorize_url(uid) -> URL; user opens it, logs in at Yoto, is redirected
#      to REDIRECT_URI with ?code=...&state=...
#   2. user pastes the code (or the whole callback URL) back;
#   3. exchange(uid, code_or_url) swaps it for a token and caches it per user.
#
# This whole PKCE web-login flow does not exist in upstream yoto_api (which only
# offers device-code auth) — see FORK NOTICE.

AUDIENCE = BASE_URL
REDIRECT_URI = "http://127.0.0.1:8787/callback"


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def _extract_code(arg: str) -> tuple[str, str | None]:
    arg = (arg or "").strip()
    if arg.startswith("http"):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(arg).query)
        return q.get("code", [""])[0], q.get("state", [None])[0]
    return arg, None


def _decode_scopes(access_token: str) -> list[str]:
    try:
        p = access_token.split(".")[1]
        p += "=" * (-len(p) % 4)
        payload = json.loads(base64.urlsafe_b64decode(p))
        return sorted((payload.get("scope") or "").split())
    except Exception:
        return []


def authorize_url(uid: str) -> str:
    client_id = load_client_id(uid)
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    pkce = pkce_path(uid)
    pkce.parent.mkdir(parents=True, exist_ok=True)
    pkce.write_text(json.dumps({"verifier": verifier, "state": state}))
    params = {
        "audience": AUDIENCE,
        "scope": SCOPES,
        "response_type": "code",
        "client_id": client_id,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "redirect_uri": REDIRECT_URI,
        "state": state,
    }
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)


async def exchange(uid: str, code_or_url: str):
    client_id = load_client_id(uid)
    code, _state = _extract_code(code_or_url)
    if not code:
        raise RuntimeError("回调里没有找到 code")
    saved = json.loads(pkce_path(uid).read_text())
    data = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": saved["verifier"],
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            TOKEN_URL, data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ) as r:
            body = await r.json(content_type=None)
            if not r.ok or body.get("error"):
                raise RuntimeError(f"token 交换失败: {body}")
            token = _build_token_from_body(body, SCOPES)
    save_token(token, uid)
    pkce_path(uid).unlink(missing_ok=True)
    return token


# ─── uploader: media upload + playlist create/append ──────────────────────────
#
# Network functions take a `get_token` async callable (the TokenProvider from
# authed_session) and resolve a fresh access token immediately before each
# request, so a long multi-part upload never uses an expired token. None of this
# (upload/transcode, POST /content create/append) exists upstream — see FORK NOTICE.


def part_titles(base: str, n_parts: int) -> list[str]:
    if n_parts <= 1:
        return [base]
    return [f"{base}-{i + 1}" for i in range(n_parts)]


def normalize_icon(val) -> str | None:
    """Coerce a display icon to the POST write format `yoto:#<43-char mediaId>`.

    GET /card returns icons as signed display URLs whose last path segment is
    the 43-char mediaId; POST /content only accepts `yoto:#<id>`. We emit an
    icon ONLY when we can produce that exact 43-char form from a value already
    present on the live card — otherwise return None so the caller drops it,
    rather than guessing an id that might not exist ("mediaId not found")."""
    if not isinstance(val, str) or not val:
        return None
    if val.startswith("yoto:#"):
        mid = val[len("yoto:#"):]
        return val if len(mid) == 43 else None
    if val.startswith("yoto:"):
        return None
    seg = val.split("?")[0].rstrip("/").split("/")[-1]
    return f"yoto:#{seg}" if len(seg) == 43 else None


def _sanitize_display(obj: dict) -> None:
    """In place: normalize obj['display']['icon16x16'], dropping it (and an
    empty display) when it can't be coerced to a valid write-format ref."""
    disp = obj.get("display")
    if not isinstance(disp, dict) or "icon16x16" not in disp:
        return
    norm = normalize_icon(disp.get("icon16x16"))
    if norm:
        disp["icon16x16"] = norm
    else:
        disp.pop("icon16x16", None)
        if not disp:
            obj.pop("display", None)


def _next_base_seq(chapters: list[dict]) -> int:
    """Highest existing chapter number, robust to non-integer keys."""
    mx = len(chapters)
    for ch in chapters:
        try:
            mx = max(mx, int(ch.get("key")))
        except (TypeError, ValueError):
            pass
    return mx


def build_appended_payload(card_detail: dict, new_parts: list[dict],
                           icon_ref: str | None = None) -> dict:
    """Append new_parts (each: title, trackUrl, duration, fileSize, channels,
    format) as chapters to an existing card; return a POST /content payload that
    updates the card in place (cardId included)."""
    card = card_detail["card"]
    card_id = card.get("cardId")
    content = card.get("content", {}) or {}
    meta = card.get("metadata", {}) or {}
    title = card.get("title")
    # Deep-copy existing chapters so we can normalize their icons without
    # mutating the caller's fetched card.
    chapters = copy.deepcopy(list(content.get("chapters", []) or []))
    cover = (content.get("cover") or {}).get("imageL") or (meta.get("cover") or {}).get("imageL")

    # Existing chapters come from GET /card with display-URL icons that POST
    # rejects; coerce them to the write format (or drop) so the update passes
    # while preserving icons that are valid.
    for ch in chapters:
        _sanitize_display(ch)
        for tr in ch.get("tracks", []) or []:
            _sanitize_display(tr)

    # Optional user-chosen icon for the appended chapters. Validated to the
    # write format; an unusable value is dropped (chapter uploads icon-free)
    # rather than risking a 400 / nonexistent mediaId.
    icon = normalize_icon(icon_ref) if icon_ref else None

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
        if icon:
            track["display"] = {"icon16x16": icon}
        chapter = {
            "key": f"{seq:02d}",
            "overlayLabel": str(seq),
            "title": part["title"],
            "tracks": [track],
        }
        if icon:
            chapter["display"] = {"icon16x16": icon}
        chapters.append(chapter)

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


def extract_playlist_tracks(card_detail: dict) -> dict:
    """Flatten a fetched card into a view model for the playlist page:
    {id, title, duration, chapters:[{key, title, icon(url), duration,
    tracks:[{title, duration, format}]}]}. Icons are the card's signed display
    URLs (directly loadable by the browser); missing icons become ""."""
    card = card_detail.get("card", card_detail) if isinstance(card_detail, dict) else {}
    content = card.get("content", {}) or {}
    chapters_out = []
    for ch in content.get("chapters", []) or []:
        icon = ((ch.get("display") or {}).get("icon16x16")) or ""
        tracks = [
            {
                "title": tr.get("title"),
                "duration": tr.get("duration") or 0,
                "format": tr.get("format"),
            }
            for tr in (ch.get("tracks") or [])
        ]
        chapters_out.append({
            "key": ch.get("key"),
            "title": ch.get("title"),
            "icon": icon if isinstance(icon, str) else "",
            "duration": ch.get("duration") or 0,
            "tracks": tracks,
        })
    media = (card.get("metadata", {}) or {}).get("media", {}) or {}
    return {
        "id": card.get("cardId"),
        "title": card.get("title"),
        "duration": media.get("duration") or 0,
        "chapters": chapters_out,
    }


def build_new_playlist_payload(title: str) -> dict:
    """A minimal POST /content payload that creates a brand-new empty playlist
    (no cardId → Yoto mints one). Chapters get appended later on upload."""
    title = (title or "").strip()
    if not title:
        raise ValueError("playlist 名称不能为空")
    return {
        "title": title,
        "content": {
            "playbackType": "linear",
            "config": {"autoadvance": "next", "onlineOnly": False, "shuffle": []},
            "cover": {},
            "chapters": [],
        },
        "metadata": {
            "author": "",
            "cover": {},
            "media": {
                "duration": 0,
                "readableDuration": readable_duration(0),
                "fileSize": 0,
                "readableFileSize": "0.0MB",
            },
        },
    }


async def list_playlists(session, get_token) -> list[dict]:
    async with session.get(
        BASE_URL + CARDS_LIBRARY,
        headers=auth_headers(await get_token()),
    ) as r:
        txt = await r.text()
        if not r.ok:
            raise RuntimeError(f"library -> {r.status}: {txt[:200]}")
        cards = json.loads(txt).get("cards", []) or []
    out = []
    for item in cards:
        card = item.get("card", {}) or {}
        cid = item.get("cardId") or card.get("cardId")
        if not cid:
            continue
        title = card.get("title") or item.get("title") or cid
        # The library summary omits content.chapters, so count tracks when
        # present but fall back to the always-available media duration.
        chapters = (card.get("content", {}) or {}).get("chapters", []) or []
        n = sum(len(ch.get("tracks", []) or []) for ch in chapters)
        duration = ((card.get("metadata", {}) or {}).get("media", {}) or {}).get("duration") or 0
        out.append({"id": cid, "title": title, "n_tracks": n, "duration": duration})
    return out


async def fetch_card(session, get_token, card_id: str) -> dict:
    async with session.get(
        BASE_URL + f"/card/{card_id}",
        headers=auth_headers(await get_token()),
    ) as r:
        txt = await r.text()
        if not r.ok:
            raise RuntimeError(f"GET /card/{card_id} -> {r.status}: {txt[:200]}")
        return json.loads(txt)


async def _get_upload_url(session, get_token) -> tuple[str, str]:
    async with session.get(
        BASE_URL + "/media/transcode/audio/uploadUrl",
        headers=auth_headers(await get_token()),
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


async def _poll_transcode(session, get_token, upload_id: str,
                          tries: int = 90, delay: float = 1.0) -> dict:
    url = BASE_URL + f"/media/upload/{upload_id}/transcoded?loudnorm=false"
    for _ in range(tries):
        async with session.get(url, headers=auth_headers(await get_token())) as r:
            if r.ok:
                tc = (json.loads(await r.text())).get("transcode", {})
                if tc.get("transcodedSha256"):
                    return tc
        await asyncio.sleep(delay)
    raise TimeoutError(f"transcode timed out for upload {upload_id}")


async def upload_file(session, get_token, path: Path) -> dict:
    """Upload + transcode one mp3; return {sha,duration,fileSize,channels,format}.
    A sibling .sha.json short-circuits re-uploads on resume."""
    cache = path.with_suffix(path.suffix + ".sha.json")
    if cache.exists():
        return json.loads(cache.read_text())
    upload_url, upload_id = await _get_upload_url(session, get_token)
    await _put_upload(session, upload_url, path)
    tc = await _poll_transcode(session, get_token, upload_id)
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


async def create_playlist(session, get_token, title: str) -> dict:
    """Create an empty playlist; return a list-item dict {id,title,n_tracks,duration}
    matching list_playlists so the UI can drop it straight into the dropdown."""
    payload = build_new_playlist_payload(title)
    resp = await create_content(session, get_token, payload)
    card = resp.get("card", resp) if isinstance(resp, dict) else {}
    card_id = card.get("cardId") or resp.get("cardId")
    if not card_id:
        raise RuntimeError(f"创建 playlist 失败：响应缺少 cardId ({str(resp)[:200]})")
    return {"id": card_id, "title": payload["title"], "n_tracks": 0, "duration": 0}


async def create_content(session, get_token, payload: dict) -> dict:
    async with session.post(
        BASE_URL + "/content",
        headers={**auth_headers(await get_token()), "Content-Type": "application/json"},
        json=payload,
    ) as r:
        txt = await r.text()
        if not r.ok:
            raise RuntimeError(f"POST /content -> {r.status}: {txt[:400]}")
        return json.loads(txt)
