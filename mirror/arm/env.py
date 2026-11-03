"""Physics execution of a validated arm trajectory.

``ArmEnv`` mirrors :class:`mirror.sim.SimEnv` (same scene physics, same jaw,
same grasp and drop detection, same per-step :class:`mirror.statelog.StateLog`
that the independent checker reads), but the gripper is carried by the arm:
the only commands are the arm's joint position servos and the jaw. Object
poses are never written during execution.

Every TCP segment of the plan is executed along the joint path that
``motion.validate`` checked, with a trapezoidal time scaling that respects the
legacy executor's TCP speed and acceleration limits and the arm's joint speed
and acceleration limits (whichever is slowest sets the segment time). Each
servo gets the reference position plus velocity feed-forward
(ctrl = q_ref + (kv / kp) * qd_ref).

Tracking is monitored in task space: if the measured TCP strays more than the
legacy executor's tolerances from the commanded TCP for longer than its grace
period (for example because a link is pressed against a wall), execution stops
with ``execution_failed``.
"""
from __future__ import annotations

import math
from typing import Optional

import mujoco
import numpy as np

from mirror.contracts import PlanResult, Pose, SceneState
from mirror.geometry import GRIPPER
from mirror.sim import rotations as rot
from mirror.sim.env import (ANGULAR_ACCEL_MAX, ANGULAR_SPEED_MAX, LINEAR_ACCEL_MAX, LINEAR_SPEED_MAX,
                            MIN_SEGMENT_TIME, RESET_SETTLE, ExecutionResult, SimEnv, _Execution, _Failure)
from mirror.sim.model import FINGER_RANGE

from .kinematics import Kinematics
from .model import ARM_JOINTS, DEFAULT_MOUNT, Mount, build_arm_model
from .motion import ArmTrajectory, Segment, Validator, flip_tool, validate

ARM_ACCEL_MAX = 3.0        # rad/s^2, per joint, used by the time scaling
JOINT_LOG_HZ = 10.0        # downsampled joint log stored in the step log's metadata


