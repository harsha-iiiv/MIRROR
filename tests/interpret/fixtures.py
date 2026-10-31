"""Synthetic scenes, demonstrations and candidate plans for interpretation tests.

They mirror the reference world (start region x < 0, tray at x > 0, a tall
wall at x = 0) without depending on the simulator.
"""
from __future__ import annotations

import math

import numpy as np

from mirror.contracts import (DemoTrace, ObjectSpec, ObstacleSpec, PlanResult, PlanStep, Pose,
                              SceneState, SlotSpec, TraceEvent, TraceFrame, TraySpec, Workspace)
from mirror.geometry import BLOCK, CUP, MARKER, TRAY, WORLD

UP = (1.0, 0.0, 0.0, 0.0)


def quat_y(deg: float):
    a = math.radians(deg) / 2
    return (math.cos(a), 0.0, math.sin(a), 0.0)


def quat_x(deg: float):
    a = math.radians(deg) / 2
    return (math.cos(a), math.sin(a), 0.0, 0.0)


def make_scene(slot: bool = False, scene_id: str = "fixture_demo", with_marker: bool = True) -> SceneState:
    objs = [
        ObjectSpec(id="cup_0", kind="cup", size={"outer_half": CUP.outer_half, "wall": CUP.wall},
                   mass=CUP.mass, pose=Pose(pos=(-0.25, -0.10, CUP.outer_half[2])), beads=CUP.beads),
        ObjectSpec(id="block_0", kind="block", size={"half": BLOCK.half}, mass=BLOCK.mass,
                   pose=Pose(pos=(-0.25, 0.05, BLOCK.half[2]))),
    ]
    if with_marker:
        objs.append(ObjectSpec(id="marker_0", kind="marker",
                               size={"radius": MARKER.radius, "half_length": MARKER.half_length},
                               mass=MARKER.mass, pose=Pose(pos=(-0.32, 0.18, MARKER.half_length))))
    slots = [SlotSpec(y_min=-0.20, y_max=0.0, z_min=WORLD.slot_z[0], z_max=WORLD.slot_z[1])] if slot else []
    return SceneState(
        scene_id=scene_id, split="demo", objects=objs,
        trays=[TraySpec(id="tray_0", pose=Pose(pos=(0.25, -0.05, 0.0)), inner_half=TRAY.inner_half)],
        obstacles=[ObstacleSpec(id="wall_0", kind="wall", pose=Pose(pos=(0.0, -0.05, 0.0)),
                                length=0.50, height=WORLD.wall_height, thickness=WORLD.wall_thickness,
                                slots=slots)],
        workspace=Workspace(x_range=WORLD.workspace_x, y_range=WORLD.workspace_y, z_range=WORLD.workspace_z),
        gripper_start=Pose(pos=(0.0, 0.0, 0.30)))


PLACES = {"cup_0": (0.20, -0.10), "block_0": (0.29, -0.02), "marker_0": (0.30, -0.10)}


