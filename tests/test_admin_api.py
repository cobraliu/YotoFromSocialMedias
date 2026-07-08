import app.accounts as accounts
import app.routers as video2mp3
from fastapi.testclient import TestClient

app = video2mp3.app


def _login(username, password="pw"):
    c = TestClient(app)
    c.post("/api/auth/login", json={"username": username, "password": password})
    return c


def test_register_endpoint_gone():
    r = TestClient(app).post("/api/auth/register",
                             json={"username": "x", "password": "pw"})
    assert r.status_code in (404, 405)   # route removed


def test_admin_can_create_and_list_users():
    accounts.create_user("adm_boss", "pw", is_admin=True)
    c = _login("adm_boss")
    r = c.post("/api/admin/users",
               json={"username": "adm_staff", "password": "pw"})
    assert r.status_code == 200
    names = [u["username"] for u in c.get("/api/admin/users").json()["users"]]
    assert "adm_staff" in names and "adm_boss" in names


def test_non_admin_forbidden():
    accounts.create_user("adm_plain", "pw")            # non-admin
    c = _login("adm_plain")
    assert c.get("/api/admin/users").status_code == 403
    r = c.post("/api/admin/users", json={"username": "z", "password": "pw"})
    assert r.status_code == 403


def test_admin_delete_and_reset_password():
    accounts.create_user("adm_boss2", "pw", is_admin=True)
    victim = accounts.create_user("adm_victim", "pw")
    c = _login("adm_boss2")
    # reset password
    assert c.post(f"/api/admin/users/{victim}/password",
                  json={"password": "new"}).status_code == 200
    assert accounts.authenticate("adm_victim", "new") == victim
    # delete
    assert c.request("DELETE", f"/api/admin/users/{victim}").status_code == 200
    assert accounts.get_user(victim) is None


def test_me_reports_is_admin():
    accounts.create_user("adm_boss3", "pw", is_admin=True)
    c = _login("adm_boss3")
    assert c.get("/api/auth/me").json()["is_admin"] is True
