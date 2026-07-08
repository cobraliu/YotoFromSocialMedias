import app.accounts as accounts
import app.routers as video2mp3
from fastapi.testclient import TestClient
import app.yoto_client as yconfig

app = video2mp3.app


def _client(username):
    accounts.create_user(username, "pw", is_admin=True)
    c = TestClient(app)
    c.post("/api/auth/login", json={"username": username, "password": "pw"})
    return c


def test_bind_writes_env_and_status():
    c = _client("binder1")
    r = c.post("/api/yoto/bind", json={"client_id": "CID42"})
    assert r.status_code == 200
    st = c.get("/api/yoto/status").json()
    assert st["bound"] is True and st["authed"] is False


def test_bind_rejects_empty():
    c = _client("binder2")
    assert c.post("/api/yoto/bind", json={"client_id": "  "}).status_code == 400


def test_auth_url_returned(monkeypatch):
    c = _client("binder3")
    c.post("/api/yoto/bind", json={"client_id": "CID42"})
    monkeypatch.setattr(video2mp3.yoto_login, "authorize_url", lambda uid: "https://auth/x")
    r = c.get("/api/yoto/auth/url")
    assert r.json()["url"] == "https://auth/x"


def test_status_requires_auth():
    assert TestClient(app).get("/api/yoto/status").status_code == 401
