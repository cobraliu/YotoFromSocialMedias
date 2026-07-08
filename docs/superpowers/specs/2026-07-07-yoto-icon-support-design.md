# Yoto Icon Support — Design

**Date:** 2026-07-07
**Extends:** `2026-07-06-yoto-autoupload-design.md` (the per-track "⬆ Yoto" append feature)

## Goal

When appending a downloaded track to a Yoto playlist, let the user optionally
attach a display icon (`display.icon16x16 = yoto:#<mediaId>`) to the new
chapter(s), chosen from a **local icon catalog** of Yoto's built-in icons and
the user's previously-uploaded icons — plus the ability to pull a fresh icon
from yotoicons.com (download → upload to Yoto → record locally).

Picking no icon leaves the chapter icon-free (current behavior, unchanged).

## Background — how Yoto icons work

A chapter/track icon is the string `yoto:#<mediaId>` where `mediaId` is 43 chars.
A `mediaId` only exists once the icon file lives in Yoto's media system.

Confirmed APIs (yoto.dev), all requiring OAuth scope `user:icons:manage`:

| Purpose | Method + path | Response |
|---|---|---|
| Yoto built-in icons | `GET /media/displayIcons/user/yoto` | `{displayIcons:[{mediaId,title,publicTags,url,...}]}` |
| My uploaded icons | `GET /media/displayIcons/user/me` | `{displayIcons:[{mediaId,url,createdAt,...}]}` (no title/tags) |
| Upload custom icon | `POST /media/displayIcons/user/me/upload?autoConvert=true&filename=<name>` (raw PNG body) | `{displayIcon:{mediaId,url,...}}` |

The two GET responses have already been captured to disk by the user:
- `.yoto/yoto.icons.json` — 523 built-ins (static; searchable by title + publicTags)
- `.yoto/me.icons.json` — 57 uploaded (no titles; browse-only)

yotoicons.com (public, no auth):
- Search page: `https://www.yotoicons.com/icons?tag=<q>&sort=popular&type=singles`
- Thumbnail: `/static/uploads/{id}.png` · Full PNG: `/uploads/{id}.png`

## Decisions (from brainstorming)

- **Local-first:** the picker reads from the two `.yoto/*.icons.json` files, not
  live API calls. A manual "refresh" re-syncs both from the API.
- **Default when nothing picked:** no icon.
- **"我的图标" tab:** browse-only (thumbnail grid, most-recent first) — no text
  search, since those records carry no titles.
- **yotoicons import:** on select, download the PNG, upload it via the custom-icon
  endpoint, append the returned `{mediaId,url,createdAt}` to `me.icons.json`, and
  auto-select it.
- **Scope:** add `user:icons:manage` to `auth.SCOPES` so a future re-login grants
  it. Reading/attaching existing icons needs no new scope; only uploading does.
  If the cached token lacks it, upload returns 401 → user re-logs in.

## Architecture

### `yoto/icons.py` (new) — catalog + acquisition

Normalized catalog entry:
```python
{"mediaId": str, "ref": "yoto:#<mediaId>", "source": "yoto"|"me",
 "title": str|None, "tags": list[str], "url": str}
```

Pure/local helpers (unit-tested, no network):
- `_read_catalog(path) -> list[dict]` — parse `{displayIcons:[...]}`; missing file → `[]`.
- `to_entry(raw, source) -> dict` — normalize one API record.
- `search_yoto(entries, q) -> list` — case-insensitive substring over title+tags; empty q → all.
- `list_me(entries) -> list` — reverse-chronological by `createdAt`.
- `parse_yotoicons(html) -> list[{id, thumb}]` — regex `/static/uploads/(\d+)\.png`, de-duped, order-preserved.
- `append_me(path, raw) -> None` — add record to `me.icons.json`, dedup by `mediaId`.

Network helpers (take `session, get_token`, resolve token per call like the rest of the package):
- `upload_custom_icon(session, get_token, data: bytes, filename: str) -> dict` — POST upload, return raw `displayIcon`.
- `import_yotoicon(session, get_token, icon_id: str) -> dict` — GET `/uploads/{id}.png`, `upload_custom_icon`, `append_me`, return entry.
- `search_yotoicons(session, q: str) -> list[{id, thumb}]` — GET search page, `parse_yotoicons`.
- `fetch_icon_bytes(session, get_token, url: str) -> (bytes, content_type)` — auth'd fetch for the thumbnail proxy.
- `refresh_from_api(session, get_token) -> None` — GET both lists, overwrite both JSON files.

