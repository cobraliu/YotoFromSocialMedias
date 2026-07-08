import datetime

from app.yoto_client import _build_token_from_body, auth_headers, _needs_refresh
from app.yoto_client import Token

UTC = datetime.timezone.utc


def test_build_token_defaults_scope_and_expiry():
    tok = _build_token_from_body(
        {"access_token": "aa", "refresh_token": "rr", "expires_in": 100}, "sc"
    )
    assert tok.access_token == "aa"
    assert tok.refresh_token == "rr"
    assert tok.scope == "sc"
    assert tok.valid_until is not None


def test_auth_headers():
    h = auth_headers("tok")
    assert h["Authorization"] == "Bearer tok"
    assert h["Accept"] == "application/json"


def test_needs_refresh_missing_access_token():
    now = datetime.datetime.now(UTC)
    tok = Token(access_token=None, refresh_token="r",
                valid_until=now + datetime.timedelta(hours=5))
    assert _needs_refresh(tok, now) is True


def test_needs_refresh_within_margin():
    now = datetime.datetime.now(UTC)
    tok = Token(access_token="a", refresh_token="r",
                valid_until=now + datetime.timedelta(minutes=5))  # < 10min margin
    assert _needs_refresh(tok, now) is True


def test_needs_refresh_fresh_token():
    now = datetime.datetime.now(UTC)
    tok = Token(access_token="a", refresh_token="r",
                valid_until=now + datetime.timedelta(hours=2))
    assert _needs_refresh(tok, now) is False


def test_needs_refresh_naive_datetime_treated_as_utc():
    now = datetime.datetime.now(UTC)
    naive = (now + datetime.timedelta(hours=2)).replace(tzinfo=None)
    tok = Token(access_token="a", refresh_token="r", valid_until=naive)
    assert _needs_refresh(tok, now) is False


def test_authed_session_requires_binding(monkeypatch, tmp_path):
    import importlib
    import asyncio
    import app.yoto_client as config
    import app.yoto_client as auth
    monkeypatch.setenv("YOTO_STATE_DIR", str(tmp_path))
    importlib.reload(config)
    importlib.reload(auth)

    async def go():
        async with auth.authed_session("nouser") as _:
            pass

    import pytest
    with pytest.raises(RuntimeError):
        asyncio.run(go())
