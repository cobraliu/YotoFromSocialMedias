import contextlib
import time
import app.accounts as accounts
import app.routers as video2mp3
from fastapi.testclient import TestClient
from app.routers import app

client = TestClient(app)
# Authenticate this module's client so the (login-gated) icon routes work.
accounts.create_user("icontester", "pw", is_admin=True)
client.post("/api/auth/login", json={"username": "icontester", "password": "pw"})
ID43 = "I5B8p0HYRhwFq5apZWW0hqvfX_JPhoLUJ1n4zGoBekM"


def test_icons_local_requires_login():
    assert TestClient(app).get("/api/yoto/icons").status_code == 401


def test_icons_local_search(monkeypatch):
    monkeypatch.setattr(video2mp3.yoto_icons, "load_yoto",
                        lambda uid=None: [{"mediaId": ID43, "ref": f"yoto:#{ID43}",
                                           "source": "yoto", "title": "Car",
                                           "tags": ["car"], "url": "u"}])
    monkeypatch.setattr(video2mp3.yoto_icons, "load_me", lambda uid=None: [])
    r = client.get("/api/yoto/icons", params={"q": "car", "source": "yoto"})
    assert r.status_code == 200
    assert r.json()["icons"][0]["mediaId"] == ID43


def test_icons_import_passes_id(monkeypatch):
    async def fake_import(session, gt, icon_id, uid=None):
        assert icon_id == "62"
        return {"mediaId": ID43, "ref": f"yoto:#{ID43}", "source": "me", "url": "u"}
    monkeypatch.setattr(video2mp3.yoto_icons, "import_yotoicon", fake_import)

    @contextlib.asynccontextmanager
    async def fake_authed(uid=None):
        yield (None, None)
    monkeypatch.setattr(video2mp3, "authed_session", fake_authed)

    r = client.post("/api/yoto/icons/import", json={"icon_id": "62"})
    assert r.status_code == 200
    assert r.json()["mediaId"] == ID43


def test_icons_external_error_502(monkeypatch):
    async def boom(session, q):
        raise RuntimeError("down")

    @contextlib.asynccontextmanager
    async def fake_authed(uid=None):
        yield (None, None)
    monkeypatch.setattr(video2mp3, "authed_session", fake_authed)
    monkeypatch.setattr(video2mp3.yoto_icons, "search_yotoicons", boom)
    r = client.get("/api/yoto/icons/search-external", params={"q": "x"})
    assert r.status_code == 502
    assert "error" in r.json()


def test_upload_accepts_icon_media_id(monkeypatch, tmp_path):
    f = tmp_path / "audio.mp3"; f.write_bytes(b"x")
    monkeypatch.setattr(video2mp3.taskManager, "get_file_path",
                        lambda tid, ft: str(f))
    seen = {}

    async def fake_run(audio_path, filename, playlist_id, job,
                       icon_media_id=None, uid=None):
        seen["icon"] = icon_media_id
        job.finish(True, "ok")
    monkeypatch.setattr(video2mp3.yoto_pipeline, "run_upload", fake_run)

    r = client.post("/api/yoto/upload", json={"task_id": "t", "filename": "s",
                    "playlist_id": "p", "icon_media_id": "MID"})
    assert r.status_code == 200
    time.sleep(0.05)
    assert seen.get("icon") == "MID"
