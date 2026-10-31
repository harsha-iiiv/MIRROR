"""Small rigid-body helpers (quaternions are (w, x, y, z))."""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np


def quat_mul(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float, float]:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw)


def quat_conj(q: Sequence[float]) -> tuple[float, float, float, float]:
    w, x, y, z = q
    return (w, -x, -y, -z)


def quat_normalize(q: Sequence[float]) -> tuple[float, float, float, float]:
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    return tuple(c / n for c in q)  # type: ignore[return-value]


def rotate(q: Sequence[float], v: Sequence[float]) -> np.ndarray:
    w, x, y, z = quat_normalize(q)
    p = quat_mul(quat_mul((w, x, y, z), (0.0, *v)), (w, -x, -y, -z))
    return np.array(p[1:])


def tilt_deg(q: Sequence[float]) -> float:
    """Angle between the body's local +z axis and world +z, in degrees."""
    w, x, y, z = quat_normalize(q)
    zz = 1.0 - 2.0 * (x * x + y * y)
    return math.degrees(math.acos(max(-1.0, min(1.0, zz))))


def point_box_distance(p: np.ndarray, center: np.ndarray, quat: Sequence[float],
                       half: Sequence[float]) -> float:
    local = rotate(quat_conj(quat), p - center)
    d = np.maximum(np.abs(local) - np.asarray(half, dtype=float), 0.0)
    return float(np.linalg.norm(d))


def to_local(p: np.ndarray, center: Sequence[float], quat: Sequence[float]) -> np.ndarray:
    return rotate(quat_conj(quat), np.asarray(p, dtype=float) - np.asarray(center, dtype=float))
