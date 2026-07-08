import importlib

import app.accounts as accounts


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("YOTO_STATE_DIR", str(tmp_path))
    importlib.reload(accounts)
    return accounts


def test_session_lifecycle(monkeypatch, tmp_path):
    a = _fresh(monkeypatch, tmp_path)
    uid = a.create_user("alice", "pw")
    tok = a.new_session(uid)
    assert a.session_user(tok) == uid
    a.end_session(tok)
    assert a.session_user(tok) is None
    assert a.session_user("bogus") is None
    assert a.session_user("") is None
