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
