"""Independent feasibility analysis for the upright-transport rule.

Used only to label outcomes in evaluation reports: to distinguish a proven
infeasibility from an ordinary planner failure, and to count feasible scenes
that a planner wrongly rejected. It is not used by the planner.

The infeasibility argument is conservative. It considers only the carried
object (the gripper body would only make passage harder) and assumes the
tool centre point lies within the grasped object's bounding sphere, which
holds for a pinch grasp whose pads touch the object.
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from mirror.contracts import ObstacleSpec, SceneState, Status, TaskSpec
from mirror.geometry import DEFAULT_UPRIGHT_TOL_DEG, GRIPPER

from . import geom
from .grounding import destination_tray, select

# Horizontal clearance needed beside a wall end for the gripper carrying an
# object upright: the palm's long side plus a margin on both sides.
AROUND_CLEARANCE_M = 2 * GRIPPER.palm_half[1] + 2 * 0.01
# An opening at most this much taller than the object's smallest vertical
# extent admits no collision-free passage (the object would touch both edges).
CONTACT_FREE_EPS_M = 1e-4


def _upright_bounds(spec: TaskSpec, scene: SceneState) -> dict[str, float]:
    """Object id -> tightest enforced tilt bound that applies while carried."""
    out: dict[str, float] = {}
    for c in spec.enforced_constraints():
        if c.type != "upright" or c.phase not in ("transport", "always"):
            continue
        tol = float(c.params.get("max_tilt_deg", DEFAULT_UPRIGHT_TOL_DEG))
        for oid in select(c.applies_to, spec, scene):
            out[oid] = min(tol, out.get(oid, 180.0))
    return out


def _side(wall: ObstacleSpec, p) -> float:
    ax = geom.wall_normal_axis(wall)
    return float(np.sign(p[ax] - wall.pose.pos[ax]))


def _required(spec: TaskSpec, scene: SceneState) -> list[str]:
    ids: list[str] = []
    for g in spec.active_goals():
        ids += [i for i in select(g.object, spec, scene) if i not in ids]
    return ids


def upright_infeasibility(spec: TaskSpec, scene: SceneState) -> Optional[dict[str, Any]]:
    """Return a proof record if some required object cannot reach the tray
    under its enforced upright bound, else None."""
    bounds = _upright_bounds(spec, scene)
    objs = geom.objects_by_id(scene)
    ws = scene.workspace
    for oid in _required(spec, scene):
        if oid not in bounds or oid not in objs:
            continue
        obj = objs[oid]
        goal = next(g for g in spec.goals if oid in select(g.object, spec, scene))
        tray, _ = destination_tray(goal.target, scene)
        if tray is None:
            continue
        theta = bounds[oid]
        r = geom.bounding_radius(obj)
        ext = geom.min_vertical_extent(obj, theta)
        for wall in (o for o in scene.obstacles if o.kind == "wall"):
            if _side(wall, obj.pose.pos) * _side(wall, tray.pose.pos) >= 0:
                continue  # wall does not separate start and destination
            ax = geom.wall_normal_axis(wall)
            lo, hi = geom.wall_y_extent(wall)
            span_lo, span_hi = (ws.y_range if ax == 0 else ws.x_range)
            spans = lo <= span_lo - r and hi >= span_hi + r
            over_blocked = wall.height + ext / 2.0 - r > ws.z_range[1]
            openings = []
            for s in wall.slots:
                notch = s.z_max >= wall.height
                h = float("inf") if notch else s.z_max - s.z_min
                openings.append(dict(y=(s.y_min, s.y_max), z=(s.z_min, s.z_max), height=h,
                                     passable=h > ext + CONTACT_FREE_EPS_M))
            if spans and over_blocked and all(not o["passable"] for o in openings):
                return dict(
                    proven=True, object_id=oid, wall_id=wall.id, max_tilt_deg=theta,
                    min_vertical_extent_m=ext, openings=openings,
                    argument=(f"wall '{wall.id}' separates {oid} from {tray.id}; it extends past both "
                              f"workspace edges by at least the object's bounding radius ({r:.3f} m); "
                              f"passing over it would need the tool centre above "
                              f"{wall.height + ext / 2 - r:.3f} m > workspace limit {ws.z_range[1]:.3f} m; "
                              f"no opening exceeds {ext:.3f} m, the smallest vertical extent of {oid} at "
                              f"tilt <= {theta:g} deg, by more than {CONTACT_FREE_EPS_M * 1000:.1f} mm, so "
                              f"no collision-free passage exists"),
                    assumptions=["only the carried object is considered (the gripper can only add height)",
                                 "the tool centre lies within the grasped object's bounding sphere",
                                 "passage is required to be collision-free (no sliding contact with both slot edges)"])
    return None


def upright_feasibility_witness(spec: TaskSpec, scene: SceneState) -> Optional[dict[str, Any]]:
    """A sufficient geometric condition that an upright route exists: no box
    obstacles, and every separating wall leaves a gap at one end of at least
    AROUND_CLEARANCE_M inside the workspace. Not a constructed trajectory."""
    if any(o.kind == "box" for o in scene.obstacles):
        return None
    ws = scene.workspace
    gaps = []
    for wall in (o for o in scene.obstacles if o.kind == "wall"):
        ax = geom.wall_normal_axis(wall)
        lo, hi = geom.wall_y_extent(wall)
        span_lo, span_hi = (ws.y_range if ax == 0 else ws.x_range)
        gap = max(lo - span_lo, span_hi - hi)
        if gap < AROUND_CLEARANCE_M:
            return None
        gaps.append(dict(wall_id=wall.id, gap_m=round(gap, 4)))
    return dict(kind="clearance_witness", gaps=gaps,
                note="geometric sufficient condition, not a constructed trajectory")


def feasibility(spec: TaskSpec, scene: SceneState) -> dict[str, Any]:
    proof = upright_infeasibility(spec, scene)
    if proof:
        return dict(verdict="proven_infeasible", detail=proof)
    wit = upright_feasibility_witness(spec, scene)
    if wit:
        return dict(verdict="feasible_witness", detail=wit)
    return dict(verdict="unknown", detail={})


def planner_claims_proof(infeasibility: Optional[dict]) -> bool:
    if not infeasibility:
        return False
    for k in ("proven", "proof", "certificate", "is_proof"):
        v = infeasibility.get(k)
        if v is True or (isinstance(v, (dict, str)) and v and k != "proven"):
            return True
    return False


def classify_planning_failure(plan_infeasibility: Optional[dict], verdict: str) -> str:
    claim = planner_claims_proof(plan_infeasibility)
    if claim and verdict == "proven_infeasible":
        return "proven_infeasible"
    if claim and verdict == "feasible_witness":
        return "planner_certificate_contradicted"
    if claim:
        return "planner_certificate_unconfirmed"
    if verdict == "proven_infeasible":
        return "infeasible_without_planner_certificate"
    if verdict == "feasible_witness":
        return "planner_failure_on_feasible_scene"
    return "planner_failure_feasibility_unknown"
