# User Management + Per-User Yoto Binding — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Multi-user V2M — username/password login, per-user Yoto client binding + OAuth, per-user icons/playlists, shared downloads, per-user upload target.

**Architecture:** New `accounts.py` (users + sessions, file-backed, stdlib crypto). `yoto/config.py` path helpers become uid-aware (optional uid → legacy flat path for back-compat). New `yoto/login.py` (PKCE, ported). `authed_session(uid)`, `icons.*(…, uid)`, `run_upload(…, uid)` thread the session user. Routes gain a `require_user` cookie dependency.

**Tech Stack:** FastAPI, aiohttp, stdlib hashlib/hmac/secrets, pytest TestClient, vanilla JS.

## Global Constraints

- Passwords: `hashlib.pbkdf2_hmac('sha256', pw, salt, 200_000)`, per-user random salt, `hmac.compare_digest` verify. Never store plaintext.
- Per-user Yoto state under `.yoto/users/<uid>/`; downloads + `data/yoto_icons/` shared.
- `uid` is always explicit (never a context-var) so detached upload tasks stay correct.
- Cookie `v2m_sid`: HttpOnly, SameSite=Lax, Path=/, max-age 30d.
- Run tests from `V2M/tests/` with `python -m pytest -q`.
- `accounts.py` and `yoto/config.py` honor `YOTO_STATE_DIR` (tests set it).

---

## Phase A — Accounts subsystem

### Task 1: Password hashing + user store

**Files:** Create `accounts.py`; Test `tests/test_accounts.py`.

**Interfaces (Produces):**
- `hash_password(pw, salt=None) -> (salt, hexhash)`
- `verify_password(pw, salt, expected) -> bool`
- `create_user(username, password) -> uid` (raises ValueError on dup/empty)
- `authenticate(username, password) -> uid | None`
- `list_users() -> [{"uid","username"}]`, `get_user(uid) -> dict|None`

- [ ] **Step 1: Failing test** — `tests/test_accounts.py`

```python
import importlib
import accounts


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("YOTO_STATE_DIR", str(tmp_path))
    importlib.reload(accounts)
    return accounts


def test_hash_verify_roundtrip(monkeypatch, tmp_path):
    a = _fresh(monkeypatch, tmp_path)
    salt, h = a.hash_password("pw")
    assert a.verify_password("pw", salt, h)
    assert not a.verify_password("nope", salt, h)


def test_create_and_authenticate(monkeypatch, tmp_path):
    a = _fresh(monkeypatch, tmp_path)
    uid = a.create_user("alice", "secret")
    assert a.authenticate("alice", "secret") == uid
    assert a.authenticate("alice", "wrong") is None
    assert a.authenticate("ghost", "x") is None


def test_duplicate_username_rejected(monkeypatch, tmp_path):
    a = _fresh(monkeypatch, tmp_path)
    a.create_user("bob", "p1")
    import pytest
    with pytest.raises(ValueError):
        a.create_user("bob", "p2")


def test_list_users_hides_secrets(monkeypatch, tmp_path):
    a = _fresh(monkeypatch, tmp_path)
    a.create_user("carol", "pw")
    us = a.list_users()
    assert us[0]["username"] == "carol" and "uid" in us[0]
    assert "pw" not in us[0] and "salt" not in us[0]
```

- [ ] **Step 2: Run — expect fail** (`ModuleNotFoundError: accounts`)

Run: `python -m pytest tests/test_accounts.py -q`

- [ ] **Step 3: Implement** — `accounts.py`

