"""Shared helpers for planning tests: cached plans and an independent replay
of a plan's gripper motion with finer sampling and zero safety margin."""
from __future__ import annotations

import functools
import math

import numpy as np

from mirror.contracts import PlanResult, SceneState
from mirror.plan import plan
from mirror.plan.collision import World, check_segment, make_envelope
from mirror.plan.grasp import grasp_model
from mirror.plan.se3 import quat_to_mat, slerp_mats, tilt_deg_of, wrap, yaw_of
from mirror.plan.world import object_box, scene_world
from mirror.scenes import reference_scene

from .fixtures import corrected_spec, original_spec, proposed_spec

SPECS = {"orig": original_spec, "corr": corrected_spec, "prop": proposed_spec}


@functools.lru_cache(maxsize=None)
def cached_plan(scene_name: str, spec_name: str) -> PlanResult:
    return plan(SPECS[spec_name](), reference_scene(scene_name))


def chosen_routes(r: PlanResult) -> dict[str, str]:
    out = {}
    for s in r.steps:
        if s.kind == "transport":
            out[s.object_id] = s.note.split(";")[0].split("=", 1)[1]
    return out


def replay_collisions(r: PlanResult, scene: SceneState, step: float = 0.003) -> list[str]:
    """Re-check every commanded segment of an ok plan against the scene with
    zero margin and finer sampling. Returns a list of problems."""
    problems = []
    static = scene_world(scene)
    objs = {o.id: o for o in scene.objects}
    boxes = {o.id: object_box(o) for o in scene.objects}
    pos = np.array(scene.gripper_start.pos, float)
    R = quat_to_mat(scene.gripper_start.quat)
    held = None
    width = 0.09
    for st in r.steps:
        if st.gripper_width is not None and st.kind in ("move", "approach", "release"):
            width = st.gripper_width
        if st.kind == "grasp":
            o = objs[st.object_id]
            gm = grasp_model(o)
            rel = wrap(yaw_of(o.pose.quat) - math.atan2(R[1, 0], R[0, 0]))
            held = (o.id, gm, gm.held(o.id, rel))
            width = gm.width
            continue
        if st.kind == "release":
            oid, gm, h = held
            c = pos + R @ h.c
            Rb = R @ h.R
            from mirror.plan.collision import Box
            boxes[oid] = Box(c, Rb, h.h, oid, "object")
            held = None
            width = st.gripper_width
            continue
        env = make_envelope(width, held[2] if held else None)
        ex = {held[0]} if held else set()
        if st.kind == "approach":
            ex = {st.object_id}
        world = World(static.boxes + [b for k, b in boxes.items() if k not in ex], 0.0)
        near = st.kind in ("approach", "lift", "place", "retreat")
        for wp in st.waypoints:
            p1 = np.array(wp.pos, float)
            R1 = quat_to_mat(wp.quat)
            hit = check_segment(world, env, pos, R, p1, R1, margin=0.0,
                                ground_margin=-0.002 if near else 0.0, step=step)
            if hit is not None:
                problems.append(f"{st.kind} {st.object_id}: {hit.obstacle}")
            pos, R = p1, R1
    return problems


def max_transport_tilt(r: PlanResult, object_id: str) -> float:
    """Largest object tilt implied by the gripper orientation along the
    object's lift/transport/place waypoints (top grasp: object axis = gripper z),
    including slerp interpolation between waypoints."""
    worst = 0.0
    prev = None
    for st in r.steps:
        if st.object_id != object_id or st.kind not in ("grasp", "lift", "transport", "place"):
            continue
        for wp in st.waypoints:
            Rm = quat_to_mat(wp.quat)
            if prev is not None:
                for Ri in slerp_mats(prev, Rm, np.linspace(0, 1, 11)):
                    worst = max(worst, math.degrees(math.acos(max(-1, min(1, Ri[2, 2])))))
            prev = Rm
    return worst
