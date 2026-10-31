import pytest

from mirror.interpret import summarize_demo
from tests.interpret.fixtures import make_demo


def test_reference_like_demo_features():
    s = summarize_demo(make_demo())
    assert s.event_source == "events"
    assert s.order == ["cup_0", "block_0", "marker_0"]
    cup = s.object("cup_0")
    assert cup.moved and cup.ended_in_tray == "tray_0"
    assert len(cup.grasp_times) == 1 and cup.release_times[0] > cup.grasp_times[0]
    assert cup.carry_max_tilt_deg == pytest.approx(3.0, abs=0.2)
    assert cup.end_tilt_deg == pytest.approx(0.0, abs=0.1)
    assert cup.wall_crossings == ["wall_0:around_+y"]
    assert cup.carry_path_length_m > 0.5
    assert cup.min_obstacle_distance_m is not None and cup.nearest_obstacle == "wall_0"
    kinds = {(r.kind, r.object_kind) for r in s.regularities}
    assert ("carry_upright", "cup") in kinds and ("final_upright", "cup") in kinds
    assert ("order", None) in kinds
    assert [r.id for r in s.regularities] == [f"R{i + 1}" for i in range(len(s.regularities))]


def test_tilted_carry_is_measured_not_assumed():
    s = summarize_demo(make_demo(tilt_kind="marker", tilt_deg=80.0))
    m = s.object("marker_0")
    assert m.carry_max_tilt_deg == pytest.approx(80.0, abs=1.0)
    kinds = {(r.kind, r.object_kind) for r in s.regularities}
    assert ("carry_tilted", "marker") in kinds
    assert ("carry_upright", "marker") not in kinds


def test_slot_crossing_detected():
    s = summarize_demo(make_demo(through_slot_for="cup"))
    assert s.object("cup_0").wall_crossings == ["wall_0:through_slot"]
    assert any("slots are ignored" in n for n in s.notes)


def test_motion_fallback_without_events():
    with_ev = summarize_demo(make_demo())
    no_ev = summarize_demo(make_demo(events=False))
    assert no_ev.event_source == "motion"
    assert no_ev.order == with_ev.order
    for oid in ("cup_0", "block_0", "marker_0"):
        assert no_ev.object(oid).ended_in_tray == "tray_0"
        assert no_ev.object(oid).carry_max_tilt_deg == pytest.approx(
            with_ev.object(oid).carry_max_tilt_deg, abs=1.0)


def test_object_left_behind_is_not_inside():
    s = summarize_demo(make_demo(leave_out=("marker_0",)))
    m = s.object("marker_0")
    assert not m.moved and m.ended_in_tray is None
    assert s.order == ["cup_0", "block_0"]


def test_summary_is_deterministic():
    a = summarize_demo(make_demo()).model_dump_json()
    b = summarize_demo(make_demo()).model_dump_json()
    assert a == b


def test_empty_trace_rejected():
    tr = make_demo()
    tr.frames = []
    with pytest.raises(ValueError):
        summarize_demo(tr)