```python
"""V2M user accounts + sessions (file-backed, stdlib crypto).

State lives under the Yoto state dir (honors YOTO_STATE_DIR):
    users.json     -> [{uid, username, salt, pw, created}]
    sessions.json  -> {token: {uid, created}}
Per-user Yoto state is separate, under users/<uid>/ (see yoto.config).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from yoto import config as yconfig

_ITERS = 200_000


def _users_path() -> Path:
    return yconfig.state_dir() / "users.json"


def _sessions_path() -> Path:
    return yconfig.state_dir() / "sessions.json"


def _read(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return default


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    os.chmod(path, 0o600)


def hash_password(pw: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), _ITERS)
    return salt, dk.hex()


def verify_password(pw: str, salt: str, expected: str) -> bool:
    _, h = hash_password(pw, salt)
    return hmac.compare_digest(h, expected)


def _load_users() -> list[dict]:
    return _read(_users_path(), {"users": []}).get("users", [])


def _save_users(users: list[dict]) -> None:
    _write(_users_path(), {"users": users})


def create_user(username: str, password: str) -> str:
    username = (username or "").strip()
    if not username or not password:
        raise ValueError("用户名和密码不能为空")
    users = _load_users()
    if any(u["username"].lower() == username.lower() for u in users):
        raise ValueError("用户名已存在")
    salt, pw = hash_password(password)
    uid = secrets.token_hex(8)
    first = not users
    users.append({"uid": uid, "username": username, "salt": salt, "pw": pw,
                  "created": datetime.now(timezone.utc).isoformat()})
    _save_users(users)
    if first:
        migrate_legacy_into(uid)   # defined in Task 4
    return uid


def authenticate(username: str, password: str) -> str | None:
    for u in _load_users():
        if u["username"].lower() == (username or "").strip().lower():
            if verify_password(password, u["salt"], u["pw"]):
                return u["uid"]
            return None
    return None


def get_user(uid: str) -> dict | None:
    for u in _load_users():
        if u["uid"] == uid:
            return {"uid": u["uid"], "username": u["username"]}
    return None


def list_users() -> list[dict]:
    return [{"uid": u["uid"], "username": u["username"]} for u in _load_users()]


def migrate_legacy_into(uid: str) -> None:
    """Placeholder; real body added in Task 4."""
    pass
```

- [ ] **Step 4: Run — expect pass**; **Step 5: Commit**

```bash
git add accounts.py tests/test_accounts.py
git commit -m "feat(users): password hashing + file-backed user store"
```

---

### Task 2: Sessions

**Files:** Modify `accounts.py`; Test `tests/test_accounts_sessions.py`.

**Interfaces (Produces):** `new_session(uid) -> token`, `session_user(token) -> uid|None`, `end_session(token) -> None`.

- [ ] **Step 1: Failing test** — `tests/test_accounts_sessions.py`

```python
import importlib
import accounts


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("YOTO_STATE_DIR", str(tmp_path))
    importlib.reload(accounts)
    return accounts


def test_session_lifecycle(monkeypatch, tmp_path):
    a = _fresh(monkeypatch, tmp_path)
    uid = a.create_user("alice", "pw")
    tok = a.new_session(uid)
    assert a.session_user(tok) == uid
    a.end_session(tok)
    assert a.session_user(tok) is None
    assert a.session_user("bogus") is None
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement** — append to `accounts.py`

```python
def _load_sessions() -> dict:
    return _read(_sessions_path(), {})


def _save_sessions(s: dict) -> None:
    _write(_sessions_path(), s)


def new_session(uid: str) -> str:
    token = secrets.token_urlsafe(32)
    s = _load_sessions()
    s[token] = {"uid": uid, "created": datetime.now(timezone.utc).isoformat()}
    _save_sessions(s)
    return token


def session_user(token: str) -> str | None:
    if not token:
        return None
    rec = _load_sessions().get(token)
    return rec.get("uid") if rec else None


def end_session(token: str) -> None:
    s = _load_sessions()
    if token in s:
        del s[token]
        _save_sessions(s)
```

- [ ] **Step 4: Run — expect pass**; **Step 5: Commit**

```bash
git add accounts.py tests/test_accounts_sessions.py
git commit -m "feat(users): server-side sessions"
```

---

## Phase B — Per-user Yoto state

### Task 3: uid-aware config paths

**Files:** Modify `yoto/config.py`; Test `tests/test_yoto_config.py`.

**Interfaces (Produces):** `user_dir(uid)`, and `env_path/token_path/pkce_path/yoto_icons_path/me_icons_path/yotoicons_cache_path/load_client_id/load_token/save_token` all accept optional `uid` (None → legacy flat path). Add `pkce_path`.

- [ ] **Step 1: Failing test** — append to `tests/test_yoto_config.py`

```python
def test_user_scoped_paths(monkeypatch, tmp_path):
    import importlib, yoto.config as config
    monkeypatch.setenv("YOTO_STATE_DIR", str(tmp_path))
    importlib.reload(config)
    assert config.user_dir("u1") == tmp_path / "users" / "u1"
    assert config.token_path("u1") == tmp_path / "users" / "u1" / ".yoto_token.json"
    assert config.me_icons_path("u1") == tmp_path / "users" / "u1" / "me.icons.json"
    # legacy (no uid) unchanged
    assert config.token_path() == tmp_path / ".yoto_token.json"
    importlib.reload(config)
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement** — in `yoto/config.py`, add `user_dir` and give each helper an optional uid:

