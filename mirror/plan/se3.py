"""Small rotation helpers. Quaternions are (w, x, y, z) as everywhere in MIRROR."""
from __future__ import annotations

import math

import numpy as np


def quat_to_mat(q) -> np.ndarray:
    w, x, y, z = (float(v) for v in q)
    n = math.sqrt(w * w + x * x + y * y + z * z) or 1.0
    w, x, y, z = w / n, x / n, y / n, z / n
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def mat_to_quat(m: np.ndarray) -> tuple[float, float, float, float]:
    m = np.asarray(m, dtype=float)
    tr = m[0, 0] + m[1, 1] + m[2, 2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        w, x, y, z = 0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        w, x, y, z = (m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        w, x, y, z = (m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        w, x, y, z = (m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s
    q = np.array([w, x, y, z])
    q /= np.linalg.norm(q)
    if q[0] < 0:
        q = -q
    return tuple(float(round(v, 12)) for v in q)


def rot_z(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def rot_y(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def rot_x(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


def gripper_rot(yaw: float, pitch: float = 0.0) -> np.ndarray:
    """Gripper orientation R = Rz(yaw) Ry(pitch). Pitch turns the gripper about
    its own y axis, which is the finger closing axis."""
    return rot_z(yaw) @ rot_y(pitch)


def yaw_of(q) -> float:
    m = quat_to_mat(q)
    return math.atan2(m[1, 0], m[0, 0])


def tilt_deg_of(q) -> float:
    m = quat_to_mat(q)
    return math.degrees(math.acos(max(-1.0, min(1.0, m[2, 2]))))


def wrap(a: float) -> float:
    """Wrap an angle to (-pi, pi]."""
    a = math.fmod(a + math.pi, 2 * math.pi)
    if a <= 0:
        a += 2 * math.pi
    return a - math.pi


def angle_between_mats(a: np.ndarray, b: np.ndarray) -> float:
    r = a.T @ b
    c = (np.trace(r) - 1.0) / 2.0
    return math.acos(max(-1.0, min(1.0, c)))


def slerp_mats(a: np.ndarray, b: np.ndarray, ts: np.ndarray) -> np.ndarray:
    """Rotation matrices along the geodesic from a to b at fractions ts. (n,3,3)"""
    ts = np.asarray(ts, dtype=float)
    r = a.T @ b
    ang = angle_between_mats(a, b)
    if ang < 1e-9:
        return np.repeat(a[None], len(ts), axis=0)
    if abs(ang - math.pi) < 1e-6:
        # axis from the symmetric part
        m = (r + np.eye(3)) / 2.0
        axis = m[np.argmax(np.diag(m))]
        axis = axis / np.linalg.norm(axis)
    else:
        axis = np.array([r[2, 1] - r[1, 2], r[0, 2] - r[2, 0], r[1, 0] - r[0, 1]]) / (2 * math.sin(ang))
    kx = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    th = ts * ang
    s, c = np.sin(th)[:, None, None], np.cos(th)[:, None, None]
    rots = np.eye(3)[None] + s * kx[None] + (1 - c) * (kx @ kx)[None]
    return a[None] @ rots