### `yoto/endpoints.py` — add constants
`DISPLAY_ICONS_YOTO`, `DISPLAY_ICONS_ME`, `DISPLAY_ICONS_UPLOAD`, `YOTOICONS_BASE`.

### `yoto/uploader.py` — attach icon
`build_appended_payload(card_detail, new_parts, icon_ref=None)`:
- when `icon_ref` (a validated `yoto:#<43>`), set `display={"icon16x16":icon_ref}` on
  each appended chapter **and** its track (all split parts share it);
- when `None`, emit no `display` (unchanged). Existing-chapter icon normalization stays.
- Validate `icon_ref` via existing `normalize_icon`; drop if invalid.

### `yoto/config.py` — paths
`yoto_icons_path()`, `me_icons_path()` under `state_dir()`.

### `yoto/pipeline.py` — thread the choice
`run_upload(audio_path, filename, playlist_id, job, icon_media_id=None)` builds
`icon_ref = f"yoto:#{icon_media_id}"` when present and passes it to
`build_appended_payload`.

### `video2mp3.py` — routes
- `GET  /api/yoto/icons?q=&source=yoto|me|all` → local catalog search/browse.
- `GET  /api/yoto/icons/search-external?q=car` → yotoicons results `[{id,thumb}]`.
- `POST /api/yoto/icons/import` `{icon_id}` → `{mediaId,url}` (download+upload+record).
- `GET  /api/yoto/icons/thumb?media_id=` → auth'd PNG proxy (StreamingResponse).
- `POST /api/yoto/icons/refresh` → re-sync both files; returns counts.
- `POST /api/yoto/upload` body gains optional `icon_media_id: str|None`.

### `templates/scrape.html` — modal
Add an optional "图标" block to `#yoto-modal`: three tabs
**[Yoto图标][我的图标][yotoicons搜索]**, a search box (hidden on 我的图标), a
thumbnail grid. Click selects (highlight); yotoicons click imports (spinner →
lands in 我的图标, auto-selected). Selected `mediaId` (or none) → `submitYoto`.
Yoto/me thumbnails load via the proxy; yotoicons thumbnails load directly.

## Data flow (yotoicons pick)

1. User types "car" in yotoicons tab → `GET /api/yoto/icons/search-external?q=car`.
2. Backend scrapes yotoicons, returns `[{id, thumb}]`; grid shows public thumbs.
3. User clicks one → `POST /api/yoto/icons/import {icon_id}`.
4. Backend: download `/uploads/{id}.png` → `POST …/me/upload` → mediaId →
   append to `me.icons.json` → return `{mediaId,url}`.
5. Frontend selects that mediaId; on submit it's sent as `icon_media_id`.
6. `run_upload` sets `display.icon16x16=yoto:#<mediaId>` on the new chapter(s).

## Error handling

- Missing/corrupt JSON file → treated as empty catalog (picker still opens).
- yotoicons search/download failure → route returns 502 `{error}`; modal shows a toast, upload still works icon-free.
- Icon upload 401 (missing scope) → surfaced as `{error}`; message tells user to re-login.
- Invalid/short mediaId → `normalize_icon` drops it; chapter uploads without icon rather than 400.

## Testing

- `icons.py` pure helpers: `_read_catalog`, `to_entry`, `search_yoto`,
  `list_me`, `parse_yotoicons` (regex against a saved yotoicons HTML fixture),
  `append_me` (dedup).
- `uploader.build_appended_payload(..., icon_ref=...)`: icon applied to all
  split parts + track; `None` → no display; invalid ref dropped.
- Network helpers via aiohttp mock (fake session): `upload_custom_icon`,
  `import_yotoicon`, `search_yotoicons`.
- API routes via TestClient with `icons` monkeypatched: icons search, import,
  upload-with-icon passes `icon_media_id` through.

## Out of scope

- Editing/deleting existing Yoto icons.
- "Upload my own local file" tab (yotoicons covers acquisition; can add later).
- Setting the playlist **cover** image (this is per-chapter icons only).
