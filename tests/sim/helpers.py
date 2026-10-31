"""Ground-truth measurements on a StateLog used by the simulator tests."""
from __future__ import annotations

import numpy as np

from mirror.contracts import SceneState
from mirror.sim.rotations import quat_to_mat
from mirror.statelog import StateLog, tilt_deg


def beads_in_cup(log: StateLog, scene: SceneState, cup_id: str = "cup_0", step: int = -1) -> int:
    cup = next(o for o in scene.objects if o.id == cup_id)
    hx, hy, hz = cup.size["outer_half"]
    w = cup.size["wall"]
    k = log.index(cup_id)
    c, q = log.obj_pos[step, k], log.obj_quat[step, k]
    local = (log.beads[cup_id][step] - c) @ quat_to_mat(q)
    inside = ((np.abs(local[:, 0]) < hx - w + 1e-3) & (np.abs(local[:, 1]) < hy - w + 1e-3)
              & (local[:, 2] > -hz) & (local[:, 2] < hz))
    return int(inside.sum())


def in_tray(log: StateLog, scene: SceneState, obj_id: str, step: int = -1) -> bool:
    tray = scene.trays[0]
    p = log.obj_pos[step, log.index(obj_id)] - np.asarray(tray.pose.pos)
    local = quat_to_mat(tray.pose.quat).T @ p
    hx, hy = tray.inner_half
    return bool(abs(local[0]) < hx and abs(local[1]) < hy and local[2] > 0.0)


def max_tilt(log: StateLog, obj_id: str) -> float:
    return float(tilt_deg(log.obj_quat[:, log.index(obj_id)]).max())


def final_speed(log: StateLog, obj_id: str, window: float = 0.3) -> float:
    k = log.index(obj_id)
    n = max(2, int(window / log.dt))
    p = log.obj_pos[-n:, k]
    return float(np.linalg.norm(np.diff(p, axis=0), axis=1).max() / log.dt)
