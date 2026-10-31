import math

import numpy as np
import pytest

from mirror.evaluate.feasibility import (PALM_INSCRIBED, classify_planning_failure, feasibility,
                                         planner_claims_proof, upright_infeasibility, witness_bound)
from mirror.evaluate.geom import object_half_extents

from .synth import make_scene, make_spec


def _rand_axis(rng, alpha):
    th = rng.uniform(0, alpha)
    ph = rng.uniform(0, 2 * math.pi)
    return np.array([math.sin(th) * math.cos(ph), math.sin(th) * math.sin(ph), math.cos(th)])


def _witness(p, a, n, u):
    """Closest point to the axis point p + u a on the line where the plane
    perpendicular to a through that point meets the plane n.(x - p) = 0,
    computed as a minimum-norm least-squares solution (independent of the
    closed form used in feasibility.py)."""
    A = np.vstack([a, n])
    b = np.array([0.0, -u * float(n @ a)])
    v, *_ = np.linalg.lstsq(A, b, rcond=None)
    return p + u * a + v, float(np.linalg.norm(v))


def test_witness_height_bound_holds_for_random_motions():
    scene = make_scene(full_wall=True)
    cup = scene.objects[0]
    alpha = math.radians(15)
    wb = witness_bound(cup, 15.0)
    H = 2 * object_half_extents(cup)[2]
    rng = np.random.default_rng(0)
    for _ in range(4000):
        a = _rand_axis(rng, alpha)
        phi = rng.uniform(0, 2 * math.pi)
        n = np.array([math.cos(phi), math.sin(phi), 0.0])
        g = rng.uniform(0, wb["g_max"])
        p = rng.uniform(-0.3, 0.3, 3)
        wt, dt = _witness(p, a, n, wb["u_top"])
        wbot, db = _witness(p, a, n, -(H - g))
        assert dt < PALM_INSCRIBED                       # top witness is a palm point
        assert db < min(object_half_extents(cup)[:2])    # bottom witness is on the cup floor
        assert wt[2] - wbot[2] >= wb["lower"] - 1e-12
        assert wt[2] - wbot[2] <= (wb["u_top"] + H) / math.cos(alpha) ** 2 + 1e-12
        assert np.linalg.norm((wt - p)[:2]) <= wb["offset"] + 1e-12


def test_proof_on_full_wall_and_scope_limits():
    full = make_scene(full_wall=True)
    proof = upright_infeasibility(make_spec(corrected=True), full)
    assert proof and proof["proven"] and proof["scope"] == "any_motion_rigid_top_grasp"
    assert proof["witness_height_lower_m"] > proof["openings_vertical_span_m"]
    assert upright_infeasibility(make_spec(), full) is None
    assert upright_infeasibility(make_spec(corrected=True), make_scene()) is None
    assert upright_infeasibility(make_spec(corrected=True), make_scene(full_wall=True, slot_height=(0.05, 0.25))) is None
    assert witness_bound(full.objects[0], 40.0) is None     # no valid witness: no claim


def test_only_proven_true_counts_as_a_planner_proof():
    assert planner_claims_proof({"proven": True, "scope": "any_motion"})
    assert not planner_claims_proof({"proven": False, "scope": "planner_model", "proof": "text"})
    assert classify_planning_failure({"proven": False, "scope": "planner_model"}, "unknown") == \
        "planner_failure_feasibility_unknown"
    assert classify_planning_failure({"proven": False}, "proven_infeasible") == \
        "infeasible_without_planner_certificate"
