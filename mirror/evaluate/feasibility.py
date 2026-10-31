"""Independent feasibility analysis for upright-transport rules.

Used only to label evaluation outcomes: to tell a proven infeasibility from an
ordinary planner failure, and to count feasible scenes a planner rejected. It
is separate code from the planner's certificate and is checked against it.

Infeasibility bound (scope: any motion of a rigidly top-grasped object)
------------------------------------------------------------------------
Model. Object O (height H, axis a) is held by a rigid top grasp: the tool
centre point (TCP) lies on O's axis, the gripper's z axis is O's axis, the
palm is above O's top, so the grasp depth g (TCP below O's top) satisfies
0 <= g <= palm_bottom, where palm_bottom is the palm's lower face in the
gripper frame. An enforced rule keeps tilt(O) <= alpha while O is carried.
The TCP stays in the workspace box.

Crossing moment. O starts upright on one side of wall W and must end inside a
tray lying entirely on the other side, with its axis there, so by continuity
the TCP lies in W's mid-plane at some moment. Fix it; let s = n.a with n the
wall normal (|s| <= sin(alpha)) and u the coordinate along a from the TCP.

Witnesses. The mid-plane meets the cross-section plane at u in a line whose
closest point to the axis is w(u) = TCP + u a + u c, with c perpendicular to
a, |c| = |s| / sqrt(1 - s^2) <= tan(alpha), and c_z = s^2 a_z / (1 - s^2).
If |u| tan(alpha) is below the inscribed radius of the body's cross-section
at u (centred on the axis), w(u) is a point of the body. Take a top witness in
the palm at u_top and a bottom witness on O's solid bottom face at
u_bot = -(H - g). Then exactly

    z(w_top) - z(w_bot) = (u_top - u_bot) a_z / (1 - s^2) >= (u_top + H - g) cos(alpha)

and each witness is within |u| (sin(alpha) + tan(alpha)) of the TCP
horizontally.

Contradiction. If W extends past the workspace's TCP range along W by more
than that offset, both witnesses lie inside W's footprint (they are on its
mid-plane). The bottom witness is below the TCP, hence below W's top (W is
taller than the workspace), so it must be in an opening: z_bot <= max z_max.
The top witness is then at most z_bot + (u_top - u_bot) / cos^2(alpha) high;
if that is still below W's top it must also be in an opening. Two points in
openings differ in height by at most the openings' total vertical span
(max z_max - min z_min). If the lower bound above exceeds that span (by
CONTACT_FREE_EPS_M), no motion within the model delivers O to the tray.

Scope: rigid top grasps within the stated depth range, TCP in the workspace,
the gripper body as given in mirror.geometry. It says nothing about other
grasps or about physics.
"""
from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np

from mirror.contracts import ObjectSpec, ObstacleSpec, SceneState, TaskSpec
from mirror.geometry import DEFAULT_UPRIGHT_TOL_DEG, GRIPPER

from . import geom
from .grounding import destination_tray, select

# Strictness margin: the lower bound on the witnesses' height difference must
# exceed the openings' vertical span by more than this.
CONTACT_FREE_EPS_M = 1e-4
# Horizontal clearance needed beside a wall end for the gripper carrying an
# object upright (feasibility witness): the palm's long side plus 1 cm a side.
AROUND_CLEARANCE_M = 2 * GRIPPER.palm_half[1] + 2 * 0.01

PALM_BOTTOM = GRIPPER.palm_center_z - GRIPPER.palm_half[2]
PALM_TOP = GRIPPER.palm_center_z + GRIPPER.palm_half[2]
PALM_INSCRIBED = min(GRIPPER.palm_half[0], GRIPPER.palm_half[1])

SCOPE = "any_motion_rigid_top_grasp"


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


def _required(spec: TaskSpec, scene: SceneState) -> list[str]:
    ids: list[str] = []
    for g in spec.active_goals():
        ids += [i for i in select(g.object, spec, scene) if i not in ids]
    return ids


def _bottom_inscribed(obj: ObjectSpec) -> float:
    hx, hy, _ = geom.object_half_extents(obj)
    return min(hx, hy)


def witness_bound(obj: ObjectSpec, alpha_deg: float) -> Optional[dict[str, float]]:
    """Choose the top witness height and return the lower bound on the
    witnesses' height difference over every orientation with tilt <= alpha and
    every grasp depth in [0, PALM_BOTTOM]. None if no valid witness exists."""
    a = math.radians(alpha_deg)
    if a >= math.radians(60):
        return None
    t = math.tan(a)
    H = 2.0 * geom.object_half_extents(obj)[2]
    g_max = min(PALM_BOTTOM, H)
    # Top witness: highest palm section whose inscribed circle still contains
    # the line's closest point (|u| tan(alpha) < inscribed radius).
    u_top = PALM_TOP if t == 0 else min(PALM_TOP, 0.999 * PALM_INSCRIBED / t)
    if u_top < PALM_BOTTOM:
        return None
    # Bottom witness at u_bot = -(H - g), g >= 0, so |u_bot| <= H.
    if H * t >= _bottom_inscribed(obj):
        return None
    span_axis_min = u_top + H - g_max
    span_axis_max = u_top + H
    return dict(u_top=u_top, height=H, g_max=g_max, alpha_deg=alpha_deg,
                lower=span_axis_min * math.cos(a),
                upper_rise=span_axis_max / math.cos(a) ** 2,
                offset=max(u_top, H) * (math.sin(a) + t))


