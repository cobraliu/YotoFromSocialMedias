import importlib
import subprocess
import sys

import pytest

import app.accounts as accounts
import app.yoto_client as yconfig


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("YOTO_STATE_DIR", str(tmp_path))
    importlib.reload(yconfig)
    importlib.reload(accounts)
    return accounts


def test_create_admin_flag(monkeypatch, tmp_path):
    a = _fresh(monkeypatch, tmp_path)
    uid = a.create_user("root", "pw", is_admin=True)
    assert a.is_admin(uid) is True
    u2 = a.create_user("bob", "pw")
    assert a.is_admin(u2) is False
    # list surfaces the flag but no secrets
    row = next(u for u in a.list_users() if u["uid"] == uid)
    assert row["is_admin"] is True
    assert "pw" not in row and "salt" not in row


def test_set_password(monkeypatch, tmp_path):
    a = _fresh(monkeypatch, tmp_path)
    uid = a.create_user("carl", "old")
    a.set_password(uid, "new")
    assert a.authenticate("carl", "new") == uid
    assert a.authenticate("carl", "old") is None


def test_delete_user_removes_login_and_state(monkeypatch, tmp_path):
    a = _fresh(monkeypatch, tmp_path)
    admin = a.create_user("root", "pw", is_admin=True)
    uid = a.create_user("dan", "pw")
    a.new_session(uid)
    ud = yconfig.user_dir(uid)
    ud.mkdir(parents=True, exist_ok=True)
    (ud / ".env").write_text("client_id: X")
    a.delete_user(uid)
    assert a.get_user(uid) is None
    assert a.authenticate("dan", "pw") is None
    assert not ud.exists()          # per-user Yoto state cleaned up
    assert a.get_user(admin) is not None


def test_cannot_delete_last_admin(monkeypatch, tmp_path):
    a = _fresh(monkeypatch, tmp_path)
    admin = a.create_user("root", "pw", is_admin=True)
    with pytest.raises(ValueError):
        a.delete_user(admin)


def test_cli_create_admin(monkeypatch, tmp_path):
    env = {"YOTO_STATE_DIR": str(tmp_path), "PATH": __import__("os").environ["PATH"]}
    r = subprocess.run(
        [sys.executable, "-m", "app.accounts", "create-admin", "cliadmin", "secret"],
        cwd=str(yconfig._DEFAULT_STATE_DIR.parent),  # repo root (parent of .yoto)
        env=env, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    _fresh(monkeypatch, tmp_path)
    uid = accounts.authenticate("cliadmin", "secret")
    assert uid and accounts.is_admin(uid)
