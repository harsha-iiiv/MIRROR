"""A scripted operator that records demonstrations through the teleoperation
interface.

The operator sees the true object and tray poses (it stands in for a person
looking at the table) and steers the gripper with velocity commands at a
fixed command rate, the same interface a keyboard operator uses. Grasps and
releases happen through contact physics and are detected from finger
contacts. The resulting DemoTrace has source="teleop_scripted".
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from mirror.contracts import DemoTrace, SceneState
from mirror.geometry import GRIPPER
from mirror.sim import rotations as rot
from mirror.sim.teleop import Teleop

COMMAND_HZ = 30.0
OPERATOR_SPEED = 0.22        # m/s cruise speed of the operator's commands
OPERATOR_SLOW = 0.07         # m/s near objects
OPERATOR_ANG_SPEED = 1.0     # rad/s
CARRY_Z = 0.30
PASS_CLEARANCE = 0.14        # distance kept from the wall end when going around it
GRASP_OFFSET = {"cup": 0.030, "block": 0.0, "marker": 0.040}
PLACE_OFFSETS = {"cup": (-0.07, 0.0), "block": (0.005, 0.0), "marker": (0.075, 0.0)}
PACK_ORDER = ("cup", "block", "marker")

REFERENCE_DEMO_PATH = Path(__file__).resolve().parents[2] / "data" / "demos" / "reference_demo.json"


class _Operator:
    def __init__(self, tele: Teleop):
        self.tele = tele
        self.dt = 1.0 / COMMAND_HZ

    def goto(self, pos, quat=None, speed: float = OPERATOR_SPEED, tol: float = 0.004,
             timeout: float = 20.0) -> None:
        tele = self.tele
        target = np.asarray(pos, dtype=float)
        q_goal = rot.qnormalize(quat) if quat is not None else tele._target_quat
        t = 0.0
        while t < timeout:
            cur = tele._target_pos
            err = target - cur
            dist = float(np.linalg.norm(err))
            ang = rot.qangle(tele._target_quat, q_goal)
            if dist < 1e-4 and ang < 1e-3:
                break
            v = min(speed, 1.5 * dist + 0.01)       # operators slow down near the goal
            dpos = err if dist <= v * self.dt else err * (v * self.dt / dist)
            if ang > 1e-3:
                s = min(1.0, OPERATOR_ANG_SPEED * self.dt / ang)
                q_next = rot.slerp(tele._target_quat, q_goal, s)
                tele.set_target(cur + dpos, q_next)
            else:
                tele.command(tuple(dpos), (0.0, 0.0, 0.0))
            tele.advance(self.dt)
            t += self.dt
        # Let the gripper catch up with the commanded pose.
        for _ in range(int(2.0 / self.dt)):
            g = np.asarray(tele.env.gripper_pose().pos)
            if np.linalg.norm(g - target) < tol:
                break
            tele.advance(self.dt)

    def grip(self, width: float, wait_for_hold: str | None = None, timeout: float = 2.0) -> None:
        tele = self.tele
        tele.command((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), grip=width)
        t = 0.0
        while t < timeout:
            tele.advance(self.dt)
            t += self.dt
            if wait_for_hold is not None:
                if tele.held_object == wait_for_hold and t > 0.4:
                    break
            elif tele.held_object is None and abs(tele.env.jaw_width() - width) < 0.003:
                break
        tele.advance(0.15)


def _yaw_of(q) -> float:
    w, x, y, z = q
    return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


def _grasp_yaw(obj) -> float:
    if obj.kind == "marker":
        return 0.0
    yaw = _yaw_of(obj.pose.quat)
    # Square footprints: pick the equivalent yaw closest to zero.
    return (yaw + math.pi / 4) % (math.pi / 2) - math.pi / 4


def _around_waypoints(scene: SceneState, x_from: float, x_to: float) -> list[tuple[float, float]]:
    """Planar waypoints that pass every wall around its open end."""
    pts = []
    ws = scene.workspace
    for ob in scene.obstacles:
        if ob.kind != "wall":
            continue
        wx, wy = ob.pose.pos[:2]
        y0, y1 = wy - ob.length / 2, wy + ob.length / 2
        if not (min(x_from, x_to) < wx < max(x_from, x_to)):
            continue
        if y1 + PASS_CLEARANCE <= ws.y_range[1] - 0.01:
            yp = y1 + PASS_CLEARANCE
        elif y0 - PASS_CLEARANCE >= ws.y_range[0] + 0.01:
            yp = y0 - PASS_CLEARANCE
        else:
            raise ValueError(f"wall {ob.id} has no open end; the reference operator only goes around walls")
        off = ob.thickness / 2 + 0.12
        sgn = 1.0 if x_to > x_from else -1.0
        pts += [(wx - sgn * off, yp), (wx + sgn * off, yp)]
    return pts


def record_scripted_demo(scene: SceneState, script: str = "reference") -> DemoTrace:
    """Pack every object into the first tray, carrying the cup upright around the wall."""
    if script != "reference":
        raise ValueError(f"unknown demo script {script!r}")
    tele = Teleop(scene, demo_id=f"demo_{scene.scene_id}_{script}", source="teleop_scripted",
                  source_note=("Scripted operator using teleoperation velocity commands at "
                               f"{COMMAND_HZ:.0f} Hz from true object poses; grasp and release "
                               "events detected from finger contacts in the physics simulation."))
    op = _Operator(tele)
    tray = scene.trays[0]
    tq = tray.pose.quat
    tyaw = _yaw_of(tq)
    order = sorted(scene.objects, key=lambda o: PACK_ORDER.index(o.kind))
    for obj in order:
        x, y, z = obj.pose.pos
        gyaw = _grasp_yaw(obj)
        q_grasp = rot.wrist_quat(gyaw, 0.0, 0.0)
        here = tele.env.gripper_pose().pos
        for px, py in _around_waypoints(scene, here[0], x):
            op.goto((px, py, CARRY_Z), rot.wrist_quat(0.0, 0.0, 0.0))
        op.goto((x, y, CARRY_Z), q_grasp)
        op.grip(GRIPPER.max_opening)
        gz = z + GRASP_OFFSET[obj.kind]
        op.goto((x, y, gz), q_grasp, speed=OPERATOR_SLOW, tol=0.003)
        op.grip(0.0, wait_for_hold=obj.id)
        op.goto((x, y, CARRY_Z), q_grasp, speed=OPERATOR_SLOW * 2)
        ox, oy = PLACE_OFFSETS[obj.kind]
        px = tray.pose.pos[0] + math.cos(tyaw) * ox - math.sin(tyaw) * oy
        py = tray.pose.pos[1] + math.sin(tyaw) * ox + math.cos(tyaw) * oy
        q_up = rot.wrist_quat(0.0, 0.0, 0.0)
        for wx, wy in _around_waypoints(scene, x, px):
            op.goto((wx, wy, CARRY_Z), q_up)
        op.goto((px, py, CARRY_Z), q_up)
        half_h = {"cup": obj.size.get("outer_half", [0, 0, 0])[2] if obj.kind == "cup" else 0.0,
                  "block": obj.size.get("half", [0, 0, 0])[2] if obj.kind == "block" else 0.0,
                  "marker": obj.size.get("half_length", 0.0) if obj.kind == "marker" else 0.0}[obj.kind]
        op.goto((px, py, tray.pose.pos[2] + half_h + GRASP_OFFSET[obj.kind] + 0.006), q_up,
                speed=OPERATOR_SLOW, tol=0.003)
        op.grip(GRIPPER.max_opening)
        op.goto((px, py, CARRY_Z), q_up, speed=OPERATOR_SLOW * 2)
    tele.advance(1.0)
    return tele.finish()


def save_demo(trace: DemoTrace, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(trace.model_dump_json(indent=None))


def load_demo(path: str | Path = REFERENCE_DEMO_PATH) -> DemoTrace:
    return DemoTrace.model_validate_json(Path(path).read_text())
