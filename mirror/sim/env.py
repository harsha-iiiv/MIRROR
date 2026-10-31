"""Physics execution of plans.

``SimEnv.execute`` turns a PlanResult into actuator commands only: it
interpolates gripper targets between plan waypoints with bounded linear and
angular speed, writes them to the position actuators, closes and opens the
jaw, and steps MuJoCo. Object poses are never written during execution; they
change only through contact physics. ``reset`` (separate from execution)
restores the scene's initial state.

Every physics step is recorded in a :class:`mirror.statelog.StateLog`.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Literal, Optional

import mujoco
import numpy as np

from mirror.contracts import PlanResult, PlanStep, Pose, RolloutFrame, SceneState, VisualBody, VisualGeom, VisualScene
from mirror.geometry import GRIPPER
from mirror.statelog import ContactRecord, StateLog
from mirror.sim import rotations as rot
from mirror.sim.model import FINGER_RANGE, PITCH_RANGE, ROLL_RANGE, YAW_RANGE, SceneIndex, build_model

# Executor limits.
ANGULAR_SPEED_MAX = 1.2        # rad/s, peak wrist rotation rate
LINEAR_ACCEL_MAX = 1.0         # m/s^2
ANGULAR_ACCEL_MAX = 4.0        # rad/s^2
LINEAR_SPEED_MAX = 0.40        # m/s, hard cap regardless of the plan step
MIN_SEGMENT_TIME = 0.08        # s
GRASP_TIMEOUT = 1.2            # s
GRASP_MIN_TIME = 0.15          # s
GRASP_HOLD_TIME = 0.10         # s of squeezing after the jaw stops
RELEASE_TIMEOUT = 0.8          # s
JAW_SPEED = 0.10               # m/s, rate limit on the commanded jaw opening
DROP_GRACE = 0.15              # s without a two-finger hold before a drop is declared
TRACK_POS_TOL = 0.02           # m
TRACK_ANG_TOL = 0.25           # rad
TRACK_GRACE = 0.30             # s above tolerance before declaring failure
SETTLE_SPEED = 0.01            # m/s
SETTLE_WINDOW = 0.30           # s
SETTLE_MAX = 3.0               # s
RESET_SETTLE = 0.40            # s of physics after a reset, before execution starts

Status = Literal["completed", "execution_failed", "timeout"]


@dataclass
class ExecutionResult:
    log: StateLog
    frames: list[RolloutFrame]
    status: Status
    reason: str = ""
    sim_time: float = 0.0
    wall_time: float = 0.0
    steps: int = 0
    events: list[dict] = field(default_factory=list)   # grasp / release / failure events


class _Failure(Exception):
    def __init__(self, status: Status, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _pose_tuple(p) -> tuple:
    return tuple(float(x) for x in p)


class _Recorder:
    """Accumulates the per-step StateLog and the playback frames."""

    def __init__(self, env: "SimEnv", capacity: int, frame_every: "int | float"):
        """``frame_every``: playback frame period in seconds (float) or in steps (int)."""
        idx = env.index
        self.env = env
        self.K = len(idx.object_ids)
        self.cap = capacity
        self.n = 0
        self.t = np.zeros(capacity)
        self.obj_pos = np.zeros((capacity, self.K, 3))
        self.obj_quat = np.zeros((capacity, self.K, 4))
        self.g_pos = np.zeros((capacity, 3))
        self.g_quat = np.zeros((capacity, 4))
        self.width = np.zeros(capacity)
        self.held = np.full(capacity, -1, dtype=np.int32)
        self.phase: list[str] = []
        self.beads = {cid: np.zeros((capacity, len(b), 3)) for cid, b in idx.bead_bodies.items()}
        self._raw_contacts: list[tuple[int, list]] = []   # (step, [(obj, other, depth)])
        self._raw_frames: list[tuple] = []                 # (t, phase, width, positions, quats)
        self._frames_built: list[RolloutFrame] = []
        self.frame_period = frame_every * float(env.model.opt.timestep) if isinstance(frame_every, int) else float(frame_every)
        self._next_frame = -1.0
        self.obj_bodies = np.array([idx.object_body[o] for o in idx.object_ids], dtype=int)
        self.frame_bodies = [b for b in idx.dynamic_bodies if env.model.body_geomnum[b] > 0]
        self.frame_names = [idx.body_names[b] for b in self.frame_bodies]
        # One gather per step: objects, then the gripper, then every bead.
        self._bead_slices = []
        gather = list(self.obj_bodies) + [idx.gripper_body]
        for cid, bodies in idx.bead_bodies.items():
            self._bead_slices.append((cid, len(gather), len(gather) + len(bodies)))
            gather += list(bodies)
        self._gather = np.array(gather, dtype=int)
        self._fb = np.array(self.frame_bodies, dtype=int)

    def grow(self):
        extra = self.cap
        def g(a, fill=0):
            pad = np.full((extra,) + a.shape[1:], fill, dtype=a.dtype)
            return np.concatenate([a, pad])
        self.t, self.obj_pos, self.obj_quat = g(self.t), g(self.obj_pos), g(self.obj_quat)
        self.g_pos, self.g_quat, self.width = g(self.g_pos), g(self.g_quat), g(self.width)
        self.held = g(self.held, -1)
        self.beads = {k: g(v) for k, v in self.beads.items()}
        self.cap += extra

    def record(self, t: float, phase: str, held: int, contacts: list[tuple[str, str, float]]):
        if self.n >= self.cap:
            self.grow()
        env = self.env
        i = self.n
        K = self.K
        xp = env._xpos[self._gather]
        self.t[i] = t
        self.obj_pos[i] = xp[:K]
        self.obj_quat[i] = env._xquat[self.obj_bodies]
        self.g_pos[i] = xp[K]
        self.g_quat[i] = env._xquat[env.index.gripper_body]
        self.width[i] = env.jaw_width()
        self.held[i] = held
        self.phase.append(phase)
        for cid, a, b in self._bead_slices:
            self.beads[cid][i] = xp[a:b]
        if contacts:
            self._raw_contacts.append((i, contacts))
        if t >= self._next_frame - 1e-9:
            self.add_frame(t, phase)
            self._next_frame = (t if self._next_frame < 0 else self._next_frame) + self.frame_period
        self.n += 1

    def add_frame(self, t: float, phase: str):
        env = self.env
        fb = self._fb
        self._raw_frames.append((float(t), phase, float(env.jaw_width()),
                                 env._xpos[fb].tolist(), env._xquat[fb].tolist()))

    @property
    def frames(self) -> list[RolloutFrame]:
        """Playback frames, built on demand from the raw samples."""
        built = self._frames_built
        names = self.frame_names
        for t, phase, width, ps, qs in self._raw_frames[len(built):]:
            bodies = {nm: Pose.model_construct(pos=tuple(p), quat=tuple(q))
                      for nm, p, q in zip(names, ps, qs)}
            built.append(RolloutFrame.model_construct(t=t, bodies=bodies, gripper_width=width,
                                                      phase=phase))
        return built

    @property
    def contacts(self) -> list[ContactRecord]:
        return [ContactRecord(step=i, object_id=o, other=x, depth=dep)
                for i, recs in self._raw_contacts for o, x, dep in recs]

    def to_log(self, meta: dict) -> StateLog:
        n = self.n
        idx = self.env.index
        return StateLog(dt=float(self.env.model.opt.timestep), t=self.t[:n].copy(),
                        object_ids=list(idx.object_ids), obj_pos=self.obj_pos[:n].copy(),
                        obj_quat=self.obj_quat[:n].copy(), gripper_pos=self.g_pos[:n].copy(),
                        gripper_quat=self.g_quat[:n].copy(), gripper_width=self.width[:n].copy(),
                        held=self.held[:n].copy(), phase=list(self.phase[:n]),
                        beads={k: v[:n].copy() for k, v in self.beads.items()},
                        contacts=list(self.contacts), meta=meta)


class SimEnv:
    """MuJoCo environment for one scene.

    Command interface: the gripper is driven only through ``data.ctrl``
    (six pose actuators plus two finger actuators).
    """

    def __init__(self, scene: SceneState, *, seed: int = 0):
        """``seed`` is accepted for interface compatibility only: the simulator
        is deterministic (no noise is injected anywhere), so identical scene
        and plan give an identical rollout regardless of the seed."""
        self.scene = scene
        self.seed = seed
        self.model, self.index = build_model(scene)
        self.data = mujoco.MjData(self.model)
        idx = self.index
        self._obj_index_of_geom = np.full(self.model.ngeom, -1, dtype=np.int32)
        self._is_bead_geom = np.zeros(self.model.ngeom, dtype=bool)
        self._is_gripper_geom = np.zeros(self.model.ngeom, dtype=bool)
        for g, owner in enumerate(idx.geom_owner):
            if owner in idx.object_ids:
                self._obj_index_of_geom[g] = idx.object_ids.index(owner)
            elif owner.startswith("bead:"):
                self._is_bead_geom[g] = True
            elif owner == "gripper":
                self._is_gripper_geom[g] = True
        self._pair_cache: dict[tuple[int, int], object] = {}
        # Views into MjData buffers (they stay valid for the life of self.data).
        d = self.data
        self._qpos, self._xpos, self._xquat, self._ctrl = d.qpos, d.xpos, d.xquat, d.ctrl
        self._oi_list = self._obj_index_of_geom.tolist()
        self._grip_list = self._is_gripper_geom.tolist()
        self._robot_q = np.array(idx.robot_qposadr, dtype=int)
        self._finger_qa = tuple(idx.finger_qposadr)
        self._finger_q = np.array(idx.finger_qposadr, dtype=int)
        self._robot_act = np.array(idx.robot_actuators, dtype=int)
        self._finger_act = np.array(idx.finger_actuators, dtype=int)
        self._ref_angles = (0.0, 0.0, 0.0)
        self.reset()

    # ----------------------------------------------------------- reset ---
    def reset(self) -> None:
        """Restore the scene's initial state (allowed only between trials)."""
        m, d, idx = self.model, self.data, self.index
        mujoco.mj_resetData(m, d)
        # Objects and beads: their compiled initial poses are the scene poses.
        gs = self.scene.gripper_start
        ang = rot.wrist_angles(gs.quat)
        robot_q = list(gs.pos) + [ang[0], ang[1], ang[2]]
        for a, v in zip(idx.robot_qposadr, robot_q):
            d.qpos[a] = v
        for a in idx.finger_qposadr:
            d.qpos[a] = FINGER_RANGE[1]
        self._ref_pos = np.array(gs.pos, dtype=float)
        self._ref_quat = rot.qnormalize(gs.quat)
        self._ref_angles = ang
        self._jaw_target = GRIPPER.max_opening
        self._jaw_cmd = GRIPPER.max_opening
        self._apply_ctrl()
        mujoco.mj_forward(m, d)
        # Let beads and objects come to rest before any execution.
        for _ in range(int(RESET_SETTLE / m.opt.timestep)):
            mujoco.mj_step(m, d)
        self._t0 = float(d.time)

    # ------------------------------------------------------ state reads ---
    def jaw_width(self) -> float:
        a, b = self._finger_qa
        q = self._qpos
        return float(q[a] + q[b])

    def gripper_pose(self) -> Pose:
        b = self.index.gripper_body
        return Pose(pos=_pose_tuple(self.data.xpos[b]), quat=_pose_tuple(self.data.xquat[b]))

    def object_pose(self, obj_id: str) -> Pose:
        b = self.index.object_body[obj_id]
        return Pose(pos=_pose_tuple(self.data.xpos[b]), quat=_pose_tuple(self.data.xquat[b]))

    def sim_time(self) -> float:
        return float(self.data.time) - self._t0

    def _pair_info(self, a: int, b: int):
        """Classification of a geom pair, computed once per pair and cached:
        (records, object touching the left finger, object touching the right
        finger), or None for pairs that are not logged (beads)."""
        if self._is_bead_geom[a] or self._is_bead_geom[b]:
            return None
        oi, grip = self._oi_list, self._grip_list
        labels, oids = self.index.geom_label, self.index.object_ids
        fl, fr = self.index.finger_l_geom, self.index.finger_r_geom
        ia, ib = oi[a], oi[b]
        left = right = -1
        if ib >= 0:
            if a == fl: left = ib
            elif a == fr: right = ib
        if ia >= 0:
            if b == fl: left = ia
            elif b == fr: right = ia
        if ia >= 0 and ib >= 0:
            pairs = ((oids[ia], oids[ib]), (oids[ib], oids[ia]))
        elif ia >= 0:
            pairs = ((oids[ia], labels[b]),)
        elif ib >= 0:
            pairs = ((oids[ib], labels[a]),)
        elif grip[a] and not grip[b]:
            pairs = (("gripper", labels[b]),)
        elif grip[b] and not grip[a]:
            pairs = (("gripper", labels[a]),)
        else:
            pairs = ()
        return pairs, left, right

    def _contacts(self) -> tuple[int, list[tuple[str, str, float]]]:
        """(held object index or -1, contact records for this step).

        One record per (object, other) with the maximum penetration depth, in
        order of first occurrence. Geom-pair classification is cached, so the
        per-step cost is one dictionary lookup per active contact.
        """
        d = self.data
        n = d.ncon
        if n == 0:
            return -1, []
        c = d.contact
        g1 = c.geom1[:n].tolist()
        g2 = c.geom2[:n].tolist()
        dist = c.dist[:n].tolist()
        cache = self._pair_cache
        info_of = self._pair_info
        left, right = set(), set()
        out: dict[tuple[str, str], float] = {}
        get = out.get
        for a, b, dd in zip(g1, g2, dist):
            key = (a, b)
            try:
                info = cache[key]
            except KeyError:
                info = cache[key] = info_of(a, b)
            if info is None:
                continue
            pairs, l, r = info
            if l >= 0: left.add(l)
            if r >= 0: right.add(r)
            depth = -dd if dd < 0.0 else 0.0
            for pr in pairs:
                if depth >= get(pr, -1.0):
                    out[pr] = depth
        both = left & right
        held = min(both) if both else -1
        return held, [(o, x, dep) for (o, x), dep in out.items()]

    # --------------------------------------------------------- control ---
    def _apply_ctrl(self) -> None:
        """Write the current reference to the actuators (the only command path)."""
        ctrl = self._ctrl
        y, r, p = self._ref_angles
        ctrl[self._robot_act] = (self._ref_pos[0], self._ref_pos[1], self._ref_pos[2], y, r, p)
        # The jaw command moves toward its target at a bounded rate so that the
        # fingers meet an object slowly (no tunnelling through thin walls).
        step = JAW_SPEED * float(self.model.opt.timestep)
        self._jaw_cmd += min(max(self._jaw_target - self._jaw_cmd, -step), step)
        half = min(max(self._jaw_cmd / 2.0, FINGER_RANGE[0]), FINGER_RANGE[1])
        ctrl[self._finger_act] = half

    def outside_workspace(self, pos) -> list[str]:
        """Axes on which ``pos`` lies outside the robot's reachable box."""
        ws = self.scene.workspace
        eps = 1e-9
        out = []
        for name, v, (lo, hi) in zip("xyz", pos, (ws.x_range, ws.y_range, ws.z_range)):
            if v < lo - eps or v > hi + eps:
                out.append(f"{name}={float(v):.4f} not in [{lo:g}, {hi:g}]")
        return out

    def _set_reference(self, pos, quat) -> None:
        # Targets are never clamped: a target outside the reachable box stops
        # execution with a failure instead of being executed somewhere else.
        bad = self.outside_workspace(pos)
        if bad:
            raise _Failure("execution_failed", "target outside the workspace: " + ", ".join(bad))
        pos = np.array([pos[0], pos[1], pos[2]], dtype=float)
        quat = rot.qnormalize(quat)
        ang = rot.wrist_angles(quat, self._ref_angles)
        if not (YAW_RANGE[0] <= ang[0] <= YAW_RANGE[1] and ROLL_RANGE[0] <= ang[1] <= ROLL_RANGE[1]
                and PITCH_RANGE[0] <= ang[2] <= PITCH_RANGE[1]):
            raise _Failure("execution_failed",
                           f"orientation target outside the wrist joint limits "
                           f"(yaw, roll, pitch = {tuple(round(math.degrees(a), 1) for a in ang)} deg)")
        self._ref_pos, self._ref_quat, self._ref_angles = pos, quat, ang

    def _step_physics(self) -> None:
        mujoco.mj_step(self.model, self.data)

    def _tracking_error(self) -> tuple[float, float]:
        q = self._qpos[self._robot_q].tolist()
        rp = self._ref_pos
        y, r, p = self._ref_angles
        dx, dy, dz = q[0] - rp[0], q[1] - rp[1], q[2] - rp[2]
        return (math.sqrt(dx * dx + dy * dy + dz * dz),
                max(abs(q[3] - y), abs(q[4] - r), abs(q[5] - p)))

    # ------------------------------------------------------- execution ---
    def execute(self, plan: PlanResult, *, frame_hz: float = 30.0,
                max_sim_time: float = 120.0) -> ExecutionResult:
        """Execute ``plan`` from the current state and record every physics step."""
        ex = _Execution(self, frame_hz=frame_hz, max_sim_time=max_sim_time)
        return ex.run(plan)

    # -------------------------------------------------------- visuals ---
    def visual_scene(self) -> VisualScene:
        return visual_scene(self.model, self.index)


