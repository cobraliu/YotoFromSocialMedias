# User Management + Per-User Yoto Binding — Design

**Date:** 2026-07-07
**Extends:** the Yoto upload + icon features (single-account today).

## Goal

Turn V2M from a single shared Yoto account into a multi-user app:
- Users register/log in to V2M with a username + password.
- Each user binds **their own** Yoto `client_id` and completes a Yoto OAuth
  login (PKCE). If the bound client has no valid token, they run the login.
- Each user's Yoto **icons and playlists are separated** (their own account).
- **Downloaded audio is shared** across all users (unchanged `data/`).
- The **upload-to-Yoto flow targets the logged-in user's** bound account.

## Decisions (brainstorming)

1. **App auth:** username + password, server-side sessions.
2. **Yoto OAuth redirect:** paste-callback — show the authorize URL, user logs
   in at Yoto, pastes the `127.0.0.1:8787/callback?...` URL back, we exchange.
   (No public URL needed; works from any device. Matches `yoto_fork/login.py`.)
3. **Current user:** per-device **cookie** session — each browser/phone selects
   its own active user; two phones can drive two Yoto accounts at once.

## State layout

```
.yoto/
  users.json                # [{uid, username, salt, pw, created}]
  sessions.json             # {session_token: {uid, created}}
  users/<uid>/
    .env                    # this user's Yoto client_id
    .yoto_token.json        # this user's OAuth token
    .yoto_pkce.json         # transient PKCE verifier during login
    yoto.icons.json         # this user's Yoto built-in catalog
    me.icons.json           # this user's uploaded-icon catalog
    yotoicons.cache.json    # this user's yotoicons import map (id -> mediaId)
data/                       # SHARED (unchanged)
  <task_id>/...             # downloaded audio, shared by all users
  yoto_icons/               # icon image files, keyed by globally-unique
                            #   mediaId (uploads) or source yotoicon-<id> (raw)
```

Playlists are never stored — fetched live per request from the user's account,
so they're inherently separated. `data/yoto_icons/` stays shared: upload thumbs
are keyed by account-unique mediaId, raw yotoicons PNGs by source id (identical
across users), so no cross-user leakage.

## Modules

### `accounts.py` (new, V2M root) — users + sessions
Pure, file-backed, unit-tested. No Yoto knowledge.
- `hash_password(pw, salt=None) -> (salt, hexhash)` — stdlib pbkdf2_hmac sha256,
  200k iters, per-user salt.
- `verify_password(pw, salt, expected) -> bool` — `hmac.compare_digest`.
- `create_user(username, password) -> uid` — reject duplicate username; on the
  **first** user, migrate any legacy `.yoto/` binding into that user's dir.
- `authenticate(username, password) -> uid | None`.
- `list_users() -> [{uid, username}]`, `get_user(uid)`, `delete_user(uid)`.
- `new_session(uid) -> token`, `session_user(token) -> uid | None`,
  `end_session(token)`. Persisted to `.yoto/sessions.json`.
- `migrate_legacy_into(uid)` — if `.yoto/.env`/`.yoto_token.json` exist and
  `users/<uid>/` is empty, move legacy files (env, token, pkce, *.icons.json,
  yotoicons.cache.json) into it. Runs once, best-effort.

### `yoto/config.py` — per-user paths
All path helpers gain a `uid` argument and resolve under `user_dir(uid)`:
- `user_dir(uid) -> state_dir()/"users"/uid`
- `env_path(uid)`, `token_path(uid)`, `pkce_path(uid)`, `yoto_icons_path(uid)`,
  `me_icons_path(uid)`, `yotoicons_cache_path(uid)`
- `load_client_id(uid)`, `load_token(uid)`, `save_token(token, uid)`
- `icons_cache_dir()` / `data_dir()` unchanged (shared).

### `yoto/login.py` (new) — PKCE login, ported from `yoto_fork/login.py`
- `authorize_url(uid) -> str` — builds the authorize URL with SCOPES (incl.
  `user:icons:manage`), generates + stores PKCE verifier/state at `pkce_path(uid)`.
- `async exchange(uid, code_or_url) -> Token` — extracts code (raw or full
  callback URL), exchanges with the saved verifier, `save_token(uid)`, removes
  PKCE file. Returns the token (for scope reporting).
- Reuses `endpoints`, `_build_token_from_body`, `_pkce_pair`, `_extract_code`,
  `_decode_scopes`. `REDIRECT_URI = http://127.0.0.1:8787/callback`.