def make_demo(order=("cup_0", "block_0", "marker_0"), tilt_kind: str | None = None, tilt_deg: float = 80.0,
              events: bool = True, demo_id: str = "fixture_demo", leave_out: tuple = (),
              through_slot_for: str | None = None) -> DemoTrace:
    scene = make_scene(slot=through_slot_for is not None)
    hz = 20.0
    dt = 1.0 / hz
    speed = 0.25
    t = 0.0
    obj_pose = {o.id: [np.array(o.pose.pos, float), o.pose.quat] for o in scene.objects}
    kind = {o.id: o.kind for o in scene.objects}
    grip = np.array(scene.gripper_start.pos, float)
    frames, evs = [], []

    def snap(width: float, gq=UP):
        frames.append(TraceFrame(t=round(t, 4), gripper=Pose(pos=tuple(grip), quat=gq), gripper_width=width,
                                 objects={k: Pose(pos=tuple(float(c) for c in v[0]), quat=tuple(v[1]))
                                          for k, v in obj_pose.items()}))

    def move(to, held=None, width=0.09, tilt_fn=None):
        nonlocal grip, t
        to = np.array(to, float)
        dist = float(np.linalg.norm(to - grip))
        n = max(1, int(math.ceil(dist / (speed * dt))))
        start = grip.copy()
        for i in range(1, n + 1):
            grip = start + (to - start) * (i / n)
            t += dt
            if held:
                q = tilt_fn(i / n) if tilt_fn else UP
                obj_pose[held][0] = grip - np.array([0, 0, 0.0])
                obj_pose[held][1] = q
            snap(width)

    snap(0.09)
    for oid in order:
        if oid in leave_out:
            continue
        p = obj_pose[oid][0].copy()
        move((p[0], p[1], 0.25))
        move((p[0], p[1], p[2]))
        if events:
            evs.append(TraceEvent(t=round(t, 4), type="grasp", object_id=oid))
        tilt_this = tilt_kind is not None and kind[oid] == tilt_kind
        wobble = (lambda s: quat_x(3.0 * math.sin(math.pi * s)))
        lift_z = 0.20
        if through_slot_for == kind[oid]:
            tilt = lambda s: quat_y(tilt_deg * math.sin(math.pi * min(1.0, s)))
            move((p[0], p[1], lift_z), held=oid, tilt_fn=wobble)
            move((-0.05, -0.10, 0.14), held=oid, tilt_fn=tilt)
            move((0.05, -0.10, 0.14), held=oid, tilt_fn=lambda s: quat_y(tilt_deg))
            move((0.12, -0.10, lift_z), held=oid, tilt_fn=lambda s: quat_y(tilt_deg * (1 - s)))
        else:
            fn = (lambda s: quat_y(tilt_deg * math.sin(math.pi * s))) if tilt_this else wobble
            move((p[0], p[1], lift_z), held=oid, tilt_fn=wobble)
            move((-0.10, 0.30, lift_z), held=oid, tilt_fn=fn)
            move((0.10, 0.30, lift_z), held=oid, tilt_fn=fn)
        px, py = PLACES[oid]
        z_rest = {"cup": CUP.outer_half[2], "block": BLOCK.half[2], "marker": MARKER.half_length}[kind[oid]]
        move((px, py, lift_z), held=oid, tilt_fn=wobble)
        move((px, py, z_rest), held=oid, tilt_fn=lambda s: UP)
        obj_pose[oid][1] = UP
        if events:
            evs.append(TraceEvent(t=round(t, 4), type="release", object_id=oid))
        move((px, py, 0.25))
    return DemoTrace(demo_id=demo_id, scene=scene, source="teleop_scripted", sample_hz=hz,
                     frames=frames, events=evs)


def make_candidates(scene: SceneState | None = None):
    """Candidate A carries the cup upright around the wall; B pitches it 90
    degrees through the slot. Both place it upright."""
    scene = scene or make_scene(slot=True, scene_id="fixture_shortcut")
    cup = scene.objects[0].pose.pos
    place = (0.20, -0.10)

    def plan(tilted: bool) -> PlanResult:
        steps = [
            PlanStep(kind="approach", object_id="cup_0", waypoints=[Pose(pos=(cup[0], cup[1], 0.25)),
                                                                     Pose(pos=(cup[0], cup[1], cup[2]))]),
            PlanStep(kind="grasp", object_id="cup_0", gripper_width=0.05),
            PlanStep(kind="lift", object_id="cup_0", waypoints=[Pose(pos=(cup[0], cup[1], 0.2))]),
        ]
        if tilted:
            q = quat_y(90.0)
            steps.append(PlanStep(kind="transport", object_id="cup_0", note="through_slot",
                                  waypoints=[Pose(pos=(-0.05, -0.10, 0.14), quat=q),
                                             Pose(pos=(0.05, -0.10, 0.14), quat=q),
                                             Pose(pos=(0.15, -0.10, 0.20), quat=UP)]))
        else:
            steps.append(PlanStep(kind="transport", object_id="cup_0", note="around_+y",
                                  waypoints=[Pose(pos=(-0.10, 0.30, 0.2)), Pose(pos=(0.10, 0.30, 0.2)),
                                             Pose(pos=(place[0], place[1], 0.2))]))
        steps += [
            PlanStep(kind="place", object_id="cup_0", waypoints=[Pose(pos=(place[0], place[1], cup[2]))]),
            PlanStep(kind="release", object_id="cup_0", gripper_width=0.09),
            PlanStep(kind="retreat", waypoints=[Pose(pos=(place[0], place[1], 0.25))]),
        ]
        return PlanResult(status="ok", spec_version=1, scene_id=scene.scene_id, steps=steps,
                          object_order=["cup_0"], planner="fixture")

    return scene, [plan(False), plan(True)]
