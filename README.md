# YotoFromSocialMedias

Download audio from social-media / video sites (YouTube, Bilibili, Douyin, QQ音乐)
and push it straight into your [Yoto](https://www.yotoplay.com/) player as playlist
cards — with per-user accounts, in-browser trimming, and a Yoto icon picker.

It's a FastAPI app: users log in, bind their Yoto account (OAuth), fetch/scrape a
track, optionally trim a clip, pick a card icon, and upload. Audio is bitrate-capped
and silence-split before upload, then appended as chapters to a chosen Yoto playlist.

## Forked dependency: `cdnninja/yoto_api`

This project **forks** https://github.com/cdnninja/yoto_api. Our fork lives at
https://github.com/cobraliu/yoto_api and is vendored here as a git submodule at
[`vendor/yoto_api`](vendor/yoto_api).

**Why a fork instead of the upstream package, and what we use it for:**

Upstream `yoto_api` is a **read / playback** library — device-code auth, reading the
library, player state, and MQTT control. It has **no** content upload, no playlist
create/append, no Authorization-Code + PKCE web login, and no icon management, which
are exactly the write operations this app is built around. So the entire Yoto write
path is our own code, consolidated in [`app/yoto_client.py`](app/yoto_client.py):

- Authorization-Code **+ PKCE** web login (paste-callback), per-user token storage
  and lazy refresh;
- media **upload + transcode** and playlist **create / append** (`POST /content`);
- icon catalog + yotoicons.com import ([`app/yoto_icons.py`](app/yoto_icons.py)).

The one thing we take directly from the fork is its `Token` dataclass: `yoto_client`
loads `vendor/yoto_api/yoto_api/Token.py` **by file path** and re-exports it (loading
it directly avoids pulling the fork's `aiohttp`/`aiomqtt` client stack in just for a
dataclass, while still sourcing the model from the submodule). The fork is kept
unmodified from upstream so it can track and contribute back.

Every module that borrows from or reimplements upstream carries a `FORK NOTICE`
comment pointing here.

## Layout

```
yotofromsocialmedias.py   # entry point: serves app.routers:app
app/                      # all backend business logic
  routers.py              #   FastAPI routes + app instance
  yoto_client.py          #   core Yoto REST client (auth + PKCE + upload + content)
  yoto_icons.py           #   icon catalog + yotoicons import
  yoto_audio.py           #   ffmpeg probe / split / encode
  yoto_pipeline.py        #   optimize -> upload -> append orchestration
  yoto_jobs.py            #   in-memory upload-job tracking
  accounts.py             #   file-backed users + sessions
  taskmanager.py schemas.py utils.py
  bilibili.py douyin.py qqmusic.py ytdownloader.py   # source downloaders
vendor/yoto_api/          # git submodule: fork of cdnninja/yoto_api (Token model)
templates/ static/        # frontend (Jinja2 + vanilla JS)
tests/                    # pytest suite (run from tests/)
scripts/                  # yoto_dryrun.py and other dev helpers
```

## Setup

```bash
git clone git@github-cobraliu:cobraliu/YotoFromSocialMedias.git
cd YotoFromSocialMedias
git submodule update --init          # populate vendor/yoto_api (the fork)
pip install -r requirements.txt
```

`ffmpeg` / `ffprobe` must be on `PATH` (audio probing, splitting, encoding).

Each user supplies their own Yoto `client_id`; per-user state (client_id, tokens,
icon catalogs) lives under `.yoto/users/<uid>/` and is never committed.

## Run

```bash
python yotofromsocialmedias.py            # serves on 0.0.0.0:8081
# or:
uvicorn yotofromsocialmedias:app --host 0.0.0.0 --port 8081
```

Run from the repo root so `templates/`, `static/` and `data/` resolve.

## Tests

```bash
cd tests && python -m pytest -q
```

## Notes on secrets

Never committed: `.env` / per-user `client_id`, Yoto tokens under `.yoto/`,
`cookies.txt` (site session cookies), logs, and the `data/` media tree. See
`.gitignore`.

## License

This project is licensed under the [Apache License 2.0](LICENSE); see also
[`NOTICE`](NOTICE) for attribution.

The vendored fork at [`vendor/yoto_api`](vendor/yoto_api) is a git submodule
and keeps its own upstream license (see `vendor/yoto_api/LICENSE`); it is not
covered by this project's Apache-2.0 license.
