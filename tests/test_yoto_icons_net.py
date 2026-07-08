import asyncio
import json
import app.yoto_icons as icons

ID43 = "I5B8p0HYRhwFq5apZWW0hqvfX_JPhoLUJ1n4zGoBekM"


class FakeResp:
    def __init__(self, status=200, body=b"", text=""):
        self.status, self.ok = status, 200 <= status < 300
        self._body, self._text = body, text
        self.headers = {"Content-Type": "image/png"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def read(self):
        return self._body

    async def text(self):
        return self._text


class FakeSession:
    """Records calls; returns queued responses by (method, url-substr)."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def _match(self, method, url):
        for (m, frag), resp in self.routes.items():
            if m == method and frag in url:
                return resp
        raise AssertionError(f"no route for {method} {url}")

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return self._match("POST", url)

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return self._match("GET", url)


async def _tok():
    return "TESTTOKEN"


def test_upload_custom_icon_returns_displayicon():
    body = json.dumps({"displayIcon": {"mediaId": ID43, "url": "u"}})
    s = FakeSession({("POST", "/media/displayIcons/user/me/upload"):
                     FakeResp(200, text=body)})
    out = asyncio.run(icons.upload_custom_icon(s, _tok, b"PNGDATA", "car"))
    assert out["mediaId"] == ID43
    _, url, kw = s.calls[0]
    assert kw["params"]["filename"] == "car"
    assert kw["data"] == b"PNGDATA"


def test_import_yotoicon_downloads_uploads_and_records(tmp_path, monkeypatch):
    monkeypatch.setattr(icons.config, "me_icons_path",
                        lambda uid=None: tmp_path / "me.icons.json")
    monkeypatch.setattr(icons.config, "yotoicons_cache_path",
                        lambda uid=None: tmp_path / "yotoicons.cache.json")
    monkeypatch.setattr(icons.config, "icons_cache_dir",
                        lambda: tmp_path / "yoto_icons")
    body = json.dumps({"displayIcon": {"mediaId": ID43, "url": "u"}})
    s = FakeSession({
        ("GET", "/uploads/62.png"): FakeResp(200, body=b"PNGDATA"),
        ("POST", "/media/displayIcons/user/me/upload"): FakeResp(200, text=body),
    })
    entry = asyncio.run(icons.import_yotoicon(s, _tok, "62"))
    assert entry["mediaId"] == ID43
    assert entry["ref"] == f"yoto:#{ID43}"
    saved = json.loads((tmp_path / "me.icons.json").read_text())
    assert saved["displayIcons"][0]["mediaId"] == ID43


def test_download_yotoicon_caches_file_to_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(icons.config, "icons_cache_dir", lambda: tmp_path / "yoto_icons")
    s = FakeSession({("GET", "/uploads/62.png"): FakeResp(200, body=b"PNGDATA")})
    d1 = asyncio.run(icons._download_yotoicon(s, "62"))
    assert d1 == b"PNGDATA"
    f = tmp_path / "yoto_icons" / "yotoicon-62.png"
    assert f.read_bytes() == b"PNGDATA"          # file cached under data/
    calls_after_first = len(s.calls)
    d2 = asyncio.run(icons._download_yotoicon(s, "62"))   # served from disk
    assert d2 == b"PNGDATA"
    assert len(s.calls) == calls_after_first       # no second network fetch


def test_cached_icon_bytes_writes_and_reuses(tmp_path, monkeypatch):
    monkeypatch.setattr(icons.config, "icons_cache_dir", lambda: tmp_path / "yoto_icons")
    s = FakeSession({("GET", "media-secure"): FakeResp(200, body=b"IMG")})
    b1, _ = asyncio.run(icons.cached_icon_bytes(s, _tok, ID43))
    assert b1 == b"IMG"
    assert (tmp_path / "yoto_icons" / f"{ID43}.png").read_bytes() == b"IMG"
    n = len(s.calls)
    b2, _ = asyncio.run(icons.cached_icon_bytes(s, _tok, ID43))   # disk hit
    assert b2 == b"IMG" and len(s.calls) == n


def test_import_yotoicon_cached_skips_second_download(tmp_path, monkeypatch):
    monkeypatch.setattr(icons.config, "icons_cache_dir", lambda: tmp_path / "yoto_icons")
    monkeypatch.setattr(icons.config, "me_icons_path",
                        lambda uid=None: tmp_path / "me.icons.json")
    monkeypatch.setattr(icons.config, "yotoicons_cache_path",
                        lambda uid=None: tmp_path / "yotoicons.cache.json")
    body = json.dumps({"displayIcon": {"mediaId": ID43, "url": "u"}})
    s = FakeSession({
        ("GET", "/uploads/62.png"): FakeResp(200, body=b"PNGDATA"),
        ("POST", "/media/displayIcons/user/me/upload"): FakeResp(200, text=body),
    })
    e1 = asyncio.run(icons.import_yotoicon(s, _tok, "62"))
    calls_after_first = len(s.calls)
    e2 = asyncio.run(icons.import_yotoicon(s, _tok, "62"))   # cache hit
    assert e1["mediaId"] == e2["mediaId"] == ID43
    assert len(s.calls) == calls_after_first             # no new network calls
    cache = json.loads((tmp_path / "yotoicons.cache.json").read_text())
    assert cache["62"]["mediaId"] == ID43


def test_search_yotoicons_parses_results():
    html = '<img src="/static/uploads/62.png"><img src="/static/uploads/7.png">'
    s = FakeSession({("GET", "yotoicons.com/icons"): FakeResp(200, text=html)})
    out = asyncio.run(icons.search_yotoicons(s, "car"))
    assert [o["id"] for o in out] == ["62", "7"]


def test_upload_custom_icon_error_raises():
    s = FakeSession({("POST", "/media/displayIcons/user/me/upload"):
                     FakeResp(401, text="no scope")})
    try:
        asyncio.run(icons.upload_custom_icon(s, _tok, b"x", "n"))
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "401" in str(e)
