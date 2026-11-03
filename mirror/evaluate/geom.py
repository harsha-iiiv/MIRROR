"""Geometry helpers used by the checker.

Everything here is a deterministic function of logged simulator state and
the scene description. No planner or model code is imported.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from mirror.contracts import ObjectSpec, ObstacleSpec, SceneState, TraySpec


def quat_conj(q: np.ndarray) -> np.ndarray:
    q = np.asarray(q, dtype=float)
    out = q.copy()
    out[..., 1:] *= -1.0
    return out


def quat_rotate(q: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Rotate vectors ``v`` (..., 3) by unit quaternions ``q`` (..., 4) w,x,y,z."""
    q = np.asarray(q, dtype=float)
    v = np.asarray(v, dtype=float)
    w = q[..., :1]
    u = q[..., 1:]
    t = 2.0 * np.cross(u, v)
    return v + w * t + np.cross(u, t)


def to_local(points: np.ndarray, origin: np.ndarray, quat: np.ndarray) -> np.ndarray:
    """Express world points in a frame with the given origin and orientation."""
    return quat_rotate(quat_conj(quat), np.asarray(points, float) - np.asarray(origin, float))


def yaw_of(quat) -> float:
    w, x, y, z = (float(c) for c in quat)
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


# ------------------------------------------------------------------ tray ---

def tray_local_xy(tray: TraySpec, pos: np.ndarray) -> np.ndarray:
    """Positions (..., 3) in the tray frame (origin at floor-top centre)."""
    return to_local(pos, np.array(tray.pose.pos), np.array(tray.pose.quat))


def inside_tray(tray: TraySpec, pos: np.ndarray, margin: float = 0.0,
                max_height: float | None = None) -> np.ndarray:
    """Object centre inside the tray's usable footprint (shrunk by ``margin``)
    and above its floor. Returns a boolean array over leading dimensions."""
    loc = tray_local_xy(tray, pos)
    hx, hy = tray.inner_half
    ok = (np.abs(loc[..., 0]) <= hx - margin) & (np.abs(loc[..., 1]) <= hy - margin) & (loc[..., 2] > 0.0)
    if max_height is not None:
        ok &= loc[..., 2] <= max_height
    return ok


# ------------------------------------------------------------------- cup ---

def cup_inner_box(obj: ObjectSpec) -> tuple[np.ndarray, np.ndarray]:
    """(lo, hi) of the cup's interior volume in the cup frame: inside the walls,
    above the floor, up to the rim plane."""
    hx, hy, hz = (float(v) for v in obj.size["outer_half"])
    wall = float(obj.size.get("wall", 0.004))
    lo = np.array([-hx + wall, -hy + wall, -hz + wall])
    hi = np.array([hx - wall, hy - wall, hz])
    return lo, hi


def beads_inside_cup(obj: ObjectSpec, cup_pos: np.ndarray, cup_quat: np.ndarray,
                     beads: np.ndarray, tol: float = 0.002) -> np.ndarray:
    """Boolean (B,) for bead centres inside the cup interior (with ``tol``)."""
    loc = to_local(beads, cup_pos, cup_quat)
    lo, hi = cup_inner_box(obj)
    return np.all((loc >= lo - tol) & (loc <= hi + tol), axis=-1)


# ------------------------------------------------------- object envelope ---

def object_half_extents(obj: ObjectSpec) -> tuple[float, float, float]:
    if obj.kind == "cup":
        return tuple(float(v) for v in obj.size["outer_half"])
    if obj.kind == "block":
        return tuple(float(v) for v in obj.size["half"])
    if obj.kind == "marker":
        r = float(obj.size["radius"])
        h = float(obj.size["half_length"]) + r  # capped ends
        return (r, r, h)
    raise ValueError(f"unknown kind {obj.kind}")


def bounding_radius(obj: ObjectSpec) -> float:
    return float(np.linalg.norm(object_half_extents(obj)))


