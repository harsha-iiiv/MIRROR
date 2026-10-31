"""Hand-written PlanResult fixtures for the simulator tests.

These are deliberately simple waypoint scripts, independent of the planner,
so that the physics claims can be tested on their own.
"""
from __future__ import annotations

import math

from mirror.contracts import PlanResult, PlanStep, Pose, SceneState
from mirror.geometry import GRIPPER
from mirror.sim.rotations import qmul, wrist_quat

DOWN = (1.0, 0.0, 0.0, 0.0)
SAFE_Z = 0.30

# TCP height above the object's centre for a top grasp.
GRASP_OFFSET = {"cup": 0.030, "block": 0.0, "marker": 0.040}


def pitched(deg: float, yaw: float = 0.0):
    """Gripper orientation pitched about its jaw axis by ``deg`` degrees."""
    return tuple(wrist_quat(yaw, 0.0, math.radians(deg)))


def pick_steps(scene: SceneState, obj_id: str, *, yaw: float = 0.0, speed: float = 0.25,
               via: list[Pose] | None = None):
    o = next(o for o in scene.objects if o.id == obj_id)
    x, y, z = o.pose.pos
    gz = z + GRASP_OFFSET[o.kind]
    q = tuple(wrist_quat(yaw, 0.0, 0.0))
    return [
        PlanStep(kind="move", waypoints=list(via or []) + [Pose(pos=(x, y, SAFE_Z), quat=q)],
                 max_speed=speed, gripper_width=GRIPPER.max_opening),
        PlanStep(kind="approach", object_id=obj_id, waypoints=[Pose(pos=(x, y, gz), quat=q)],
                 max_speed=0.12),
        PlanStep(kind="grasp", object_id=obj_id),
        PlanStep(kind="lift", object_id=obj_id, waypoints=[Pose(pos=(x, y, SAFE_Z), quat=q)],
                 max_speed=0.15),
    ]


def place_steps(scene: SceneState, obj_id: str, x: float, y: float, *, yaw: float = 0.0,
                route: list[Pose] | None = None, speed: float = 0.25):
    o = next(o for o in scene.objects if o.id == obj_id)
    floor = scene.trays[0].pose.pos[2]
    half_h = {"cup": o.size.get("outer_half", [0, 0, 0.05])[2] if o.kind == "cup" else 0,
              "block": o.size.get("half", [0, 0, 0.02])[2] if o.kind == "block" else 0,
              "marker": o.size.get("half_length", 0.06) if o.kind == "marker" else 0}[o.kind]
    gz = floor + half_h + GRASP_OFFSET[o.kind] + 0.006
    q = tuple(wrist_quat(yaw, 0.0, 0.0))
    steps = []
    if route:
        steps.append(PlanStep(kind="transport", object_id=obj_id, waypoints=route, max_speed=speed))
    steps += [
        PlanStep(kind="transport", object_id=obj_id, waypoints=[Pose(pos=(x, y, SAFE_Z), quat=q)],
                 max_speed=speed),
        PlanStep(kind="place", object_id=obj_id, waypoints=[Pose(pos=(x, y, gz), quat=q)],
                 max_speed=0.10),
        PlanStep(kind="release", object_id=obj_id, gripper_width=GRIPPER.max_opening),
        PlanStep(kind="retreat", object_id=obj_id, waypoints=[Pose(pos=(x, y, SAFE_Z), quat=q)],
                 max_speed=0.15),
    ]
    return steps


def plan_of(scene: SceneState, steps, note: str = "") -> PlanResult:
    return PlanResult(status="ok", scene_id=scene.scene_id, steps=steps, planner="test-fixture",
                      reason=note)


def around_route_demo(z: float = SAFE_Z, y_pass: float = 0.28, x_pass: float = 0.12):
    """Waypoints around the +y end of the reference wall, gripper upright."""
    q = DOWN
    return [Pose(pos=(-x_pass, y_pass, z), quat=q), Pose(pos=(x_pass, y_pass, z), quat=q)]


def demo_pack_plan(scene: SceneState) -> PlanResult:
    """Pack cup, block and marker into the tray of the demo scene, going around the wall."""
    tx, ty = scene.trays[0].pose.pos[:2]
    spots = {"cup_0": (tx - 0.07, ty), "block_0": (tx + 0.005, ty), "marker_0": (tx + 0.075, ty)}
    steps = []
    back = list(reversed(around_route_demo()))
    for k, oid in enumerate(("cup_0", "block_0", "marker_0")):
        steps += pick_steps(scene, oid, via=back if k else None)
        steps += place_steps(scene, oid, *spots[oid], route=around_route_demo())
    return plan_of(scene, steps, "demo pack around the wall end")


def slot_plan(scene: SceneState, *, tilt_deg: float, slot_z: float = 0.14,
              y: float = -0.18, place: bool = True) -> PlanResult:
    """Carry the cup through the wall slot of the shortcut scene at a given pitch."""
    q_t = pitched(tilt_deg)
    steps = pick_steps(scene, "cup_0")
    steps += [
        PlanStep(kind="transport", object_id="cup_0", max_speed=0.2,
                 waypoints=[Pose(pos=(-0.22, y, SAFE_Z), quat=q_t),
                            Pose(pos=(-0.20, y, slot_z), quat=q_t),
                            Pose(pos=(0.16, y, slot_z), quat=q_t),
                            Pose(pos=(0.20, y, SAFE_Z), quat=q_t),
                            Pose(pos=(0.20, y, SAFE_Z), quat=DOWN)]),
    ]
    if place:
        tx, ty = scene.trays[0].pose.pos[:2]
        steps += place_steps(scene, "cup_0", tx - 0.04, ty)
    return plan_of(scene, steps, f"cup through the slot at {tilt_deg:.0f} deg")
