# Yoto Icon Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user attach a display icon to the chapter(s) appended when uploading a track to Yoto, chosen from a local catalog (Yoto built-ins + user uploads) or freshly imported from yotoicons.com.

**Architecture:** New `yoto/icons.py` owns two local JSON catalogs under `.yoto/`; pure search/parse helpers plus thin aiohttp network helpers (upload/import/refresh) following the package's `get_token` per-request pattern. `build_appended_payload` gains an optional `icon_ref`. New FastAPI routes back an icon-picker block in the upload modal.

**Tech Stack:** FastAPI, aiohttp, pytest + httpx TestClient, vanilla JS.

## Global Constraints

- Package pattern: every network function takes `(session, get_token)` and calls `auth_headers(await get_token())` immediately before the request.
- Icon ref format is `yoto:#<43-char mediaId>`; validate via existing `uploader.normalize_icon`.
- No-icon is the default: when no icon chosen, appended chapters carry no `display` block (unchanged behavior).
- Run tests from `V2M/tests/` with `python -m pytest -q`.
- All new state files live under `config.state_dir()` (honors `YOTO_STATE_DIR`).

---

### Task 1: Endpoints, config paths, and upload scope

**Files:**
- Modify: `yoto/endpoints.py`
- Modify: `yoto/config.py`
- Modify: `yoto/auth.py:21`
- Test: `tests/test_yoto_config.py`

**Interfaces:**
- Produces: `endpoints.DISPLAY_ICONS_YOTO`, `endpoints.DISPLAY_ICONS_ME`, `endpoints.DISPLAY_ICONS_UPLOAD`, `endpoints.YOTOICONS_BASE`; `config.yoto_icons_path()`, `config.me_icons_path()`.

- [ ] **Step 1: Write failing test** (append to `tests/test_yoto_config.py`)

```python
def test_icon_catalog_paths(monkeypatch, tmp_path):
    import importlib, yoto.config as config
    monkeypatch.setenv("YOTO_STATE_DIR", str(tmp_path))
    importlib.reload(config)
    assert config.yoto_icons_path() == tmp_path / "yoto.icons.json"
    assert config.me_icons_path() == tmp_path / "me.icons.json"
    importlib.reload(config)  # restore default env for other tests
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_yoto_config.py -q`
Expected: FAIL (`AttributeError: yoto_icons_path`)

- [ ] **Step 3: Add endpoint constants** (append to `yoto/endpoints.py`)

```python
DISPLAY_ICONS_YOTO = "/media/displayIcons/user/yoto"
DISPLAY_ICONS_ME = "/media/displayIcons/user/me"
DISPLAY_ICONS_UPLOAD = "/media/displayIcons/user/me/upload"
YOTOICONS_BASE = "https://www.yotoicons.com"
```

- [ ] **Step 4: Add path helpers** (in `yoto/config.py`, after `token_path`)

```python
def yoto_icons_path() -> Path:
    return state_dir() / "yoto.icons.json"


def me_icons_path() -> Path:
    return state_dir() / "me.icons.json"
```

- [ ] **Step 5: Add upload scope** — edit `yoto/auth.py:21`

```python
SCOPES = "family:library:view family:library:manage user:content:manage user:icons:manage offline_access"
```

- [ ] **Step 6: Run to verify pass**

Run: `python -m pytest tests/test_yoto_config.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add yoto/endpoints.py yoto/config.py yoto/auth.py tests/test_yoto_config.py
git commit -m "feat(yoto): icon catalog paths, displayIcons endpoints, icons scope"
```

---

### Task 2: `icons.py` pure helpers (catalog + parsing)

**Files:**
- Create: `yoto/icons.py`
- Test: `tests/test_yoto_icons.py`
- Fixture: `tests/fixtures/yotoicons_car.html`

**Interfaces:**
- Consumes: `config.yoto_icons_path`, `config.me_icons_path`.
- Produces:
  - `read_catalog(path) -> list[dict]`
  - `to_entry(raw: dict, source: str) -> dict` → `{mediaId, ref, source, title, tags, url}`
  - `load_yoto() -> list[dict]`, `load_me() -> list[dict]` (normalized entries)
  - `search_yoto(entries, q) -> list[dict]`
  - `list_me(entries) -> list[dict]`
  - `parse_yotoicons(html) -> list[dict]` → `[{id, thumb}]`
  - `append_me(path, raw: dict) -> None`