```python
def user_dir(uid: str) -> Path:
    return state_dir() / "users" / uid


def _base(uid):
    return user_dir(uid) if uid else state_dir()


def env_path(uid: str | None = None) -> Path:
    return _base(uid) / ".env"


def token_path(uid: str | None = None) -> Path:
    return _base(uid) / ".yoto_token.json"


def pkce_path(uid: str | None = None) -> Path:
    return _base(uid) / ".yoto_pkce.json"


def yoto_icons_path(uid: str | None = None) -> Path:
    return _base(uid) / "yoto.icons.json"


def me_icons_path(uid: str | None = None) -> Path:
    return _base(uid) / "me.icons.json"


def yotoicons_cache_path(uid: str | None = None) -> Path:
    return _base(uid) / "yotoicons.cache.json"
```
Update `load_client_id(uid=None)`, `load_token(uid=None)`, `save_token(token, uid=None)` to call `env_path(uid)` / `token_path(uid)`. (`data_dir`/`icons_cache_dir` unchanged.)

- [ ] **Step 4: Run whole config test — expect pass**; **Step 5: Commit**

```bash
git add yoto/config.py tests/test_yoto_config.py
git commit -m "feat(yoto): uid-aware config paths (legacy flat when uid=None)"
```

---

### Task 4: Legacy migration into first user

**Files:** Modify `accounts.py`; Test `tests/test_accounts_migrate.py`.

**Interfaces (Produces):** real `migrate_legacy_into(uid)` — move legacy flat files into `users/<uid>/` when that dir has none.

- [ ] **Step 1: Failing test** — `tests/test_accounts_migrate.py`

```python
import importlib
import accounts
from yoto import config as yconfig


def test_first_user_inherits_legacy(monkeypatch, tmp_path):
    monkeypatch.setenv("YOTO_STATE_DIR", str(tmp_path))
    importlib.reload(yconfig); importlib.reload(accounts)
    (tmp_path / ".env").write_text("client_id: LEGACY123")
    (tmp_path / ".yoto_token.json").write_text('{"access_token":"a"}')
    uid = accounts.create_user("first", "pw")
    ud = tmp_path / "users" / uid
    assert (ud / ".env").read_text() == "client_id: LEGACY123"
    assert (ud / ".yoto_token.json").exists()
    # a SECOND user does NOT inherit
    uid2 = accounts.create_user("second", "pw")
    assert not (tmp_path / "users" / uid2 / ".yoto_token.json").exists()
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement** — replace the `migrate_legacy_into` stub:

```python
_LEGACY_FILES = (".env", ".yoto_token.json", ".yoto_pkce.json",
                 "yoto.icons.json", "me.icons.json", "yotoicons.cache.json")


def migrate_legacy_into(uid: str) -> None:
    import shutil
    base = yconfig.state_dir()
    dest = yconfig.user_dir(uid)
    if any((dest / f).exists() for f in _LEGACY_FILES):
        return
    moved = False
    for f in _LEGACY_FILES:
        src = base / f
        if src.exists():
            dest.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest / f))
            moved = True
    return None if moved else None
```

- [ ] **Step 4: Run — expect pass**; **Step 5: Commit**

```bash
git add accounts.py tests/test_accounts_migrate.py
git commit -m "feat(users): first user inherits legacy Yoto binding"
```

---

### Task 5: `yoto/login.py` (PKCE, ported)

**Files:** Create `yoto/login.py`; Test `tests/test_yoto_login.py`.

**Interfaces (Produces):** `authorize_url(uid) -> str` (writes pkce), `async exchange(uid, code_or_url) -> Token`, `_extract_code(arg) -> (code,state)`.

- [ ] **Step 1: Failing test** — `tests/test_yoto_login.py`

```python
import asyncio, importlib, json
import yoto.config as config
import yoto.login as login


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("YOTO_STATE_DIR", str(tmp_path))
    importlib.reload(config); importlib.reload(login)