def min_vertical_extent(obj: ObjectSpec, max_tilt_deg: float) -> float:
    """Lower bound on the object's vertical extent over every orientation whose
    tilt is at most ``max_tilt_deg`` (for tilt below 90 degrees).

    For a box with half extents (hx, hy, hz), tilting by theta about any
    horizontal axis gives a vertical extent of at least
    2 * (hz * cos(theta) + h_min * sin(theta)), with h_min = min(hx, hy). That
    function is concave on [0, pi/2], so its minimum over [0, theta_max] is at an
    end point."""
    hx, hy, hz = object_half_extents(obj)
    hmin = min(hx, hy)
    th = math.radians(max(0.0, min(max_tilt_deg, 89.999)))
    return 2.0 * min(hz, hz * math.cos(th) + hmin * math.sin(th))


# ------------------------------------------------------------ obstacles ---

@dataclass
class Box:
    """Oriented box: centre, half extents, quaternion."""
    owner: str
    center: np.ndarray
    half: np.ndarray
    quat: np.ndarray


def obstacle_boxes(obs: ObstacleSpec) -> list[Box]:
    """Decompose an obstacle into solid boxes (walls minus their slots)."""
    q = np.array(obs.pose.quat, float)
    c = np.array(obs.pose.pos, float)
    if obs.kind == "box":
        return [Box(obs.id, c, np.array(obs.half, float), q)]
    L, H, T = float(obs.length), float(obs.height), float(obs.thickness)
    # wall local frame: x normal, y along length, z up from the table (pose z = 0)
    pieces: list[tuple[float, float, float, float]] = []  # y0, y1, z0, z1 in local
    slots = sorted(obs.slots, key=lambda s: s.y_min)
    y_cursor = -L / 2.0
    for s in slots:
        y0, y1 = max(s.y_min, -L / 2), min(s.y_max, L / 2)
        if y0 > y_cursor:
            pieces.append((y_cursor, y0, 0.0, H))
        if s.z_min > 0:
            pieces.append((y0, y1, 0.0, s.z_min))
        if s.z_max < H:
            pieces.append((y0, y1, s.z_max, H))
        y_cursor = max(y_cursor, y1)
    if y_cursor < L / 2.0:
        pieces.append((y_cursor, L / 2.0, 0.0, H))
    out = []
    for y0, y1, z0, z1 in pieces:
        local = np.array([0.0, (y0 + y1) / 2.0, (z0 + z1) / 2.0])
        world = c + quat_rotate(q, local)
        out.append(Box(obs.id, world, np.array([T / 2.0, (y1 - y0) / 2.0, (z1 - z0) / 2.0]), q))
    return out


def point_box_distance(p: np.ndarray, box: Box) -> np.ndarray:
    """Euclidean distance from points (..., 3) to a solid oriented box (0 inside)."""
    loc = to_local(p, box.center, box.quat)
    d = np.maximum(np.abs(loc) - box.half, 0.0)
    return np.linalg.norm(d, axis=-1)


def wall_y_extent(obs: ObstacleSpec) -> tuple[float, float]:
    """World y-interval spanned by an axis-aligned wall whose normal is world x.
    For a wall whose normal is world y the result is its x-interval."""
    yaw = yaw_of(obs.pose.quat)
    L = float(obs.length)
    if abs(math.sin(yaw)) < 0.5:  # normal along world x, length along y
        c = obs.pose.pos[1]
    else:
        c = obs.pose.pos[0]
    return (c - L / 2.0, c + L / 2.0)


def wall_normal_axis(obs: ObstacleSpec) -> int:
    """0 if the wall separates along world x, 1 if along world y."""
    yaw = yaw_of(obs.pose.quat)
    return 0 if abs(math.sin(yaw)) < 0.5 else 1


def objects_by_id(scene: SceneState) -> dict[str, ObjectSpec]:
    return {o.id: o for o in scene.objects}


def tilt_of(quat) -> float:
    """Tilt in degrees of a single (w, x, y, z) quaternion."""
    w, x, y, z = (float(c) for c in quat)
    zz = 1.0 - 2.0 * (x * x + y * y)
    return math.degrees(math.acos(max(-1.0, min(1.0, zz))))


# ------------------------------------------------------------- distance ---

