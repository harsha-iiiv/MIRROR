import math

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from mirror.contracts import Constraint, ObjectSelector, Provenance, Status
from mirror.plan import PlannerConfig, plan
from mirror.plan.collision import make_envelope
from mirror.plan.grasp import grasp_model
from mirror.plan.se3 import rot_y, rot_z
from mirror.scenes import reference_scene
from mirror.scenes.objects import make_cup

from .fixtures import base_spec, corrected_spec
from .helpers import cached_plan


def _section_z(box_c, box_R, box_h, R):
    """z range of the intersection of a box (gripper frame, TCP at the origin)
    with the plane x = 0 for gripper orientation R; None if empty."""
    corners = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)], float)
    P = (R @ (box_c[:, None] + box_R @ (box_h[:, None] * corners.T))).T
    edges = [(i, j) for i in range(8) for j in range(i + 1, 8) if np.sum(corners[i] != corners[j]) == 1]
    zs = []
    for i, j in edges:
        a, b = P[i], P[j]
        if (a[0] <= 0 <= b[0]) or (b[0] <= 0 <= a[0]):
            if abs(b[0] - a[0]) < 1e-12:
                zs += [a[2], b[2]]
            else:
                t = -a[0] / (b[0] - a[0])
                zs.append(a[2] + t * (b[2] - a[2]))
    return (min(zs), max(zs)) if zs else None


def test_certificate_bound_holds_on_sampled_orientations():
    """Numerical check of the analytic bound: with the TCP in the wall plane and
    the cup within 15 deg of upright, the palm and the cup floor meet the plane
    and span at least the certified height."""
    cert = cached_plan("blocked", "corr").infeasibility
    e_min = cert["quantities"]["min_vertical_span_m"]
    cup = make_cup("cup_0", 0, 0)
    gm = grasp_model(cup)
    env = make_envelope(gm.width, None)
    palm = next(b for b in env.boxes if b.name == "palm")
    h = gm.half
    floor_c = np.array([0.0, 0.0, -gm.tcp_offset_z - h[2] + 0.002])
    floor_h = np.array([h[0], h[1], 0.002])
    rng = np.random.default_rng(0)
    worst = math.inf
    for _ in range(1500):
        a = math.radians(15.0) * math.sqrt(rng.random())
        R = rot_z(rng.uniform(-math.pi, math.pi)) @ rot_y(a) @ rot_z(rng.uniform(-math.pi, math.pi))
        top = _section_z(palm.c, palm.R, palm.h, R)
        bot = _section_z(floor_c, np.eye(3), floor_h, R)
        assert top is not None and bot is not None
        worst = min(worst, top[1] - bot[0])
    assert worst >= e_min - 1e-9


def test_loose_tilt_limit_gives_no_certificate():
    spec = corrected_spec()
    spec.constraints[1].params["max_tilt_deg"] = 60.0
    r = plan(spec, reference_scene("blocked"), config=PlannerConfig(rrt_iterations=200))
    # 60 deg still bans the 90 deg modes, but impossibility is not established
    assert r.status == "no_plan"
    assert r.infeasibility is None
    assert "not a proof" in r.reason


def test_marker_upright_rule_is_not_certified():
    spec = base_spec(None)
    spec.constraints.append(Constraint(id="c_marker_upright", type="upright",
                                       applies_to=ObjectSelector(kind="marker"), phase="transport",
                                       params={"max_tilt_deg": 15.0}, status=Status.confirmed,
                                       provenance=Provenance(source="manual")))
    cfg = PlannerConfig(rrt_iterations=200)
    r = plan(spec, reference_scene("blocked"), config=cfg)
    assert r.status == "no_plan"
    assert r.infeasibility is None  # the thin marker section defeats this proof
    assert "marker_0" in r.reason and "not a proof" in r.reason


def test_no_certificate_when_a_route_exists():
    r = cached_plan("shortcut", "corr")
    assert r.status == "ok" and r.infeasibility is None