_GEOM_TYPES = {int(mujoco.mjtGeom.mjGEOM_BOX): "box", int(mujoco.mjtGeom.mjGEOM_SPHERE): "sphere",
               int(mujoco.mjtGeom.mjGEOM_CYLINDER): "cylinder", int(mujoco.mjtGeom.mjGEOM_CAPSULE): "capsule",
               int(mujoco.mjtGeom.mjGEOM_PLANE): "plane", int(mujoco.mjtGeom.mjGEOM_MESH): "mesh"}


def visual_scene(model: mujoco.MjModel, index: SceneIndex) -> VisualScene:
    """Every body with geoms, its geoms in the body frame, static or not."""
    bodies = []
    static = set(index.static_bodies) | {0}
    for b in range(model.nbody):
        n = int(model.body_geomnum[b])
        if n == 0:
            continue
        g0 = int(model.body_geomadr[b])
        geoms = []
        for g in range(g0, g0 + n):
            t = _GEOM_TYPES.get(int(model.geom_type[g]))
            if t is None:
                continue
            sz = model.geom_size[g]
            size = {"box": 3, "sphere": 1, "cylinder": 2, "capsule": 2, "plane": 3, "mesh": 3}[t]
            geoms.append(VisualGeom(type=t, size=tuple(float(x) for x in sz[:size]),
                                    pos=_pose_tuple(model.geom_pos[g]),
                                    quat=_pose_tuple(model.geom_quat[g]),
                                    rgba=_pose_tuple(model.geom_rgba[g]), name=model.geom(g).name))
        name = index.body_names[b] or "world"
        if b in static:
            # Static bodies are drawn at their compiled pose: fold it into the geoms.
            bp, bq = model.body_pos[b], model.body_quat[b]
            if b != 0:
                geoms = [VisualGeom(type=vg.type, size=vg.size,
                                    pos=_pose_tuple(np.asarray(bp) + rot.quat_to_mat(bq) @ np.asarray(vg.pos)),
                                    quat=_pose_tuple(rot.qmul(bq, vg.quat)), rgba=vg.rgba, name=vg.name)
                         for vg in geoms]
        bodies.append(VisualBody(name=name, static=b in static, geoms=geoms))
    return VisualScene(bodies=bodies)


