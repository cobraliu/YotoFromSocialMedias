from app.yoto_client import build_appended_payload, normalize_icon, part_titles

ID43 = "I5B8p0HYRhwFq5apZWW0hqvfX_JPhoLUJ1n4zGoBekM"  # 43 chars


def test_normalize_icon_forms():
    assert len(ID43) == 43
    # display URL -> yoto:# ref
    url = f"https://card-content.yotoplay.com/sig~/{ID43}"
    assert normalize_icon(url) == f"yoto:#{ID43}"
    # already-valid ref preserved
    assert normalize_icon(f"yoto:#{ID43}") == f"yoto:#{ID43}"
    # bad / uncertain -> dropped
    assert normalize_icon("yoto:#short") is None
    assert normalize_icon("https://x/y/tooshort") is None
    assert normalize_icon("") is None
    assert normalize_icon(None) is None


def test_part_titles_single_and_split():
    assert part_titles("song", 1) == ["song"]
    assert part_titles("song", 3) == ["song-1", "song-2", "song-3"]


def _fake_card():
    return {"card": {
        "cardId": "CARD1",
        "title": "My List",
        "metadata": {"author": "me", "cover": {"imageL": "u"}},
        "content": {
            "playbackType": "linear",
            "config": {"autoadvance": "next", "onlineOnly": False, "shuffle": []},
            "cover": {"imageL": "u"},
            "chapters": [
                {"key": "01", "overlayLabel": "1", "title": "old",
                 "tracks": [{"key": "01", "title": "old", "trackUrl": "yoto:#A",
                             "duration": 100, "fileSize": 1000, "channels": 2,
                             "format": "mp3", "type": "audio"}]},
            ],
        },
    }}


def test_build_appended_payload_appends_and_numbers():
    parts = [{"title": "new", "trackUrl": "yoto:#B", "duration": 60,
              "fileSize": 500, "channels": 2, "format": "mp3"}]
    p = build_appended_payload(_fake_card(), parts)
    chapters = p["content"]["chapters"]
    assert len(chapters) == 2                        # appended, existing kept
    assert chapters[0]["title"] == "old"             # existing preserved
    assert chapters[1]["key"] == "02"                # continued numbering
    assert chapters[1]["overlayLabel"] == "2"
    assert chapters[1]["tracks"][0]["trackUrl"] == "yoto:#B"
    assert p["cardId"] == "CARD1"                     # in-place update
    assert p["metadata"]["media"]["duration"] == 160  # 100 + 60 recomputed
    assert p["metadata"]["media"]["fileSize"] == 1500


def test_build_appended_payload_multi_parts_continue_numbering():
    parts = [
        {"title": "n-1", "trackUrl": "yoto:#B", "duration": 60,
         "fileSize": 500, "channels": 2, "format": "mp3"},
        {"title": "n-2", "trackUrl": "yoto:#C", "duration": 40,
         "fileSize": 300, "channels": 2, "format": "mp3"},
    ]
    p = build_appended_payload(_fake_card(), parts)
    keys = [ch["key"] for ch in p["content"]["chapters"]]
    assert keys == ["01", "02", "03"]
    assert p["metadata"]["media"]["duration"] == 200  # 100 + 60 + 40


def test_build_appended_payload_normalizes_existing_icons_and_no_new_icon():
    card = _fake_card()
    # existing chapter/track carry display-URL icons that POST would reject
    url = f"https://card-content.yotoplay.com/sig~/{ID43}"
    card["card"]["content"]["chapters"][0]["display"] = {"icon16x16": url}
    card["card"]["content"]["chapters"][0]["tracks"][0]["display"] = {"icon16x16": url}
    parts = [{"title": "new", "trackUrl": "yoto:#B", "duration": 60,
              "fileSize": 500, "channels": 2, "format": "mp3"}]
    p = build_appended_payload(card, parts)
    ch0, ch1 = p["content"]["chapters"]
    # existing icons coerced to write format
    assert ch0["display"]["icon16x16"] == f"yoto:#{ID43}"
    assert ch0["tracks"][0]["display"]["icon16x16"] == f"yoto:#{ID43}"
    # new appended chapter/track has NO icon
    assert "display" not in ch1
    assert "display" not in ch1["tracks"][0]
    # caller's input card was not mutated (deep-copied)
    assert card["card"]["content"]["chapters"][0]["display"]["icon16x16"] == url


def test_build_appended_payload_drops_invalid_existing_icon():
    card = _fake_card()
    card["card"]["content"]["chapters"][0]["display"] = {"icon16x16": "yoto:#bad"}
    parts = [{"title": "n", "trackUrl": "yoto:#B", "duration": 1,
              "fileSize": 1, "channels": 2, "format": "mp3"}]
    p = build_appended_payload(card, parts)
    # invalid icon dropped, empty display removed
    assert "display" not in p["content"]["chapters"][0]


