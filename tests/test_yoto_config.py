import json
import os
import app.yoto_client as config


def test_load_client_id_colon_and_equals(tmp_path, monkeypatch):
    monkeypatch.setenv("YOTO_STATE_DIR", str(tmp_path))
    (tmp_path / ".env").write_text("client_id: ABC123\n")
    assert config.load_client_id() == "ABC123"
    (tmp_path / ".env").write_text("YOTO_CLIENT_ID=XYZ\n")
    assert config.load_client_id() == "XYZ"


def test_token_roundtrip(tmp_path, monkeypatch):
    from app.yoto_client import Token
    import datetime
    monkeypatch.setenv("YOTO_STATE_DIR", str(tmp_path))
    t = Token(access_token="a", refresh_token="r",
              valid_until=datetime.datetime(2030, 1, 1))
    config.save_token(t)
    loaded = config.load_token()
    assert loaded.access_token == "a"
    assert loaded.refresh_token == "r"
    assert loaded.valid_until.year == 2030


def test_load_token_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("YOTO_STATE_DIR", str(tmp_path))
    assert config.load_token() is None


def test_icon_catalog_paths(monkeypatch, tmp_path):
    import importlib, app.yoto_client as config
    monkeypatch.setenv("YOTO_STATE_DIR", str(tmp_path))
    importlib.reload(config)
    assert config.yoto_icons_path() == tmp_path / "yoto.icons.json"
    assert config.me_icons_path() == tmp_path / "me.icons.json"
    importlib.reload(config)  # restore default env for other tests


def test_user_scoped_paths(monkeypatch, tmp_path):
    import importlib, app.yoto_client as config
    monkeypatch.setenv("YOTO_STATE_DIR", str(tmp_path))
    importlib.reload(config)
    assert config.user_dir("u1") == tmp_path / "users" / "u1"
    assert config.token_path("u1") == tmp_path / "users" / "u1" / ".yoto_token.json"
    assert config.me_icons_path("u1") == tmp_path / "users" / "u1" / "me.icons.json"
    assert config.pkce_path("u1") == tmp_path / "users" / "u1" / ".yoto_pkce.json"
    # legacy (no uid) unchanged
    assert config.token_path() == tmp_path / ".yoto_token.json"
    importlib.reload(config)
