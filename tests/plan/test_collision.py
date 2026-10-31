import math

import numpy as np
import pytest
from scipy.optimize import linprog
from scipy.spatial.transform import Rotation

from mirror.plan.collision import make_envelope, sat_overlap, segment_samples
from mirror.plan.grasp import grasp_model
from mirror.plan.se3 import gripper_rot, mat_to_quat, quat_to_mat, slerp_mats
from mirror.scenes.objects import make_cup


def _lp_overlap(ca, Ra, ha, cb, Rb, hb):
    A, b = [], []
    for c, R, h in ((ca, Ra, ha), (cb, Rb, hb)):
        A += list(R.T); b += list(R.T @ c + h)
        A += list(-R.T); b += list(-(R.T @ c) + h)
    res = linprog(np.zeros(3), A_ub=np.array(A), b_ub=np.array(b), bounds=[(None, None)] * 3, method="highs")
    return res.status == 0


def test_sat_matches_linear_programming_reference():
    rng = np.random.default_rng(3)
    n = 200
    ca, cb = rng.uniform(-0.2, 0.2, (n, 3)), rng.uniform(-0.2, 0.2, (n, 3))
    Ra = Rotation.random(n, random_state=4).as_matrix()
    Rb = Rotation.random(n, random_state=5).as_matrix()
    ha, hb = rng.uniform(0.01, 0.12, (n, 3)), rng.uniform(0.01, 0.12, (n, 3))
    full = sat_overlap(ca, Ra, ha, cb, Rb, hb)
    for i in range(n):
        assert full[i, i] == _lp_overlap(ca[i], Ra[i], ha[i], cb[i], Rb[i], hb[i])


def test_quaternion_round_trip():
    for q in Rotation.random(50, random_state=1).as_quat():
        wxyz = (q[3], q[0], q[1], q[2])
        m = quat_to_mat(wxyz)
        assert np.allclose(quat_to_mat(mat_to_quat(m)), m, atol=1e-9)


def test_segment_sampling_bounds_point_travel():
    env = make_envelope(0.06)
    R0, R1 = gripper_rot(0.0), gripper_rot(0.3, math.pi / 2)
    pos, rots = segment_samples([0, 0, 0.2], R0, [0.3, -0.1, 0.25], R1, env.reach, 0.01)
    pts = []
    for p, R in zip(pos, rots):
        pts.append([p + R @ (b.c + b.R @ (b.h * s)) for b in env.boxes
                    for s in (np.array([1, 1, 1]), np.array([-1, -1, -1]))])
    pts = np.array(pts)
    step = np.linalg.norm(np.diff(pts, axis=0), axis=2).max()
    assert step <= 0.01 + 1e-9


def test_cup_envelope_heights_match_reference_geometry():
    cup = make_cup("cup_0", 0, 0)
    gm = grasp_model(cup)
    env = make_envelope(gm.width, gm.held("cup_0", 0.0))
    lo, hi = env.vertical_extent(gripper_rot(0.0))
    assert hi - lo == pytest.approx(0.170, abs=1e-6)   # cup bottom to palm top, carried upright
    lo, hi = env.vertical_extent(gripper_rot(0.0, math.pi / 2))
    assert hi - lo == pytest.approx(0.060, abs=1e-6)   # on its side: the cup's width dominates


def test_slerp_endpoints():
    a, b = gripper_rot(0.1), gripper_rot(1.2, 0.7)
    s = slerp_mats(a, b, np.array([0.0, 1.0]))
    assert np.allclose(s[0], a) and np.allclose(s[1], b)
