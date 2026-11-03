"""Rays, triangulation and table-plane intersection in the table frame."""
from __future__ import annotations

from typing import Sequence

import numpy as np


def pixel_rays(K, dist, R, t, px) -> tuple[np.ndarray, np.ndarray]:
    """Camera centre (table frame) and unit ray directions through pixels."""
    import cv2
    px = np.asarray(px, np.float64).reshape(-1, 1, 2)
    n = cv2.undistortPoints(px, np.asarray(K, float), np.asarray(dist, float)).reshape(-1, 2)
    R = np.asarray(R, float)
    t = np.asarray(t, float).reshape(3)
    d_cam = np.concatenate([n, np.ones((len(n), 1))], 1)
    d = d_cam @ R  # R^T applied to row vectors
    d /= np.linalg.norm(d, axis=1, keepdims=True)
    return -R.T @ t, d


def triangulate(origins: Sequence[np.ndarray], dirs: Sequence[np.ndarray]) -> tuple[np.ndarray, float]:
    """Least-squares point closest to all rays and the RMS ray distance (m)."""
    A = np.zeros((3, 3))
    b = np.zeros(3)
    for o, d in zip(origins, dirs):
        P = np.eye(3) - np.outer(d, d)
        A += P
        b += P @ o
    X = np.linalg.solve(A, b)
    res = [np.linalg.norm((np.eye(3) - np.outer(d, d)) @ (X - o)) for o, d in zip(origins, dirs)]
    return X, float(np.sqrt(np.mean(np.square(res))))


def ray_plane(origin, d, z: float) -> np.ndarray:
    if abs(d[2]) < 1e-9:
        return np.full(3, np.nan)
    s = (z - origin[2]) / d[2]
    return origin + s * d if s > 0 else np.full(3, np.nan)


def project(K, dist, R, t, X) -> np.ndarray:
    import cv2
    rvec = cv2.Rodrigues(np.asarray(R, float))[0]
    uv, _ = cv2.projectPoints(np.asarray(X, float).reshape(-1, 3), rvec, np.asarray(t, float), np.asarray(K, float),
                              np.asarray(dist, float))
    return uv.reshape(-1, 2)
