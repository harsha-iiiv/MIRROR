"""Arm motion validation: from a planner's TCP waypoints to a checked joint path.

``validate(plan, scene)`` walks every plan step and waypoint in the order the
executor will follow them, from the arm's start configuration, and for each
straight TCP segment (linear position, slerp orientation; the same path the
legacy executor commands) it

  1. samples the segment densely (<= 3 mm of TCP travel, <= 0.02 rad of TCP
     rotation per sample) and solves inverse kinematics at every sample,
     warm-started from the previous sample, so the joint path stays on one
     continuous branch;
  2. rejects the segment if IK fails, a joint range is exceeded, or the joint
     path jumps (a branch flip near a singularity);
  3. checks collisions of every arm link, with a safety margin, against the
     table, trays, walls, boxes, every object (the held one included) and the
     rest of the robot (self-collision), at joint-space sub-samples spaced so
     that no point of the arm moves more than about 8 mm between checks.

Object poses during validation follow the plan: objects rest where the scene
puts them, the held object moves rigidly with the TCP from its grasp to its
release, and stays at its release pose afterwards. Beads are not checked
(they move only with their cup and sit inside it).

The gripper itself (palm and fingers) is not re-checked here: the planner's
swept-volume check covers it, and its contact with the grasped object is by
design. The physics run covers everything regardless.

A failure is a planning failure of this backend (no executable joint path was
found for the given TCP path and start branch). It is never a proof that the
task is impossible for the arm.
"""
from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass, field
from typing import Optional

import mujoco
import numpy as np

from mirror.contracts import PlanResult, Pose, SceneState

from .kinematics import Kinematics, quat_to_mat
from .model import ArmIndex, Mount, DEFAULT_MOUNT, build_arm_model

SAMPLE_POS = 0.003          # m of TCP travel between IK samples
SAMPLE_ROT = 0.02           # rad of TCP rotation between IK samples
JUMP_MAX = 0.12             # rad: largest joint change allowed between neighbouring samples
COLLISION_DQ = 0.008        # rad: joint-space spacing of collision checks (~8 mm at 1 m)
ARM_MARGIN = 0.010          # m: clearance required between arm links and anything else
SELF_MARGIN = 0.002         # m: clearance required between non-adjacent robot links
TOOL_MARGIN = 0.003         # m: clearance of gripper and held object in free-space legs
N_BRANCHES = 4              # start configurations tried
N_SEEDS = 120               # random IK seeds for the start configurations


def _mat_to_quat(R: np.ndarray) -> np.ndarray:
    q = np.zeros(4)
    mujoco.mju_mat2Quat(q, np.asarray(R, dtype=float).reshape(9))
    return q


def _slerp(q0, q1, s):
    q0 = np.asarray(q0, float)
    q1 = np.asarray(q1, float)
    d = float(np.dot(q0, q1))
    if d < 0:
        q1, d = -q1, -d
    if d > 0.9995:
        q = q0 + s * (q1 - q0)
        return q / np.linalg.norm(q)
    th = math.acos(d)
    return (math.sin((1 - s) * th) * q0 + math.sin(s * th) * q1) / math.sin(th)


def _qangle(q0, q1) -> float:
    d = abs(float(np.dot(np.asarray(q0, float), np.asarray(q1, float))))
    return 2.0 * math.acos(min(1.0, d))


@dataclass
class Segment:
    """One straight TCP segment, i.e. one plan waypoint, as a joint path."""

    step_index: int
    step_kind: str
    object_id: Optional[str]
    waypoint_index: int
    target: Pose
    s: np.ndarray            # (n,) normalised path parameter, 0 .. 1
    q: np.ndarray            # (n, 6) joint configuration at each sample
    dist: float              # TCP travel, m
    ang: float               # TCP rotation, rad
    max_speed: float         # plan step speed cap, m/s

    def q_at(self, s: float) -> np.ndarray:
        s = min(1.0, max(0.0, s))
        i = int(np.searchsorted(self.s, s, side="right")) - 1
        i = min(max(i, 0), len(self.s) - 2) if len(self.s) > 1 else 0
        if len(self.s) == 1:
            return self.q[0].copy()
        s0, s1 = self.s[i], self.s[i + 1]
        w = 0.0 if s1 <= s0 else (s - s0) / (s1 - s0)
        return (1 - w) * self.q[i] + w * self.q[i + 1]

    def dq_ds_max(self) -> np.ndarray:
        """Per-joint peak |dq/ds| over the segment."""
        if len(self.s) < 2:
            return np.zeros(6)
        ds = np.diff(self.s)
        ds = np.where(ds > 0, ds, 1e-12)
        return np.max(np.abs(np.diff(self.q, axis=0)) / ds[:, None], axis=0)


