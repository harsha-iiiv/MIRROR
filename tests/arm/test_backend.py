"""The arm backend end to end: same scenes, specs, planner and checker."""
import time

import numpy as np
import pytest

from mirror.arm.backend import ArmBackend
from mirror.backends import BACKENDS, DEFAULT_BACKEND, GripperBackend, get_backend
from mirror.contracts import Constraint, ObjectSelector, Provenance, Status
from mirror.evaluate.checker import check
from mirror.evaluate.trial import execute_trial
from mirror.scenes.sampling import sample_layout


def test_registry_keeps_the_gripper_as_the_default_baseline():
    assert DEFAULT_BACKEND == "gripper" and BACKENDS == ("gripper", "arm")
    assert isinstance(get_backend(), GripperBackend)
    assert "baseline" in GripperBackend.label
    assert isinstance(get_backend("arm"), ArmBackend)
    with pytest.raises(ValueError):
        get_backend("hexapod")


def test_proven_infeasibility_is_passed_through(arm_backend, specs, scenes):
    p = arm_backend.plan(specs[1], scenes["blocked"])
    assert p.status == "no_plan"
    assert p.infeasibility and p.infeasibility["proven"] is True
    assert p.infeasibility["kind"] == "separating_wall_slot_clearance"
    assert p.reason.startswith("planner (before arm validation)")


def test_arm_failure_is_never_called_proven(arm_backend, specs, scenes):
    p = arm_backend.plan(specs[0], scenes["blocked"])     # gripper plans exist (on its side, through the slot)
    assert p.status == "no_plan" and not p.steps
    cert = p.infeasibility
    assert cert["proven"] is False and cert["kind"] == "arm_motion_planning_failure"
    assert cert["failure_kind"] in ("collision", "ik_failed", "joint_limit", "discontinuity", "self_collision")
    assert "not a proof" in p.reason
    assert any(r.rejected_reason and r.rejected_reason.startswith("arm backend") for r in p.routes)


def test_budget_exhaustion_is_reported_as_such(specs, scenes):
    b = ArmBackend(max_attempts=1)
    p = b.plan(specs[0], scenes["shortcut"])               # the first variant (slot route) fails
    assert p.status == "no_plan"
    assert p.infeasibility["search_budget_exhausted"] is True and p.infeasibility["proven"] is False
    assert "search budget exhausted" in p.reason


def test_slot_route_is_replaced_by_an_admissible_route(arm_backend, specs, scenes):
    p = arm_backend.plan(specs[0], scenes["shortcut"])
    assert p.status == "ok"
    chosen = [r.label for r in p.routes if r.chosen and not r.label.startswith("transit:")]
    assert chosen and not any("through_slot" in c for c in chosen)
    # the pitched slot routes the planner found feasible are rejected for the arm, with the reason
    arm_rejected = [r for r in p.routes if "through_slot" in r.label
                    and (r.rejected_reason or "").startswith("arm backend")]
    assert arm_rejected and all(not r.feasible and not r.chosen for r in arm_rejected)
    assert "UR10e motion validation" in p.planner


@pytest.mark.slow
def test_full_pack_on_the_reference_scene(arm_backend, specs, scenes):
    t0 = time.perf_counter()
    ro, log, plan = execute_trial(scenes["demo"], specs[1], condition="corrected",
                                  planner=arm_backend.plan, env_factory=arm_backend.env)
    rep = ro.report
    assert rep.status == "success", rep.notes
    assert all(o.in_target and o.released and o.settled for o in rep.outcomes)
    cup = next(o for o in rep.outcomes if o.object_id == "cup_0")
    assert cup.beads_in_container == cup.beads_total == 8 and cup.max_transport_tilt_deg < 5
    assert log.meta["backend"]["robot"] == "UR10e"
    assert log.meta["arm_joint_log"]["peak_tcp_tracking_error_mm"] < 10
    assert ro.visual is not None and any(b.name.startswith("arm/") for b in ro.visual.bodies)
    assert any(k.startswith("arm/") for k in ro.frames[0].bodies)
    assert time.perf_counter() - t0 < 120


def _clearance_spec(base, m):
    s = base.model_copy(deep=True)
    s.version, s.parent_version = base.version + 1, base.version
    s.constraints.append(Constraint(
        id="c_container_clearance", type="keep_clearance", applies_to=ObjectSelector(role="container"),
        phase="transport", params={"min_m": m}, status=Status.confirmed,
        provenance=Provenance(source="manual", version_introduced=s.version, evidence="synthetic test rule")))
    return s


@pytest.mark.slow
def test_old_arm_run_is_rejected_under_a_new_rule_by_the_checker(arm_backend, specs, scenes):
    """The checker re-reads the arm's step log: the around route that passes
    v2 fails a newly confirmed clearance rule. (Synthetic rule for the test.)"""
    s = scenes["demo"]
    ro, log, _ = execute_trial(s, specs[1], condition="v2", planner=arm_backend.plan, env_factory=arm_backend.env)
    assert ro.report.status == "success"
    v3 = _clearance_spec(specs[1], 0.04)
    rep = check(log, v3, s)
    assert rep.status == "constraint_violated"
    assert {v.constraint_id for v in rep.violations} == {"c_container_clearance"}
    adv = check(log, specs[1], s, extra_rules=v3.constraints)
    assert adv.status == "success" and adv.advisory_violations


@pytest.mark.slow
@pytest.mark.parametrize("seed", [300, 301, 302])
def test_development_layouts_paired(arm_backend, specs, seed):
    """Development layouts not used to choose the mount (that used seeds 100-107).
    Every outcome must be one of the honest categories."""
    s = sample_layout(seed, "dev")
    for spec in specs:
        ro, log, plan = execute_trial(s, spec, condition=f"v{spec.version}",
                                      planner=arm_backend.plan, env_factory=arm_backend.env)
        rep = ro.report
        if rep.status == "planning_failed":
            assert plan.infeasibility and (plan.infeasibility.get("proven") is False
                                           or plan.infeasibility.get("kind") != "arm_motion_planning_failure")
            continue
        assert rep.status == "success", (seed, spec.version, rep.status, rep.notes)
        cup = next(o for o in rep.outcomes if o.object_id.startswith("cup"))
        assert cup.beads_in_container == cup.beads_total


def test_arm_plans_clearance_with_a_tracking_margin(arm_backend):
    from mirror.arm.backend import CLEARANCE_TRACKING_MARGIN
    assert CLEARANCE_TRACKING_MARGIN > 0
    assert arm_backend.planner_config.clearance_tracking_margin == CLEARANCE_TRACKING_MARGIN


@pytest.mark.slow
def test_arm_execution_keeps_clearance_with_margin(arm_backend, specs, scenes):
    """The arm tracks its trajectory with a 2-3 mm lag, so clearance rules are planned
    CLEARANCE_TRACKING_MARGIN wider than min_m; the checker still judges min_m."""
    from mirror.evaluate.geom import quat_to_mat
    from mirror.plan.planner import object_clearance
    s = scenes["demo"]
    spec = _clearance_spec(specs[1], 0.04)
    ro, log, plan = execute_trial(s, spec, condition="v3", planner=arm_backend.plan, env_factory=arm_backend.env)
    assert plan.status == "ok" and ro.report.status == "success", ro.report.notes
    cup = next(o for o in s.objects if o.kind == "cup")
    i = log.object_ids.index(cup.id)
    held = [k for k in range(log.n) if int(log.held[k]) == i]
    dmin = min(object_clearance(cup, s, log.obj_pos[k, i], quat_to_mat(log.obj_quat[k, i]))[0] for k in held[::5])
    assert dmin >= 0.04
