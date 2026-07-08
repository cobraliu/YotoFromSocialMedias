# Yoto Auto-Upload for V2M — Design

Date: 2026-07-06

## Problem

V2M downloads audio from Douyin/Bilibili/QQ Music/YouTube into `data/<task_id>/`.
Those raw files can stutter ("卡顿") when played on a Yoto player — typically
because the bitrate is too high or a single track is very long. We want a
one-click path from a downloaded track to a Yoto playlist that also *optimizes*
the audio on the way up:

1. Cap the bitrate.
2. Cap single-file length (split long tracks).

The `yoto-update` sibling project already implements these primitives; we port
the needed pieces into V2M rather than depending on the sibling repo, so V2M
stays independently deployable.

## User-facing behavior

In the V2M download/history list, each downloaded item gets an **"⬆ Yoto"**
button. Clicking it opens a modal:

- **文件名 (filename)** — text input, prefilled with the track's filename
  (extension stripped). Becomes the Yoto chapter/track title.
- **Playlist** — a `<select>` populated from the user's existing Yoto library.
  Upload **appends** the optimized track to the chosen playlist (no new-card
  creation in this version).

On submit the modal polls job status and shows a success/failure toast. If the
track is split into N parts, they append as chapters titled `<name>-1 … <name>-N`.

## Architecture

### New package `V2M/yoto/` (slim, vendored from yoto-update)

Only what's needed is ported; the full `yoto_api` package is **not** copied.

| File | Responsibility |
|------|----------------|
| `endpoints.py` | URL constants: `BASE_URL`, `TOKEN_URL`, `AUTHORIZE_URL`, `CARDS_LIBRARY` |
| `token.py` | Minimal `Token` dataclass (copied verbatim from `yoto_api/Token.py`) |
| `config.py` | Resolve state dir (`YOTO_STATE_DIR`, default `V2M/.yoto/`); load `client_id` from `.env`; load/save `.yoto_token.json` |
| `auth.py` | `authed_session()` async ctx mgr: refresh-if-near-expiry + persist rotated token |
| `audio.py` | `probe_duration`, `src_kbps`, `target_kbps`, `detect_silence_mids`, `plan_cuts`, `encode_mp3`, `readable_duration` (ported from `forklib.py`) |
| `uploader.py` | `get_upload_url`, `put_upload`, `poll_transcode`, `upload_file` (sha-cached), `fetch_card`, `list_playlists`, `append_track_to_playlist` |

`append_track_to_playlist` is the one genuinely new function: it fetches the
target card, builds a new chapter per optimized part, appends to the existing
`content.chapters` (continuing the `key`/`overlayLabel` numbering), recomputes
`metadata.media` totals, and `POST /content` with the existing `cardId` to
update in place.

### New `V2M/yoto_jobs.py`

In-memory upload-job manager mirroring the existing `taskManager` pattern:
each job tracks `progress`, `status`, `success`, `error_message`, `log`. The UI
polls it. (In-memory is sufficient — an upload job is short-lived; no disk
persistence required.)

### Edits to `video2mp3.py` — 3 endpoints

- `GET  /api/yoto/playlists` → `[{id, title, n_tracks}]` from `/card/family/library`.
- `POST /api/yoto/upload` → body `{task_id, filename, playlist_id}`; starts a
  background asyncio task and returns `{job_id}`.
- `GET  /api/yoto/upload-status/{job_id}` → job status dict.

### Edits to `templates/scrape.html`

Add the "⬆ Yoto" button to each list item and the upload modal (filename input +
playlist select + progress). Playlists load lazily when the modal opens.

## Data flow (upload job)

```
data/<task_id>/audio.*  (from taskManager.get_file_path(task_id, "audio"))
  → probe_duration + src_kbps
  → kbps_eff = min(BITRATE_CAP, src_kbps)
  → if dur > SEG_MAX: silence-aware split into ~equal ≤SEG_MAX parts, else 1 part
  → encode each part → data/<task_id>/yoto/enc_XX.mp3   (skip if exists)
  → upload_file each  (sha cached in enc_XX.mp3.sha.json) → transcodedSha
  → fetch_card(playlist_id)
  → append one chapter per part (title = filename or "<filename>-i")
  → POST /content (cardId = playlist_id)
```

Working/encoded files + `.sha.json` cache live under `data/<task_id>/yoto/`,
making a re-run **resumable** (finished parts skip encode + upload).

## Optimization defaults (match yoto-update fork)

- `BITRATE_CAP = 96` kbps — never upscales a lower-bitrate source
  (`min(cap, source_kbps)`).
- `SEG_MAX = 300` s (5 min) — silence-aware equal split at `-30dB`/0.35s.

These are module-level constants in `yoto/audio.py` / `yoto/uploader.py`, easy to
tune later. Not exposed in the UI in this version.

## Credentials

`V2M/.yoto/` (gitignored) holds `.env` (`client_id`) and `.yoto_token.json`.
Seed it once by copying both from `yoto-update`. `YOTO_STATE_DIR` env var
overrides the location.

Caveat: Yoto refresh tokens are single-use and rotate on refresh. V2M keeping
its **own** token copy avoids racing `yoto-update`'s refreshes. If the token is
ever fully invalid, re-run the two-step PKCE login (documented in
`yoto-update/yoto_fork/login.py`) against V2M's state dir.

## Error handling

- **No token / expired refresh** → job fails with `请重新登录 Yoto`; surfaced in
  the modal. `/api/yoto/playlists` returns a clear error so the modal can show it.
- **ffmpeg / upload / transcode failure** → job marked failed with the message;
  already-finished parts remain sha-cached for retry.
- **Invalid `playlist_id` / fetch failure** → job fails with the API error text.
- Audio file missing for `task_id` → 404-style job failure.

## Testing

- **Unit (no network):**
  - `target_kbps` caps correctly (source below/above cap, unknown bitrate).
  - `plan_cuts` produces ≤SEG_MAX equal parts and snaps to silence within window.
  - filename → chapter title derivation (single vs split `-i` naming; extension
    stripping).
  - `append_track_to_playlist` payload builder (pure): given a fake existing card
    + parts, produces correct appended chapters, continued numbering, and
    recomputed media totals. Factor the payload build into a pure helper so it is
    testable without HTTP.
- **Manual end-to-end:** a `--dry-run` code path (or a small script) that runs the
  optimize + payload build and prints the payload without uploading, then a real
  run against a throwaway playlist.

## Dependencies

- `aiohttp` (3.13.2 present), `ffmpeg`/`ffprobe` (present, already used by V2M's
  trim feature). No new installs expected. Add a `requirements.txt` entry for
  `aiohttp` for reproducibility.

## Out of scope (this version)

- Creating a brand-new playlist/card from the V2M UI (append-only for now).
- Per-upload bitrate/length controls in the UI (fixed defaults).
- Editing/removing already-uploaded tracks.
- Icon assignment for uploaded chapters (uploaded tracks get no custom icon).
