"""Scripted demonstrations with a chosen cup tilt while carried.

The reference operator (mirror.sim.demo) always carries the cup upright, so
every registered rendered clip so far has true cup tilt of about 0.3 degrees
or less and a constant-zero estimator beats any tilt measurement. These
demonstrations tilt the gripper (and the cup it holds) to a commanded angle
partway through the cup's carry, hold it, and return upright before placing.
Everything else is the reference script: same operator, velocity commands,
contact-physics grasps, and simulator ground truth. The true tilt is whatever
the physics produced (read from the trace), not the commanded value.
"""
from __future__ import annotations

import math

import numpy as np

from mirror.contracts import DemoTrace, SceneState
from mirror.geometry import GRIPPER
from mirror.sim import rotations as rot
import mirror.sim.demo as demo_mod
from mirror.sim.demo import (CARRY_Z, GRASP_OFFSET, OPERATOR_SLOW, PACK_ORDER, PLACE_OFFSETS, _around_waypoints,
                             _grasp_yaw, _Operator, _yaw_of)

TILT_AXES = ("roll", "pitch")


def record_tilted_demo(scene: SceneState, tilt_deg: float, *, axis: str = "roll", hold_s: float = 4.0,
                       at_fraction: float = 0.5) -> DemoTrace:
    """Pack every object into the first tray; while carrying the cup, tilt the
    wrist by ``tilt_deg`` about ``axis`` at ``at_fraction`` of the way to the
    tray, hold ``hold_s`` seconds, then return upright."""
    if axis not in TILT_AXES:
        raise ValueError(f"axis must be one of {TILT_AXES}")
    script = f"tilt{int(round(tilt_deg)):02d}{axis[0]}"
    tele = demo_mod.Teleop(scene, demo_id=f"demo_{scene.scene_id}_{script}", source="teleop_scripted",
                  source_note=(f"Scripted operator (reference script with the cup tilted by {tilt_deg:g} degrees about "
                               f"the wrist {axis} axis for {hold_s:g} s mid-carry); velocity commands at 30 Hz from true "
                               "object poses; grasps and releases from finger contacts in the physics simulation."))
    op = _Operator(tele)
    tray = scene.trays[0]
    tyaw = _yaw_of(tray.pose.quat)
    order = sorted(scene.objects, key=lambda o: PACK_ORDER.index(o.kind))
    q_up = rot.wrist_quat(0.0, 0.0, 0.0)
    for obj in order:
        x, y, z = obj.pose.pos
        q_grasp = rot.wrist_quat(_grasp_yaw(obj), 0.0, 0.0)
        here = tele.env.gripper_pose().pos
        for px, py in _around_waypoints(scene, here[0], x):
            op.goto((px, py, CARRY_Z), q_up)
        op.goto((x, y, CARRY_Z), q_grasp)
        op.grip(GRIPPER.max_opening)
        op.goto((x, y, z + GRASP_OFFSET[obj.kind]), q_grasp, speed=OPERATOR_SLOW, tol=0.003)
        op.grip(0.0, wait_for_hold=obj.id)
        op.goto((x, y, CARRY_Z), q_grasp, speed=OPERATOR_SLOW * 2)
        ox, oy = PLACE_OFFSETS[obj.kind]
        px = tray.pose.pos[0] + math.cos(tyaw) * ox - math.sin(tyaw) * oy
        py = tray.pose.pos[1] + math.sin(tyaw) * ox + math.cos(tyaw) * oy
        route = [(x, y)] + list(_around_waypoints(scene, x, px)) + [(px, py)]
        seg = np.cumsum([0.0] + [math.dist(a, b) for a, b in zip(route, route[1:])])
        tilt_at = at_fraction * seg[-1] if obj.kind == "cup" and tilt_deg > 0 else None
        for k, (wx, wy) in enumerate(route[1:], start=1):
            if tilt_at is not None and seg[k - 1] <= tilt_at <= seg[k]:
                s = (tilt_at - seg[k - 1]) / max(1e-9, seg[k] - seg[k - 1])
                mx = route[k - 1][0] + s * (wx - route[k - 1][0])
                my = route[k - 1][1] + s * (wy - route[k - 1][1])
                op.goto((mx, my, CARRY_Z), q_up)
                a = math.radians(tilt_deg)
                q_t = rot.wrist_quat(0.0, a, 0.0) if axis == "roll" else rot.wrist_quat(0.0, 0.0, a)
                op.goto((mx, my, CARRY_Z), q_t)
                tele.advance(hold_s)
                op.goto((mx, my, CARRY_Z), q_up)
                tilt_at = None
            op.goto((wx, wy, CARRY_Z), q_up)
        half_h = {"cup": obj.size.get("outer_half", [0, 0, 0])[2] if obj.kind == "cup" else 0.0,
                  "block": obj.size.get("half", [0, 0, 0])[2] if obj.kind == "block" else 0.0,
                  "marker": obj.size.get("half_length", 0.0) if obj.kind == "marker" else 0.0}[obj.kind]
        op.goto((px, py, tray.pose.pos[2] + half_h + GRASP_OFFSET[obj.kind] + 0.006), q_up,
                speed=OPERATOR_SLOW, tol=0.003)
        op.grip(GRIPPER.max_opening)
        op.goto((px, py, CARRY_Z), q_up, speed=OPERATOR_SLOW * 2)
    tele.advance(1.0)
    return tele.finish()


def true_cup_carry_tilt(trace: DemoTrace) -> dict:
    """Max and mean true cup tilt while carried, from the simulator trace."""
    from mirror.interpret.geom import tilt_deg
    cup = next(o.id for o in trace.scene.objects if o.kind == "cup")
    ev = sorted((e for e in trace.events if e.object_id == cup and e.type in ("grasp", "release")), key=lambda e: e.t)
    g = next((e.t for e in ev if e.type == "grasp"), None)
    r = next((e.t for e in ev if e.type == "release"), None)
    tilts = [tilt_deg(f.objects[cup].quat) for f in trace.frames if g is not None and r is not None and g <= f.t <= r]
    return dict(max_deg=round(max(tilts), 2) if tilts else None, mean_deg=round(float(np.mean(tilts)), 2) if tilts else None,
                frames=len(tilts))


def render_tilted_demo(seed: int, split: str, tilt_deg: float, out_dir, *, axis: str = "roll", fps: float = 15.0,
                       width: int = 960, height: int = 540, cams=("top", "side"), hold_s: float = 4.0):
    """Render a tilted-carry demonstration with the standard renderer (same
    cameras, materials and ground truth as the registered rendered clips)."""
    import contextlib
    import mirror.perception.video as video
    from mirror.scenes import sample_layout

    def record(source, split_=None):
        scene = sample_layout(seed, split)
        with video._capture_teleop() as box:
            trace = record_tilted_demo(scene, tilt_deg, axis=axis, hold_s=hold_s)
        tele = box["tele"]
        return scene, trace, tele.rollout_frames(), tele.visual_scene()

    @contextlib.contextmanager
    def patched():
        base = video.record_demo
        video.record_demo = record
        try:
            yield
        finally:
            video.record_demo = base
    with patched():
        return video.render_demo_video(seed, out_dir, cams=cams, fps=fps, width=width, height=height, split=split)