def quat_to_mat(q: np.ndarray) -> np.ndarray:
    """(..., 4) w,x,y,z -> (..., 3, 3)."""
    q = np.asarray(q, float)
    q = q / np.linalg.norm(q, axis=-1, keepdims=True)
    w, x, y, z = q[..., 0], q[..., 1], q[..., 2], q[..., 3]
    return np.stack([
        np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)], -1),
        np.stack([2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)], -1),
        np.stack([2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)], -1)], -2)


def box_box_distance(cA: np.ndarray, RA: np.ndarray, hA, cB: np.ndarray, RB: np.ndarray, hB,
                     iters: int = 400) -> np.ndarray:
    """Euclidean distance between solid oriented boxes (0 when they overlap).

    cA (N, 3), RA (N, 3, 3), hA (3,) or (N, 3); B likewise or a single box.
    Solves the box-constrained convex problem
        min ||cA + RA a - cB - RB b||  s.t. |a| <= hA, |b| <= hB
    with accelerated projected gradient (step 1/L, L = 4), so a segment
    (half extents (0, 0, l)) or a point works as well. Converges to the exact
    distance; the residual after ``iters`` iterations is far below a micrometre
    for the box sizes in this task family."""
    cA = np.atleast_2d(np.asarray(cA, float))
    N = cA.shape[0]
    RA = np.broadcast_to(np.asarray(RA, float), (N, 3, 3))
    cB = np.broadcast_to(np.asarray(cB, float), (N, 3))
    RB = np.broadcast_to(np.asarray(RB, float), (N, 3, 3))
    hA = np.broadcast_to(np.asarray(hA, float), (N, 3))
    hB = np.broadcast_to(np.asarray(hB, float), (N, 3))
    d0 = cA - cB
    a = np.clip(-np.einsum("nji,nj->ni", RA, d0), -hA, hA)
    b = np.clip(np.einsum("nji,nj->ni", RB, d0), -hB, hB)
    ya, yb, tk = a.copy(), b.copy(), 1.0
    step = 0.25
    for _ in range(iters):
        d = d0 + np.einsum("nij,nj->ni", RA, ya) - np.einsum("nij,nj->ni", RB, yb)
        ga = np.einsum("nji,nj->ni", RA, d)
        gb = -np.einsum("nji,nj->ni", RB, d)
        a1 = np.clip(ya - step * 2 * ga, -hA, hA)
        b1 = np.clip(yb - step * 2 * gb, -hB, hB)
        t1 = 0.5 * (1 + math.sqrt(1 + 4 * tk * tk))
        ya = a1 + ((tk - 1) / t1) * (a1 - a)
        yb = b1 + ((tk - 1) / t1) * (b1 - b)
        a, b, tk = a1, b1, t1
    d = d0 + np.einsum("nij,nj->ni", RA, a) - np.einsum("nij,nj->ni", RB, b)
    return np.linalg.norm(d, axis=-1)


def object_core(obj: ObjectSpec) -> tuple[tuple[float, float, float], float]:
    """(core box half extents, radius): the object is the core box swept by a
    ball of that radius. Cups and blocks are boxes (radius 0); a marker is a
    capsule, i.e. its axis segment swept by its radius."""
    if obj.kind == "marker":
        return (0.0, 0.0, float(obj.size["half_length"])), float(obj.size["radius"])
    return object_half_extents(obj), 0.0


def object_obstacle_distance(obj: ObjectSpec, pos: np.ndarray, quat: np.ndarray, boxes: list) -> np.ndarray:
    """Exact distance from the object's solid shape at poses (N, 3)/(N, 4) to
    the nearest of ``boxes`` (oriented solid boxes)."""
    pos = np.atleast_2d(np.asarray(pos, float))
    if not boxes:
        return np.full(pos.shape[0], np.inf)
    core, radius = object_core(obj)
    RA = quat_to_mat(quat)
    out = np.full(pos.shape[0], np.inf)
    for bx in boxes:
        RB = quat_to_mat(np.asarray(bx.quat, float))
        d = box_box_distance(pos, RA, core, np.asarray(bx.center, float), RB, np.asarray(bx.half, float))
        out = np.minimum(out, d - radius)
    return np.maximum(out, 0.0)