- [ ] **Step 1: Save a small yotoicons fixture** — create `tests/fixtures/yotoicons_car.html`

```html
<div class="icon"><img src="/static/uploads/1126.png"><p>@a</p></div>
<div class="icon"><img src="/static/uploads/62.png"><p>@b</p></div>
<div class="icon"><img src="/static/uploads/1126.png"><p>@dup</p></div>
```

- [ ] **Step 2: Write failing tests** — create `tests/test_yoto_icons.py`

```python
import json
import yoto.icons as icons

ID43 = "I5B8p0HYRhwFq5apZWW0hqvfX_JPhoLUJ1n4zGoBekM"


def test_to_entry_yoto():
    raw = {"mediaId": ID43, "title": "Car", "publicTags": ["car", "auto"],
           "url": "https://m/icons/" + ID43}
    e = icons.to_entry(raw, "yoto")
    assert e["ref"] == f"yoto:#{ID43}"
    assert e["source"] == "yoto"
    assert e["title"] == "Car"
    assert e["tags"] == ["car", "auto"]


def test_search_yoto_matches_title_and_tags():
    ents = [icons.to_entry({"mediaId": ID43, "title": "Race Car",
                            "publicTags": ["vehicle"]}, "yoto"),
            icons.to_entry({"mediaId": ID43[::-1], "title": "Dog",
                            "publicTags": ["animal"]}, "yoto")]
    assert len(icons.search_yoto(ents, "car")) == 1        # title hit
    assert len(icons.search_yoto(ents, "VEHICLE")) == 1    # tag, case-insensitive
    assert len(icons.search_yoto(ents, "")) == 2           # empty -> all


def test_list_me_reverse_chronological():
    ents = [icons.to_entry({"mediaId": "a", "createdAt": "2025-01-01T00:00:00Z"}, "me"),
            icons.to_entry({"mediaId": "b", "createdAt": "2025-06-01T00:00:00Z"}, "me")]
    assert [e["mediaId"] for e in icons.list_me(ents)] == ["b", "a"]


def test_parse_yotoicons_dedups_and_orders():
    html = open("fixtures/yotoicons_car.html").read()
    out = icons.parse_yotoicons(html)
    assert [o["id"] for o in out] == ["1126", "62"]
    assert out[0]["thumb"] == "/static/uploads/1126.png"


def test_read_catalog_missing_file_is_empty(tmp_path):
    assert icons.read_catalog(tmp_path / "nope.json") == []


def test_append_me_dedups_by_mediaid(tmp_path):
    p = tmp_path / "me.icons.json"
    p.write_text(json.dumps({"displayIcons": [{"mediaId": "x"}]}))
    icons.append_me(p, {"mediaId": "y", "url": "u"})
    icons.append_me(p, {"mediaId": "x", "url": "dup"})   # existing -> ignored
    ids = [r["mediaId"] for r in json.loads(p.read_text())["displayIcons"]]
    assert ids == ["x", "y"]
```

- [ ] **Step 3: Run to verify failure**

Run: `python -m pytest tests/test_yoto_icons.py -q`
Expected: FAIL (`ModuleNotFoundError: yoto.icons`)

- [ ] **Step 4: Implement pure helpers** — create `yoto/icons.py`

```python
"""Local Yoto icon catalog + yotoicons.com acquisition.

Two JSON files under the state dir hold the icon lists fetched from Yoto:
    yoto.icons.json  -> built-in icons (GET /media/displayIcons/user/yoto)
    me.icons.json    -> the user's uploaded icons (GET .../user/me)
The picker reads these locally; network helpers upload new icons and keep
me.icons.json current. Every network helper resolves a fresh token per request.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import config, endpoints
from .auth import auth_headers

_THUMB_RE = re.compile(r"/static/uploads/(\d+)\.png")


def read_catalog(path: Path) -> list[dict]:
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


def load_yoto() -> list[dict]:
    return [to_entry(r, "yoto") for r in read_catalog(config.yoto_icons_path())]


def load_me() -> list[dict]:
    return [to_entry(r, "me") for r in read_catalog(config.me_icons_path())]


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


def append_me(path: Path, raw: dict) -> None:
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
```

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/test_yoto_icons.py -q`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add yoto/icons.py tests/test_yoto_icons.py tests/fixtures/yotoicons_car.html
git commit -m "feat(yoto): local icon catalog + yotoicons HTML parse (pure helpers)"
```

