"""Inverse kinematics accuracy, joint limits and failure reporting."""
import math

import numpy as np
import pytest

from mirror.arm.kinematics import Kinematics, quat_to_mat
from mirror.arm.model import DEFAULT_MOUNT, Mount, build_arm_model


@pytest.fixture(scope="module", params=["ur10e", "ur5e"])
def kin(request, scenes):
    mount = DEFAULT_MOUNT if request.param == "ur10e" else Mount(arm="ur5e", pos=(0.0, 0.45, 0.95),
                                                                  tool_extension=0.30)
    m, idx = build_arm_model(scenes["demo"], mount)
    return Kinematics(m, idx)


def test_ik_recovers_random_reachable_poses(kin):
    rng = np.random.default_rng(0)
    errs, n_ok, n = [], 0, 60
    for _ in range(n):
        q = rng.uniform(kin.lo / 2, kin.hi / 2)
        p, R = kin.fk(q)
        r = kin.ik(p, R, q + rng.normal(0.0, 0.15, 6))
        if r.ok:
            n_ok += 1
            p2, R2 = kin.fk(r.q)
            errs.append(float(np.linalg.norm(p2 - p)))
            ang = math.acos(max(-1.0, min(1.0, (np.trace(R2.T @ R) - 1) / 2)))
            assert ang < 3e-3
            assert kin.within_limits(r.q)
    assert n_ok >= 0.9 * n
    assert max(errs) < 3e-4      # 0.3 mm


def test_ik_reports_unreachable_targets_as_failure(kin):
    q0 = np.zeros(6)
    r = kin.ik((3.0, 0.0, 0.0), np.eye(3), q0, max_iters=100)
    assert not r.ok and "did not converge" in r.reason
    assert r.pos_err > 0.5


def test_ik_respects_joint_limits(kin):
    lo, hi = kin.lo.copy(), kin.hi.copy()
    try:
        # pose reachable only with the elbow bent beyond +/- 0.4 rad
        q = np.array([0.3, -1.0, 1.6, -1.0, -1.2, 0.4])
        p, R = kin.fk(q)
        assert kin.ik(p, R, q + 0.05).ok
        kin.lo[2], kin.hi[2] = -0.4, 0.4
        r = kin.ik(p, R, np.array([0.3, -1.0, 0.3, -1.0, -1.2, 0.4]))
        assert np.all(r.q >= kin.lo - 1e-9) and np.all(r.q <= kin.hi + 1e-9)
        assert not r.ok and "joint limit" in r.reason
    finally:
        kin.lo[:], kin.hi[:] = lo, hi


def test_tcp_frame_is_the_gripper_frame(scenes):
    """FK of the arm lands on the gripper body origin, and the approach axis
    (gripper -z) is the flange normal, i.e. the TCP the planner talks about."""
    import mujoco
    m, idx = build_arm_model(scenes["demo"], DEFAULT_MOUNT)
    k = Kinematics(m, idx)
    q = np.array([0.4, -1.1, 1.5, -2.0, -1.57, 0.2])
    p, R = k.fk(q)
    d = mujoco.MjData(m)
    d.qpos[idx.arm_qposadr] = q
    mujoco.mj_forward(m, d)
    site = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SITE, "tcp")
    assert np.allclose(d.site_xpos[site], p, atol=1e-9)
    w3 = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "arm/wrist_3_link")
    flange_normal = d.xmat[w3].reshape(3, 3)[:, 1]          # wrist_3 local +y
    assert np.allclose(-R[:, 2], flange_normal, atol=1e-9)
