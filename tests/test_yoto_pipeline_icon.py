import asyncio
import app.yoto_pipeline as pipeline


def test_run_upload_passes_icon_ref(monkeypatch, tmp_path):
    src = tmp_path / "a.mp3"; src.write_bytes(b"x")
    monkeypatch.setattr(pipeline, "optimize_track", lambda s, w, **k: [src])

    class Ctx:
        async def __aenter__(self): return (None, None)
        async def __aexit__(self, *a): return False
    monkeypatch.setattr(pipeline, "authed_session", lambda uid=None: Ctx())

    async def fake_upload(session, gt, p):
        return {"sha": "S", "duration": 1, "fileSize": 1, "channels": 2, "format": "mp3"}
    monkeypatch.setattr(pipeline, "upload_file", fake_upload)

    async def fake_fetch(session, gt, pid): return {"card": {}}
    monkeypatch.setattr(pipeline, "fetch_card", fake_fetch)

    captured = {}
    def fake_build(detail, parts, icon_ref=None):
        captured["icon_ref"] = icon_ref
        return {"cardId": "c"}
    monkeypatch.setattr(pipeline, "build_appended_payload", fake_build)

    async def fake_create(session, gt, payload): return {}
    monkeypatch.setattr(pipeline, "create_content", fake_create)

    class Job:
        def update(self, *a): pass
        def add_log(self, *a): pass
        def finish(self, *a): pass

    asyncio.run(pipeline.run_upload(str(src), "song", "pl", Job(),
                                    icon_media_id="MID123"))
    assert captured["icon_ref"] == "yoto:#MID123"


def test_run_upload_no_icon_ref_when_none(monkeypatch, tmp_path):
    src = tmp_path / "a.mp3"; src.write_bytes(b"x")
    monkeypatch.setattr(pipeline, "optimize_track", lambda s, w, **k: [src])

    class Ctx:
        async def __aenter__(self): return (None, None)
        async def __aexit__(self, *a): return False
    monkeypatch.setattr(pipeline, "authed_session", lambda uid=None: Ctx())

    async def fake_upload(session, gt, p):
        return {"sha": "S", "duration": 1, "fileSize": 1, "channels": 2, "format": "mp3"}
    monkeypatch.setattr(pipeline, "upload_file", fake_upload)

    async def fake_fetch(session, gt, pid): return {"card": {}}
    monkeypatch.setattr(pipeline, "fetch_card", fake_fetch)

    captured = {}
    def fake_build(detail, parts, icon_ref=None):
        captured["icon_ref"] = icon_ref
        return {"cardId": "c"}
    monkeypatch.setattr(pipeline, "build_appended_payload", fake_build)

    async def fake_create(session, gt, payload): return {}
    monkeypatch.setattr(pipeline, "create_content", fake_create)

    class Job:
        def update(self, *a): pass
        def add_log(self, *a): pass
        def finish(self, *a): pass

    asyncio.run(pipeline.run_upload(str(src), "song", "pl", Job()))
    assert captured["icon_ref"] is None


def test_run_upload_opens_session_for_uid(monkeypatch, tmp_path):
    src = tmp_path / "a.mp3"; src.write_bytes(b"x")
    monkeypatch.setattr(pipeline, "optimize_track", lambda s, w, **k: [src])
    seen = {}

    class Ctx:
        async def __aenter__(self): return (None, None)
        async def __aexit__(self, *a): return False

    def fake_authed(uid=None):
        seen["uid"] = uid
        return Ctx()
    monkeypatch.setattr(pipeline, "authed_session", fake_authed)

    async def fake_upload(session, gt, p):
        return {"sha": "S", "duration": 1, "fileSize": 1, "channels": 2, "format": "mp3"}
    monkeypatch.setattr(pipeline, "upload_file", fake_upload)

    async def fake_fetch(session, gt, pid): return {"card": {}}
    monkeypatch.setattr(pipeline, "fetch_card", fake_fetch)
    monkeypatch.setattr(pipeline, "build_appended_payload",
                        lambda d, p, icon_ref=None: {"cardId": "c"})

    async def fake_create(session, gt, payload): return {}
    monkeypatch.setattr(pipeline, "create_content", fake_create)

    class Job:
        def update(self, *a): pass
        def add_log(self, *a): pass
        def finish(self, *a): pass

    asyncio.run(pipeline.run_upload(str(src), "n", "pl", Job(), uid="U7"))
    assert seen["uid"] == "U7"
