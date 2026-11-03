"""Arm motion validation: collision rejection, reachability, honest failure reports."""
import numpy as np
import pytest

from mirror.arm.model import DEFAULT_MOUNT
from mirror.arm.motion import Validator, flip_tool, validate
from mirror.arm.refine import raise_free_space
from mirror.contracts import PlanResult, PlanStep, Pose
from mirror.plan import plan


@pytest.fixture(scope="module")
def demo_plan(specs, scenes):
    return plan(specs[1], scenes["demo"])


def test_reference_plan_validates_on_one_continuous_branch(demo_plan, scenes):
    tr = validate(demo_plan, scenes["demo"], DEFAULT_MOUNT)
    assert tr.ok, tr.failure and tr.failure.text()
    n_wp = sum(len(s.waypoints) for s in demo_plan.steps)
    assert len(tr.segments) == n_wp
    v = Validator(scenes["demo"], DEFAULT_MOUNT)
    q_prev = tr.q_start
    for seg in tr.segments:
        assert np.allclose(seg.q[0], q_prev)                      # continuous across segments
        assert np.max(np.abs(np.diff(seg.q, axis=0)), initial=0) <= 0.12 + 1e-9
        assert np.all(seg.q >= v.k.lo) and np.all(seg.q <= v.k.hi)
        p, R = v.k.fk(seg.q[-1])                                   # ends exactly at the plan waypoint
        assert np.linalg.norm(p - np.asarray(seg.target.pos)) < 5e-4
        q_prev = seg.q[-1]


def test_arm_pressed_into_the_wall_is_detected(scenes):
    v = Validator(scenes["shortcut"], DEFAULT_MOUNT)
    q = v.start_configs(scenes["shortcut"].gripper_start)[0]
    assert v.collisions(q) == []
    # drive the TCP horizontally to the wall face at the slot height, tool pitched:
    # the wrist and the tool tube must hit the wall
    found = False
    from mirror.arm.kinematics import quat_to_mat
    R = quat_to_mat((np.cos(np.pi / 4), 0.0, np.sin(np.pi / 4), 0.0))
    for x in np.linspace(-0.30, -0.02, 15):
        r = v.k.ik((x, 0.0, 0.25), R, q)
        if r.ok:
            q = r.q
            if any(k == "collision" and "wall_0" in other for k, _, other, _ in v.collisions(q)):
                found = True
                break
    assert found


def test_slot_route_is_rejected_for_the_arm_as_a_collision(specs, scenes):
    s = scenes["shortcut"]
    p = plan(specs[0], s, prefer="cup_0:through_slot")
    assert p.status == "ok" and any("through_slot" in r for r in [x.label for x in p.routes if x.chosen])
    for flip in (False, True):
        tr = validate(p, s, DEFAULT_MOUNT, flip=flip)
        assert not tr.ok
        assert tr.failure.kind == "collision"
        assert tr.failure.step_kind == "transport" and tr.failure.object_id == "cup_0"
        assert "wall_0" in tr.failure.detail


def test_unreachable_waypoint_is_an_ik_failure_not_a_clamp(scenes):
    s = scenes["demo"].model_copy(update={"obstacles": []})
    far = Pose(pos=(-0.15, -1.6, 0.30))           # straight out past the table, outside the arm's reach
    p = PlanResult(status="ok", steps=[PlanStep(kind="move", waypoints=[far])])
    tr = validate(p, s, DEFAULT_MOUNT)
    assert not tr.ok and tr.failure.kind in ("ik_failed", "joint_limit", "discontinuity")
    assert "this is" not in tr.failure.detail    # a plain failure record; labelled by the backend


def test_raising_keeps_orientations_xy_and_endpoints(demo_plan, scenes):
    raised, notes = raise_free_space(demo_plan, scenes["demo"].gripper_start, 0.40)
    assert notes
    for a, b in zip(demo_plan.steps, raised.steps):
        assert a.kind == b.kind and a.object_id == b.object_id
        if a.kind not in ("move", "transport"):
            assert a.waypoints == b.waypoints
            continue
        assert b.waypoints[-1] == a.waypoints[-1]
        quats = {tuple(w.quat) for w in a.waypoints}
        assert all(tuple(w.quat) in quats or w is b.waypoints[0] for w in b.waypoints)
        assert all(w.pos[2] >= min(x.pos[2] for x in a.waypoints) - 1e-9 for w in b.waypoints)


def test_flipped_tool_is_the_same_jaw(demo_plan, scenes):
    flipped, start = flip_tool(demo_plan, scenes["demo"].gripper_start)
    from mirror.arm.kinematics import quat_to_mat
    for a, b in zip(demo_plan.steps, flipped.steps):
        for wa, wb in zip(a.waypoints, b.waypoints):
            Ra, Rb = quat_to_mat(wa.quat), quat_to_mat(wb.quat)
            assert wa.pos == wb.pos
            assert np.allclose(Ra[:, 2], Rb[:, 2])          # same approach axis
            assert np.allclose(Ra[:, 1], -Rb[:, 1])         # fingers swapped
