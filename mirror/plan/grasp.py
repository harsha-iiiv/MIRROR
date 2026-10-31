"""Top-grasp model for the kit objects.

The gripper approaches from above (local -z down) with the fingers closing
along its local y axis across the object's narrow horizontal extent. Grasp
heights (TCP relative to the object's centre, along the object's axis):

  cup:    25 mm below the rim, so the pads hold the top of the side walls
          and the palm stays 30 mm above the rim;
  marker: 25 mm below its top end;
  block:  5 mm above its centre.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..contracts import ObjectSpec
from ..geometry import GRIPPER
from .collision import HeldObject
from .se3 import rot_z, wrap
from .world import object_half

GRASP_DEPTH_BELOW_TOP = {"cup": 0.025, "marker": 0.025}
BLOCK_TCP_ABOVE_CENTRE = 0.005
APPROACH_CLEARANCE = 0.030  # extra opening beyond the object width before closing
SQUEEZE = 0.010             # commanded closing width below the object width


@dataclass
class GraspModel:
    kind: str
    tcp_offset_z: float      # TCP z relative to the object centre (object frame)
    width: float             # object extent across the fingers
    open_width: float        # jaw opening for approach and release
    close_width: float       # commanded width on the grasp step
    symmetric_yaw: float     # yaw period of the object (pi/2 square, 0 round)
    half: np.ndarray

    def held(self, object_id: str, rel_yaw: float) -> HeldObject:
        """Object box in the gripper frame for a grasp where the object's yaw
        minus the gripper's yaw is ``rel_yaw``."""
        return HeldObject(object_id, np.array([0.0, 0.0, -self.tcp_offset_z]), rot_z(rel_yaw),
                          self.half.copy())


def grasp_model(obj: ObjectSpec) -> GraspModel:
    h = object_half(obj)
    if obj.kind == "cup":
        off = h[2] - GRASP_DEPTH_BELOW_TOP["cup"]
        width = 2 * min(h[0], h[1])
        period = math.pi / 2 if abs(h[0] - h[1]) < 1e-9 else math.pi
    elif obj.kind == "marker":
        off = h[2] - GRASP_DEPTH_BELOW_TOP["marker"]
        width = 2 * h[0]
        period = 0.0
    else:
        off = BLOCK_TCP_ABOVE_CENTRE
        width = 2 * min(h[0], h[1])
        period = math.pi / 2 if abs(h[0] - h[1]) < 1e-9 else math.pi
    open_w = min(GRIPPER.max_opening, width + APPROACH_CLEARANCE)
    close_w = max(0.0, width - SQUEEZE)
    return GraspModel(obj.kind, float(off), float(width), float(open_w), float(close_w), period, h)


def grasp_yaws(obj: ObjectSpec, obj_yaw: float) -> list[float]:
    """Gripper yaws that put the pads across the object's narrow extent,
    reduced modulo pi (the jaw is symmetric). For a round object any yaw
    works; the candidates are then a fixed 0 and pi/2."""
    h = object_half(obj)
    if obj.kind == "marker":
        return [0.0, math.pi / 2]
    # closing axis (gripper y) along the object's narrow horizontal axis
    if h[0] < h[1] - 1e-9:
        base = obj_yaw - math.pi / 2  # narrow along object x: gripper y = object x
        cands = [base]
    elif h[1] < h[0] - 1e-9:
        cands = [obj_yaw]
    else:
        cands = [obj_yaw, obj_yaw + math.pi / 2]
    out = []
    for c in cands:
        c = wrap(c)
        if c > math.pi / 2:
            c -= math.pi
        if c <= -math.pi / 2:
            c += math.pi
        out.append(c)
    return out


def nearest_equivalent_yaw(target: float, period: float, ref: float) -> float:
    """Yaw equivalent to ``target`` modulo ``period`` (and modulo pi for the
    symmetric jaw), closest to ``ref``."""
    p = math.pi if period == 0 else min(period, math.pi)
    k = round((ref - target) / p)
    return target + k * p
