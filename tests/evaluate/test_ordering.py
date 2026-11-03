"""Ordering rules are judged from execution evidence (contract 1.2)."""
from __future__ import annotations

import glob
import json
from pathlib import Path

import numpy as np
import pytest

from mirror.contracts import (Goal, ObjectSelector, ObjectSpec, OrderingConstraint, PlanResult, Pose,
                              Rollout, Status, TaskSpec)
from mirror.evaluate import check
from mirror.evaluate.evidence import log_from_rollout_frames, object_events, scene_from_rollout
from mirror.geometry import BLOCK

from .synth import DT, TRAY_Z, LogBuilder, make_scene, make_spec, pack_all, prov

REPO = Path(__file__).resolve().parents[2]


def order(before, after, status=Status.confirmed, be="placement_complete", ae="grasp_start", oid=None):
    return OrderingConstraint(id=oid or f"o_{before}_{after}_{be}_{ae}", before=ObjectSelector(kind=before),
                              after=ObjectSelector(kind=after), status=status, provenance=prov("user_correction", 2),
                              before_event=be, after_event=ae)


def with_order(*orders, base=None):
    s = (base or make_spec()).model_copy(deep=True)
    s.ordering = list(orders)
    return s


def oresult(report, oid=None):
    rs = [r for r in report.rule_results if r.kind == "ordering" and (oid is None or r.rule_id == oid)]
    assert len(rs) == 1, report.rule_results
    return rs[0]


@pytest.fixture(scope="module")
def scene():
    return make_scene()


@pytest.fixture(scope="module")
def log(scene):          # packs cup, then block, then marker
    return pack_all(scene).build()


EVENTS = [("placement_complete", "grasp_start"), ("grasp_start", "grasp_start"),
          ("placement_complete", "placement_complete")]


@pytest.mark.parametrize("be,ae", EVENTS)
def test_wrong_order_fails_and_compliant_order_passes(scene, log, be, ae):
    wrong = check(log, with_order(order("marker", "cup", be=be, ae=ae)), scene)
    assert wrong.status == "constraint_violated"
    r = oresult(wrong)
    assert r.verdict == "violated" and r.enforced
    assert any(v.constraint_id == r.rule_id and v.object_id == "cup_0" for v in wrong.violations)
    right = check(log, with_order(order("cup", "marker", be=be, ae=ae)), scene)
    assert right.status == "success", right.notes
    assert oresult(right).verdict == "satisfied"


def test_events_come_from_motion(scene, log):
    tray = scene.trays[0]
    cup, marker = (object_events(log, scene, o, tray) for o in ("cup_0", "marker_0"))
    assert cup.grasp_start < cup.placement_complete < marker.grasp_start < marker.placement_complete
    assert cup.placement == "occurred"


def test_checker_ignores_plan_order_and_phase_labels(scene, log):
    spec = with_order(order("marker", "cup"))
    fake_plan = PlanResult(status="ok", object_order=["marker_0", "cup_0", "block_0"],
                           reason="marker_0 packed first as required")
    lying = log.__class__(**{**log.__dict__, "phase": ["transport:marker_0"] * log.n})
    for kw in ({}, {"plan": fake_plan}):
        for lg in (log, lying):
            assert oresult(check(lg, spec, scene, **kw)).verdict == "violated"


def test_proposed_ordering_is_reported_not_enforced(scene, log):
    rep = check(log, with_order(order("marker", "cup", status=Status.proposed)), scene)
    assert rep.status == "success"
    r = oresult(rep)
    assert r.verdict == "violated" and not r.enforced and r.status == Status.proposed
    assert any(v.constraint_id == r.rule_id for v in rep.advisory_violations)
    assert not rep.violations


def test_rejected_ordering_is_recorded_not_evaluated(scene, log):
    rep = check(log, with_order(order("marker", "cup", status=Status.rejected)), scene)
    assert rep.status == "success"
    r = oresult(rep)
    assert r.verdict == "not_evaluated" and not r.enforced
    assert not rep.advisory_violations


def test_newer_version_ordering_is_advisory(scene, log):
    rep = check(log, make_spec(), scene, extra_ordering=[order("marker", "cup")])
    assert rep.status == "success"
    assert oresult(rep).verdict == "violated" and not oresult(rep).enforced


def test_missing_object_is_unknown_and_never_success(scene, log):
    # the marker is not a goal here, and it is absent from the evidence
    spec = with_order(order("marker", "cup"))
    spec.goals = [g for g in spec.goals if g.object.kind != "marker"]
    k = log.index("marker_0")
    keep = [i for i in range(len(log.object_ids)) if i != k]
    held = np.where(log.held == k, -1, log.held)
    held = np.array([keep.index(h) if h >= 0 else -1 for h in held])
    partial = log.__class__(dt=log.dt, t=log.t, object_ids=[log.object_ids[i] for i in keep],
                            obj_pos=log.obj_pos[:, keep], obj_quat=log.obj_quat[:, keep],
                            gripper_pos=log.gripper_pos, gripper_quat=log.gripper_quat,
                            gripper_width=log.gripper_width, held=held, phase=log.phase, beads=log.beads,
                            contacts=log.contacts, meta=log.meta)
    rep = check(partial, spec, scene)
    assert oresult(rep).verdict == "unknown"
    assert rep.status == "unverified"
    assert rep.status != "success"


