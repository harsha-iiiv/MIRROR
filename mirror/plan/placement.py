"""Choose where each object goes in the destination tray."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..contracts import ObjectSpec, TraySpec
from .collision import Box, World, check_poses, check_segment, make_envelope
from .config import PlannerConfig
from .grasp import GraspModel
from .se3 import gripper_rot, quat_to_mat, rot_z, wrap, yaw_of
from .world import object_half


@dataclass
class Placement:
    object_id: str
    obj_pos: np.ndarray       # object centre at release
    obj_yaw: float
    tcp_pos: np.ndarray       # TCP at release
    gripper_yaw: float
    box: Box                  # object footprint box at the spot (for later obstacles)
    clearance: float


def place_spot(obj: ObjectSpec, gm: GraspModel, rel_yaw: float, tray: TraySpec, world: World,
               placed: list[Box], cfg: PlannerConfig, prefer_gripper_yaw: float,
               far_from: Optional[tuple] = None) -> Optional[Placement]:
    """Pick a collision-free release pose inside the tray.

    rel_yaw: object yaw minus gripper yaw for the grasp in use. Objects are
    placed upright and aligned with the tray axes. Spots are ranked by their
    clearance to objects already placed and to the tray walls; the first
    spot whose release, open-jaw and retreat poses pass the collision check
    is used. Deterministic.

    far_from: optional (point_xy, normal_xy) of a wall separating the object
    from the tray. The tray is then filled from the side away from that wall,
    which keeps the entry side free for objects that come later.
    """
    Rt = quat_to_mat(tray.pose.quat)
    t_yaw = yaw_of(tray.pose.quat)
    tp = np.array(tray.pose.pos, float)
    hx, hy = tray.inner_half
    h = object_half(obj)
    floor = tp[2]
    period = gm.symmetric_yaw if gm.symmetric_yaw > 0 else math.pi / 2
    # object yaw options aligned with the tray; gripper yaw = object yaw - rel_yaw
    opts = []
    for k in range(int(round(2 * math.pi / period))):
        oy = t_yaw + k * period
        gy = oy - rel_yaw
        gy_n = gy + math.pi * round((prefer_gripper_yaw - gy) / math.pi)
        opts.append((abs(wrap(gy_n - prefer_gripper_yaw)), k, oy, gy_n))
    opts.sort(key=lambda t: (round(t[0], 9), t[1]))
    if gm.symmetric_yaw == 0:  # round: keep the arriving yaw
        opts = [(0.0, 0, prefer_gripper_yaw + rel_yaw, prefer_gripper_yaw)]
    placed_local = []
    for b in placed:
        c = Rt.T @ (b.c - tp)
        bR = Rt.T @ b.R
        ex = abs(bR[0, 0]) * b.h[0] + abs(bR[0, 1]) * b.h[1]
        ey = abs(bR[1, 0]) * b.h[0] + abs(bR[1, 1]) * b.h[1]
        placed_local.append((c[0], c[1], ex, ey))
    for _, _, oy, gy in opts:
        rel = wrap(oy - t_yaw)
        c_, s_ = abs(math.cos(rel)), abs(math.sin(rel))
        ex = c_ * h[0] + s_ * h[1]
        ey = s_ * h[0] + c_ * h[1]
        cw = cfg.tray_wall_clearance
        xs = _grid(hx - ex - cw, cfg.place_grid_step)
        ys = _grid(hy - ey - cw, cfg.place_grid_step)
        scored = []
        for x in xs:
            for y in ys:
                wall = min(hx - ex - abs(x), hy - ey - abs(y))
                pc = math.inf
                for (px, py, pex, pey) in placed_local:
                    gap = max(abs(x - px) - (ex + pex), abs(y - py) - (ey + pey))
                    pc = min(pc, gap)
                if pc < 0.012:
                    continue
                if far_from is not None:
                    w = tp[:2] + (Rt @ np.array([x, y, 0.0]))[:2]
                    far = abs(float(np.dot(np.asarray(far_from[1]), w - np.asarray(far_from[0]))))
                    key = (-round(far, 3), round(abs(y), 6), round(x, 6))
                else:
                    key = (-round(min(pc, 2.0 * wall), 6), round(x, 6), round(y, 6))
                scored.append((key, round(x, 6), round(y, 6), min(pc, 2.0 * wall)))
        scored.sort()
        Rg = gripper_rot(gy)
        for _, x, y, clr in scored:
            centre = tp + Rt @ np.array([x, y, 0.0])
            centre[2] = floor + cfg.place_drop + h[2]
            tcp = centre + np.array([0.0, 0.0, gm.tcp_offset_z])
            held = gm.held(obj.id, wrap(oy - gy))
            env_c = make_envelope(gm.width, held)
            env_o = make_envelope(gm.open_width, None)
            top = tcp + np.array([0.0, 0.0, cfg.retreat_height])
            P = np.array([tcp])
            Rs = np.array([Rg])
            if check_poses(world, env_c, P, Rs, cfg.fine_margin, cfg.fine_ground_margin) is not None:
                continue
            if check_segment(world, env_o, tcp, Rg, top, Rg, margin=cfg.fine_margin,
                             ground_margin=cfg.fine_ground_margin) is not None:
                continue
            box = Box(centre, rot_z(oy), h.copy(), obj.id, "object")
            return Placement(obj.id, centre, oy, tcp, gy, box, clr)
    return None


def _grid(lim: float, step: float) -> list[float]:
    if lim < 0:
        return []
    n = int(math.floor(lim / step + 1e-9))
    vals = [0.0]
    for i in range(1, n + 1):
        vals += [i * step, -i * step]
    return sorted(vals)
