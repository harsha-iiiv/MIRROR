"""Arm execution in physics: commands go only through the joint servos."""
import numpy as np
import pytest

from mirror.arm.env import ArmEnv
from mirror.arm.model import DEFAULT_MOUNT
from mirror.contracts import PlanResult
from mirror.evaluate.checker import check
from mirror.plan import plan
from mirror.statelog import StateLog


@pytest.fixture(scope="module")
def first_pick(specs, scenes):
    """The reference plan cut after the cup is grasped and lifted."""
    full = plan(specs[1], scenes["demo"])
    steps = []
    for st in full.steps:
        steps.append(st)
        if st.kind == "lift":
            break
    return full.model_copy(update={"steps": steps})


@pytest.fixture(scope="module")
def run_first_pick(first_pick, scenes):
    env = ArmEnv(scenes["demo"], mount=DEFAULT_MOUNT)
    q_before = None
    res = env.execute(first_pick, frame_hz=10.0)
    return env, res


def test_grasp_and_lift_with_the_arm(run_first_pick):
    env, res = run_first_pick
    assert res.status == "completed", res.reason
    assert [e["type"] for e in res.events][:1] == ["grasp"] and res.events[0]["object_id"] == "cup_0"
    log = res.log
    k = log.index("cup_0")
    assert (log.held == k).any()
    lifted = log.obj_pos[-1, k, 2] - log.obj_pos[0, k, 2]
    assert lifted > 0.04          # moved by contact, never written
    assert res.log.meta["backend"]["name"] == "arm"
    assert res.log.meta["arm_joint_log"]["peak_tcp_tracking_error_mm"] < 10.0


def test_joint_limits_hold_during_execution(run_first_pick):
    env, res = run_first_pick
    q = np.array(res.log.meta["arm_joint_log"]["q"])
    lo, hi = env.index.arm_range[:, 0], env.index.arm_range[:, 1]
    assert q.size and np.all(q >= lo - 1e-3) and np.all(q <= hi + 1e-3)


def test_step_log_is_checker_compatible(run_first_pick, specs, scenes, tmp_path):
    env, res = run_first_pick
    log = res.log
    p = tmp_path / "arm_log.npz"
    log.save(p)
    back = StateLog.load(p)
    assert back.object_ids == ["cup_0", "block_0", "marker_0"]
    assert back.obj_pos.shape == (log.n, 3, 3) and back.gripper_quat.shape == (log.n, 4)
    assert "cup_0" in back.beads and back.beads["cup_0"].shape[1:] == (8, 3)
    rep = check(back, specs[1], scenes["demo"], execution_status=res.status)
    # partial plan: nothing is in the tray yet, so the task is honestly incomplete
    assert rep.status == "task_incomplete" and not rep.violations


def test_plan_the_arm_cannot_execute_is_not_run(specs, scenes):
    s = scenes["shortcut"]
    p = plan(specs[0], s, prefer="cup_0:through_slot")
    res = ArmEnv(s, mount=DEFAULT_MOUNT).execute(p, frame_hz=10.0)
    assert res.status == "execution_failed"
    assert res.reason.startswith("arm motion validation failed before execution")
    k = res.log.index("cup_0")
    assert np.allclose(res.log.obj_pos[0, k], res.log.obj_pos[-1, k], atol=1e-3)