---

### Task 3: `icons.py` network helpers

**Files:**
- Modify: `yoto/icons.py`
- Test: `tests/test_yoto_icons_net.py`

**Interfaces:**
- Consumes: `endpoints.*`, `auth_headers`, `append_me`, `parse_yotoicons`, `config.me_icons_path`.
- Produces:
  - `async upload_custom_icon(session, get_token, data: bytes, filename: str) -> dict` (returns raw `displayIcon`)
  - `async import_yotoicon(session, get_token, icon_id: str) -> dict` (returns `to_entry`'d record)
  - `async search_yotoicons(session, q: str) -> list[dict]`
  - `async fetch_icon_bytes(session, get_token, url: str) -> tuple[bytes, str]`
  - `async refresh_from_api(session, get_token) -> dict` (counts)

- [ ] **Step 1: Write failing tests** — create `tests/test_yoto_icons_net.py`

```python
import json
import pytest
import yoto.icons as icons

ID43 = "I5B8p0HYRhwFq5apZWW0hqvfX_JPhoLUJ1n4zGoBekM"


class FakeResp:
    def __init__(self, status=200, body=b"", text=""):
        self.status, self.ok = status, 200 <= status < 300
        self._body, self._text = body, text
        self.headers = {"Content-Type": "image/png"}
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def read(self): return self._body
    async def text(self): return self._text


class FakeSession:
    """Records calls; returns queued responses by (method, url-substr)."""
    def __init__(self, routes): self.routes, self.calls = routes, []
    def _match(self, method, url):
        for (m, frag), resp in self.routes.items():
            if m == method and frag in url:
                return resp
        raise AssertionError(f"no route for {method} {url}")
    def post(self, url, **kw):
        self.calls.append(("POST", url, kw)); return self._match("POST", url)
    def get(self, url, **kw):
        self.calls.append(("GET", url, kw)); return self._match("GET", url)


async def _tok(): return "TESTTOKEN"


@pytest.mark.asyncio
async def test_upload_custom_icon_returns_displayicon():
    body = json.dumps({"displayIcon": {"mediaId": ID43, "url": "u"}})
    s = FakeSession({("POST", "/media/displayIcons/user/me/upload"):
                     FakeResp(200, text=body)})
    out = await icons.upload_custom_icon(s, _tok, b"PNGDATA", "car")
    assert out["mediaId"] == ID43
    # filename + autoConvert passed as query params
    _, url, kw = s.calls[0]
    assert kw["params"]["filename"] == "car"
    assert kw["data"] == b"PNGDATA"


@pytest.mark.asyncio
async def test_import_yotoicon_downloads_uploads_and_records(tmp_path, monkeypatch):
    monkeypatch.setattr(icons.config, "me_icons_path", lambda: tmp_path / "me.icons.json")
    body = json.dumps({"displayIcon": {"mediaId": ID43, "url": "u"}})
    s = FakeSession({
        ("GET", "/uploads/62.png"): FakeResp(200, body=b"PNGDATA"),
        ("POST", "/media/displayIcons/user/me/upload"): FakeResp(200, text=body),
    })
    entry = await icons.import_yotoicon(s, _tok, "62")
    assert entry["mediaId"] == ID43
    assert entry["ref"] == f"yoto:#{ID43}"
    saved = json.loads((tmp_path / "me.icons.json").read_text())
    assert saved["displayIcons"][0]["mediaId"] == ID43


@pytest.mark.asyncio
async def test_search_yotoicons_parses_results():
    html = '<img src="/static/uploads/62.png"><img src="/static/uploads/7.png">'
    s = FakeSession({("GET", "yotoicons.com/icons"): FakeResp(200, text=html)})
    out = await icons.search_yotoicons(s, "car")
    assert [o["id"] for o in out] == ["62", "7"]
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_yoto_icons_net.py -q`
Expected: FAIL (`AttributeError: upload_custom_icon`)

- [ ] **Step 3: Implement network helpers** — append to `yoto/icons.py`

```python
async def upload_custom_icon(session, get_token, data: bytes, filename: str) -> dict:
    async with session.post(
        endpoints.BASE_URL + endpoints.DISPLAY_ICONS_UPLOAD,
        params={"autoConvert": "true", "filename": filename},
        data=data,
        headers={**auth_headers(await get_token()), "Content-Type": "image/png"},
    ) as r:
        txt = await r.text()
        if not r.ok:
            raise RuntimeError(f"icon upload -> {r.status}: {txt[:200]}")
        return json.loads(txt).get("displayIcon", {}) or {}


async def import_yotoicon(session, get_token, icon_id: str) -> dict:
    url = f"{endpoints.YOTOICONS_BASE}/uploads/{icon_id}.png"
    async with session.get(url) as r:
        if not r.ok:
            raise RuntimeError(f"yotoicons download -> {r.status}")
        data = await r.read()
    raw = await upload_custom_icon(session, get_token, data, f"yotoicon-{icon_id}")
    append_me(config.me_icons_path(), raw)
    return to_entry(raw, "me")


async def search_yotoicons(session, q: str) -> list[dict]:
    url = f"{endpoints.YOTOICONS_BASE}/icons"
    async with session.get(url, params={"tag": q, "sort": "popular",
                                        "type": "singles"}) as r:
        if not r.ok:
            raise RuntimeError(f"yotoicons search -> {r.status}")
        return parse_yotoicons(await r.text())


async def fetch_icon_bytes(session, get_token, url: str) -> tuple[bytes, str]:
    async with session.get(url, headers=auth_headers(await get_token())) as r:
        if not r.ok:
            raise RuntimeError(f"icon fetch -> {r.status}")
        return await r.read(), r.headers.get("Content-Type", "image/png")


async def _get_list(session, get_token, path: str) -> list[dict]:
    async with session.get(endpoints.BASE_URL + path,
                           headers=auth_headers(await get_token())) as r:
        txt = await r.text()
        if not r.ok:
            raise RuntimeError(f"GET {path} -> {r.status}: {txt[:200]}")
        return json.loads(txt).get("displayIcons", []) or []


async def refresh_from_api(session, get_token) -> dict:
    y = await _get_list(session, get_token, endpoints.DISPLAY_ICONS_YOTO)
    m = await _get_list(session, get_token, endpoints.DISPLAY_ICONS_ME)
    config.yoto_icons_path().write_text(json.dumps({"displayIcons": y}, indent=2))
    config.me_icons_path().write_text(json.dumps({"displayIcons": m}, indent=2))
    return {"yoto": len(y), "me": len(m)}
```

- [ ] **Step 4: Ensure `pytest-asyncio`** is available and configured. Check `tests/pytest.ini`; if it lacks `asyncio_mode`, add under `[pytest]`:

```ini
asyncio_mode = auto
```

Run: `python -c "import pytest_asyncio"` — if ImportError, `pip install pytest-asyncio` and add `pytest-asyncio` to `requirements.txt`.

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/test_yoto_icons_net.py -q`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add yoto/icons.py tests/test_yoto_icons_net.py tests/pytest.ini requirements.txt
git commit -m "feat(yoto): icon upload/import/search/refresh network helpers"
```

---

### Task 4: Attach icon in `build_appended_payload`

**Files:**
- Modify: `yoto/uploader.py:73`
- Test: `tests/test_yoto_uploader.py`

**Interfaces:**
- Consumes: existing `normalize_icon`.
- Produces: `build_appended_payload(card_detail, new_parts, icon_ref=None)`.

- [ ] **Step 1: Write failing tests** (append to `tests/test_yoto_uploader.py`)

```python
def test_build_appended_payload_sets_icon_on_all_parts():
    parts = [{"title": "n-1", "trackUrl": "yoto:#B", "duration": 1,
              "fileSize": 1, "channels": 2, "format": "mp3"},
             {"title": "n-2", "trackUrl": "yoto:#C", "duration": 1,
              "fileSize": 1, "channels": 2, "format": "mp3"}]
    ref = f"yoto:#{ID43}"
    p = build_appended_payload(_fake_card(), parts, icon_ref=ref)
    new = p["content"]["chapters"][1:]           # skip existing "old"
    assert len(new) == 2
    for ch in new:
        assert ch["display"]["icon16x16"] == ref
        assert ch["tracks"][0]["display"]["icon16x16"] == ref


def test_build_appended_payload_invalid_icon_ref_omitted():
    parts = [{"title": "n", "trackUrl": "yoto:#B", "duration": 1,
              "fileSize": 1, "channels": 2, "format": "mp3"}]
    p = build_appended_payload(_fake_card(), parts, icon_ref="yoto:#bad")
    assert "display" not in p["content"]["chapters"][1]
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_yoto_uploader.py -q`
Expected: FAIL (unexpected `icon_ref` kwarg)

- [ ] **Step 3: Implement** — edit `yoto/uploader.py` signature and new-track loop.

Change signature (line ~73):
```python
def build_appended_payload(card_detail: dict, new_parts: list[dict],
                           icon_ref: str | None = None) -> dict:
```
Right after `chapters = copy.deepcopy(...)` sanitize block, normalize the ref once:
```python
    icon = normalize_icon(icon_ref) if icon_ref else None
```
In the append loop, build `track` then conditionally add display to both track and chapter:
```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_yoto_uploader.py -q`
Expected: PASS (all, including the two new)

- [ ] **Step 5: Commit**

```bash
git add yoto/uploader.py tests/test_yoto_uploader.py
git commit -m "feat(yoto): optional icon_ref applied to appended chapters/tracks"
```

---

### Task 5: Thread icon through the pipeline

**Files:**
- Modify: `yoto/pipeline.py:38`
- Test: `tests/test_yoto_pipeline_icon.py`

**Interfaces:**
- Produces: `run_upload(audio_path, filename, playlist_id, job, icon_media_id=None)`.

- [ ] **Step 1: Write failing test** — create `tests/test_yoto_pipeline_icon.py`

```python
import asyncio
import yoto.pipeline as pipeline


def test_run_upload_passes_icon_ref(monkeypatch, tmp_path):
    src = tmp_path / "a.mp3"; src.write_bytes(b"x")
    monkeypatch.setattr(pipeline, "optimize_track", lambda s, w, **k: [src])

    class Ctx:
        async def __aenter__(self): return (None, None)
        async def __aexit__(self, *a): return False
    monkeypatch.setattr(pipeline, "authed_session", lambda: Ctx())
    async def fake_upload(session, gt, p):
        return {"sha": "S", "duration": 1, "fileSize": 1, "channels": 2, "format": "mp3"}
    monkeypatch.setattr(pipeline, "upload_file", fake_upload)
    async def fake_fetch(session, gt, pid): return {"card": {}}
    monkeypatch.setattr(pipeline, "fetch_card", fake_fetch)
    captured = {}
    def fake_build(detail, parts, icon_ref=None):
        captured["icon_ref"] = icon_ref
        return {"cardId": "c"}
    monkeypatch.setattr(pipeline, "build_appended_payload", fake_build)
    async def fake_create(session, gt, payload): return {}
    monkeypatch.setattr(pipeline, "create_content", fake_create)

    class Job:
        def update(self, *a): pass
        def add_log(self, *a): pass
        def finish(self, *a): pass
    asyncio.run(pipeline.run_upload(str(src), "song", "pl", Job(),
                                    icon_media_id="MID123"))
    assert captured["icon_ref"] == "yoto:#MID123"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_yoto_pipeline_icon.py -q`
Expected: FAIL (unexpected `icon_media_id` kwarg)

- [ ] **Step 3: Implement** — edit `yoto/pipeline.py`

Signature:
```python
async def run_upload(audio_path: str, filename: str, playlist_id: str, job,
                     icon_media_id: str | None = None) -> None:
```
Before the `job.update(85, ...)` block compute the ref and pass it:
```python
            job.update(85, "更新 playlist …")
            detail = await fetch_card(session, get_token, playlist_id)
            icon_ref = f"yoto:#{icon_media_id}" if icon_media_id else None
            payload = build_appended_payload(detail, new_parts, icon_ref=icon_ref)
            await create_content(session, get_token, payload)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_yoto_pipeline_icon.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add yoto/pipeline.py tests/test_yoto_pipeline_icon.py
git commit -m "feat(yoto): pipeline forwards icon_media_id as icon_ref"
```

---

### Task 6: FastAPI icon routes + upload accepts icon

**Files:**
- Modify: `video2mp3.py` (imports near line 23-26; routes near line 451-483)
- Test: `tests/test_yoto_icons_api.py`

**Interfaces:**
- Consumes: `yoto.icons`, `authed_session`.
- Produces routes: `GET /api/yoto/icons`, `GET /api/yoto/icons/search-external`, `POST /api/yoto/icons/import`, `GET /api/yoto/icons/thumb`, `POST /api/yoto/icons/refresh`; `YotoUploadRequest.icon_media_id`.

- [ ] **Step 1: Write failing tests** — create `tests/test_yoto_icons_api.py`

```python
import video2mp3
from fastapi.testclient import TestClient
from video2mp3 import app

client = TestClient(app)
ID43 = "I5B8p0HYRhwFq5apZWW0hqvfX_JPhoLUJ1n4zGoBekM"


def test_icons_local_search(monkeypatch):
    monkeypatch.setattr(video2mp3.yoto_icons, "load_yoto",
                        lambda: [{"mediaId": ID43, "ref": f"yoto:#{ID43}",
                                  "source": "yoto", "title": "Car",
                                  "tags": ["car"], "url": "u"}])
    monkeypatch.setattr(video2mp3.yoto_icons, "load_me", lambda: [])
    r = client.get("/api/yoto/icons", params={"q": "car", "source": "yoto"})
    assert r.status_code == 200
    assert r.json()["icons"][0]["mediaId"] == ID43


def test_icons_import_passes_id(monkeypatch):
    async def fake_import(session, gt, icon_id):
        return {"mediaId": ID43, "ref": f"yoto:#{ID43}", "source": "me", "url": "u"}
    monkeypatch.setattr(video2mp3.yoto_icons, "import_yotoicon", fake_import)
    # authed_session -> yield (None, None)
    import contextlib
    @contextlib.asynccontextmanager
    async def fake_authed():
        yield (None, None)
    monkeypatch.setattr(video2mp3, "authed_session", fake_authed)
    r = client.post("/api/yoto/icons/import", json={"icon_id": "62"})
    assert r.status_code == 200
    assert r.json()["mediaId"] == ID43


def test_upload_accepts_icon_media_id(monkeypatch, tmp_path):
    f = tmp_path / "audio.mp3"; f.write_bytes(b"x")
    monkeypatch.setattr(video2mp3.taskManager, "get_file_path",
                        lambda tid, ft: str(f))
    seen = {}
    async def fake_run(audio_path, filename, playlist_id, job, icon_media_id=None):
        seen["icon"] = icon_media_id; job.finish(True, "ok")
    monkeypatch.setattr(video2mp3.yoto_pipeline, "run_upload", fake_run)
    r = client.post("/api/yoto/upload", json={"task_id": "t", "filename": "s",
                    "playlist_id": "p", "icon_media_id": "MID"})
    assert r.status_code == 200
    import time; time.sleep(0.05)
    assert seen.get("icon") == "MID"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_yoto_icons_api.py -q`
Expected: FAIL (routes/attr missing)

- [ ] **Step 3: Add import** — in `video2mp3.py` near line 23:

```python
from yoto import icons as yoto_icons
```

- [ ] **Step 4: Extend request model** — `YotoUploadRequest` (line ~451):

```python
class YotoUploadRequest(BaseModel):
    task_id: str
    filename: str
    playlist_id: str
    icon_media_id: str | None = None
```

- [ ] **Step 5: Pass icon into the job** — in `yoto_upload` (line ~479):

```python
    asyncio.create_task(
        yoto_pipeline.run_upload(audio_path, filename, req.playlist_id, job,
                                 icon_media_id=req.icon_media_id)
    )
```

- [ ] **Step 6: Add icon routes** — after `yoto_upload_status` (line ~490):

```python
class IconImportRequest(BaseModel):
    icon_id: str


@app.get("/api/yoto/icons")
async def yoto_icons_local(q: str = "", source: str = "all"):
    """Search/browse the local icon catalog (no network)."""
    out = []
    if source in ("all", "yoto"):
        out += yoto_icons.search_yoto(yoto_icons.load_yoto(), q)
    if source in ("all", "me"):
        out += yoto_icons.list_me(yoto_icons.load_me())
    return {"icons": out}


@app.get("/api/yoto/icons/search-external")
async def yoto_icons_external(q: str):
    try:
        async with authed_session() as (session, get_token):
            items = await yoto_icons.search_yotoicons(session, q)
        return {"icons": items}
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


@app.post("/api/yoto/icons/import")
async def yoto_icons_import(req: IconImportRequest):
    try:
        async with authed_session() as (session, get_token):
            entry = await yoto_icons.import_yotoicon(session, get_token, req.icon_id)
        return entry
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


@app.get("/api/yoto/icons/thumb")
async def yoto_icons_thumb(media_id: str = "", url: str = ""):
    try:
        target = url or f"https://media-secure-v2.api.yotoplay.com/icons/{media_id}"
        async with authed_session() as (session, get_token):
            data, ctype = await yoto_icons.fetch_icon_bytes(session, get_token, target)
        return Response(content=data, media_type=ctype)
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


@app.post("/api/yoto/icons/refresh")
async def yoto_icons_refresh():
    try:
        async with authed_session() as (session, get_token):
            counts = await yoto_icons.refresh_from_api(session, get_token)
        return counts
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e)})
```

- [ ] **Step 7: Run to verify pass**

Run: `python -m pytest tests/test_yoto_icons_api.py -q`
Expected: PASS (3 tests)

- [ ] **Step 8: Commit**

```bash
git add video2mp3.py tests/test_yoto_icons_api.py
git commit -m "feat(yoto): icon routes (local search, external, import, thumb, refresh) + upload icon_media_id"
```

---

### Task 7: Icon picker in the upload modal

**Files:**
- Modify: `templates/scrape.html` (modal block ~272-286; JS ~615-680)

**Interfaces:**
- Consumes: `/api/yoto/icons`, `/api/yoto/icons/search-external`, `/api/yoto/icons/import`, `/api/yoto/icons/thumb`; sends `icon_media_id` to `/api/yoto/upload`.

- [ ] **Step 1: Add the icon block** — inside `.lm-box`, before `#yoto-progress`:

```html
    <label class="in-hint" style="display:block;margin:10px 0 4px">图标（可选）</label>
    <div id="yoto-icon-tabs" style="display:flex;gap:6px;margin-bottom:6px">
      <button type="button" class="btn-s yi-tab" data-src="yoto" onclick="switchIconTab('yoto')">Yoto图标</button>
      <button type="button" class="btn-s yi-tab" data-src="me" onclick="switchIconTab('me')">我的图标</button>
      <button type="button" class="btn-s yi-tab" data-src="ext" onclick="switchIconTab('ext')">yotoicons搜索</button>
    </div>
    <input id="yoto-icon-q" class="url-in" type="text" placeholder="搜索图标关键字，如 car"
           style="margin-bottom:6px" onkeydown="if(event.key==='Enter'){event.preventDefault();runIconSearch();}">
    <div id="yoto-icon-grid" style="display:flex;flex-wrap:wrap;gap:6px;max-height:150px;overflow:auto;min-height:20px"></div>
```

- [ ] **Step 2: Add picker state + reset** — in JS Yoto section, extend state and `openYotoModal`:

```javascript
let yotoIcon = null, yotoIconTab = 'yoto';
```
At the end of `openYotoModal(...)` add:
```javascript
  yotoIcon = null; yotoIconTab = 'yoto';
  document.getElementById('yoto-icon-q').value = '';
  switchIconTab('yoto');
```

- [ ] **Step 3: Add picker functions** — append to the Yoto JS section:

```javascript
function switchIconTab(src) {
  yotoIconTab = src;
  document.querySelectorAll('.yi-tab').forEach(b =>
    b.classList.toggle('active', b.dataset.src === src));
  const q = document.getElementById('yoto-icon-q');
  q.style.display = (src === 'me') ? 'none' : '';
  runIconSearch();
}

function iconThumbURL(it) {
  if (it.thumb) return 'https://www.yotoicons.com' + it.thumb;   // external
  return '/api/yoto/icons/thumb?media_id=' + encodeURIComponent(it.mediaId);
}

function renderIcons(items, external) {
  const grid = document.getElementById('yoto-icon-grid');
  grid.innerHTML = '';
  items.forEach(it => {
    const img = document.createElement('img');
    img.src = iconThumbURL(it);
    img.title = it.title || it.id || '';
    img.style.cssText =
      'width:32px;height:32px;image-rendering:pixelated;border:2px solid transparent;border-radius:4px;cursor:pointer;background:#222';
    const key = external ? it.id : it.mediaId;
    if (!external && yotoIcon === it.mediaId) img.style.borderColor = '#4caf50';
    img.onclick = () => external ? importExternalIcon(it.id) : selectIcon(it.mediaId, grid, img);
    grid.appendChild(img);
  });
  if (!items.length) grid.innerHTML = '<span class="lm-hint">无结果</span>';
}

function selectIcon(mediaId, grid, img) {
  yotoIcon = mediaId;
  grid.querySelectorAll('img').forEach(i => i.style.borderColor = 'transparent');
  img.style.borderColor = '#4caf50';
}

async function runIconSearch() {
  const grid = document.getElementById('yoto-icon-grid');
  const q = document.getElementById('yoto-icon-q').value.trim();
  grid.innerHTML = '<span class="lm-hint">加载中…</span>';
  try {
    if (yotoIconTab === 'ext') {
      if (!q) { grid.innerHTML = '<span class="lm-hint">输入关键字搜索</span>'; return; }
      const r = await fetch('/api/yoto/icons/search-external?q=' + encodeURIComponent(q));
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || '搜索失败');
      renderIcons(d.icons, true);
    } else {
      const r = await fetch('/api/yoto/icons?source=' + yotoIconTab +
                            '&q=' + encodeURIComponent(q));
      const d = await r.json();
      renderIcons(d.icons, false);
    }
  } catch (e) { grid.innerHTML = '<span class="lm-hint">' + e.message + '</span>'; }
}

async function importExternalIcon(iconId) {
  const grid = document.getElementById('yoto-icon-grid');
  grid.innerHTML = '<span class="lm-hint">导入中…</span>';
  try {
    const r = await fetch('/api/yoto/icons/import', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({icon_id: iconId})
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || '导入失败');
    yotoIcon = d.mediaId;
    switchIconTab('me');          // now lives under 我的图标, selected
  } catch (e) { grid.innerHTML = '<span class="lm-hint">' + e.message + '</span>'; }
}
```

- [ ] **Step 4: Send the chosen icon** — in `submitYoto`, include it in the body:

```javascript
      body: JSON.stringify({task_id: yotoTaskId, filename, playlist_id,
                            icon_media_id: yotoIcon}),
```

- [ ] **Step 5: Add minimal active-tab style** — near existing modal styles, add:

```css
.yi-tab.active{background:#4caf50;color:#fff}
```

- [ ] **Step 6: Manual verification** — start the app, open the ⬆ Yoto modal:
  - "Yoto图标" tab shows a grid; typing "car" filters it.
  - "我的图标" hides the search box and shows uploaded icons.
  - "yotoicons搜索" + "car" + Enter shows external results; clicking one imports it (spinner → lands selected under 我的图标).
  - Upload with an icon selected; confirm the new chapter shows that icon in the Yoto app.

- [ ] **Step 7: Commit**

```bash
git add templates/scrape.html
git commit -m "feat(yoto): icon picker (Yoto/me/yotoicons tabs) in upload modal"
```

---

## Final verification

- [ ] Run the whole suite: `python -m pytest -q` from `tests/` — expect all green.
- [ ] Confirm `me.icons.json` gains a record after a yotoicons import.
- [ ] Use superpowers:finishing-a-development-branch to wrap up.
