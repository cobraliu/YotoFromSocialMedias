"""Local Yoto icon catalog + yotoicons.com acquisition.

Two JSON files under the state dir hold the icon lists fetched from Yoto:
    yoto.icons.json  -> built-in icons (GET /media/displayIcons/user/yoto)
    me.icons.json    -> the user's uploaded icons (GET .../user/me)
The picker reads these locally; network helpers upload new icons and keep
me.icons.json current. Every network helper resolves a fresh token per request.

Icon management is entirely our own — upstream cdnninja/yoto_api has no icon
support (see yoto_client.py FORK NOTICE). Config paths, endpoints and auth
headers live in yoto_client.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import app.yoto_client as config

_THUMB_RE = re.compile(r"/static/uploads/(\d+)\.png")


# ─── pure helpers (unit-tested) ───────────────────────────────────────


def read_catalog(path) -> list[dict]:
    """Return the displayIcons list from a catalog file; [] if missing/bad."""
    try:
        data = json.loads(Path(path).read_text())
    except (FileNotFoundError, ValueError):
        return []
    return data.get("displayIcons", []) or []


def to_entry(raw: dict, source: str) -> dict:
    mid = raw.get("mediaId", "")
    return {
        "mediaId": mid,
        "ref": f"yoto:#{mid}",
        "source": source,
        "title": raw.get("title"),
        "tags": raw.get("publicTags", []) or [],
        "url": raw.get("url", ""),
        "createdAt": raw.get("createdAt", ""),
    }


def load_yoto(uid: str | None = None) -> list[dict]:
    return [to_entry(r, "yoto") for r in read_catalog(config.yoto_icons_path(uid))]


def load_me(uid: str | None = None) -> list[dict]:
    return [to_entry(r, "me") for r in read_catalog(config.me_icons_path(uid))]


def search_yoto(entries: list[dict], q: str) -> list[dict]:
    q = (q or "").strip().lower()
    if not q:
        return entries
    out = []
    for e in entries:
        hay = " ".join([e.get("title") or ""] + (e.get("tags") or [])).lower()
        if q in hay:
            out.append(e)
    return out


def list_me(entries: list[dict]) -> list[dict]:
    return sorted(entries, key=lambda e: e.get("createdAt", ""), reverse=True)


def parse_yotoicons(html: str) -> list[dict]:
    seen, out = set(), []
    for m in _THUMB_RE.finditer(html):
        iid = m.group(1)
        if iid in seen:
            continue
        seen.add(iid)
        out.append({"id": iid, "thumb": f"/static/uploads/{iid}.png"})
    return out


def append_me(path, raw: dict) -> None:
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        data = {"displayIcons": []}
    lst = data.setdefault("displayIcons", [])
    if any(r.get("mediaId") == raw.get("mediaId") for r in lst):
        return
    lst.append(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


# ─── network helpers (token resolved per request) ─────────────────────


async def upload_custom_icon(session, get_token, data: bytes, filename: str) -> dict:
    async with session.post(
        config.BASE_URL + config.DISPLAY_ICONS_UPLOAD,
        params={"autoConvert": "true", "filename": filename},
        data=data,
        headers={**config.auth_headers(await get_token()), "Content-Type": "image/png"},
    ) as r:
        txt = await r.text()
        if not r.ok:
            raise RuntimeError(f"icon upload -> {r.status}: {txt[:200]}")
        return json.loads(txt).get("displayIcon", {}) or {}


def _read_import_cache(uid: str | None = None) -> dict:
    """Map yotoicons id -> the displayIcon record we already uploaded for it."""
    try:
        return json.loads(config.yotoicons_cache_path(uid).read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _write_import_cache(cache: dict, uid: str | None = None) -> None:
    p = config.yotoicons_cache_path(uid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cache, indent=2))


def _icon_file(name: str):
    return config.icons_cache_dir() / f"{name}.png"


async def _download_yotoicon(session, icon_id: str) -> bytes:
    """Fetch a yotoicons PNG, caching the file under data/yoto_icons/ so the
    same icon is never downloaded twice."""
    path = _icon_file(f"yotoicon-{icon_id}")
    if path.exists() and path.stat().st_size > 0:
        return path.read_bytes()
    url = f"{config.YOTOICONS_BASE}/uploads/{icon_id}.png"
    async with session.get(url) as r:
        if not r.ok:
            raise RuntimeError(f"yotoicons download -> {r.status}")
        data = await r.read()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


async def import_yotoicon(session, get_token, icon_id: str,
                          uid: str | None = None) -> dict:
    # Re-importing the same yotoicon is a no-op: return the cached upload
    # instead of downloading + re-uploading it.
    cache = _read_import_cache(uid)
    if icon_id in cache:
        return to_entry(cache[icon_id], "me")
    data = await _download_yotoicon(session, icon_id)
    raw = await upload_custom_icon(session, get_token, data, f"yotoicon-{icon_id}")
    cache[icon_id] = raw
    _write_import_cache(cache, uid)
    append_me(config.me_icons_path(uid), raw)
    return to_entry(raw, "me")


async def search_yotoicons(session, q: str) -> list[dict]:
    url = f"{config.YOTOICONS_BASE}/icons"
    async with session.get(
        url, params={"tag": q, "sort": "popular", "type": "singles"}
    ) as r:
        if not r.ok:
            raise RuntimeError(f"yotoicons search -> {r.status}")
        return parse_yotoicons(await r.text())


async def fetch_icon_bytes(session, get_token, url: str) -> tuple[bytes, str]:
    async with session.get(url, headers=config.auth_headers(await get_token())) as r:
        if not r.ok:
            raise RuntimeError(f"icon fetch -> {r.status}")
        return await r.read(), r.headers.get("Content-Type", "image/png")


async def cached_icon_bytes(session, get_token, media_id: str,
                            url: str = "") -> tuple[bytes, str]:
    """Return a Yoto/me icon's PNG, caching the file under data/yoto_icons/
    keyed by mediaId so the picker grid serves repeats from disk."""
    if media_id:
        path = _icon_file(media_id)
        if path.exists() and path.stat().st_size > 0:
            return path.read_bytes(), "image/png"
    target = url or f"https://media-secure-v2.api.yotoplay.com/icons/{media_id}"
    data, ctype = await fetch_icon_bytes(session, get_token, target)
    if media_id:
        path = _icon_file(media_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return data, ctype


async def _get_list(session, get_token, path: str) -> list[dict]:
    async with session.get(
        config.BASE_URL + path, headers=config.auth_headers(await get_token())
    ) as r:
        txt = await r.text()
        if not r.ok:
            raise RuntimeError(f"GET {path} -> {r.status}: {txt[:200]}")
        return json.loads(txt).get("displayIcons", []) or []


async def refresh_from_api(session, get_token, uid: str | None = None) -> dict:
    y = await _get_list(session, get_token, config.DISPLAY_ICONS_YOTO)
    m = await _get_list(session, get_token, config.DISPLAY_ICONS_ME)
    yp, mp = config.yoto_icons_path(uid), config.me_icons_path(uid)
    yp.parent.mkdir(parents=True, exist_ok=True)
    yp.write_text(json.dumps({"displayIcons": y}, indent=2))
    mp.write_text(json.dumps({"displayIcons": m}, indent=2))
    return {"yoto": len(y), "me": len(m)}