def test_authorize_url_writes_pkce(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    config.user_dir("u1").mkdir(parents=True)
    (config.user_dir("u1") / ".env").write_text("client_id: CID9")
    url = login.authorize_url("u1")
    assert "client_id=CID9" in url and "code_challenge=" in url
    assert "user%3Aicons%3Amanage" in url or "user:icons:manage" in url
    saved = json.loads(config.pkce_path("u1").read_text())
    assert "verifier" in saved and "state" in saved


def test_extract_code_from_url_and_raw():
    assert login._extract_code("ABC")[0] == "ABC"
    u = "http://127.0.0.1:8787/callback?code=XYZ&state=s1"
    assert login._extract_code(u) == ("XYZ", "s1")


def test_exchange_saves_token(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    ud = config.user_dir("u1"); ud.mkdir(parents=True)
    (ud / ".env").write_text("client_id: CID9")
    config.pkce_path("u1").write_text(json.dumps({"verifier": "v", "state": "s"}))

    class Resp:
        ok = True
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def json(self, content_type=None):
            return {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}

    class Sess:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def post(self, *a, **k): return Resp()
    monkeypatch.setattr(login.aiohttp, "ClientSession", lambda: Sess())

    tok = asyncio.run(login.exchange("u1", "CODE"))
    assert tok.access_token == "AT"
    assert config.load_token("u1").refresh_token == "RT"
    assert not config.pkce_path("u1").exists()   # consumed
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement** — `yoto/login.py` (adapted from `yoto_fork/login.py`)

```python
"""Yoto OAuth (Authorization Code + PKCE) per user, paste-callback style."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.parse

import aiohttp

from . import config, endpoints
from .auth import SCOPES, _build_token_from_body

AUTHORIZE_URL = endpoints.AUTHORIZE_URL
TOKEN_URL = endpoints.TOKEN_URL
AUDIENCE = endpoints.BASE_URL
REDIRECT_URI = "http://127.0.0.1:8787/callback"


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _extract_code(arg: str) -> tuple[str, str | None]:
    arg = (arg or "").strip()
    if arg.startswith("http"):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(arg).query)
        return q.get("code", [""])[0], q.get("state", [None])[0]
    return arg, None


def _decode_scopes(access_token: str) -> list[str]:
    try:
        p = access_token.split(".")[1]; p += "=" * (-len(p) % 4)
        return sorted((json.loads(base64.urlsafe_b64decode(p)).get("scope") or "").split())
    except Exception:
        return []


def authorize_url(uid: str) -> str:
    client_id = config.load_client_id(uid)
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    config.pkce_path(uid).parent.mkdir(parents=True, exist_ok=True)
    config.pkce_path(uid).write_text(json.dumps({"verifier": verifier, "state": state}))
    params = {
        "audience": AUDIENCE, "scope": SCOPES, "response_type": "code",
        "client_id": client_id, "code_challenge": challenge,
        "code_challenge_method": "S256", "redirect_uri": REDIRECT_URI, "state": state,
    }
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)


async def exchange(uid: str, code_or_url: str):
    client_id = config.load_client_id(uid)
    code, _state = _extract_code(code_or_url)
    if not code:
        raise RuntimeError("回调里没有找到 code")
    saved = json.loads(config.pkce_path(uid).read_text())
    data = {
        "grant_type": "authorization_code", "client_id": client_id, "code": code,
        "redirect_uri": REDIRECT_URI, "code_verifier": saved["verifier"],
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(TOKEN_URL, data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"}) as r:
            body = await r.json(content_type=None)
            if not r.ok or body.get("error"):
                raise RuntimeError(f"token 交换失败: {body}")
            token = _build_token_from_body(body, SCOPES)
    config.save_token(token, uid)
    config.pkce_path(uid).unlink(missing_ok=True)
    return token
```

- [ ] **Step 4: Run — expect pass**; **Step 5: Commit**

```bash
git add yoto/login.py tests/test_yoto_login.py
git commit -m "feat(yoto): per-user PKCE login (authorize_url + exchange)"
```

---

### Task 6: `authed_session(uid)`

**Files:** Modify `yoto/auth.py`; Test `tests/test_yoto_auth.py`.

**Interfaces:** `authed_session(uid=None)` loads client_id/token for uid; TokenProvider persists with `save_token(token, uid)`.

- [ ] **Step 1: Failing test** — append to `tests/test_yoto_auth.py`

```python
def test_authed_session_requires_binding(monkeypatch, tmp_path):
    import importlib, asyncio, yoto.config as config, yoto.auth as auth
    monkeypatch.setenv("YOTO_STATE_DIR", str(tmp_path))
    importlib.reload(config); importlib.reload(auth)

    async def go():
        async with auth.authed_session("nouser") as _:
            pass
    import pytest
    with pytest.raises(RuntimeError):
        asyncio.run(go())
```

- [ ] **Step 2: Run — expect fail** (signature rejects uid)

- [ ] **Step 3: Implement** — edit `yoto/auth.py`:

`TokenProvider.__init__` gains `uid`; in `__call__` after refresh call `config.save_token(self._token, self._uid)`. Change:
```python
@contextlib.asynccontextmanager
async def authed_session(uid: str | None = None):
    client_id = config.load_client_id(uid)
    token = config.load_token(uid)
    if token is None or not token.refresh_token:
        raise RuntimeError("请先绑定并登录 Yoto 账号。")
    async with aiohttp.ClientSession() as session:
        yield session, TokenProvider(session, client_id, token, uid)
```
And `TokenProvider(... , uid)` storing `self._uid = uid`; refresh persists with uid.

- [ ] **Step 4: Run — expect pass** (plus existing auth tests still green); **Step 5: Commit**

```bash
git add yoto/auth.py tests/test_yoto_auth.py
git commit -m "feat(yoto): authed_session(uid) with per-user token persistence"
```

---

### Task 7: Thread uid through `yoto/icons.py`

**Files:** Modify `yoto/icons.py`; Modify `tests/test_yoto_icons.py`, `tests/test_yoto_icons_net.py`.

**Interfaces:** `load_yoto(uid=None)`, `load_me(uid=None)`, `import_yotoicon(session, get_token, icon_id, uid=None)`, `refresh_from_api(session, get_token, uid=None)`, `_read_import_cache(uid)`, `_write_import_cache(cache, uid)`.

- [ ] **Step 1: Failing test** — append to `tests/test_yoto_icons.py`

```python
def test_load_me_is_uid_scoped(monkeypatch, tmp_path):
    import importlib, yoto.config as config, yoto.icons as icons
    monkeypatch.setenv("YOTO_STATE_DIR", str(tmp_path))
    importlib.reload(config); importlib.reload(icons)
    config.me_icons_path("u1").parent.mkdir(parents=True)
    config.me_icons_path("u1").write_text('{"displayIcons":[{"mediaId":"A"}]}')
    assert [e["mediaId"] for e in icons.load_me("u1")] == ["A"]
    assert icons.load_me("u2") == []            # isolated
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement** — in `yoto/icons.py` pass uid into the config calls:
  - `load_yoto(uid=None)` → `read_catalog(config.yoto_icons_path(uid))`
  - `load_me(uid=None)` → `read_catalog(config.me_icons_path(uid))`
  - `_read_import_cache(uid=None)` / `_write_import_cache(cache, uid=None)` → `config.yotoicons_cache_path(uid)`
  - `import_yotoicon(session, get_token, icon_id, uid=None)` → use `_read/_write_import_cache(uid)` and `append_me(config.me_icons_path(uid), raw)`
  - `refresh_from_api(session, get_token, uid=None)` → write `config.yoto_icons_path(uid)` / `me_icons_path(uid)`

- [ ] **Step 4: Fix existing tests** — `tests/test_yoto_icons_net.py` monkeypatches must accept a uid arg. Change the no-arg lambdas:
```python
monkeypatch.setattr(icons.config, "me_icons_path", lambda uid=None: tmp_path / "me.icons.json")
monkeypatch.setattr(icons.config, "yotoicons_cache_path", lambda uid=None: tmp_path / "yotoicons.cache.json")
```
(and `icons_cache_dir` stays no-arg).

- [ ] **Step 5: Run icons suites — expect pass**; **Step 6: Commit**

```bash
git add yoto/icons.py tests/test_yoto_icons.py tests/test_yoto_icons_net.py
git commit -m "feat(yoto): uid-scoped icon catalogs + import cache"
```

---

### Task 8: `run_upload(…, uid)`

**Files:** Modify `yoto/pipeline.py`; Test `tests/test_yoto_pipeline_icon.py`.

**Interfaces:** `run_upload(audio_path, filename, playlist_id, job, icon_media_id=None, uid=None)` opens `authed_session(uid)`; icon import uses uid.

- [ ] **Step 1: Failing test** — append to `tests/test_yoto_pipeline_icon.py`

```python
def test_run_upload_opens_session_for_uid(monkeypatch, tmp_path):
    import asyncio, yoto.pipeline as pipeline
    src = tmp_path / "a.mp3"; src.write_bytes(b"x")
    monkeypatch.setattr(pipeline, "optimize_track", lambda s, w, **k: [src])
    seen = {}
    class Ctx:
        async def __aenter__(self): return (None, None)
        async def __aexit__(self, *a): return False
    def fake_authed(uid=None):
        seen["uid"] = uid; return Ctx()
    monkeypatch.setattr(pipeline, "authed_session", fake_authed)
    async def fu(s, g, p): return {"sha":"S","duration":1,"fileSize":1,"channels":2,"format":"mp3"}
    monkeypatch.setattr(pipeline, "upload_file", fu)
    async def ff(s, g, pid): return {"card": {}}
    monkeypatch.setattr(pipeline, "fetch_card", ff)
    monkeypatch.setattr(pipeline, "build_appended_payload", lambda d, p, icon_ref=None: {"cardId": "c"})
    async def fc(s, g, pl): return {}
    monkeypatch.setattr(pipeline, "create_content", fc)
    class Job:
        def update(self,*a): pass
        def add_log(self,*a): pass
        def finish(self,*a): pass
    asyncio.run(pipeline.run_upload(str(src), "n", "pl", Job(), uid="U7"))
    assert seen["uid"] == "U7"
```

- [ ] **Step 2: Run — expect fail**; **Step 3: Implement** — add `uid=None` to `run_upload`, pass to `authed_session(uid)`; if it does an icon import path (none currently in run_upload — icon import happens via route), no change beyond session. **Step 4: Run — expect pass**; **Step 5: Commit**

```bash
git add yoto/pipeline.py tests/test_yoto_pipeline_icon.py
git commit -m "feat(yoto): run_upload targets a specific user's account"
```

---

## Phase C — Routes + auth dependency

### Task 9: Auth routes + `require_user` + `/login`

**Files:** Modify `video2mp3.py`; Create `templates/login.html`; Test `tests/test_auth_api.py`.

**Interfaces (Produces):** `require_user(request)`; routes `/api/auth/{register,login,logout,me}`, `GET /login`.

- [ ] **Step 1: Failing test** — `tests/test_auth_api.py`

```python
import importlib, os, tempfile
os.environ["YOTO_STATE_DIR"] = tempfile.mkdtemp()
import accounts; importlib.reload(accounts)
import video2mp3
from fastapi.testclient import TestClient

client = TestClient(video2mp3.app)


def test_register_sets_cookie_and_me():
    r = client.post("/api/auth/register", json={"username": "amy", "password": "pw"})
    assert r.status_code == 200
    assert client.cookies.get("v2m_sid")
    me = client.get("/api/auth/me")
    assert me.json()["username"] == "amy"


def test_unauthed_yoto_is_401():
    c2 = TestClient(video2mp3.app)
    assert c2.get("/api/yoto/status").status_code == 401


def test_login_logout():
    client.post("/api/auth/register", json={"username": "ben", "password": "pw"})
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401
    r = client.post("/api/auth/login", json={"username": "ben", "password": "pw"})
    assert r.status_code == 200
    assert client.get("/api/auth/me").status_code == 200
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement** — in `video2mp3.py`:

```python
import accounts

COOKIE = "v2m_sid"


def require_user(request: Request) -> str:
    uid = accounts.session_user(request.cookies.get(COOKIE, ""))
    if not uid:
        raise HTTPException(status_code=401, detail="未登录")
    return uid


class AuthRequest(BaseModel):
    username: str
    password: str


def _set_session_cookie(resp, uid):
    tok = accounts.new_session(uid)
    resp.set_cookie(COOKIE, tok, httponly=True, samesite="lax",
                    max_age=30 * 24 * 3600, path="/")


@app.post("/api/auth/register")
async def auth_register(req: AuthRequest):
    try:
        uid = accounts.create_user(req.username, req.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    resp = JSONResponse({"uid": uid, "username": req.username})
    _set_session_cookie(resp, uid)
    return resp


@app.post("/api/auth/login")
async def auth_login(req: AuthRequest):
    uid = accounts.authenticate(req.username, req.password)
    if not uid:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    resp = JSONResponse({"uid": uid, "username": req.username})
    _set_session_cookie(resp, uid)
    return resp


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    accounts.end_session(request.cookies.get(COOKIE, ""))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


@app.get("/api/auth/me")
async def auth_me(request: Request):
    uid = require_user(request)
    return {**accounts.get_user(uid), "users": accounts.list_users()}


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})
```
Create a minimal `templates/login.html` (form posting to the endpoints via fetch, toggle register/login, redirect to `/` on success).

- [ ] **Step 4: Run — expect pass**; **Step 5: Commit**

```bash
git add video2mp3.py templates/login.html tests/test_auth_api.py
git commit -m "feat(users): auth routes, require_user dependency, login page"
```

---

### Task 10: Yoto binding routes

**Files:** Modify `video2mp3.py`; Test `tests/test_yoto_bind_api.py`.

**Interfaces (Produces):** `GET /api/yoto/status`, `POST /api/yoto/bind`, `GET /api/yoto/auth/url`, `POST /api/yoto/auth/exchange` — all `Depends(require_user)`.

- [ ] **Step 1: Failing test** — `tests/test_yoto_bind_api.py`

```python
import importlib, os, tempfile
os.environ["YOTO_STATE_DIR"] = tempfile.mkdtemp()
import accounts; importlib.reload(accounts)
import video2mp3
from fastapi.testclient import TestClient
from yoto import config as yconfig

client = TestClient(video2mp3.app)


def _login():
    client.post("/api/auth/register", json={"username": "z", "password": "pw"})


def test_bind_writes_env_and_status(monkeypatch):
    _login()
    r = client.post("/api/yoto/bind", json={"client_id": "CID42"})
    assert r.status_code == 200
    st = client.get("/api/yoto/status").json()
    assert st["bound"] is True and st["authed"] is False


def test_auth_url_returned(monkeypatch):
    _login()
    client.post("/api/yoto/bind", json={"client_id": "CID42"})
    monkeypatch.setattr(video2mp3.yoto_login, "authorize_url", lambda uid: "https://auth/x")
    r = client.get("/api/yoto/auth/url")
    assert r.json()["url"] == "https://auth/x"
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement** — add `from yoto import login as yoto_login` and:

```python
class BindRequest(BaseModel):
    client_id: str


class ExchangeRequest(BaseModel):
    code: str


@app.get("/api/yoto/status")
async def yoto_status(uid: str = Depends(require_user)):
    try:
        cid = yconfig.load_client_id(uid)
        bound = bool(cid)
    except Exception:
        bound = False
    tok = yconfig.load_token(uid)
    authed = bool(tok and tok.refresh_token)
    scopes = (tok.scope or "").split() if authed else []
    return {"bound": bound, "authed": authed, "scopes": scopes}


@app.post("/api/yoto/bind")
async def yoto_bind(req: BindRequest, uid: str = Depends(require_user)):
    p = yconfig.env_path(uid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"client_id: {req.client_id.strip()}\n")
    return {"ok": True}


@app.get("/api/yoto/auth/url")
async def yoto_auth_url(uid: str = Depends(require_user)):
    try:
        return {"url": yoto_login.authorize_url(uid)}
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})


