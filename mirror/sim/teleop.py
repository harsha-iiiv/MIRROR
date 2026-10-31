"""Incremental teleoperation of the simulated gripper.

``Teleop.command`` nudges the gripper's target pose (and optionally sets the
jaw opening); ``advance`` runs physics while the commanded pose moves toward
that target with bounded speed, exactly as a keyboard or joystick operator
would drive it. Grasp and release events are detected from finger contacts,
never declared by the operator.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from mirror.contracts import DemoTrace, Pose, RolloutFrame, SceneState, TraceEvent, TraceFrame, Vec3
from mirror.geometry import GRIPPER
from mirror.sim import rotations as rot
from mirror.sim.env import SimEnv, _Failure, _Recorder
from mirror.statelog import StateLog

TELEOP_SPEED = 0.30          # m/s, how fast the commanded pose chases the target
TELEOP_ANG_SPEED = 1.5       # rad/s
EVENT_DEBOUNCE = 0.10        # s a hold must persist (or be gone) before an event

# Default keyboard layout for interactive teleoperation: key -> (dpos unit
# vector, drot unit vector, grip). Translation steps are scaled by
# ``Teleop.press(step=...)``, rotation steps by ``rot_step``.
TELEOP_KEYS: dict[str, tuple[tuple[float, float, float], tuple[float, float, float], Optional[float]]] = {
    "w": ((1, 0, 0), (0, 0, 0), None), "s": ((-1, 0, 0), (0, 0, 0), None),
    "a": ((0, 1, 0), (0, 0, 0), None), "d": ((0, -1, 0), (0, 0, 0), None),
    "r": ((0, 0, 1), (0, 0, 0), None), "f": ((0, 0, -1), (0, 0, 0), None),
    "q": ((0, 0, 0), (0, 0, 1), None), "e": ((0, 0, 0), (0, 0, -1), None),     # yaw
    "t": ((0, 0, 0), (0, 1, 0), None), "g": ((0, 0, 0), (0, -1, 0), None),     # pitch (world y)
    "c": ((0, 0, 0), (0, 0, 0), 0.0), "o": ((0, 0, 0), (0, 0, 0), GRIPPER.max_opening),
}


def _r(x: float, nd: int = 5) -> float:
    return round(float(x), nd)


def _pose(p, q) -> Pose:
    return Pose(pos=tuple(_r(v) for v in p), quat=tuple(_r(v, 6) for v in q))


class Teleop:
    def __init__(self, scene: SceneState, *, sample_hz: float = 30.0, frame_hz: float = 30.0,
                 demo_id: str | None = None, source: str = "teleop_keyboard", source_note: str = ""):
        self.env = SimEnv(scene)
        self.scene = scene
        self.sample_hz = sample_hz
        self.demo_id = demo_id or f"demo_{scene.scene_id}"
        self.source = source
        self.source_note = source_note
        self._target_pos = self.env._ref_pos.copy()
        self._target_quat = self.env._ref_quat.copy()
        dt = float(self.env.model.opt.timestep)
        self.dt = dt
        self._rec = _Recorder(self.env, int(30.0 / dt), 1.0 / float(frame_hz))
        self._sample_period = 1.0 / float(sample_hz)
        self._next_sample = 0.0
        self.frames: list[TraceFrame] = []
        self.events: list[TraceEvent] = []
        self._held_state = -1          # debounced held object index
        self._held_candidate = -1
        self._candidate_since = 0.0
        self.held = -1
        self._sample()

    # --------------------------------------------------------- commands ---
    def command(self, dpos: Vec3, drot: Vec3, grip: Optional[float] = None) -> None:
        """Move the target by ``dpos`` (m, world frame) and rotate it by the
        rotation vector ``drot`` (rad, world frame). ``grip`` sets the jaw
        opening in metres (0 closes until contact)."""
        ws = self.scene.workspace
        p = self._target_pos + np.asarray(dpos, dtype=float)
        p[0] = min(max(p[0], ws.x_range[0]), ws.x_range[1])
        p[1] = min(max(p[1], ws.y_range[0]), ws.y_range[1])
        p[2] = min(max(p[2], ws.z_range[0]), ws.z_range[1])
        self._target_pos = p
        if np.linalg.norm(drot) > 0:
            q = rot.qnormalize(rot.qmul(rot.rotvec_quat(drot), self._target_quat))
            y, r, pt = rot.wrist_angles(q, self.env._ref_angles)
            from mirror.sim.model import PITCH_RANGE, ROLL_RANGE, YAW_RANGE
            if (YAW_RANGE[0] <= y <= YAW_RANGE[1] and ROLL_RANGE[0] <= r <= ROLL_RANGE[1]
                    and PITCH_RANGE[0] <= pt <= PITCH_RANGE[1]):
                self._target_quat = q
        if grip is not None:
            self.env._jaw_target = min(max(float(grip), 0.0), GRIPPER.max_opening)

    def press(self, key: str, step: float = 0.01, rot_step: float = 0.05) -> None:
        """Apply one key of TELEOP_KEYS (unknown keys are ignored)."""
        spec = TELEOP_KEYS.get(key.lower())
        if spec is None:
            return
        dp, dr, grip = spec
        self.command(tuple(step * v for v in dp), tuple(rot_step * v for v in dr), grip)

    def visual_scene(self):
        return self.env.visual_scene()

    def set_target(self, pos: Vec3, quat=None, grip: Optional[float] = None) -> None:
        """Absolute variant of :meth:`command` (used by scripted operators)."""
        cur = self._target_pos
        drot = (0.0, 0.0, 0.0)
        if quat is not None:
            dq = rot.qmul(rot.qnormalize(quat), rot.qconj(self._target_quat))
            if dq[0] < 0:
                dq = -dq
            ang = 2 * math.acos(min(1.0, dq[0]))
            axis = dq[1:] / (math.sin(ang / 2) + 1e-12)
            drot = tuple(axis * ang) if ang > 1e-9 else (0.0, 0.0, 0.0)
        self.command(tuple(np.asarray(pos, dtype=float) - cur), drot, grip)

    # ---------------------------------------------------------- physics ---
    def advance(self, seconds: float) -> RolloutFrame:
        env = self.env
        n = max(1, int(round(seconds / self.dt)))
        vstep = TELEOP_SPEED * self.dt
        wstep = TELEOP_ANG_SPEED * self.dt
        for _ in range(n):
            dp = self._target_pos - env._ref_pos
            d = float(np.linalg.norm(dp))
            pos = self._target_pos if d <= vstep else env._ref_pos + dp * (vstep / d)
            ang = rot.qangle(env._ref_quat, self._target_quat)
            quat = self._target_quat if ang <= wstep else rot.slerp(env._ref_quat, self._target_quat, wstep / ang)
            try:
                env._set_reference(pos, quat)
            except _Failure:
                self._target_quat = env._ref_quat.copy()
            env._apply_ctrl()
            env._step_physics()
            held, contacts = env._contacts()
            self.held = held
            t = env.sim_time()
            self._rec.record(t, "teleop", held, contacts)
            self._update_events(t, held)
            if t >= self._next_sample - 1e-9:
                self._sample()
        return self._rec.frames[-1] if self._rec.frames else self._frame_now()

    def _frame_now(self) -> RolloutFrame:
        self._rec.add_frame(self.env.sim_time(), "teleop")
        return self._rec.frames[-1]

    def _update_events(self, t: float, held: int) -> None:
        if held != self._held_candidate:
            self._held_candidate = held
            self._candidate_since = t
            return
        if held != self._held_state and t - self._candidate_since >= EVENT_DEBOUNCE:
            oids = self.env.index.object_ids
            t_ev = _r(self._candidate_since, 4)
            if self._held_state >= 0:
                self.events.append(TraceEvent(t=t_ev, type="release", object_id=oids[self._held_state],
                                              detail={"width": _r(self.env.jaw_width())}))
            if held >= 0:
                self.events.append(TraceEvent(t=t_ev, type="grasp", object_id=oids[held],
                                              detail={"width": _r(self.env.jaw_width())}))
            self._held_state = held

    @property
    def held_object(self) -> Optional[str]:
        """Debounced id of the object currently held between both fingers."""
        return self.env.index.object_ids[self._held_state] if self._held_state >= 0 else None

    def _sample(self) -> None:
        env = self.env
        d = env.data
        gb = env.index.gripper_body
        objs = {o: _pose(d.xpos[env.index.object_body[o]], d.xquat[env.index.object_body[o]])
                for o in env.index.object_ids}
        self.frames.append(TraceFrame(t=_r(env.sim_time(), 4), gripper=_pose(d.xpos[gb], d.xquat[gb]),
                                      gripper_width=_r(env.jaw_width()), objects=objs))
        self._next_sample += self._sample_period

    # ----------------------------------------------------------- output ---
    def state_log(self) -> StateLog:
        return self._rec.to_log({"scene_id": self.scene.scene_id, "source": self.source,
                                 "timestep": self.dt})

    def rollout_frames(self) -> list[RolloutFrame]:
        return list(self._rec.frames)

    def finish(self) -> DemoTrace:
        return DemoTrace(demo_id=self.demo_id, scene=self.scene, source=self.source,
                         source_note=self.source_note, sample_hz=self.sample_hz,
                         frames=list(self.frames), events=list(self.events))