def _side(wall: ObstacleSpec, p) -> float:
    ax = geom.wall_normal_axis(wall)
    return float(np.sign(p[ax] - wall.pose.pos[ax]))


def _tray_clear_of(wall: ObstacleSpec, tray, margin: float) -> bool:
    """Tray interior entirely on one side of the wall's mid-plane, at least
    ``margin`` away (so an object axis inside it is on that side)."""
    ax = geom.wall_normal_axis(wall)
    corners = []
    hx, hy = tray.inner_half
    for sx in (-1, 1):
        for sy in (-1, 1):
            corners.append(np.array(tray.pose.pos) + geom.quat_rotate(np.array(tray.pose.quat),
                                                                    np.array([sx * hx, sy * hy, 0.0])))
    d = [c[ax] - wall.pose.pos[ax] for c in corners]
    return all(x > margin for x in d) or all(x < -margin for x in d)


def upright_infeasibility(spec: TaskSpec, scene: SceneState) -> Optional[dict[str, Any]]:
    """A proof record (scope any_motion_rigid_top_grasp) if some required
    object cannot reach its tray under its enforced upright bound, else None."""
    bounds = _upright_bounds(spec, scene)
    objs = geom.objects_by_id(scene)
    ws = scene.workspace
    for oid in _required(spec, scene):
        if oid not in bounds or oid not in objs:
            continue
        obj = objs[oid]
        if geom.tilt_of(obj.pose.quat) > 1.0:
            continue  # argument assumes the object starts upright
        goal = next(g for g in spec.active_goals() if oid in select(g.object, spec, scene))
        tray, _ = destination_tray(goal.target, scene)
        if tray is None:
            continue
        wb = witness_bound(obj, bounds[oid])
        if wb is None:
            continue
        for wall in (o for o in scene.obstacles if o.kind == "wall"):
            if _side(wall, obj.pose.pos) * _side(wall, tray.pose.pos) >= 0:
                continue
            if not _tray_clear_of(wall, tray, wb["height"] * math.sin(math.radians(bounds[oid]))):
                continue
            ax = geom.wall_normal_axis(wall)
            lo, hi = geom.wall_y_extent(wall)
            span_lo, span_hi = (ws.y_range if ax == 0 else ws.x_range)
            spans = lo < span_lo - wb["offset"] and hi > span_hi + wb["offset"]
            taller = wall.height > ws.z_range[1]
            if not (spans and taller):
                continue
            if not wall.slots:
                openings_span, top_ok = 0.0, True
                max_zmax = 0.0
            else:
                if any(s.z_max >= wall.height for s in wall.slots):
                    continue  # a notch open to the top: argument does not apply
                max_zmax = max(s.z_max for s in wall.slots)
                min_zmin = min(s.z_min for s in wall.slots)
                openings_span = max_zmax - min_zmin
                top_ok = max_zmax + wb["upper_rise"] < wall.height
            if not top_ok:
                continue
            if wb["lower"] > openings_span + CONTACT_FREE_EPS_M:
                return dict(
                    proven=True, scope=SCOPE, object_id=oid, wall_id=wall.id,
                    max_tilt_deg=bounds[oid], witness_height_lower_m=round(wb["lower"], 5),
                    openings_vertical_span_m=round(openings_span, 5), u_top_m=round(wb["u_top"], 5),
                    grasp_depth_max_m=round(wb["g_max"], 5), eps_m=CONTACT_FREE_EPS_M,
                    argument=(f"in every rigid top grasp of {oid} (depth 0-{wb['g_max'] * 1000:.0f} mm) at "
                              f"tilt <= {bounds[oid]:g} deg, two body points on wall '{wall.id}''s mid-plane "
                              f"differ in height by at least {wb['lower']:.4f} m when the tool centre crosses "
                              f"it, but the wall's openings span only {openings_span:.4f} m vertically and the "
                              f"wall extends past the workspace on both ends and above it"),
                    assumptions=["rigid top grasp: tool centre on the object's axis, palm above its top",
                                 "tool centre within the workspace box",
                                 "object starts upright on the far side of the wall from the tray",
                                 f"strict margin {CONTACT_FREE_EPS_M * 1000:.1f} mm"])
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
    """Only a planner record with ``proven: True`` counts as a proof claim."""
    return bool(infeasibility) and infeasibility.get("proven") is True


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