class ArmEnv(SimEnv):
    """MuJoCo environment for one scene with the arm backend."""

    backend_name = "arm"

    def __init__(self, scene: SceneState, *, mount: Mount = DEFAULT_MOUNT, backend=None, seed: int = 0):
        self.scene = scene
        self.seed = seed
        self.mount = mount
        self.backend = backend
        self.model, self.index = build_arm_model(scene, mount)
        self.data = mujoco.MjData(self.model)
        idx = self.index
        self._obj_index_of_geom = np.full(self.model.ngeom, -1, dtype=np.int32)
        self._is_bead_geom = np.zeros(self.model.ngeom, dtype=bool)
        self._is_gripper_geom = np.zeros(self.model.ngeom, dtype=bool)
        self._is_arm_geom = np.zeros(self.model.ngeom, dtype=bool)
        for g, owner in enumerate(idx.geom_owner):
            if owner in idx.object_ids:
                self._obj_index_of_geom[g] = idx.object_ids.index(owner)
            elif owner.startswith("bead:"):
                self._is_bead_geom[g] = True
            elif owner == "gripper":
                self._is_gripper_geom[g] = True
            elif owner == "arm":
                self._is_arm_geom[g] = True
        self._pair_cache: dict = {}
        d = self.data
        self._qpos, self._xpos, self._xquat, self._ctrl = d.qpos, d.xpos, d.xquat, d.ctrl
        self._oi_list = self._obj_index_of_geom.tolist()
        self._grip_list = self._is_gripper_geom.tolist()
        self._arm_list = self._is_arm_geom.tolist()
        self._robot_q = np.array(idx.arm_qposadr, dtype=int)
        self._finger_qa = tuple(idx.finger_qposadr)
        self._finger_q = np.array(idx.finger_qposadr, dtype=int)
        self._robot_act = np.array(idx.arm_actuators, dtype=int)
        self._finger_act = np.array(idx.finger_actuators, dtype=int)
        kp = self.model.actuator_gainprm[self._robot_act, 0]
        kv = -self.model.actuator_biasprm[self._robot_act, 2]
        self._ff = kv / kp
        self.kin = Kinematics(self.model, idx)
        self._q_ref = np.zeros(6)
        self._qd_ref = np.zeros(6)
        self._ref_pos = np.zeros(3)
        self._ref_quat = np.array([1.0, 0, 0, 0])
        self._q_start: Optional[np.ndarray] = None
        self._is_reset = False

    # ----------------------------------------------------------- reset ---
    def default_start(self) -> np.ndarray:
        if self._q_start is None:
            starts = Validator(self.scene, self.mount).start_configs(self.scene.gripper_start)
            if not starts:
                raise RuntimeError("no collision-free arm configuration for the gripper start pose")
            self._q_start = starts[0]
        return self._q_start

    def reset(self, q_start: Optional[np.ndarray] = None) -> None:
        """Restore the scene's initial state with the arm at ``q_start``
        (default: the best start configuration found by the validator)."""
        m, d, idx = self.model, self.data, self.index
        if q_start is not None:
            self._q_start = np.asarray(q_start, dtype=float).copy()
        q0 = self.default_start()
        mujoco.mj_resetData(m, d)
        d.qpos[self._robot_q] = q0
        for a in idx.finger_qposadr:
            d.qpos[a] = FINGER_RANGE[1]
        self._set_joint_ref(q0, np.zeros(6))
        self._jaw_target = GRIPPER.max_opening
        self._jaw_cmd = GRIPPER.max_opening
        self._apply_ctrl()
        mujoco.mj_forward(m, d)
        for _ in range(int(RESET_SETTLE / m.opt.timestep)):
            mujoco.mj_step(m, d)
        self._t0 = float(d.time)
        self._is_reset = True

    # ------------------------------------------------------ references ---
    def _set_joint_ref(self, q, qd) -> None:
        self._q_ref = np.asarray(q, dtype=float).copy()
        self._qd_ref = np.asarray(qd, dtype=float).copy()
        p, R = self.kin.fk(self._q_ref)
        self._ref_pos = p
        qq = np.zeros(4)
        mujoco.mju_mat2Quat(qq, R.reshape(9))
        self._ref_quat = qq

    def _apply_ctrl(self) -> None:
        ctrl = self._ctrl
        ctrl[self._robot_act] = self._q_ref + self._ff * self._qd_ref
        step = 0.10 * float(self.model.opt.timestep)   # same jaw rate limit as the legacy backend
        self._jaw_cmd += min(max(self._jaw_target - self._jaw_cmd, -step), step)
        half = min(max(self._jaw_cmd / 2.0, FINGER_RANGE[0]), FINGER_RANGE[1])
        ctrl[self._finger_act] = half

    def _tracking_error(self) -> tuple[float, float]:
        b = self.index.gripper_body
        pe = float(np.linalg.norm(self._xpos[b] - self._ref_pos))
        ae = rot.qangle(self._xquat[b], self._ref_quat)
        return pe, ae

    def _set_reference(self, pos, quat) -> None:   # pragma: no cover - not used by the arm executor
        raise _Failure("execution_failed", "the arm backend executes validated joint paths only")

    def arm_q(self) -> np.ndarray:
        return self._qpos[self._robot_q].copy()

    # -------------------------------------------------------- contacts ---
    def _pair_info(self, a: int, b: int):
        """As the legacy classification, plus arm-link contacts logged as
        ("arm", other) so that every robot contact is in the step log."""
        info = super()._pair_info(a, b)
        if info is None:
            return None
        pairs, left, right = info
        arm = self._arm_list
        if arm[a] or arm[b]:
            labels = self.index.geom_label
            other = b if arm[a] else a
            if not arm[other] and self._oi_list[other] < 0:
                pairs = (("arm", labels[other]),)
        return pairs, left, right

    # ------------------------------------------------------- execution ---
    def trajectory_for(self, plan: PlanResult) -> tuple[ArmTrajectory, bool]:
        if self.backend is not None:
            hit = self.backend.cached(plan, self.scene)
            if hit is not None:
                return hit
        v = Validator(self.scene, self.mount)
        for flip in (False, True):
            tr = validate(plan, self.scene, self.mount, validator=v, flip=flip)
            if tr.ok:
                return tr, flip
        return tr, False

    def execute(self, plan: PlanResult, *, frame_hz: float = 30.0,
                max_sim_time: float = 120.0) -> ExecutionResult:
        traj, flip = (None, False)
        if plan.status == "ok":
            traj, flip = self.trajectory_for(plan)
            if traj.ok:
                self.reset(traj.q_start)
        if not self._is_reset:
            self.reset()
        ex = _ArmExecution(self, traj.segments if (traj is not None and traj.ok) else None,
                           frame_hz=frame_hz, max_sim_time=max_sim_time)
        if plan.status == "ok" and not traj.ok:
            res = ex.run_idle(f"arm motion validation failed before execution: {traj.failure.text()}")
        else:
            res = ex.run(plan)
        res.log.meta["backend"] = self.backend_info(traj, flip)
        res.log.meta["arm_joint_log"] = ex.joint_log()
        return res

    def backend_info(self, traj: Optional[ArmTrajectory], flip: bool) -> dict:
        spec = self.mount.spec
        return {
            "name": "arm", "robot": spec.name, "model_source": spec.source,
            "mount": {"name": self.mount.name, "pos": list(self.mount.pos), "quat": list(self.mount.quat),
                      "tool_extension_m": self.mount.tool_extension},
            "gripper": "MIRROR parallel-jaw gripper (same geometry as the baseline backend) on the tool flange",
            "controller": {"type": "joint position servo (PD) with velocity feed-forward",
                           "kp": list(spec.kp), "force_limit": list(spec.force), "damping_ratio": 1.0,
                           "gravity_compensation": "arm and gripper links, through the actuators (payload not compensated)",
                           "joint_speed_max_rad_s": [round(v, 4) for v in spec.speed],
                           "joint_accel_max_rad_s2": ARM_ACCEL_MAX},
            "trajectory": None if traj is None else {
                "validated": bool(traj.ok), "start_branch": traj.branch, "tool_flipped": bool(flip),
                "q_start": None if traj.q_start is None else [round(float(x), 6) for x in traj.q_start],
                "segments": len(traj.segments), **{k: v for k, v in traj.stats.items()}},
        }


