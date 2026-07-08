import importlib

import pytest

import app.accounts as accounts


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("YOTO_STATE_DIR", str(tmp_path))
    importlib.reload(accounts)
    return accounts


def test_hash_verify_roundtrip(monkeypatch, tmp_path):
    a = _fresh(monkeypatch, tmp_path)
    salt, h = a.hash_password("pw")
    assert a.verify_password("pw", salt, h)
    assert not a.verify_password("nope", salt, h)


def test_create_and_authenticate(monkeypatch, tmp_path):
    a = _fresh(monkeypatch, tmp_path)
    uid = a.create_user("alice", "secret")
    assert a.authenticate("alice", "secret") == uid
    assert a.authenticate("alice", "wrong") is None
    assert a.authenticate("ghost", "x") is None


def test_duplicate_username_rejected(monkeypatch, tmp_path):
    a = _fresh(monkeypatch, tmp_path)
    a.create_user("bob", "p1")
    with pytest.raises(ValueError):
        a.create_user("bob", "p2")


def test_list_users_hides_secrets(monkeypatch, tmp_path):
    a = _fresh(monkeypatch, tmp_path)
    a.create_user("carol", "pw")
    us = a.list_users()
    assert us[0]["username"] == "carol" and "uid" in us[0]
    assert "pw" not in us[0] and "salt" not in us[0]
