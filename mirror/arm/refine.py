"""Arm-aware carry height.

The planner moves upright through free space at the lowest safe level, which
suits a free-flying gripper. For an arm hanging above the table, a low carry
next to a wall can put the wrist links into the wall even when the gripper and
the object clear it. ``raise_free_space`` lifts the free-space legs of a plan
(empty transits and upright transports) to a higher level, keeping everything
else the planner decided: object order, grasps, placements, route family and
the xy path of every leg, every orientation, and every approach, grasp, lift,
place, release and retreat motion.

Why this preserves the planner's guarantees: walls are taller than the
workspace and box obstacles are low, so moving a leg straight up and then
along the same xy path at a greater height can only increase clearance; the
orientation (and therefore every upright rule) is unchanged. The validator
re-checks the raised legs anyway (gripper and held object against the world,
see ``motion.Validator``).

Legs that tilt the gripper (slot passages) are never modified: a slot fixes
the height.
"""
from __future__ import annotations

import math

import numpy as np

from mirror.contracts import PlanResult, PlanStep, Pose

from .kinematics import quat_to_mat

UPRIGHT_TOL_DEG = 1.0


def _tilt_deg(quat) -> float:
    R = quat_to_mat(quat)
    return math.degrees(math.acos(max(-1.0, min(1.0, float(R[2, 2])))))


def _at(p: Pose, z: float, quat=None) -> Pose:
    return Pose(pos=(float(p.pos[0]), float(p.pos[1]), float(z)), quat=tuple(quat or p.quat))


def raise_free_space(plan: PlanResult, start: Pose, z: float) -> tuple[PlanResult, list[str]]:
    """A copy of ``plan`` whose upright move and transport legs travel at
    height ``z`` (never lower than planned), and notes describing what changed."""
    out = plan.model_copy(deep=True)
    notes: list[str] = []
    prev = start
    for step in out.steps:
        wps = list(step.waypoints)
        if step.kind in ("move", "transport") and wps:
            upright = all(_tilt_deg(w.quat) <= UPRIGHT_TOL_DEG for w in wps) and \
                _tilt_deg(prev.quat) <= UPRIGHT_TOL_DEG
            high = max([prev.pos[2]] + [w.pos[2] for w in wps])
            if upright and z > high + 1e-6:
                new = [_at(prev, z)]
                new += [_at(w, z) for w in wps]
                new.append(wps[-1])
                step.waypoints = new
                step.note = (step.note + "; " if step.note else "") + f"arm: carried at z={z:.2f} m"
                notes.append(f"{step.kind} {step.object_id}: free-space leg raised from "
                             f"z<={high:.3f} m to z={z:.2f} m")
        if step.waypoints:
            prev = step.waypoints[-1]
    return out, notes
