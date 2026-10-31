import numpy as np
import pytest

from mirror.contracts import Constraint, ObjectSelector, Status
from mirror.evaluate import advisory_violations, check
from mirror.statelog import StateLog

from .synth import DT, LogBuilder, make_scene, make_spec, pack_all, prov


@pytest.fixture(scope="module")
def scene():
    return make_scene()


@pytest.fixture(scope="module")
def upright_log(scene):
    return pack_all(scene, cup_route="around").build()


@pytest.fixture(scope="module")
def slot_log(scene):
    return pack_all(scene, cup_route="slot", spill=True, marker_tilt=90.0).build()


def test_upright_route_succeeds_under_both_specs(scene, upright_log):
    for spec in (make_spec(), make_spec(corrected=True)):
        r = check(upright_log, spec, scene)
        assert r.status == "success", r.notes
        assert r.completion and not r.violations
        cup = next(o for o in r.outcomes if o.object_id == "cup_0")
        assert cup.beads_in_container == cup.beads_total == 8
        assert cup.max_transport_tilt_deg < 1.0
        assert r.physics_steps == upright_log.n
        assert r.ground_truth_use


def test_slot_route_passes_original_but_violates_corrected(scene, slot_log):
    orig = check(slot_log, make_spec(), scene)
    assert orig.status == "success"           # original spec only checks the final pose
    cup = next(o for o in orig.outcomes if o.object_id == "cup_0")
    assert cup.beads_in_container == 0        # beads spilled: a metric, not a rule
    assert cup.max_transport_tilt_deg > 85

    corr = check(slot_log, make_spec(corrected=True), scene)
    assert corr.status == "constraint_violated"
    v = [x for x in corr.violations if x.constraint_id == "c_upright_transport"]
    assert len(v) == 1 and v[0].object_id == "cup_0"
    assert 85 < v[0].peak <= 90.0 + 1e-6
    held = np.asarray(slot_log.held) == slot_log.index("cup_0")
    t_held = slot_log.t[held]
    assert t_held[0] < v[0].t_start < v[0].t_end < t_held[-1]


def test_scope_marker_tilt_not_flagged_by_cup_rule(scene, slot_log):
    corr = check(slot_log, make_spec(corrected=True), scene)
    assert all(v.object_id == "cup_0" for v in corr.violations)
    marker = next(o for o in corr.outcomes if o.object_id == "marker_0")
    assert marker.max_transport_tilt_deg > 85   # the marker did tilt, and that is allowed


def test_recheck_old_rollout_under_newer_spec_is_advisory(scene, slot_log):
    newer = make_spec(corrected=True)
    r = check(slot_log, make_spec(), scene, extra_rules=newer.constraints)
    assert r.status == "success"
    adv = advisory_violations(slot_log, make_spec(), scene, extra_rules=newer.constraints)
    assert [a.constraint_id for a in adv] == ["c_upright_transport"]
    assert [a.constraint_id for a in r.advisory_violations] == ["c_upright_transport"]
    assert r.advisory_violations[0].peak > 85 and not r.violations


def test_unenforced_proposed_rule_reported_only_on_request(scene, slot_log):
    spec = make_spec(marker_rule=True)
    r = check(slot_log, spec, scene)
    assert r.status == "success" and not r.advisory_violations
    r2 = check(slot_log, spec, scene, include_unenforced=True)
    assert r2.status == "success" and {a.object_id for a in r2.advisory_violations} == {"marker_0"}
    adv = advisory_violations(slot_log, spec, scene, include_unenforced=True)
    assert {a.object_id for a in adv} == {"marker_0"}


def test_final_upright_violated_when_cup_left_on_its_side(scene):
    log = pack_all(scene, cup_route="slot", leave_cup_tilted=True).build()
    r = check(log, make_spec(), scene)
    assert r.status == "constraint_violated"
    assert {v.constraint_id for v in r.violations} == {"c_upright_final"}


def test_not_settled_is_incomplete(scene):
    b = pack_all(scene, settle=0.0)
    b.wait(0.4, jitter=0.0005)   # objects still moving 0.25 m/s at the end
    r = check(b.build(), make_spec(), scene)
    assert r.status == "task_incomplete"
    assert not r.completion
    assert any("settled" in n for n in r.notes)


def test_object_still_held_is_incomplete(scene):
    b = pack_all(scene)
    k = b.ids.index("marker_0")
    b.wait(0.5, held=k, phase="hold")
    r = check(b.build(), make_spec(), scene)
    assert r.status == "task_incomplete"
    m = next(o for o in r.outcomes if o.object_id == "marker_0")
    assert m.in_target and not m.released