@dataclass
class Failure:
    kind: str                # ik_failed | joint_limit | discontinuity | collision | self_collision | start
    step_index: int
    step_kind: str
    object_id: Optional[str]
    waypoint_index: int
    detail: str
    progress: int            # samples validated before the failure

    def text(self) -> str:
        obj = f" {self.object_id}" if self.object_id else ""
        return f"{self.step_kind}{obj} (step {self.step_index + 1}, waypoint {self.waypoint_index + 1}): {self.detail}"


@dataclass
class ArmTrajectory:
    ok: bool
    q_start: Optional[np.ndarray]
    segments: list[Segment] = field(default_factory=list)
    branch: int = -1
    failure: Optional[Failure] = None
    branch_failures: list[Failure] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def plan_key(plan: PlanResult, scene: SceneState, mount: Mount) -> str:
    h = hashlib.sha256()
    h.update(plan.model_dump_json(exclude={"planning_time_s"}).encode())
    h.update(scene.model_dump_json().encode())
    h.update(repr(mount).encode())
    return h.hexdigest()[:20]


class Validator:
    """Holds a validation copy of the arm scene model (margins added, beads
    excluded from collision) and checks plans against it."""

    def __init__(self, scene: SceneState, mount: Mount = DEFAULT_MOUNT):
        self.scene = scene
        self.mount = mount
        m, idx = build_arm_model(scene, mount)
        self.m, self.idx = m, idx
        arm = set(idx.arm_geoms)
        self.arm_geoms = arm
        self.robot_geoms = arm | set(idx.gripper_geoms)
        self.gripper_geoms = set(idx.gripper_geoms)
        self.obj_of_geom = {}
        for g in range(m.ngeom):
            own = idx.geom_owner[g]
            if own.startswith("bead:"):
                m.geom_contype[g] = 0
                m.geom_conaffinity[g] = 0
            if g in arm:
                m.geom_margin[g] = ARM_MARGIN
            elif g in self.gripper_geoms:
                m.geom_margin[g] = TOOL_MARGIN
            elif own in idx.object_ids:
                m.geom_margin[g] = TOOL_MARGIN
                self.obj_of_geom[g] = own
        self.static_geoms = [g for g in range(m.ngeom)
                             if idx.geom_owner[g] in ("table",) or idx.geom_owner[g].startswith(("tray:", "obstacle:"))]
        self.k = Kinematics(m, idx)
        self.d = self.k.d
        self.obj_qadr = {o: idx.object_qposadr[o] for o in idx.object_ids}
        # world object poses as the plan proceeds
        self.obj_pose = {o.id: (np.array(o.pose.pos, float), np.array(o.pose.quat, float))
                         for o in scene.objects}
        self.held: Optional[tuple[str, np.ndarray, np.ndarray]] = None   # (id, rel pos, rel quat)
        self.n_coll_checks = 0
        self.n_ik = 0
        self._start_cache: dict = {}

    # ----------------------------------------------------------- world ---
    def _place_objects(self, tcp_pos, tcp_quat) -> None:
        d = self.d
        for oid, (p, q) in self.obj_pose.items():
            if self.held is not None and self.held[0] == oid:
                rp, rq = self.held[1], self.held[2]
                R = quat_to_mat(tcp_quat)
                p = np.asarray(tcp_pos) + R @ rp
                qq = np.zeros(4)
                mujoco.mju_mulQuat(qq, np.asarray(tcp_quat, float), rq)
                q = qq
            a = self.obj_qadr[oid]
            d.qpos[a:a + 3] = p
            d.qpos[a + 3:a + 7] = q

    def _attach(self, oid: str, tcp_pos, tcp_quat) -> None:
        p, q = self.obj_pose[oid]
        R = quat_to_mat(tcp_quat)
        rp = R.T @ (p - np.asarray(tcp_pos))
        iq = np.zeros(4)
        mujoco.mju_negQuat(iq, np.asarray(tcp_quat, float))
        rq = np.zeros(4)
        mujoco.mju_mulQuat(rq, iq, q)
        self.held = (oid, rp, rq)

    def _detach(self, tcp_pos, tcp_quat) -> None:
        if self.held is None:
            return
        oid, rp, rq = self.held
        R = quat_to_mat(tcp_quat)
        p = np.asarray(tcp_pos) + R @ rp
        q = np.zeros(4)
        mujoco.mju_mulQuat(q, np.asarray(tcp_quat, float), rq)
        self.obj_pose[oid] = (p, q)
        self.held = None

    # ------------------------------------------------------- collision ---
    def collisions(self, q, tcp_pos=None, tcp_quat=None, tool: bool = False) -> list[tuple[str, str, str, float]]:
        """Contacts of arm links within the margin at configuration q:
        (kind, arm geom, other label, distance). With ``tool``, also contacts of
        the gripper and the held object with the world and the other objects
        (used on free-space legs, where nothing should touch)."""
        self.n_coll_checks += 1
        d, m, idx = self.d, self.m, self.idx
        self.k.set_q(q)
        if tcp_pos is None:
            p, R = self.k.fk(q)
            tcp_pos, tcp_quat = p, _mat_to_quat(R)
        self._place_objects(tcp_pos, tcp_quat)
        mujoco.mj_kinematics(m, d)
        mujoco.mj_collision(m, d)
        out = []
        for i in range(d.ncon):
            c = d.contact[i]
            g1, g2 = int(c.geom1), int(c.geom2)
            a1, a2 = g1 in self.arm_geoms, g2 in self.arm_geoms
            if not (a1 or a2):
                if tool:
                    hit = self._tool_contact(g1, g2, float(c.dist))
                    if hit:
                        out.append(hit)
                continue
            mine, other = (g1, g2) if a1 else (g2, g1)
            if other in self.robot_geoms:
                if float(c.dist) < SELF_MARGIN:
                    out.append(("self_collision", m.geom(mine).name, m.geom(other).name, float(c.dist)))
                continue
            out.append(("collision", m.geom(mine).name, idx.geom_label[other], float(c.dist)))
        return out

    def _tool_contact(self, g1: int, g2: int, dist: float):
        if dist >= TOOL_MARGIN:
            return None
        held = self.held[0] if self.held is not None else None
        def tool_side(g):
            return g in self.gripper_geoms or (held is not None and self.obj_of_geom.get(g) == held)
        t1, t2 = tool_side(g1), tool_side(g2)
        if t1 == t2:      # both tool (gripper-held contact) or neither
            return None
        mine, other = (g1, g2) if t1 else (g2, g1)
        if other in self.arm_geoms:
            return None
        return ("tool_collision", self.m.geom(mine).name, self.idx.geom_label[other], dist)

    # ------------------------------------------------------------- IK ---
    def clearance(self, q) -> float:
        """Smallest distance (m, capped at 0.2) between an arm link and the
        static world or an object, at configuration q with objects at rest."""
        d, m = self.d, self.m
        self.k.set_q(q)
        mujoco.mj_kinematics(m, d)
        best = 0.2
        ft = np.zeros(6)
        for g in self.arm_geoms:
            for o in self.static_geoms:
                dist = mujoco.mj_geomDistance(m, d, g, o, best, ft)
                best = min(best, dist)
        return float(best)

    def start_configs(self, pose: Pose) -> list[np.ndarray]:
        """Distinct collision-free IK solutions for ``pose``, best first: the
        largest clearance from the world (up to 0.2 m), then the best-conditioned
        Jacobian. Joints with a +/-2 pi range are wrapped into (-pi, pi]."""
        key = (tuple(round(float(x), 9) for x in pose.pos), tuple(round(float(x), 9) for x in pose.quat))
        if key in self._start_cache:
            return [q.copy() for q in self._start_cache[key]]
        R = quat_to_mat(pose.quat)
        rng = np.random.default_rng(12345)
        lo, hi = self.k.lo, self.k.hi
        seeds = [rng.uniform(np.maximum(lo, -math.pi), np.minimum(hi, math.pi)) for _ in range(N_SEEDS)]
        sols: list[np.ndarray] = []
        self.obj_pose = {o.id: (np.array(o.pose.pos, float), np.array(o.pose.quat, float))
                         for o in self.scene.objects}
        self.held = None
        for sd in seeds:
            r = self.k.ik(pose.pos, R, sd, max_iters=80)
            self.n_ik += 1
            if not r.ok:
                continue
            q = r.q.copy()
            for i in range(6):
                if hi[i] - lo[i] > 4 * math.pi - 1e-3:
                    q[i] = (q[i] + math.pi) % (2 * math.pi) - math.pi
            if any(np.max(np.abs(q - s)) < 0.15 for s in sols):
                continue
            if self.collisions(q):
                continue
            sols.append(q)
        scored = [(round(self.clearance(q), 3), round(self.k.manipulability(q), 3), i, q)
                  for i, q in enumerate(sols)]
        scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
        out = [t[3] for t in scored[:N_BRANCHES]]
        self._start_cache[key] = [q.copy() for q in out]
        return out

    # ------------------------------------------------------- segments ---
    def _segment(self, si, step, wi, q0, p0, quat0, target: Pose) -> tuple[Optional[Segment], Optional[Failure], int]:
        p1 = np.asarray(target.pos, float)
        quat1 = np.asarray(target.quat, float)
        quat1 = quat1 / np.linalg.norm(quat1)
        if float(np.dot(quat0, quat1)) < 0:
            quat1 = -quat1
        dist = float(np.linalg.norm(p1 - p0))
        ang = _qangle(quat0, quat1)
        n = max(1, int(math.ceil(max(dist / SAMPLE_POS, ang / SAMPLE_ROT))))
        ss = [0.0]
        qs = [q0.copy()]
        fail = lambda kind, detail, prog: Failure(kind, si, step.kind, step.object_id, wi, detail, prog)
        prev_q, prev_s = q0.copy(), 0.0
        k = 1
        progress = 0
        pending = [i / n for i in range(1, n + 1)]
        while pending:
            s = pending.pop(0)
            pos = p0 + s * (p1 - p0)
            qt = _slerp(quat0, quat1, s)
            r = self.k.ik(pos, quat_to_mat(qt), prev_q)
            self.n_ik += 1
            if not r.ok:
                return None, fail("ik_failed", f"no inverse-kinematics solution near the current branch at "
                                              f"TCP ({pos[0]:.3f}, {pos[1]:.3f}, {pos[2]:.3f}): {r.reason}, "
                                              f"residual {r.pos_err * 1000:.1f} mm / {math.degrees(r.rot_err):.1f} deg",
                                  progress), progress
            jump = float(np.max(np.abs(r.q - prev_q)))
            if jump > JUMP_MAX:
                if s - prev_s > 1e-4:
                    # refine: insert the midpoint and retry
                    pending.insert(0, s)
                    pending.insert(0, 0.5 * (prev_s + s))
                    continue
                return None, fail("discontinuity", f"joint path jumps {math.degrees(jump):.0f} deg for a "
                                                   f"negligible TCP motion (singular configuration or branch flip)",
                                  progress), progress
            if not self.k.within_limits(r.q):
                return None, fail("joint_limit", "joint range exceeded", progress), progress
            # collision sub-samples along the joint interpolation prev_q -> r.q
            m_sub = max(1, int(math.ceil(jump / COLLISION_DQ)))
            tool = step.kind in ("move", "transport")
            for j in range(1, m_sub + 1):
                w = j / m_sub
                qc = (1 - w) * prev_q + w * r.q
                hits = self.collisions(qc, tool=tool)
                if hits:
                    kind, mine, other, dist_c = hits[0]
                    link = mine.split("/")[1] if "/" in mine else mine
                    if kind == "collision":
                        what = (f"arm link {link} within {max(0.0, dist_c) * 1000:.0f} mm of {other} "
                                f"(margin {ARM_MARGIN * 1000:.0f} mm)")
                    elif kind == "tool_collision":
                        what = f"{mine} within {max(0.0, dist_c) * 1000:.0f} mm of {other}"
                        kind = "collision"
                    else:
                        what = f"self-collision: {link} touches {other}"
                    return None, fail(kind, what + f" at TCP ({pos[0]:.3f}, {pos[1]:.3f}, {pos[2]:.3f})",
                                      progress), progress
            ss.append(s)
            qs.append(r.q.copy())
            prev_q, prev_s = r.q.copy(), s
            progress += 1
            k += 1
        seg = Segment(si, step.kind, step.object_id, wi, target, np.array(ss), np.array(qs), dist, ang,
                      float(step.max_speed))
        return seg, None, progress

    def run_branch(self, plan: PlanResult, q_start: np.ndarray, start: Pose) -> tuple[list[Segment], Optional[Failure], int]:
        # reset the world
        self.obj_pose = {o.id: (np.array(o.pose.pos, float), np.array(o.pose.quat, float))
                         for o in self.scene.objects}
        self.held = None
        q = q_start.copy()
        p = np.asarray(start.pos, float)
        quat = np.asarray(start.quat, float) / np.linalg.norm(start.quat)
        segs: list[Segment] = []
        total = 0
        for si, step in enumerate(plan.steps):
            for wi, wp in enumerate(step.waypoints):
                seg, f, prog = self._segment(si, step, wi, q, p, quat, wp)
                total += prog
                if f is not None:
                    f.progress = total
                    return segs, f, total
                segs.append(seg)
                q = seg.q[-1].copy()
                p = np.asarray(wp.pos, float)
                quat = np.asarray(wp.quat, float) / np.linalg.norm(wp.quat)
            if step.kind == "grasp" and step.object_id:
                self._attach(step.object_id, p, quat)
            elif step.kind == "release":
                self._detach(p, quat)
        return segs, None, total