### `yoto/auth.py` — `authed_session(uid)`
`authed_session(uid)` loads that user's client_id + token; raises a user-facing
RuntimeError if unbound/untokened. Everything else (TokenProvider, refresh)
unchanged, but `save_token` calls pass `uid`.

### `yoto/icons.py` — thread `uid`
`load_yoto(uid)`, `load_me(uid)`, `import_yotoicon(session, get_token, icon_id,
uid)`, `refresh_from_api(session, get_token, uid)`, `_read/_write_import_cache(uid)`.
Pure helpers (`to_entry`, `search_yoto`, `list_me`, `parse_yotoicons`,
`append_me(path,...)`) unchanged. Icon image cache stays shared.

### `yoto/pipeline.py` — `run_upload(..., uid)`
`run_upload` takes `uid` and opens `authed_session(uid)`; icon import (if any)
scoped to `uid`.

### `video2mp3.py` — auth + binding + scoping
Auth dependency:
- `require_user(request) -> uid` — reads `v2m_sid` cookie → `session_user`;
  raise 401 if absent/invalid. Applied to all `/api/*` routes except auth
  endpoints and static/index.
App-auth routes:
- `POST /api/auth/register` `{username, password}` → set cookie, `{uid, username}`.
- `POST /api/auth/login` `{username, password}` → set cookie or 401.
- `POST /api/auth/logout` → clear cookie/session.
- `GET  /api/auth/me` → `{uid, username, users:[…]}` (users list for a switcher).
- `GET  /login` → login/register page (HTML).
Yoto-binding routes (all `Depends(require_user)`):
- `GET  /api/yoto/status` → `{bound: bool, authed: bool, scopes: [...]}`.
- `POST /api/yoto/bind` `{client_id}` → write `env_path(uid)`.
- `GET  /api/yoto/auth/url` → `{url}` (authorize_url(uid)).
- `POST /api/yoto/auth/exchange` `{code}` → exchange(uid, code) → `{scopes}`.
Existing Yoto routes gain `uid = Depends(require_user)` and pass it into
`authed_session(uid)` / `yoto_icons.*(…, uid)` / `run_upload(…, uid)`.
Download/history/trim routes require login but are **not** user-filtered (shared).

### Cookie
`v2m_sid`, HttpOnly, SameSite=Lax, Path=/, ~30-day max-age. Value = opaque
session token (`secrets.token_urlsafe`). No secret-signing lib needed — the
token is validated against the server-side session store.

## Frontend (`templates/`)
- `login.html` — username/password login + register (toggle). On success →
  redirect to `/`.
- `scrape.html` header — current username + **切换/退出** and a **Yoto 账号**
  status chip (未绑定 / 已绑定未登录 / 已登录), opening a binding modal:
  1. client_id input → **绑定**;
  2. **获取登录链接** → opens the authorize URL in a new tab;
  3. paste callback URL → **完成登录** → exchange; show resulting scopes.
- Global guard: any `/api/*` 401 → redirect to `/login`.
- The existing upload/icon modal is unchanged (cookie is sent automatically);
  it now shows a hint + link to bind if `GET /api/yoto/status` says unbound.

## Migration & back-compat
- First `create_user` inherits the existing `.yoto/` Yoto binding via
  `migrate_legacy_into(uid)` — the current working token/catalogs are preserved
  for whoever registers first.
- If no users exist, all `/api/*` (except auth) 401 → the UI shows `/login`
  with a "首次使用请注册" hint.

## Security notes
- Passwords: pbkdf2_hmac sha256, 200k iters, per-user random salt; constant-time
  compare. `users.json`/`sessions.json` chmod 600.
- Sessions server-side; logout invalidates immediately.
- Adding auth closes the previously-open app; downloads remain visible to any
  logged-in user by design ("下载的资源共享").
- Per-user Yoto tokens never cross dirs; `authed_session(uid)` is the only
  loader and always takes an explicit uid (safe for detached upload tasks).

## Testing
- `accounts.py`: hash/verify, create/dup-reject, authenticate, sessions,
  legacy migration (temp dirs).
- `config.py`: per-uid path resolution.
- `yoto/login.py`: `authorize_url` writes PKCE + contains client_id/scope/
  challenge; `exchange` parses raw code vs full URL (mock session).
- `yoto/icons.py`: uid-scoped catalog read/write isolation between two uids.
- Routes (TestClient): register→cookie set; unauth 401; login/logout; bind
  writes env; status reflects bound/authed; upload scoped to session uid;
  two sessions map to two uids.

## Out of scope
- Roles/permissions/admin UI, password reset/email, OAuth auto-capture callback,
  rate limiting. (Can follow later.)