def test_object_outside_tray_is_incomplete(scene):
    b = LogBuilder(scene)
    b.wait(0.2)
    b.move("cup_0", [(-0.25, 0.0, 0.15), (-0.25, -0.3, 0.15), (0.05, -0.3, 0.15), (0.05, -0.3, 0.05)])
    b.wait(0.5)
    r = check(b.build(), make_spec(), scene)
    assert r.status == "task_incomplete"
    cup = next(o for o in r.outcomes if o.object_id == "cup_0")
    assert not cup.in_target and cup.released and cup.settled


def test_execution_status_takes_precedence(scene, slot_log):
    r = check(slot_log, make_spec(corrected=True), scene, execution_status="execution_failed",
              execution_reason="cup dropped")
    assert r.status == "execution_failed"
    assert r.violations   # still reported
    r2 = check(slot_log, make_spec(corrected=True), scene, execution_status="timeout")
    assert r2.status == "timeout"


def test_forbidden_contact_during_transport(scene):
    rule = Constraint(id="c_no_wall", type="forbidden_contact", applies_to=ObjectSelector(kind="cup"),
                      phase="transport", params={"with": ["obstacle"]}, status=Status.confirmed,
                      provenance=prov())
    log = pack_all(scene, cup_route="around", cup_contact="obstacle:wall").build()
    r = check(log, make_spec(extra=[rule]), scene)
    assert r.status == "constraint_violated"
    assert len(r.forbidden_contacts) == 1
    fc = r.forbidden_contacts[0]
    assert fc["object_id"] == "cup_0" and fc["other"] == "obstacle:wall" and fc["steps"] > 10
    clean = check(pack_all(scene).build(), make_spec(extra=[rule]), scene)
    assert clean.status == "success"


def test_contact_record_in_reverse_direction_is_matched(scene):
    rule = Constraint(id="c_no_touch", type="forbidden_contact", applies_to=ObjectSelector(kind="cup"),
                      phase="always", params={"with": ["other_objects"]}, status=Status.confirmed,
                      provenance=prov())
    b = pack_all(scene)
    from mirror.statelog import ContactRecord
    b.contacts.append(ContactRecord(step=5, object_id="block_0", other="cup_0", depth=0.0))
    r = check(b.build(), make_spec(extra=[rule]), scene)
    assert r.status == "constraint_violated"
    assert r.forbidden_contacts[0]["object_id"] == "cup_0"


def test_keep_clearance(scene):
    rule = Constraint(id="c_clear", type="keep_clearance", applies_to=ObjectSelector(kind="cup"),
                      phase="transport", params={"min_m": 0.02}, status=Status.confirmed,
                      provenance=prov())
    slot = check(pack_all(scene, cup_route="slot").build(), make_spec(extra=[rule]), scene)
    assert any(v.constraint_id == "c_clear" for v in slot.violations)
    around = check(pack_all(scene).build(), make_spec(extra=[rule]), scene)
    assert not any(v.constraint_id == "c_clear" for v in around.violations)


def test_missing_object_for_goal(scene, upright_log):
    from mirror.contracts import Goal
    spec = make_spec()
    spec.goals.append(Goal(id="g_extra", type="inside", object=ObjectSelector(role="nonexistent"),
                           provenance=prov()))
    r = check(upright_log, spec, scene)
    assert r.status == "task_incomplete"


def test_save_load_roundtrip_gives_identical_report(scene, slot_log, tmp_path):
    p = tmp_path / "log.npz"
    slot_log.save(p)
    again = StateLog.load(p)
    a = check(slot_log, make_spec(corrected=True), scene, rollout_id="x")
    b = check(again, make_spec(corrected=True), scene, rollout_id="x")
    assert a.model_dump() == b.model_dump()


def test_check_is_pure(scene, slot_log):
    before = slot_log.obj_pos.copy()
    spec = make_spec(corrected=True)
    dump = spec.model_dump()
    check(slot_log, spec, scene)
    assert np.array_equal(before, slot_log.obj_pos)
    assert spec.model_dump() == dump


def test_proposed_goals_are_pursued_rejected_goals_are_not(scene, upright_log):
    from mirror.contracts import Status
    spec = make_spec()
    spec.goals[0].status = Status.proposed          # cup goal still required (D1)
    b = LogBuilder(scene)
    b.wait(0.5)                                      # nothing moved
    assert check(b.build(), spec, scene).status == "task_incomplete"
    for g in spec.goals:
        g.status = Status.rejected
    r = check(upright_log, spec, scene)
    assert r.status == "task_incomplete" and any("no active goal" in n for n in r.notes)
