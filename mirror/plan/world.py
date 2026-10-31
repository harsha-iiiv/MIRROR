"""Build the planner's obstacle model from a SceneState.

Dimensions follow docs/CONTRACTS.md and mirror/geometry.py: wall pieces
around each slot, box obstacles, the tray (floor slab plus four walls) and
the movable objects as boxes (a marker is represented by its square
bounding prism, which is conservative).
"""
from __future__ import annotations

import math

import numpy as np

from ..contracts import ObjectSpec, ObstacleSpec, SceneState, TraySpec
from ..geometry import TRAY
from .collision import Box, World
from .se3 import quat_to_mat, rot_z, yaw_of

# Tray floor slab thickness (the floor's top surface is the tray pose).
try:  # the scene factories define it; fall back to the reference value
    from ..scenes.objects import TRAY_FLOOR_THICKNESS as _FLOOR_T
except Exception:  # pragma: no cover - scenes package not importable
    _FLOOR_T = 0.006

TRAY_FLOOR_THICKNESS = float(_FLOOR_T)


def wall_pieces(ob: ObstacleSpec) -> list[tuple[tuple[float, float, float], tuple[float, float, float]]]:
    """(centre, half extents) of the solid boxes of a wall, in the wall frame
    (x = normal, y = along the wall, z up from the table)."""
    L, H, T = float(ob.length), float(ob.height), float(ob.thickness)
    y0, y1 = -L / 2, L / 2
    out = []
    cur = y0
    for s in sorted(ob.slots, key=lambda s: s.y_min):
        lo, hi = max(s.y_min, y0), min(s.y_max, y1)
        if hi <= lo:
            continue
        if lo > cur:
            out.append(((0.0, (cur + lo) / 2, H / 2), (T / 2, (lo - cur) / 2, H / 2)))
        yc, yh = (lo + hi) / 2, (hi - lo) / 2
        if s.z_min > 0:
            out.append(((0.0, yc, s.z_min / 2), (T / 2, yh, s.z_min / 2)))
        if s.z_max < H:
            out.append(((0.0, yc, (s.z_max + H) / 2), (T / 2, yh, (H - s.z_max) / 2)))
        cur = max(cur, hi)
    if cur < y1:
        out.append(((0.0, (cur + y1) / 2, H / 2), (T / 2, (y1 - cur) / 2, H / 2)))
    return out


def obstacle_boxes(ob: ObstacleSpec) -> list[Box]:
    R = quat_to_mat(ob.pose.quat)
    p = np.array(ob.pose.pos, float)
    if ob.kind == "wall":
        return [Box(p + R @ np.array(c), R, np.array(h, float), f"obstacle:{ob.id}", "wall")
                for c, h in wall_pieces(ob)]
    return [Box(p, R, np.array(ob.half, float), f"obstacle:{ob.id}", "box")]


def tray_boxes(tray: TraySpec) -> list[Box]:
    R = quat_to_mat(tray.pose.quat)
    p = np.array(tray.pose.pos, float)
    hx, hy = tray.inner_half
    t, h, ft = tray.wall_thickness, tray.wall_height, TRAY_FLOOR_THICKNESS
    local = [((0, 0, -ft / 2), (hx + t, hy + t, ft / 2)),
             ((hx + t / 2, 0, h / 2), (t / 2, hy + t, h / 2)),
             ((-hx - t / 2, 0, h / 2), (t / 2, hy + t, h / 2)),
             ((0, hy + t / 2, h / 2), (hx, t / 2, h / 2)),
             ((0, -hy - t / 2, h / 2), (hx, t / 2, h / 2))]
    names = ["floor", "wall", "wall", "wall", "wall"]
    return [Box(p + R @ np.array(c, float), R, np.array(hh, float), f"tray:{tray.id}", "tray_" + n)
            for (c, hh), n in zip(local, names)]


def object_half(obj: ObjectSpec) -> np.ndarray:
    if obj.kind == "cup":
        return np.array(obj.size["outer_half"], float)
    if obj.kind == "block":
        return np.array(obj.size["half"], float)
    r, hl = float(obj.size["radius"]), float(obj.size["half_length"])
    return np.array([r, r, hl])


def object_box(obj: ObjectSpec, pos=None, yaw=None) -> Box:
    h = object_half(obj)
    if pos is None:
        c = np.array(obj.pose.pos, float)
        R = quat_to_mat(obj.pose.quat)
    else:
        c = np.array(pos, float)
        R = rot_z(yaw or 0.0)
    return Box(c, R, h, obj.id, "object")


def scene_world(scene: SceneState) -> World:
    boxes: list[Box] = []
    for ob in scene.obstacles:
        boxes += obstacle_boxes(ob)
    for tr in scene.trays:
        boxes += tray_boxes(tr)
    return World(boxes, 0.0)


def object_yaw(obj: ObjectSpec) -> float:
    return yaw_of(obj.pose.quat)
