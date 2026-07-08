import asyncio
import importlib
import json

import app.yoto_client as config
import app.yoto_client as login


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("YOTO_STATE_DIR", str(tmp_path))
    importlib.reload(config)
    importlib.reload(login)


def test_authorize_url_writes_pkce(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    config.user_dir("u1").mkdir(parents=True)
    (config.user_dir("u1") / ".env").write_text("client_id: CID9")
    url = login.authorize_url("u1")
    assert "client_id=CID9" in url and "code_challenge=" in url
    assert "user%3Aicons%3Amanage" in url or "user:icons:manage" in url
    saved = json.loads(config.pkce_path("u1").read_text())
    assert "verifier" in saved and "state" in saved


def test_extract_code_from_url_and_raw(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    assert login._extract_code("ABC")[0] == "ABC"
    u = "http://127.0.0.1:8787/callback?code=XYZ&state=s1"
    assert login._extract_code(u) == ("XYZ", "s1")


def test_exchange_saves_token(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    ud = config.user_dir("u1")
    ud.mkdir(parents=True)
    (ud / ".env").write_text("client_id: CID9")
    config.pkce_path("u1").write_text(json.dumps({"verifier": "v", "state": "s"}))

    class Resp:
        ok = True

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def json(self, content_type=None):
            return {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}

    class Sess:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def post(self, *a, **k):
            return Resp()

    monkeypatch.setattr(login.aiohttp, "ClientSession", lambda: Sess())

    tok = asyncio.run(login.exchange("u1", "CODE"))
    assert tok.access_token == "AT"
    assert config.load_token("u1").refresh_token == "RT"
    assert not config.pkce_path("u1").exists()  # consumed
