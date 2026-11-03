"""Exact solid-shape distances used by the clearance monitor (checker 2)."""
import math

import numpy as np

from mirror.evaluate.geom import box_box_distance, quat_to_mat
from mirror.plan.collision import Box, box_distance


def _yaw(deg):
    a = math.radians(deg) / 2
    return np.array([math.cos(a), 0.0, 0.0, math.sin(a)])


def test_axis_aligned_gap_is_exact():
    d = box_box_distance(np.array([[0.30, 0.0, 0.0]]), np.eye(3), [0.1, 0.1, 0.1], np.zeros(3), np.eye(3),
                         [0.05, 0.2, 0.2])
    assert abs(d[0] - 0.15) < 1e-9


def test_rotated_corner_gap_is_exact():
    # unit-ish cube rotated 45 degrees about z: its corner points at the other box
    h = 0.05
    c = np.array([[0.2, 0.0, 0.0]])
    d = box_box_distance(c, quat_to_mat(_yaw(45))[None], [h, h, h], np.zeros(3), np.eye(3), [0.05, 0.3, 0.3])
    assert abs(d[0] - (0.2 - h * math.sqrt(2) - 0.05)) < 1e-7


def test_corner_to_corner_diagonal():
    d = box_box_distance(np.array([[0.3, 0.3, 0.0]]), np.eye(3), [0.1, 0.1, 0.1], np.zeros(3), np.eye(3),
                         [0.1, 0.1, 0.1])
    assert abs(d[0] - math.hypot(0.1, 0.1)) < 1e-7


def test_overlap_is_zero_and_segment_core_works():
    assert box_box_distance(np.zeros((1, 3)), np.eye(3), [0.1] * 3, np.zeros(3), np.eye(3), [0.1] * 3)[0] < 1e-12
    seg = box_box_distance(np.array([[0.0, 0.0, 0.5]]), np.eye(3), [0.0, 0.0, 0.1], np.zeros(3), np.eye(3),
                           [0.2, 0.2, 0.2])
    assert abs(seg[0] - 0.2) < 1e-9


def test_planner_and_checker_distances_agree():
    rng = np.random.default_rng(3)
    for _ in range(25):
        q = rng.normal(size=4)
        q /= np.linalg.norm(q)
        R = quat_to_mat(q)
        c = rng.uniform(-0.3, 0.3, 3)
        a = Box(c, R, np.array([0.03, 0.03, 0.05]))
        b = Box(np.zeros(3), np.eye(3), np.array([0.01, 0.2, 0.25]))
        ref = box_box_distance(c[None], R[None], a.h, b.c, b.R, b.h)[0]
        assert abs(box_distance(a, b) - ref) < 1e-6