class _Execution:
    """One run of a plan; holds the per-run bookkeeping."""

    def __init__(self, env: SimEnv, *, frame_hz: float, max_sim_time: float):
        self.env = env
        dt = float(env.model.opt.timestep)
        self.dt = dt
        self.max_sim_time = max_sim_time
        cap = int(min(max_sim_time, 60.0) / dt) + 10
        self.rec = _Recorder(env, cap, 1.0 / float(frame_hz))
        self.frame_hz = frame_hz
        self.phase = "start"
        self.expect_held: Optional[int] = None
        self.lost_since: Optional[float] = None
        self.track_bad_since: Optional[float] = None
        self.events: list[dict] = []
        self.held = -1
        self.t = 0.0

    # One physics step: command, step, record, check.
    def tick(self) -> None:
        env = self.env
        env._apply_ctrl()
        env._step_physics()
        self.t = env.sim_time()
        held, contacts = env._contacts()
        self.held = held
        self.rec.record(self.t, self.phase, held, contacts)
        if self.t > self.max_sim_time:
            raise _Failure("timeout", f"simulated time exceeded {self.max_sim_time:.0f} s")
        if self.expect_held is not None:
            if held != self.expect_held:
                if self.lost_since is None:
                    self.lost_since = self.t
                elif self.t - self.lost_since > DROP_GRACE:
                    oid = env.index.object_ids[self.expect_held]
                    raise _Failure("execution_failed",
                                   f"{oid} dropped during {self.phase.split(':')[0]} at t={self.lost_since:.2f} s")
            else:
                self.lost_since = None
        pe, ae = env._tracking_error()
        if pe > TRACK_POS_TOL or ae > TRACK_ANG_TOL:
            if self.track_bad_since is None:
                self.track_bad_since = self.t
            elif self.t - self.track_bad_since > TRACK_GRACE:
                movers = {"gripper"}
                if self.expect_held is not None:
                    movers.add(env.index.object_ids[self.expect_held])
                blockers = sorted({c[1] for c in contacts if c[0] in movers
                                   and c[1].startswith(("obstacle:", "tray:", "table"))})
                why = f"; in contact with {', '.join(blockers)}" if blockers else ""
                raise _Failure("execution_failed",
                               f"tracking error too large during {self.phase} "
                               f"(position {pe * 1000:.0f} mm, angle {math.degrees(ae):.0f} deg){why}")
        else:
            self.track_bad_since = None

    def wait(self, seconds: float) -> None:
        for _ in range(max(1, int(round(seconds / self.dt)))):
            self.tick()

    def move_to(self, target: Pose, max_speed: float) -> None:
        env = self.env
        bad = env.outside_workspace(target.pos)
        if bad:
            raise _Failure("execution_failed",
                           f"waypoint outside the workspace during {self.phase}: " + ", ".join(bad))
        p0, q0 = env._ref_pos.copy(), env._ref_quat.copy()
        p1, q1 = np.asarray(target.pos, dtype=float), rot.qnormalize(target.quat)
        if np.dot(q0, q1) < 0:
            q1 = -q1
        dist = float(np.linalg.norm(p1 - p0))
        ang = rot.qangle(q0, q1)
        v = min(max(max_speed, 1e-3), LINEAR_SPEED_MAX)
        # Trapezoidal velocity profile; the slower of the linear and angular
        # profiles sets the timing for both so they stay synchronised.
        T_lin, f_lin = rot.trapezoid_profile(dist, v, LINEAR_ACCEL_MAX)
        T_ang, f_ang = rot.trapezoid_profile(ang, ANGULAR_SPEED_MAX, ANGULAR_ACCEL_MAX)
        T, f = (T_lin, f_lin) if T_lin >= T_ang else (T_ang, f_ang)
        T = max(T, MIN_SEGMENT_TIME)
        n = max(1, int(math.ceil(T / self.dt)))
        for k in range(1, n + 1):
            s = rot.trapezoid(k / n, f)
            env._set_reference(p0 + s * (p1 - p0), rot.slerp(q0, q1, s))
            self.tick()

    def set_jaw(self, width: float, timeout: float = RELEASE_TIMEOUT) -> None:
        env = self.env
        width = min(max(width, 0.0), GRIPPER.max_opening)
        env._jaw_target = width
        t_end = self.t + timeout
        while self.t < t_end:
            self.tick()
            if abs(env.jaw_width() - width) < 0.002:
                break

    def grasp(self, step: PlanStep) -> None:
        env = self.env
        env._jaw_target = 0.0
        t0 = self.t
        hist: list[float] = []
        while True:
            self.tick()
            hist.append(env.jaw_width())
            w = int(0.05 / self.dt)
            if self.t - t0 > GRASP_MIN_TIME and len(hist) > w and abs(hist[-1] - hist[-1 - w]) < 2e-4:
                break
            if self.t - t0 > GRASP_TIMEOUT:
                break
        self.wait(GRASP_HOLD_TIME)
        want = step.object_id
        oids = env.index.object_ids
        if self.held < 0:
            raise _Failure("execution_failed",
                           f"grasp of {want} failed: no object between both fingers (jaw width {env.jaw_width() * 1000:.0f} mm)")
        got = oids[self.held]
        if want is not None and got != want:
            raise _Failure("execution_failed", f"grasp of {want} picked up {got} instead")
        self.expect_held = self.held
        self.lost_since = None
        self.events.append({"t": self.t, "type": "grasp", "object_id": got,
                            "width": env.jaw_width()})

    def release(self, step: PlanStep) -> None:
        env = self.env
        oid = env.index.object_ids[self.expect_held] if self.expect_held is not None else step.object_id
        self.expect_held = None
        self.lost_since = None
        self.set_jaw(step.gripper_width if step.gripper_width is not None else GRIPPER.max_opening)
        self.events.append({"t": self.t, "type": "release", "object_id": oid})

    def settle(self) -> None:
        self.phase = "settle"
        env = self.env
        need = int(SETTLE_WINDOW / self.dt)
        calm = 0
        t_end = self.t + SETTLE_MAX
        ids = [env.index.object_dofadr[o] for o in env.index.object_ids]
        while self.t < t_end:
            self.tick()
            v = max((float(np.linalg.norm(env.data.qvel[a:a + 3])) for a in ids), default=0.0)
            calm = calm + 1 if v < SETTLE_SPEED else 0
            if calm >= need:
                break

    def run(self, plan: PlanResult) -> ExecutionResult:
        env = self.env
        wall0 = time.perf_counter()
        status: Status = "completed"
        reason = ""
        if plan.status != "ok":
            self.phase = "idle"
            status, reason = "execution_failed", f"plan not executable (status={plan.status})"
            try:
                self.wait(0.1)
            except _Failure:
                pass
        else:
            try:
                for i, step in enumerate(plan.steps):
                    self.phase = f"{step.kind}:{step.object_id}" if step.object_id else step.kind
                    if step.gripper_width is not None and step.kind not in ("grasp", "release"):
                        self.set_jaw(step.gripper_width)
                    for wp in step.waypoints:
                        self.move_to(wp, step.max_speed)
                    if step.kind == "grasp":
                        self.grasp(step)
                    elif step.kind == "release":
                        self.release(step)
                self.settle()
            except _Failure as f:
                status, reason = f.status, f.reason
                self.events.append({"t": self.t, "type": "failure", "reason": f.reason})
        meta = {
            "scene_id": env.scene.scene_id, "spec_version": plan.spec_version,
            "planner": plan.planner, "status": status, "reason": reason,
            "events": self.events, "timestep": self.dt, "frame_hz": self.frame_hz,
            "deterministic": True,
            "executor": {"angular_speed_max": ANGULAR_SPEED_MAX, "linear_speed_max": LINEAR_SPEED_MAX,
                         "time_scaling": "trapezoidal velocity per segment", "linear_accel_max": LINEAR_ACCEL_MAX, "angular_accel_max": ANGULAR_ACCEL_MAX, "drop_grace_s": DROP_GRACE,
                         "track_pos_tol_m": TRACK_POS_TOL, "track_ang_tol_rad": TRACK_ANG_TOL},
            "contacts_note": "bead contacts are not recorded; one record per (step, object, other) with max depth",
        }
        log = self.rec.to_log(meta)
        return ExecutionResult(log=log, frames=self.rec.frames, status=status, reason=reason,
                               sim_time=self.t, wall_time=time.perf_counter() - wall0,
                               steps=self.rec.n, events=self.events)