@app.post("/api/yoto/auth/exchange")
async def yoto_auth_exchange(req: ExchangeRequest, uid: str = Depends(require_user)):
    try:
        tok = await yoto_login.exchange(uid, req.code)
        return {"scopes": (tok.scope or "").split()}
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
```
Add `from fastapi import Depends` to the imports.

- [ ] **Step 4: Run — expect pass**; **Step 5: Commit**

```bash
git add video2mp3.py tests/test_yoto_bind_api.py
git commit -m "feat(yoto): per-user bind + OAuth url/exchange routes"
```

---

### Task 11: Scope existing Yoto routes to the session user

**Files:** Modify `video2mp3.py`; Modify `tests/test_yoto_api.py`, `tests/test_yoto_icons_api.py`.

**Interfaces:** every existing Yoto route gains `uid: str = Depends(require_user)` and passes uid to `authed_session(uid)` / `yoto_icons.*(…, uid)` / `run_upload(…, uid)`.

- [ ] **Step 1: Update tests to authenticate first** — in `test_yoto_api.py` and `test_yoto_icons_api.py`, register once (module setup with a temp `YOTO_STATE_DIR`) so the TestClient carries the cookie; assert previously-passing behaviors still hold. Add one test:
```python
def test_upload_passes_uid(monkeypatch, tmp_path):
    # after login, run_upload receives uid from the session
    ...
    async def fake_run(audio_path, filename, playlist_id, job, icon_media_id=None, uid=None):
        seen["uid"] = uid; job.finish(True, "ok")
    ...
    assert seen["uid"]   # non-empty session uid
