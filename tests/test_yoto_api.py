from fastapi.testclient import TestClient
import app.accounts as accounts
import app.routers as video2mp3
from app.routers import app

client = TestClient(app)
# Authenticate this module's client so the (login-gated) Yoto routes work.
accounts.create_user("apitester", "pw", is_admin=True)
client.post("/api/auth/login", json={"username": "apitester", "password": "pw"})


def test_upload_requires_login():
    anon = TestClient(app)
    r = anon.post("/api/yoto/upload",
                  json={"task_id": "x", "filename": "n", "playlist_id": "p"})
    assert r.status_code == 401


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

    async def fake_run(audio_path, filename, playlist_id, job,
                       icon_media_id=None, uid=None):
        job.finish(True, "完成")
    monkeypatch.setattr(video2mp3.yoto_pipeline, "run_upload", fake_run)

    r = client.post("/api/yoto/upload",
                    json={"task_id": "t", "filename": "song", "playlist_id": "p"})
    assert r.status_code == 200
    jid = r.json()["job_id"]
    s = client.get(f"/api/yoto/upload-status/{jid}")
    assert s.status_code == 200


def test_upload_passes_session_uid(monkeypatch, tmp_path):
    f = tmp_path / "audio.mp3"
    f.write_bytes(b"x")
    monkeypatch.setattr(video2mp3.taskManager, "get_file_path",
                        lambda tid, ft: str(f))
    seen = {}

    async def fake_run(audio_path, filename, playlist_id, job,
                       icon_media_id=None, uid=None):
        seen["uid"] = uid
        job.finish(True, "ok")
    monkeypatch.setattr(video2mp3.yoto_pipeline, "run_upload", fake_run)

    r = client.post("/api/yoto/upload",
                    json={"task_id": "t", "filename": "s", "playlist_id": "p"})
    assert r.status_code == 200
    import time
    time.sleep(0.05)
    assert seen.get("uid")  # a non-empty session uid was threaded through


def test_upload_no_playlist_400(monkeypatch, tmp_path):
    f = tmp_path / "audio.mp3"
    f.write_bytes(b"x")
    monkeypatch.setattr(video2mp3.taskManager, "get_file_path",
                        lambda tid, ft: str(f))
    r = client.post("/api/yoto/upload",
                    json={"task_id": "t", "filename": "song", "playlist_id": ""})
    assert r.status_code == 400


def test_upload_trim_uses_trim_file(monkeypatch, tmp_path):
    clip = tmp_path / "clip.mp3"
    clip.write_bytes(b"x")
    monkeypatch.setattr(video2mp3, "_find_trim_info",
                        lambda tid: {"output_path": str(clip),
                                     "output_filename": "clip.mp3"})
    seen = {}

    async def fake_run(audio_path, filename, playlist_id, job,
                       icon_media_id=None, uid=None):
        seen["path"] = audio_path
        seen["filename"] = filename
        job.finish(True, "ok")
    monkeypatch.setattr(video2mp3.yoto_pipeline, "run_upload", fake_run)

    r = client.post("/api/yoto/upload",
                    json={"task_id": "", "filename": "", "playlist_id": "p",
                          "trim_id": "T1"})
    assert r.status_code == 200
    import time
    time.sleep(0.05)
    assert seen["path"] == str(clip)
    assert seen["filename"] == "clip"   # default from output_filename, ext stripped


def test_upload_trim_default_name_prefixes_original(monkeypatch, tmp_path):
    clip = tmp_path / "clip.mp3"
    clip.write_bytes(b"x")
    orig = tmp_path / "原曲.mp3"
    orig.write_bytes(b"y")
    monkeypatch.setattr(video2mp3, "_find_trim_info",
                        lambda tid: {"output_path": str(clip),
                                     "output_filename": "副歌.mp3",
                                     "task_id": "TASK"})
    # task audio resolves to the original file -> its stem prefixes the name
    monkeypatch.setattr(video2mp3.taskManager, "get_file_path",
                        lambda tid, ft: str(orig) if tid == "TASK" else None)
    seen = {}

    async def fake_run(audio_path, filename, playlist_id, job,
                       icon_media_id=None, uid=None):
        seen["filename"] = filename
        job.finish(True, "ok")
    monkeypatch.setattr(video2mp3.yoto_pipeline, "run_upload", fake_run)

    # filename empty -> server builds "<原名>_<片段名>"
    r = client.post("/api/yoto/upload",
                    json={"filename": "", "playlist_id": "p", "trim_id": "T1"})
    assert r.status_code == 200
    import time
    time.sleep(0.05)
    assert seen["filename"] == "原曲_副歌"


def test_upload_trim_missing_404(monkeypatch):
    monkeypatch.setattr(video2mp3, "_find_trim_info", lambda tid: None)
    r = client.post("/api/yoto/upload",
                    json={"task_id": "", "filename": "", "playlist_id": "p",
                          "trim_id": "nope"})
    assert r.status_code == 404


def test_status_unknown_404():
    assert client.get("/api/yoto/upload-status/nope").status_code == 404


def test_create_playlist_requires_login():
    anon = TestClient(app)
    assert anon.post("/api/yoto/playlists", json={"title": "X"}).status_code == 401


def test_create_playlist_returns_item(monkeypatch):
    import contextlib

    async def fake_create(session, gt, title):
        return {"id": "NID", "title": title, "n_tracks": 0, "duration": 0}
    monkeypatch.setattr(video2mp3, "yoto_create_playlist", fake_create)

    @contextlib.asynccontextmanager
    async def fake_authed(uid=None):
        yield (None, None)
    monkeypatch.setattr(video2mp3, "authed_session", fake_authed)

    r = client.post("/api/yoto/playlists", json={"title": "新歌单"})
    assert r.status_code == 200
    assert r.json()["id"] == "NID" and r.json()["title"] == "新歌单"


def test_create_playlist_empty_title_400():
    assert client.post("/api/yoto/playlists", json={"title": "  "}).status_code == 400


def test_playlists_page_renders():
    r = client.get("/yoto/playlists")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Yoto" in r.text


def test_playlist_detail_requires_login():
    anon = TestClient(app)
    assert anon.get("/api/yoto/playlists/C1").status_code == 401


def test_playlist_detail_returns_tracks(monkeypatch):
    import contextlib

    async def fake_fetch(session, gt, card_id):
        return {"card": {
            "cardId": card_id, "title": "L",
            "metadata": {"media": {"duration": 100}},
            "content": {"chapters": [
                {"key": "01", "title": "Ch", "duration": 100,
                 "tracks": [{"title": "T", "duration": 100, "format": "aac"}]},
            ]},
        }}
    monkeypatch.setattr(video2mp3, "yoto_fetch_card", fake_fetch)

    @contextlib.asynccontextmanager
    async def fake_authed(uid=None):
        yield (None, None)
    monkeypatch.setattr(video2mp3, "authed_session", fake_authed)

    r = client.get("/api/yoto/playlists/C1")
    assert r.status_code == 200
    d = r.json()
    assert d["id"] == "C1" and d["duration"] == 100
    assert d["chapters"][0]["tracks"][0]["title"] == "T"