def test_build_appended_payload_empty_card():
    empty = {"card": {"cardId": "C", "title": "T", "metadata": {},
                      "content": {"chapters": []}}}
    parts = [{"title": "only", "trackUrl": "yoto:#Z", "duration": 10,
              "fileSize": 100, "channels": 2, "format": "mp3"}]
    p = build_appended_payload(empty, parts)
    assert [ch["key"] for ch in p["content"]["chapters"]] == ["01"]
    assert p["content"]["playbackType"] == "linear"   # sensible default


def test_build_appended_payload_sets_icon_on_all_parts():
    parts = [{"title": "n-1", "trackUrl": "yoto:#B", "duration": 1,
              "fileSize": 1, "channels": 2, "format": "mp3"},
             {"title": "n-2", "trackUrl": "yoto:#C", "duration": 1,
              "fileSize": 1, "channels": 2, "format": "mp3"}]
    ref = f"yoto:#{ID43}"
    p = build_appended_payload(_fake_card(), parts, icon_ref=ref)
    new = p["content"]["chapters"][1:]           # skip existing "old"
    assert len(new) == 2
    for ch in new:
        assert ch["display"]["icon16x16"] == ref
        assert ch["tracks"][0]["display"]["icon16x16"] == ref


def test_build_appended_payload_invalid_icon_ref_omitted():
    parts = [{"title": "n", "trackUrl": "yoto:#B", "duration": 1,
              "fileSize": 1, "channels": 2, "format": "mp3"}]
    p = build_appended_payload(_fake_card(), parts, icon_ref="yoto:#bad")
    assert "display" not in p["content"]["chapters"][1]


# ─── create playlist ──────────────────────────────────────────────────
def test_build_new_playlist_payload():
    from app.yoto_client import build_new_playlist_payload
    p = build_new_playlist_payload("  My New List  ")
    assert p["title"] == "My New List"          # trimmed
    assert "cardId" not in p                      # new card, no id
    assert p["content"]["chapters"] == []
    assert p["content"]["playbackType"] == "linear"
    assert p["metadata"]["media"]["duration"] == 0


def test_build_new_playlist_rejects_empty():
    import pytest
    from app.yoto_client import build_new_playlist_payload
    with pytest.raises(ValueError):
        build_new_playlist_payload("   ")


def test_create_playlist_posts_and_returns_id():
    import asyncio
    import json
    import app.yoto_client as uploader

    class Resp:
        ok = True
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def text(self):
            return json.dumps({"card": {"cardId": "NEWID9", "title": "My New List"}})

    class Sess:
        def __init__(self): self.sent = None
        def post(self, url, headers=None, json=None):
            self.sent = {"url": url, "json": json}
            return Resp()

    async def _tok(): return "T"
    s = Sess()
    out = asyncio.run(uploader.create_playlist(s, _tok, "My New List"))
    assert out == {"id": "NEWID9", "title": "My New List", "n_tracks": 0, "duration": 0}
    assert s.sent["url"].endswith("/content")
    assert s.sent["json"]["title"] == "My New List"
    assert "cardId" not in s.sent["json"]


# ─── playlist detail extraction ───────────────────────────────────────
def test_extract_playlist_tracks():
    from app.yoto_client import extract_playlist_tracks
    detail = {"card": {
        "cardId": "C1", "title": "My List",
        "metadata": {"media": {"duration": 574}},
        "content": {"chapters": [
            {"key": "01", "title": "Ch A", "duration": 170,
             "display": {"icon16x16": "https://cc.yoto/sig~/ICON1"},
             "tracks": [
                 {"title": "T1", "duration": 100, "format": "aac"},
                 {"title": "T2", "duration": 70, "format": "aac"},
             ]},
            {"key": "02", "title": "Ch B", "duration": 404, "tracks": [
                 {"title": "T3", "duration": 404, "format": "mp3"},
             ]},
        ]},
    }}
    out = extract_playlist_tracks(detail)
    assert out["id"] == "C1" and out["title"] == "My List"
    assert out["duration"] == 574
    assert len(out["chapters"]) == 2
    assert out["chapters"][0]["title"] == "Ch A"
    assert out["chapters"][0]["icon"] == "https://cc.yoto/sig~/ICON1"
    assert [t["title"] for t in out["chapters"][0]["tracks"]] == ["T1", "T2"]
    assert out["chapters"][0]["tracks"][0]["duration"] == 100
    assert out["chapters"][1]["icon"] == ""      # no display -> empty


def test_extract_playlist_tracks_bare_card():
    from app.yoto_client import extract_playlist_tracks
    out = extract_playlist_tracks({"cardId": "X", "title": "t", "content": {}})
    assert out["id"] == "X" and out["chapters"] == []