```

- [ ] **Step 2: Run — expect fail** (routes lack uid / 401)

- [ ] **Step 3: Implement** — add `uid: str = Depends(require_user)` to `yoto_playlists`, `yoto_upload`, `yoto_upload_status` (status stays global by job_id — keep require_user for consistency), `yoto_icons_local`, `yoto_icons_external`, `yoto_icons_import`, `yoto_icons_thumb`, `yoto_icons_refresh`. Pass uid:
  - `authed_session(uid)` everywhere.
  - `yoto_icons.search_yoto(yoto_icons.load_yoto(uid), q)`, `yoto_icons.list_me(yoto_icons.load_me(uid))`.
  - `yoto_icons.import_yotoicon(session, get_token, req.icon_id, uid)`.
  - `yoto_icons.refresh_from_api(session, get_token, uid)`.
  - `run_upload(audio_path, filename, req.playlist_id, job, icon_media_id=req.icon_media_id, uid=uid)`.

- [ ] **Step 4: Run full suite — expect pass**; **Step 5: Commit**

```bash
git add video2mp3.py tests/test_yoto_api.py tests/test_yoto_icons_api.py
git commit -m "feat(yoto): scope all Yoto routes to the session user"
```

---

## Phase D — Frontend

### Task 12: Login page + global 401 guard

**Files:** `templates/login.html` (fill in), `templates/scrape.html` (guard + fetch wrapper).

- [ ] **Step 1: Implement `login.html`** — centered card, username/password inputs, a 登录/注册 toggle, posts to `/api/auth/login` or `/api/auth/register`, on 200 `location='/'`, shows error text on failure. Reuse existing dark styling classes.
- [ ] **Step 2: Global guard in `scrape.html`** — add a `apiFetch(url,opts)` helper that wraps `fetch`; on `401` does `location='/login'`. Route the Yoto calls through it. Also on page load call `/api/auth/me`; if 401 → redirect.
- [ ] **Step 3: Manual verify** — open `/` unauthenticated → redirected to `/login`; register → land on app.
- [ ] **Step 4: Commit**

```bash
git add templates/login.html templates/scrape.html
git commit -m "feat(users): login page + 401 redirect guard"
```

---

### Task 13: Header user chip + Yoto binding modal

**Files:** `templates/scrape.html`.

- [ ] **Step 1: Header** — show current username, a **退出** button (POST logout → `/login`), and a **Yoto** status chip driven by `GET /api/yoto/status` (未绑定 / 已绑定未登录 / 已登录).
- [ ] **Step 2: Binding modal** — fields: client_id → **绑定** (`POST /api/yoto/bind`); **获取登录链接** (`GET /api/yoto/auth/url`, `window.open(url)`); paste callback URL → **完成登录** (`POST /api/yoto/auth/exchange`), show returned scopes; refresh the status chip.
- [ ] **Step 3: Upload modal hint** — when `status.authed` is false, the ⬆ Yoto modal shows "请先绑定 Yoto 账号" with a button opening the binding modal; icon/playlist calls already 401→guard otherwise.
- [ ] **Step 4: Manual verify** — bind a client_id, get URL, log in at Yoto, paste callback, see scopes; then upload works against that account.
- [ ] **Step 5: Commit**

```bash
git add templates/scrape.html
git commit -m "feat(users): header user chip + Yoto account binding modal"
```

---

## Final verification
- [ ] `python -m pytest -q` from `tests/` — all green.
- [ ] Two TestClient sessions (two users) resolve to two uids and two state dirs.
- [ ] Manual: register two users in two browsers, bind different client_ids, confirm playlists/icons differ and uploads target the right account; downloads/history shared.
- [ ] superpowers:finishing-a-development-branch.
