"""The fixture session mirrors the Session surface the studio relies on."""
from mirror.contracts import Rollout, TaskSpec
from mirror.studio.fixtures import FixtureSession, fixture_layout, fixture_scene, visual_scene
from mirror.studio.session_adapter import REFERENCE_CORRECTION, compact_rollout, trace_summary


def _session():
    s = FixtureSession()
    s.record_demo()
    s.interpret()
    return s


def test_demo_has_grasp_and_release_events():
    s = FixtureSession()
    d = s.record_demo()
    kinds = [(e.type, e.object_id) for e in d.events]
    assert ("grasp", "cup_0") in kinds and ("release", "cup_0") in kinds
    assert d.source == "teleop_scripted"


def test_correction_bumps_version_and_recheck_flags_old_rollout():
    s = _session()
    s.set_scene("shortcut")
    old = s.run(condition="v1")
    assert "through_slot:pitched_90" in old.rollout.report.route_labels
    spec, rec = s.correct(REFERENCE_CORRECTION)
    assert spec.version == 2 and rec.kind == "correction"
    assert any(c.id == "c_upright_transport" and c.status.value == "confirmed" for c in spec.constraints)
    assert s.recheck(old.rollout.report.rollout_id, 2).status == "constraint_violated"
    new = s.run(condition="v2")
    assert new.rollout.report.status == "success"
    assert new.rollout.report.route_labels == ["around_wall_end"]


def test_unrelated_correction_changes_nothing():
    s = _session()
    spec, rec = s.correct("Please be quick.")
    assert spec.version == 1 and rec.diff == []


def test_blocked_scene_returns_no_plan_with_certificate():
    s = _session()
    s.correct(REFERENCE_CORRECTION)
    s.set_scene("blocked")
    e = s.run()
    assert e.rollout.report.status == "planning_failed"
    assert e.plan.status == "no_plan" and e.plan.infeasibility["proven"] is True


def test_layouts_are_deterministic_in_seed():
    assert fixture_layout(10003).model_dump() == fixture_layout(10003).model_dump()
    assert fixture_layout(10003).model_dump() != fixture_layout(10004).model_dump()


def test_visual_scene_names_match_scene_ids():
    sc = fixture_scene("shortcut")
    names = {b.name for b in visual_scene(sc).bodies}
    assert {"cup_0", "block_0", "marker_0", "tray_0", "wall_0", "gripper"} <= names
    assert sum(1 for n in names if "bead" in n) == 8


def test_compact_rollout_is_columnar_and_rounded():
    s = _session()
    e = s.run()
    p = compact_rollout(e.rollout)
    n = len(p["t"])
    assert n == len(p["width"]) == len(p["phase"]) > 10
    for arr in p["bodies"].values():
        assert len(arr) == n and len(arr[0]) == 7
    assert p["report"]["rollout_id"] == e.rollout.report.rollout_id
    tr = trace_summary(e.rollout, e.scene)
    assert tr["object_id"] == "cup_0" and len(tr["points"]) > 10 and len(tr["points"][0]) == 4


def test_paired_eval_reports_every_seed():
    s = _session()
    v1 = s.spec
    v2, _ = s.correct(REFERENCE_CORRECTION)
    rep = s.paired_eval(v1, v2, [10000, 10001, 10002])
    assert [p["seed"] for p in rep["pairs"]] == [10000, 10001, 10002]
    assert all("original" in p and "corrected" in p for p in rep["pairs"])
