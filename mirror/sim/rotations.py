"""Quaternion helpers (w, x, y, z) and the gripper's joint-angle mapping.

The wrist realises R = Rz(yaw) @ Rx(roll) @ Ry(pitch). With roll limited to
+/-80 degrees the decomposition is unique up to 2*pi multiples of yaw and
pitch, which are chosen nearest to the previous command so that joint
targets stay continuous.
"""
from __future__ import annotations

import math

import numpy as np


def qnormalize(q) -> np.ndarray:
    q = np.asarray(q, dtype=float)
    n = math.sqrt(float(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3]))
    if n < 1e-12:
        return np.array([1.0, 0.0, 0.0, 0.0])
    return q / n


def qmul(a, b) -> np.ndarray:
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return np.array([w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
                     w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
                     w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
                     w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2])


def qconj(q) -> np.ndarray:
    return np.array([q[0], -q[1], -q[2], -q[3]], dtype=float)


def qangle(a, b) -> float:
    """Rotation angle (radians) between two orientations."""
    d = abs(float(np.dot(qnormalize(a), qnormalize(b))))
    return 2.0 * math.acos(min(1.0, d))


def slerp(a, b, s: float) -> np.ndarray:
    a, b = qnormalize(a), qnormalize(b)
    d = float(np.dot(a, b))
    if d < 0.0:
        b, d = -b, -d
    if d > 0.9995:
        return qnormalize(a + s * (b - a))
    th = math.acos(d)
    return (math.sin((1 - s) * th) * a + math.sin(s * th) * b) / math.sin(th)


def quat_to_mat(q) -> np.ndarray:
    w, x, y, z = qnormalize(q)
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                     [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                     [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])


def axis_angle_quat(axis, angle: float) -> np.ndarray:
    axis = np.asarray(axis, dtype=float)
    n = np.linalg.norm(axis)
    if n < 1e-12 or abs(angle) < 1e-12:
        return np.array([1.0, 0.0, 0.0, 0.0])
    axis = axis / n
    return np.concatenate([[math.cos(angle / 2)], math.sin(angle / 2) * axis])


def rotvec_quat(v) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    return axis_angle_quat(v, float(np.linalg.norm(v)))


def wrist_quat(yaw: float, roll: float, pitch: float) -> np.ndarray:
    """Orientation produced by the wrist joints."""
    return qmul(qmul(axis_angle_quat((0, 0, 1), yaw), axis_angle_quat((1, 0, 0), roll)),
                axis_angle_quat((0, 1, 0), pitch))


def _nearest(angle: float, ref: float) -> float:
    return angle + 2 * math.pi * round((ref - angle) / (2 * math.pi))


def wrist_angles(q, prev: tuple[float, float, float] | None = None) -> tuple[float, float, float]:
    """(yaw, roll, pitch) with wrist_quat(yaw, roll, pitch) == q.

    yaw and pitch are unwrapped to the values nearest ``prev`` (yaw, roll, pitch).
    """
    R = quat_to_mat(q)
    roll = math.asin(max(-1.0, min(1.0, R[2, 1])))
    if math.cos(roll) < 1e-6:  # singular; keep the previous yaw
        yaw = prev[0] if prev else 0.0
        pitch = prev[2] if prev else 0.0
        return yaw, roll, pitch
    pitch = math.atan2(-R[2, 0], R[2, 2])
    yaw = math.atan2(-R[0, 1], R[1, 1])
    if prev is not None:
        yaw = _nearest(yaw, prev[0])
        pitch = _nearest(pitch, prev[2])
    return yaw, roll, pitch


def smoothstep5(s: float) -> float:
    """Quintic time scaling with zero velocity and acceleration at both ends.
    Its peak rate is 1.875 times the average rate."""
    s = min(1.0, max(0.0, s))
    return s * s * s * (10.0 + s * (-15.0 + 6.0 * s))


SMOOTHSTEP_PEAK = 1.875


def trapezoid_profile(length: float, v_max: float, a_max: float) -> tuple[float, float]:
    """(duration, ramp fraction) of a trapezoidal velocity profile over ``length``."""
    if length <= 0.0:
        return 0.0, 0.5
    if length >= v_max * v_max / a_max:
        tr = v_max / a_max
        T = length / v_max + tr
    else:  # triangular
        tr = math.sqrt(length / a_max)
        T = 2.0 * tr
    return T, min(0.5, tr / T)


def trapezoid(s: float, f: float) -> float:
    """Normalised position along a trapezoidal profile at normalised time s,
    with acceleration over the first and last fraction f of the duration."""
    s = min(1.0, max(0.0, s))
    f = min(0.5, max(1e-6, f))
    vp = 1.0 / (1.0 - f)            # peak normalised speed
    a = vp / f
    if s < f:
        return 0.5 * a * s * s
    if s > 1.0 - f:
        r = 1.0 - s
        return 1.0 - 0.5 * a * r * r
    return 0.5 * a * f * f + vp * (s - f)
