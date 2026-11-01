"""Fixed demonstration cameras and their pinhole geometry.

A camera is a MuJoCo free camera (look-at point, distance, azimuth,
elevation, vertical field of view). ``camera_info`` gives the equivalent
OpenCV-style pinhole model: intrinsics K and the world-to-camera rotation R
and translation t (x right, y down, z forward), so a world point X maps to
pixels by K (R X + t).
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np
from pydantic import BaseModel

# "top": nearly overhead, the whole table in view (table x along the image width).
# "side": oblique, from the front-left at a shallow angle, so heights, wall
# faces (and their slots) and tilt about the world y axis are all visible.
CAMERAS = {
    "top": dict(lookat=(0.0, 0.0, 0.0), distance=1.30, azimuth=90.0, elevation=-86.0, fovy=45.0),
    "side": dict(lookat=(0.0, 0.0, 0.06), distance=1.75, azimuth=60.0, elevation=-20.0, fovy=45.0),
}


class CameraInfo(BaseModel):
    name: str
    width: int
    height: int
    fovy: float
    lookat: tuple[float, float, float]
    distance: float
    azimuth: float
    elevation: float
    K: list[list[float]]
    R: list[list[float]]
    t: list[float]
    position: tuple[float, float, float]


def camera_frame(azimuth: float, elevation: float):
    """MuJoCo free-camera axes: forward and up (right = forward x up)."""
    az, el = math.radians(azimuth), math.radians(elevation)
    fwd = np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)])
    up = np.array([-math.sin(el) * math.cos(az), -math.sin(el) * math.sin(az), math.cos(el)])
    return fwd, up


def camera_info(name: str, width: int, height: int, cfg: dict | None = None) -> CameraInfo:
    cfg = dict(CAMERAS[name] if cfg is None else cfg)
    fwd, up = camera_frame(cfg["azimuth"], cfg["elevation"])
    right = np.cross(fwd, up)
    pos = np.asarray(cfg["lookat"], dtype=float) - cfg["distance"] * fwd
    f = (height / 2.0) / math.tan(math.radians(cfg["fovy"]) / 2.0)
    K = np.array([[f, 0.0, width / 2.0], [0.0, f, height / 2.0], [0.0, 0.0, 1.0]])
    Rm = np.stack([right, -up, fwd])
    t = -Rm @ pos
    return CameraInfo(name=name, width=width, height=height, fovy=cfg["fovy"], lookat=tuple(cfg["lookat"]),
                      distance=cfg["distance"], azimuth=cfg["azimuth"], elevation=cfg["elevation"],
                      K=K.tolist(), R=Rm.tolist(), t=t.tolist(), position=tuple(pos.tolist()))


def project(cam: CameraInfo, pts: Sequence) -> np.ndarray:
    """World points (N, 3) -> pixel coordinates (N, 2)."""
    X = np.atleast_2d(np.asarray(pts, dtype=float))
    c = (np.asarray(cam.R) @ X.T).T + np.asarray(cam.t)
    uvw = (np.asarray(cam.K) @ c.T).T
    return uvw[:, :2] / uvw[:, 2:3]