class _ArmExecution(_Execution):
    def __init__(self, env: ArmEnv, segments: Optional[list[Segment]], *, frame_hz: float, max_sim_time: float):
        super().__init__(env, frame_hz=frame_hz, max_sim_time=max_sim_time)
        self.segs = list(segments or [])
        self._log_every = max(1, int(round(1.0 / (JOINT_LOG_HZ * self.dt))))
        self._jl_t: list[float] = []
        self._jl_q: list[list[float]] = []
        self._jl_ref: list[list[float]] = []
        self._ticks = 0
        self.peak_track_mm = 0.0

    def tick(self) -> None:
        super().tick()
        self._ticks += 1
        env = self.env
        pe, _ = env._tracking_error()
        self.peak_track_mm = max(self.peak_track_mm, pe * 1000.0)
        if self._ticks % self._log_every == 0:
            self._jl_t.append(round(self.t, 4))
            self._jl_q.append([round(float(x), 5) for x in env.arm_q()])
            self._jl_ref.append([round(float(x), 5) for x in env._q_ref])

    def joint_log(self) -> dict:
        return {"hz": JOINT_LOG_HZ, "joints": list(ARM_JOINTS), "t": self._jl_t, "q": self._jl_q,
                "q_ref": self._jl_ref, "peak_tcp_tracking_error_mm": round(self.peak_track_mm, 3)}

    def move_to(self, target: Pose, max_speed: float) -> None:
        env = self.env
        if not self.segs:
            raise _Failure("execution_failed", "arm trajectory has fewer segments than the plan has waypoints")
        seg = self.segs.pop(0)
        if np.linalg.norm(np.asarray(seg.target.pos) - np.asarray(target.pos)) > 1e-6:
            raise _Failure("execution_failed", "arm trajectory does not match the plan waypoint")
        if np.max(np.abs(seg.q[0] - env._q_ref)) > 1e-6:
            raise _Failure("execution_failed", "arm trajectory is not continuous with the current reference")
        v = min(max(max_speed, 1e-3), LINEAR_SPEED_MAX)
        T_lin, f_lin = rot.trapezoid_profile(seg.dist, v, LINEAR_ACCEL_MAX)
        T_ang, f_ang = rot.trapezoid_profile(seg.ang, ANGULAR_SPEED_MAX, ANGULAR_ACCEL_MAX)
        dqds = seg.dq_ds_max()
        spd = np.asarray(env.mount.spec.speed, dtype=float)
        mask = dqds > 1e-9
        if mask.any():
            vmax_s = float(np.min(spd[mask] / dqds[mask]))
            amax_s = float(np.min(ARM_ACCEL_MAX / dqds[mask]))
            T_j, f_j = rot.trapezoid_profile(1.0, vmax_s, amax_s)
        else:
            T_j, f_j = 0.0, 0.5
        T, f = max((T_lin, f_lin), (T_ang, f_ang), (T_j, f_j), key=lambda t: t[0])
        T = max(T, MIN_SEGMENT_TIME)
        n = max(1, int(math.ceil(T / self.dt)))
        q_prev = env._q_ref.copy()
        for k in range(1, n + 1):
            s = rot.trapezoid(k / n, f)
            q = seg.q_at(s)
            env._set_joint_ref(q, (q - q_prev) / self.dt)
            q_prev = q
            self.tick()
        env._set_joint_ref(seg.q[-1], np.zeros(6))

    def run_idle(self, reason: str) -> ExecutionResult:
        import time
        wall0 = time.perf_counter()
        self.phase = "idle"
        try:
            self.wait(0.1)
        except _Failure:
            pass
        self.events.append({"t": self.t, "type": "failure", "reason": reason})
        meta = {"scene_id": self.env.scene.scene_id, "status": "execution_failed", "reason": reason,
                "events": self.events, "timestep": self.dt, "frame_hz": self.frame_hz, "deterministic": True}
        return ExecutionResult(log=self.rec.to_log(meta), frames=self.rec.frames, status="execution_failed",
                               reason=reason, sim_time=self.t, wall_time=time.perf_counter() - wall0,
                               steps=self.rec.n, events=self.events)