def test_before_object_never_placed_while_after_object_moved_is_a_violation(scene):
    b = LogBuilder(scene)
    b.wait(0.2)
    b.move("cup_0", [(-0.25, 0.0, 0.15), (0.20, -0.04, 0.15), (0.20, -0.04, TRAY_Z + 0.05)])
    b.wait(0.6)
    spec = with_order(order("marker", "cup"))
    spec.goals = [g for g in spec.goals if g.object.kind == "cup"]
    rep = check(b.build(), spec, scene)
    r = oresult(rep)
    assert r.verdict == "violated" and rep.status == "constraint_violated"


def test_after_object_never_moved_holds_vacuously(scene):
    b = LogBuilder(scene)
    b.wait(0.2)
    b.move("cup_0", [(-0.25, 0.0, 0.15), (0.20, -0.04, 0.15), (0.20, -0.04, TRAY_Z + 0.05)])
    b.wait(0.6)
    spec = with_order(order("cup", "marker"))
    spec.goals = [g for g in spec.goals if g.object.kind == "cup"]
    r = oresult(check(b.build(), spec, scene))
    assert r.verdict == "satisfied" and "vacuous" in r.detail


def test_placement_still_settling_at_the_end_is_unknown(scene):
    b = LogBuilder(scene)
    b.wait(0.2)
    b.move("block_0", [(-0.25, 0.15, 0.15), (0.30, 0.04, 0.15), (0.30, 0.04, TRAY_Z + BLOCK.half[2])])
    b.move("cup_0", [(-0.25, 0.0, 0.15), (0.20, -0.04, 0.15), (0.20, -0.04, TRAY_Z + 0.05)])
    b.wait(0.05, jitter=0.0)
    # the cup's placement is the before event; the block was grasped first anyway (violation),
    # so use a rule whose after event is the cup being grasped after the block placement window
    spec = with_order(order("cup", "block", be="placement_complete", ae="placement_complete"))
    ev = object_events(b.build(), scene, "cup_0", scene.trays[0])
    assert ev.placement == "unresolved"
    rep = check(b.build(), spec, scene)
    assert oresult(rep).verdict in ("unknown", "violated")
    assert rep.status != "success"


def test_simultaneous_events_within_the_margin_are_unknown(scene, log):
    """Events closer than the timing margin (two evidence samples) cannot be ordered."""
    from mirror.evaluate.checker import _context, monitor_ordering
    from mirror.evaluate.evidence import ObjectEvents
    spec = with_order(order("cup", "block", be="grasp_start", ae="grasp_start"))
    for gap, expect in ((0.0, "unknown"), (DT, "unknown"), (-DT, "unknown"), (10 * DT, "satisfied"),
                        (-10 * DT, "violated")):
        ctx = _context(log, spec, scene)
        ctx._events["cup_0"] = ObjectEvents("cup_0", True, grasp_start=1.0, placement="occurred",
                                            placement_complete=2.0)
        ctx._events["block_0"] = ObjectEvents("block_0", True, grasp_start=1.0 + gap, placement="occurred",
                                              placement_complete=3.0)
        verdict, *_ = monitor_ordering(ctx, spec.ordering[0])
        assert verdict == expect, (gap, verdict)


def test_several_matching_objects_every_pair_is_checked():
    base = make_scene()
    extra = ObjectSpec(id="block_1", kind="block", size={"half": BLOCK.half}, mass=BLOCK.mass,
                       pose=Pose(pos=(-0.35, 0.25, BLOCK.half[2])))
    scene = base.model_copy(update={"objects": base.objects + [extra]})
    b = LogBuilder(scene)
    b.wait(0.2)
    b.move("block_0", [(-0.25, 0.15, 0.15), (0.30, 0.04, 0.15), (0.30, 0.04, TRAY_Z + BLOCK.half[2])])
    b.wait(0.4)
    b.move("marker_0", [(-0.30, -0.15, 0.2), (0.30, -0.05, 0.2), (0.30, -0.05, TRAY_Z + 0.072)])
    b.wait(0.4)
    b.move("block_1", [(-0.35, 0.25, 0.15), (0.20, 0.05, 0.15), (0.20, 0.05, TRAY_Z + BLOCK.half[2])])
    b.wait(0.6)
    spec = make_spec()
    spec.goals = [g for g in spec.goals if g.object.kind != "cup"]
    rep = check(b.build(), with_order(order("block", "marker"), base=spec), scene)
    r = oresult(rep)
    assert r.verdict == "violated"
    bad = [p for p in r.evidence["pairs"] if p["verdict"] == "violated"]
    assert [(p["before"], p["after"]) for p in bad] == [("block_1", "marker_0")]
    assert {(p["before"], p["after"]) for p in r.evidence["pairs"]} == {("block_0", "marker_0"),
                                                                       ("block_1", "marker_0")}