def flip_tool(plan: PlanResult, start: Pose) -> tuple[PlanResult, Pose]:
    """The same plan with every TCP orientation turned by pi about the tool
    axis. The jaw is symmetric under this turn (the two fingers swap), so the
    gripper occupies the same space and grasps the same way; only the arm's
    wrist ends up differently."""
    rz = np.array([0.0, 0.0, 0.0, 1.0])            # pi about local z
    def turn(qq):
        out = np.zeros(4)
        mujoco.mju_mulQuat(out, np.asarray(qq, float), rz)
        return tuple(float(x) for x in out)
    p2 = plan.model_copy(deep=True)
    for st in p2.steps:
        st.waypoints = [Pose(pos=w.pos, quat=turn(w.quat)) for w in st.waypoints]
    return p2, Pose(pos=start.pos, quat=turn(start.quat))


def validate(plan: PlanResult, scene: SceneState, mount: Mount = DEFAULT_MOUNT,
             validator: Optional[Validator] = None, flip: bool = False) -> ArmTrajectory:
    """Joint path for ``plan`` or the furthest-reaching failure over the start
    branches. With ``flip`` the tool frame is turned by pi (see flip_tool)."""
    t0 = time.perf_counter()
    v = validator or Validator(scene, mount)
    start = scene.gripper_start
    if flip:
        plan, start = flip_tool(plan, start)
    starts = v.start_configs(start)
    if not starts:
        f = Failure("start", -1, "start", None, 0,
                    "no collision-free inverse-kinematics solution for the gripper start pose", 0)
        return ArmTrajectory(False, None, failure=f, stats={"validation_time_s": time.perf_counter() - t0})
    best: Optional[Failure] = None
    fails = []
    for b, qs in enumerate(starts):
        segs, f, prog = v.run_branch(plan, qs, start)
        if f is None:
            return ArmTrajectory(True, qs, segs, b, None, fails, stats={"tool_flipped": flip,
                "validation_time_s": round(time.perf_counter() - t0, 3), "branches_tried": b + 1,
                "start_solutions": len(starts), "ik_solves": v.n_ik, "collision_checks": v.n_coll_checks,
                "samples": int(sum(len(s.s) for s in segs))})
        fails.append(f)
        if best is None or f.progress > best.progress:
            best = f
    return ArmTrajectory(False, None, failure=best, branch_failures=fails, stats={
        "validation_time_s": round(time.perf_counter() - t0, 3), "branches_tried": len(starts),
        "start_solutions": len(starts), "ik_solves": v.n_ik, "collision_checks": v.n_coll_checks})
