import importlib

import app.accounts as accounts
import app.yoto_client as yconfig


def test_first_user_inherits_legacy(monkeypatch, tmp_path):
    monkeypatch.setenv("YOTO_STATE_DIR", str(tmp_path))
    importlib.reload(yconfig)
    importlib.reload(accounts)
    (tmp_path / ".env").write_text("client_id: LEGACY123")
    (tmp_path / ".yoto_token.json").write_text('{"access_token":"a"}')
    uid = accounts.create_user("first", "pw")
    ud = tmp_path / "users" / uid
    assert (ud / ".env").read_text() == "client_id: LEGACY123"
    assert (ud / ".yoto_token.json").exists()
    # legacy files were moved, not copied
    assert not (tmp_path / ".yoto_token.json").exists()
    # a SECOND user does NOT inherit
    uid2 = accounts.create_user("second", "pw")
    assert not (tmp_path / "users" / uid2 / ".yoto_token.json").exists()
    importlib.reload(yconfig)