def test_selector_matching_nothing_is_not_applicable(scene, log):
    spec = with_order(OrderingConstraint(id="o_x", before=ObjectSelector(role="nonexistent"),
                                         after=ObjectSelector(kind="cup"), status=Status.confirmed,
                                         provenance=prov()))
    rep = check(log, spec, scene)
    assert oresult(rep).verdict == "not_applicable"
    assert rep.status == "success"


def test_pre_1_2_ordering_loads_with_defaults_and_round_trips(scene):
    old = {"id": "o_cup_before_block", "before": {"kind": "cup", "role": None, "ids": None},
           "after": {"kind": "block", "role": None, "ids": None}, "status": "confirmed",
           "provenance": {"source": "manual", "evidence": "", "version_introduced": 1, "model": None}}
    oc = OrderingConstraint.model_validate(old)
    assert (oc.before_event, oc.after_event) == ("placement_complete", "grasp_start")
    assert json.loads(oc.model_dump_json()) == old


# ------------------------------------------------- historical evidence ---

HIST = sorted(glob.glob(str(REPO / "results" / "eval*" / "*" / "rollouts" / "*.json")))


def _load(path):
    return Rollout.model_validate_json(Path(path).read_text())


def test_historical_rollouts_round_trip_unchanged():
    for f in HIST[:: max(1, len(HIST) // 12)]:
        raw = json.loads(Path(f).read_text())
        assert json.loads(_load(f).model_dump_json()) == raw


def _hist_with_frames(n):
    out = []
    for f in HIST:
        ro = _load(f)
        if ro.frames and ro.report.status == "success":
            out.append((f, ro))
        if len(out) >= n:
            break
    return out


def test_historical_rollout_rechecks_identically_under_its_own_rules():
    from mirror.evaluate.specs import reference_specs
    orig, corr = reference_specs()
    for f, ro in _hist_with_frames(4):
        scene = scene_from_rollout(ro)
        log = log_from_rollout_frames(ro, scene)
        rep = check(log, corr if ro.report.condition == "corrected" else orig, scene)
        assert rep.status == ro.report.status, (f, rep.notes)
        assert rep.evidence_source == "playback_frames"


def test_wrong_order_historical_rollout_fails_under_a_newly_confirmed_order():
    from mirror.evaluate.specs import reference_specs
    orig, corr = reference_specs()
    f, ro = _hist_with_frames(1)[0]
    corr = corr if ro.report.condition == "corrected" else orig   # the rollout's own rules
    scene = scene_from_rollout(ro)
    log = log_from_rollout_frames(ro, scene)
    ev = {o.id: object_events(log, scene, o.id, scene.trays[0]) for o in scene.objects}
    first, last = sorted(ev, key=lambda o: ev[o].grasp_start)[0], sorted(ev, key=lambda o: ev[o].grasp_start)[-1]
    kind = lambda oid: oid.split("_")[0]
    wrong = corr.model_copy(deep=True)
    wrong.version += 1
    wrong.ordering = [order(kind(last), kind(first))]
    rep = check(log, wrong, scene)
    assert rep.status == "constraint_violated" and oresult(rep).verdict == "violated"
    right = corr.model_copy(deep=True)
    right.ordering = [order(kind(first), kind(last))]
    assert check(log, right, scene).status == "success"
    # contact rules cannot be verified from playback frames: unknown, never success
    from mirror.contracts import Constraint
    contact = corr.model_copy(deep=True)
    contact.constraints.append(Constraint(id="c_nocontact", type="forbidden_contact",
                                          applies_to=ObjectSelector(kind="cup"), phase="transport",
                                          params={"with": ["obstacle"]}, status=Status.confirmed,
                                          provenance=prov("user_correction", 3)))
    rep = check(log, contact, scene)
    assert rep.status == "unverified"


@pytest.mark.slow
def test_every_historical_rollout_rechecks_identically():
    from mirror.evaluate.specs import reference_specs
    orig, corr = reference_specs()
    n = 0
    for f in HIST:
        ro = _load(f)
        if not ro.frames:
            continue
        scene = scene_from_rollout(ro)
        rep = check(log_from_rollout_frames(ro, scene), corr if ro.report.condition == "corrected" else orig, scene)
        assert rep.status == ro.report.status, f
        n += 1
    assert n >= 250
