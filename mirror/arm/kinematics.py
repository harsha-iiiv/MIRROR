"""Forward and inverse kinematics of the arm, and collision queries.

Everything here works on a private ``MjData`` of the arm scene model, never on
the simulation's state.

Inverse kinematics: damped least squares on the six arm joints with an
adaptive damping factor, a per-iteration step limit and clamping to the joint
ranges. The task-space error is the TCP position error (m) and the rotation
vector of R_target R^T (rad). A solution is accepted only if both errors are
within tolerance; otherwise the solve reports failure (it never returns the
closest pose as if it were a solution).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import mujoco
import numpy as np

from .model import ArmIndex

IK_TOL_POS = 2e-4        # m
IK_TOL_ROT = 2e-3        # rad
IK_MAX_ITERS = 200
IK_STEP_MAX = 0.25       # rad per iteration, per joint
IK_LAMBDA_MIN = 1e-3
IK_LAMBDA_MAX = 0.08


def quat_to_mat(q) -> np.ndarray:
    w, x, y, z = (float(v) for v in q)
    n = math.sqrt(w * w + x * x + y * y + z * z)
    w, x, y, z = w / n, x / n, y / n, z / n
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                     [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                     [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])


def rotvec(R: np.ndarray) -> np.ndarray:
    """Rotation vector (axis * angle) of rotation matrix R."""
    c = max(-1.0, min(1.0, (np.trace(R) - 1.0) / 2.0))
    ang = math.acos(c)
    v = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
    s = math.sin(ang)
    if ang < 1e-9:
        return 0.5 * v
    if abs(math.pi - ang) < 1e-4:
        # near pi: axis from the symmetric part
        M = (R + np.eye(3)) / 2.0
        axis = np.sqrt(np.clip(np.diag(M), 0.0, None))
        k = int(np.argmax(axis))
        axis = M[:, k] / max(axis[k], 1e-12)
        axis /= np.linalg.norm(axis)
        return axis * ang
    return v * (ang / (2.0 * s))


@dataclass
class IKResult:
    ok: bool
    q: np.ndarray
    pos_err: float
    rot_err: float
    iters: int
    reason: str = ""


class Kinematics:
    """Kinematic queries on a private copy of the arm scene's state."""

    def __init__(self, model: mujoco.MjModel, index: ArmIndex):
        self.m = model
        self.idx = index
        self.d = mujoco.MjData(model)
        self.qa = np.array(index.arm_qposadr, dtype=int)
        self.jids = list(index.arm_joint_ids)
        self.lo = index.arm_range[:, 0].copy()
        self.hi = index.arm_range[:, 1].copy()
        self.gb = index.gripper_body

    # ------------------------------------------------------------- FK ---
    def set_q(self, q) -> None:
        self.d.qpos[self.qa] = q

    def fk(self, q) -> tuple[np.ndarray, np.ndarray]:
        self.set_q(q)
        mujoco.mj_kinematics(self.m, self.d)
        return self.d.xpos[self.gb].copy(), self.d.xmat[self.gb].reshape(3, 3).copy()

    def jacobian(self) -> np.ndarray:
        """6x6 geometric Jacobian of the TCP (after mj_kinematics)."""
        p = self.d.xpos[self.gb]
        a = self.d.xaxis[self.jids]
        r = p - self.d.xanchor[self.jids]
        J = np.empty((6, 6))
        J[0] = a[:, 1] * r[:, 2] - a[:, 2] * r[:, 1]
        J[1] = a[:, 2] * r[:, 0] - a[:, 0] * r[:, 2]
        J[2] = a[:, 0] * r[:, 1] - a[:, 1] * r[:, 0]
        J[3:] = a.T
        return J

    def within_limits(self, q, eps: float = 1e-9) -> bool:
        return bool(np.all(q >= self.lo - eps) and np.all(q <= self.hi + eps))

    # ------------------------------------------------------------- IK ---
    def ik(self, pos, R, q0, *, max_iters: int = IK_MAX_ITERS, tol_pos: float = IK_TOL_POS,
           tol_rot: float = IK_TOL_ROT) -> IKResult:
        q = np.clip(np.asarray(q0, dtype=float).copy(), self.lo, self.hi)
        pos = np.asarray(pos, dtype=float)
        R = np.asarray(R, dtype=float)
        lam = IK_LAMBDA_MIN
        pe = re = float("inf")
        for it in range(max_iters):
            p, Rc = self.fk(q)
            ep = pos - p
            er = rotvec(R @ Rc.T)
            pe, re = float(np.linalg.norm(ep)), float(np.linalg.norm(er))
            if pe <= tol_pos and re <= tol_rot:
                return IKResult(True, q, pe, re, it)
            J = self.jacobian()
            e = np.concatenate([ep, er])
            # damping grows near singularities (small smallest singular value)
            smin = float(np.linalg.svd(J, compute_uv=False)[-1])
            lam = IK_LAMBDA_MIN if smin > 0.05 else min(IK_LAMBDA_MAX, IK_LAMBDA_MIN + (0.05 - smin) * 2.0)
            dq = J.T @ np.linalg.solve(J @ J.T + (lam ** 2) * np.eye(6), e)
            mx = float(np.max(np.abs(dq)))
            if mx > IK_STEP_MAX:
                dq *= IK_STEP_MAX / mx
            q = np.clip(q + dq, self.lo, self.hi)
        reason = "IK did not converge"
        at = [i for i in range(6) if q[i] <= self.lo[i] + 1e-6 or q[i] >= self.hi[i] - 1e-6]
        if at:
            reason += f" (joint limit reached on joint {', '.join(str(i + 1) for i in at)})"
        return IKResult(False, q, pe, re, max_iters, reason)

    def manipulability(self, q) -> float:
        self.fk(q)
        return float(np.linalg.svd(self.jacobian(), compute_uv=False)[-1])
