"""Infeasibility certificates.

A certificate is attached to a no_plan result only when impossibility is
established by the argument below, within the planner's model. It is never
inferred from the search failing.

Separating wall with low slots (``separating_wall_slot_clearance``)

Setting. Object O must move from the start side of a wall W to a tray whose
whole interior lies on the other side. An enforced rule bounds O's tilt by
alpha during transport. O is held by a rigid top grasp: the TCP lies on O's
axis, the gripper's z axis is O's axis, the palm is above O's top, and the
grasp depth g (TCP below O's top) lies in a stated range.

Argument. At grasp and at release the TCP lies on O's axis, so it starts on
one side of W's mid-plane and ends on the other; by continuity there is a
moment where the TCP lies in the mid-plane. Fix that moment and let u be
the coordinate along O's axis measured from the TCP. Take two body points
in the mid-plane:

  * a top witness in the palm cross-section at u_top (inside the palm's
    height band), and
  * a bottom witness in O's solid bottom cross-section at u_bot = -(H - g)
    (the cup floor, or the bottom of a solid block or marker).

The mid-plane meets the cross-section plane at u in a line whose distance
from the axis is at most |u| tan(alpha); when that is smaller than the
cross-section's inscribed radius the line meets the solid section, and the
witness is its point nearest to the axis. Elementary bounds then give

  z_top - z_bot >= (u_top + H - g) cos(2 alpha) / cos(alpha)      (E_min)
  |horizontal offset of a witness from the TCP| <= |u| (sin alpha + tan alpha)

If W spans the workspace's TCP range along W plus that offset, and both
witnesses are lower than W's height, every witness lies inside W's footprint
below its top, so each must lie in a slot opening. If E_min exceeds the
vertical span of all of W's openings, two points of the body cannot both be
in openings: contradiction. Hence no motion within the model delivers O to
the tray while respecting the rule.

Scope. The proof holds for the planner's model: rigid top grasps in the
stated depth range, TCP within the workspace box, table not penetrated. It
is not a statement about other grasps (e.g. side grasps) or about physics.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from ..contracts import ObjectSpec, SceneState, TaskSpec, TraySpec
from ..geometry import DEFAULT_UPRIGHT_TOL_DEG, GRIPPER
from .grasp import GraspModel
from .routes import wall_frame
from .se3 import quat_to_mat
from .world import object_half


def _alpha_limit(spec: TaskSpec, g, obj_id: str):
    lim, ids = None, []
    for c in spec.enforced_constraints():
        if c.type != "upright" or c.phase not in ("transport", "always"):
            continue
        if obj_id not in g.constraint_objects.get(c.id, []):
            continue
        a = float(c.params.get("max_tilt_deg", DEFAULT_UPRIGHT_TOL_DEG))
        ids.append(c.id)
        lim = a if lim is None else min(lim, a)
    return lim, ids


def bottom_inscribed_radius(obj: ObjectSpec) -> float:
    h = object_half(obj)
    if obj.kind == "marker":
        return float(h[0])
    return float(min(h[0], h[1]))


def certify(spec: TaskSpec, g, scene: SceneState, obj: ObjectSpec, gm: GraspModel, tray: TraySpec,
            banned: dict, allowed: list, cfg) -> Optional[dict]:
    """Return the strongest infeasibility record that can be established:
    a proof for any motion (``proven: True``), else a result scoped to the
    planner's passage primitive (``proven: False``, ``scope: planner_model``),
    else None."""
    c = _any_motion(spec, g, scene, obj, gm, tray)
    if c is not None:
        return c
    return _planner_model(spec, g, scene, obj, gm, tray)


def _separating_walls(scene: SceneState, obj: ObjectSpec, tray: TraySpec):
    objxy = np.array(obj.pose.pos[:2], float)
    Rt = quat_to_mat(tray.pose.quat)
    tp = np.array(tray.pose.pos, float)
    hx, hy = tray.inner_half
    tray_pts = [tp[:2] + (Rt @ np.array([sx * hx, sy * hy, 0.0]))[:2] for sx in (-1, 1) for sy in (-1, 1)]
    for w in scene.obstacles:
        if w.kind != "wall":
            continue
        p, R = wall_frame(w)
        n = R[:2, 0]
        t2 = w.thickness / 2
        so = float(n @ (objxy - p[:2]))
        st = [float(n @ (q - p[:2])) for q in tray_pts]
        if (so < -t2 and all(x > t2 for x in st)) or (so > t2 and all(x < -t2 for x in st)):
            yield w, p, R


def min_envelope_height(gm: GraspModel, obj_id: str, alpha_deg: float, step_deg: float = 0.25):
    """Lower bound on the vertical extent of the gripper-plus-object envelope
    over every orientation whose tilt is at most alpha.

    Orientations are Rz(yaw) Ry(beta) Rz(spin) with beta in [0, alpha]; the
    vertical extent does not depend on yaw, and the envelope is symmetric
    under a half turn of spin. A grid with spacing ``step_deg`` in beta and
    spin puts every orientation within theta = step/2 + step/2 (rotation
    angle) of a grid point; no envelope point moves more than reach * theta,
    so the extent drops by at most 2 * reach * theta between grid points.
    Returns (lower_bound, grid_minimum, argmin_deg, slack)."""
    from .collision import make_envelope
    env = make_envelope(gm.width, gm.held(obj_id, 0.0))
    n_b = max(1, int(math.ceil(alpha_deg / step_deg)))
    betas = np.radians(np.linspace(0.0, alpha_deg, n_b + 1))
    spins = np.radians(np.arange(0.0, 180.0, step_deg))
    B, S = np.meshgrid(betas, spins, indexing="ij")
    B, S = B.ravel(), S.ravel()
    # third row of Ry(beta) Rz(spin)
    r20 = -np.sin(B) * np.cos(S)
    r21 = np.sin(B) * np.sin(S)
    r22 = np.cos(B)
    row = np.stack([r20, r21, r22], axis=1)
    lo = np.full(len(B), np.inf)
    hi = np.full(len(B), -np.inf)
    for b in env.boxes:
        cz = row @ b.c
        ext = np.abs(row @ b.R) @ b.h
        lo = np.minimum(lo, cz - ext)
        hi = np.maximum(hi, cz + ext)
    h = hi - lo
    k = int(np.argmin(h))
    theta = math.radians(step_deg)  # step/2 in beta + step/2 in spin
    slack = 2.0 * env.reach * theta
    return float(h[k] - slack), float(h[k]), (math.degrees(B[k]), math.degrees(S[k])), slack


def _planner_model(spec, g, scene, obj, gm, tray) -> Optional[dict]:
    alpha_deg, cids = _alpha_limit(spec, g, obj.id)
    if alpha_deg is None or alpha_deg >= 90.0:
        return None
    a = math.radians(alpha_deg)
    H = 2.0 * float(object_half(obj)[2])
    g_model = H / 2.0 - gm.tcp_offset_z
    u_bot = H - g_model  # bottom centre below the TCP along the object's axis
    off = u_bot * math.sin(a)
    ws = scene.workspace
    ws_pts = [np.array([x, y]) for x in ws.x_range for y in ws.y_range]
    for w, p, R in _separating_walls(scene, obj, tray):
        al = R[:2, 1]
        along = [float(al @ (q - p[:2])) for q in ws_pts]
        need_lo, need_hi = min(along) - off, max(along) + off
        if not (-w.length / 2 <= need_lo and need_hi <= w.length / 2):
            continue
        z_bot_max = ws.z_range[1] - u_bot * math.cos(a)
        if not z_bot_max < float(w.height):
            continue
        if not w.slots:
            continue  # the any-motion argument covers walls without openings
        tallest = max(s.z_max - s.z_min for s in w.slots)
        lb, hmin, arg, slack = min_envelope_height(gm, obj.id, alpha_deg)
        if not lb > tallest:
            continue
        return {
            "proven": False,
            "scope": "planner_model",
            "kind": "slot_height_over_tilt_interval",
            "object_id": obj.id,
            "constraint_ids": cids,
            "wall_id": w.id,
            "statement": (f"Any motion must carry the bottom of {obj.id} through an opening of {w.id}. "
                          f"The planner crosses an opening by a straight horizontal pass at a fixed "
                          f"orientation, which needs the whole gripper-and-{obj.kind} envelope to fit the "
                          f"opening's height. Over every orientation within {alpha_deg:g} deg of upright the "
                          f"envelope is at least {lb:.3f} m tall; the tallest opening is {tallest:.3f} m. "
                          "Motions that rotate or move diagonally inside the opening are not covered, so this "
                          "is not a proof that no motion exists."),
            "quantities": {
                "max_tilt_deg": alpha_deg,
                "min_envelope_height_lower_bound_m": round(lb, 6),
                "min_envelope_height_on_grid_m": round(hmin, 6),
                "argmin_tilt_and_spin_deg": [round(arg[0], 3), round(arg[1], 3)],
                "discretisation_slack_m": round(slack, 6),
                "tallest_opening_m": round(tallest, 6),
                "wall_along_range_m": [-w.length / 2, w.length / 2],
                "required_along_range_m": [round(need_lo, 6), round(need_hi, 6)],
                "highest_bottom_point_m": round(z_bot_max, 6),
            },
            "assumptions": [
                "rigid top grasp as planned (TCP on the object's axis)",
                "TCP stays inside the workspace box",
                "an opening is crossed by a straight horizontal pass at a fixed orientation (the planner's primitive)",
                "envelope = palm, fingers and the held object's box from mirror/geometry.py",
            ],
            "not_covered": ["passes that rotate or move diagonally inside the opening",
                            "other grasp types", "physical effects such as slip"],
            "method": "dense sweep over tilt and spin with a stated discretisation bound "
                      "(see min_envelope_height in mirror/plan/certificate.py)",
        }
    return None


def _any_motion(spec: TaskSpec, g, scene: SceneState, obj: ObjectSpec, gm: GraspModel,
                tray: TraySpec) -> Optional[dict]:
    alpha_deg, cids = _alpha_limit(spec, g, obj.id)
    if alpha_deg is None or alpha_deg >= 44.0:
        return None
    a = math.radians(alpha_deg)
    ta, ca, sa = math.tan(a), math.cos(a), math.sin(a)
    h = object_half(obj)
    H = 2.0 * float(h[2])
    r_bot = bottom_inscribed_radius(obj)
    palm_lo = GRIPPER.palm_center_z - GRIPPER.palm_half[2]
    palm_hi = GRIPPER.palm_center_z + GRIPPER.palm_half[2]
    r_palm = min(GRIPPER.palm_half[0], GRIPPER.palm_half[1])
    eps = 1e-6
    u_top = palm_hi if ta < 1e-12 else min(palm_hi, (r_palm - eps) / ta)
    if u_top < palm_lo:
        return None
    g_model = H / 2.0 - gm.tcp_offset_z          # the planner's grasp depth
    g_max = palm_lo                                # palm above the object's top
    g_min_contact = GRIPPER.finger_center_z - GRIPPER.finger_half[2]  # pads must reach below the top
    g_lo_radius = H - (r_bot - eps) / ta if ta > 1e-12 else -math.inf
    g_lo = max(g_min_contact, g_lo_radius)
    if not (g_lo <= g_model <= g_max):
        return None
    k = math.cos(2 * a) / ca
    e_min = (u_top + H - g_max) * k
    ub_max = H - g_lo
    off = max(u_top, ub_max) * (sa + ta)
    objxy = np.array(obj.pose.pos[:2], float)
    Rt = quat_to_mat(tray.pose.quat)
    tp = np.array(tray.pose.pos, float)
    hx, hy = tray.inner_half
    tray_pts = [tp[:2] + (Rt @ np.array([sx * hx, sy * hy, 0.0]))[:2] for sx in (-1, 1) for sy in (-1, 1)]
    ws = scene.workspace
    ws_pts = [np.array([x, y]) for x in ws.x_range for y in ws.y_range]
    for w in scene.obstacles:
        if w.kind != "wall":
            continue
        p, R = wall_frame(w)
        n, al = R[:2, 0], R[:2, 1]
        t2 = w.thickness / 2
        so = float(n @ (objxy - p[:2]))
        st = [float(n @ (q - p[:2])) for q in tray_pts]
        if not ((so < -t2 and all(s > t2 for s in st)) or (so > t2 and all(s < -t2 for s in st))):
            continue
        along = [float(al @ (q - p[:2])) for q in ws_pts]
        need_lo, need_hi = min(along) - off, max(along) + off
        if not (-w.length / 2 <= need_lo and need_hi <= w.length / 2):
            continue
        Hw = float(w.height)
        z_bot_max = ws.z_range[1] - (H - g_max) * k
        if not z_bot_max < Hw:
            continue
        if not w.slots:
            span, z_top_max = 0.0, None  # no opening at all: the bottom witness has nowhere to go
        else:
            z_open_lo = min(s.z_min for s in w.slots)
            z_open_hi = max(s.z_max for s in w.slots)
            z_top_max = z_open_hi + (ub_max + u_top) / ca
            if not z_top_max < Hw:
                continue
            span = z_open_hi - z_open_lo
            if not e_min > span:
                continue
        return {
            "proven": True,
            "scope": "any_motion",
            "kind": "separating_wall_slot_clearance",
            "object_id": obj.id,
            "constraint_ids": cids,
            "wall_id": w.id,
            "statement": (f"No motion keeps {obj.id} within {alpha_deg:g} deg of upright while carrying it "
                          f"from its start past {w.id} into {tray.id}: any two points of the gripper and "
                          f"{obj.id} that lie in the wall's mid-plane span at least {e_min:.3f} m vertically, "
                          f"but all of {w.id}'s openings span only {span:.3f} m, and the wall leaves no way "
                          "around or over it within the workspace."),
            "quantities": {
                "max_tilt_deg": alpha_deg,
                "min_vertical_span_m": round(e_min, 6),
                "openings_vertical_span_m": round(span, 6),
                "wall_height_m": Hw,
                "wall_along_range_m": [-w.length / 2, w.length / 2],
                "required_along_range_m": [round(need_lo, 6), round(need_hi, 6)],
                "witness_horizontal_offset_max_m": round(off, 6),
                "highest_bottom_witness_m": round(z_bot_max, 6),
                "highest_top_witness_in_slot_case_m": None if z_top_max is None else round(z_top_max, 6),
                "u_top_m": round(u_top, 6),
                "object_height_m": H,
                "grasp_depth_planner_m": round(g_model, 6),
                "grasp_depth_range_m": [round(g_lo, 6), round(g_max, 6)],
            },
            "assumptions": [
                "rigid top grasp: TCP on the object's axis, gripper z axis along the object's axis, palm above the object's top",
                f"grasp depth below the object's top within [{g_lo:.4f}, {g_max:.4f}] m (the planner uses {g_model:.4f} m)",
                "TCP stays inside the workspace box; the body does not penetrate the table",
                "gripper and object dimensions from mirror/geometry.py",
            ],
            "not_covered": ["other grasp types (e.g. side grasps)", "physical effects such as slip during execution"],
            "method": "analytic bound (see mirror/plan/certificate.py)",
        }
    return None
