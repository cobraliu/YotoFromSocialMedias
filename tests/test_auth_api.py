import app.accounts as accounts
import app.routers as video2mp3
from fastapi.testclient import TestClient

app = video2mp3.app


def test_login_sets_cookie_and_me():
    accounts.create_user("amy", "pw", is_admin=True)
    client = TestClient(app)
    r = client.post("/api/auth/login", json={"username": "amy", "password": "pw"})
    assert r.status_code == 200
    assert client.cookies.get("v2m_sid")
    me = client.get("/api/auth/me")
    assert me.json()["username"] == "amy"
    assert me.json()["is_admin"] is True


def test_unauthed_yoto_is_401():
    c2 = TestClient(app)
    assert c2.get("/api/yoto/status").status_code == 401


def test_login_logout():
    accounts.create_user("ben", "pw")
    client = TestClient(app)
    client.post("/api/auth/login", json={"username": "ben", "password": "pw"})
    assert client.get("/api/auth/me").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def test_login_bad_password_401():
    accounts.create_user("cara", "pw")
    client = TestClient(app)
    r = client.post("/api/auth/login", json={"username": "cara", "password": "nope"})
    assert r.status_code == 401
