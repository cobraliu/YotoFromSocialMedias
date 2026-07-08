from app.yoto_audio import plan_cuts, readable_duration, SEG_MAX


def test_plan_cuts_short_track_single_segment():
    assert plan_cuts(120.0, []) == [0.0, 120.0]


def test_plan_cuts_long_track_equal_parts_no_silence():
    # 660s -> ceil(660/300)=3 parts of 220s each, no silence -> ideal cuts.
    bounds = plan_cuts(660.0, [])
    assert bounds[0] == 0.0 and bounds[-1] == 660.0
    assert len(bounds) == 4
    for a, b in zip(bounds, bounds[1:]):
        assert (b - a) <= SEG_MAX + 0.001


def test_plan_cuts_snaps_to_silence():
    # 550s -> 2 parts, seg=275 (< seg_max so window=20); ideal cut 275,
    # silence at 270 within the window -> snap to 270.
    bounds = plan_cuts(550.0, [270.0])
    assert abs(bounds[1] - 270.0) < 0.001


def test_readable_duration():
    assert readable_duration(65) == "1m 5s"
    assert readable_duration(3661) == "1h 1m 1s"
