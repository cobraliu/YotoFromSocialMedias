import json
import app.yoto_icons as icons

ID43 = "I5B8p0HYRhwFq5apZWW0hqvfX_JPhoLUJ1n4zGoBekM"


def test_to_entry_yoto():
    raw = {"mediaId": ID43, "title": "Car", "publicTags": ["car", "auto"],
           "url": "https://m/icons/" + ID43}
    e = icons.to_entry(raw, "yoto")
    assert e["ref"] == f"yoto:#{ID43}"
    assert e["source"] == "yoto"
    assert e["title"] == "Car"
    assert e["tags"] == ["car", "auto"]


def test_search_yoto_matches_title_and_tags():
    ents = [icons.to_entry({"mediaId": ID43, "title": "Race Car",
                            "publicTags": ["vehicle"]}, "yoto"),
            icons.to_entry({"mediaId": ID43[::-1], "title": "Dog",
                            "publicTags": ["animal"]}, "yoto")]
    assert len(icons.search_yoto(ents, "car")) == 1        # title hit
    assert len(icons.search_yoto(ents, "VEHICLE")) == 1    # tag, case-insensitive
    assert len(icons.search_yoto(ents, "")) == 2           # empty -> all


def test_list_me_reverse_chronological():
    ents = [icons.to_entry({"mediaId": "a", "createdAt": "2025-01-01T00:00:00Z"}, "me"),
            icons.to_entry({"mediaId": "b", "createdAt": "2025-06-01T00:00:00Z"}, "me")]
    assert [e["mediaId"] for e in icons.list_me(ents)] == ["b", "a"]


def test_parse_yotoicons_dedups_and_orders():
    html = open("fixtures/yotoicons_car.html").read()
    out = icons.parse_yotoicons(html)
    assert [o["id"] for o in out] == ["1126", "62"]
    assert out[0]["thumb"] == "/static/uploads/1126.png"


def test_read_catalog_missing_file_is_empty(tmp_path):
    assert icons.read_catalog(tmp_path / "nope.json") == []


def test_append_me_dedups_by_mediaid(tmp_path):
    p = tmp_path / "me.icons.json"
    p.write_text(json.dumps({"displayIcons": [{"mediaId": "x"}]}))
    icons.append_me(p, {"mediaId": "y", "url": "u"})
    icons.append_me(p, {"mediaId": "x", "url": "dup"})   # existing -> ignored
    ids = [r["mediaId"] for r in json.loads(p.read_text())["displayIcons"]]
    assert ids == ["x", "y"]


def test_load_me_is_uid_scoped(monkeypatch, tmp_path):
    import importlib
    import app.yoto_client as config
    import app.yoto_icons as icons
    monkeypatch.setenv("YOTO_STATE_DIR", str(tmp_path))
    importlib.reload(config)
    importlib.reload(icons)
    config.me_icons_path("u1").parent.mkdir(parents=True)
    config.me_icons_path("u1").write_text('{"displayIcons":[{"mediaId":"A"}]}')
    assert [e["mediaId"] for e in icons.load_me("u1")] == ["A"]
    assert icons.load_me("u2") == []            # isolated
    importlib.reload(config)
    importlib.reload(icons)
