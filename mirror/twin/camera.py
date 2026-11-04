"""Calibrated phone camera in simulator coordinates, and the MuJoCo camera that matches it.

Frames:
  table  : the capture's table frame (board origin, z up, metres); x_cam = R x_table + t (OpenCV camera).
  sim    : the simulator world; x_sim = Rz(yaw) x_table + (ox, oy, 0)  (the session/scene placement).

MuJoCo renders an ideal pinhole with a centred principal point and square pixels. We render a
pinhole image large enough to cover every ray of the phone image, then resample it through the
phone's real intrinsics and lens distortion (``remap``), so rendered pixels land where the phone
would have seen them.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np


def rot_z(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


@dataclass
class Placement:
    """Table frame -> simulator world (planar)."""
    origin_xy: tuple[float, float] = (0.0, 0.0)
    yaw_deg: float = 0.0
    source: str = "unknown"

    @property
    def Rz(self) -> np.ndarray:
        return rot_z(self.yaw_deg)

    @property
    def o(self) -> np.ndarray:
        return np.array([self.origin_xy[0], self.origin_xy[1], 0.0])

    def table_to_sim(self, X) -> np.ndarray:
        X = np.asarray(X, float).reshape(-1, 3)
        return X @ self.Rz.T + self.o

    def sim_to_table(self, X) -> np.ndarray:
        X = np.asarray(X, float).reshape(-1, 3)
        return (X - self.o) @ self.Rz


@dataclass
class _Pinhole:
    """Shared projection maths. R, t map SIM points to OpenCV camera coordinates."""
    K: np.ndarray
    dist: np.ndarray
    R: np.ndarray
    t: np.ndarray
    width: int
    height: int

    @property
    def centre(self) -> np.ndarray:
        return -self.R.T @ self.t

    def project(self, X_sim) -> np.ndarray:
        import cv2
        X = np.asarray(X_sim, float).reshape(-1, 3)
        rvec = cv2.Rodrigues(self.R)[0]
        uv, _ = cv2.projectPoints(X, rvec, self.t, self.K, self.dist)
        return uv.reshape(-1, 2)

    def depth(self, X_sim) -> np.ndarray:
        X = np.asarray(X_sim, float).reshape(-1, 3)
        return (X @ self.R.T + self.t)[:, 2]

    # ---- MuJoCo pinhole that covers this camera
    def _norm_grid(self) -> np.ndarray:
        """Undistorted normalised coordinates (x, y) of every output pixel centre, (H, W, 2)."""
        import cv2
        u, v = np.meshgrid(np.arange(self.width, dtype=np.float64), np.arange(self.height, dtype=np.float64))
        pts = np.stack([u.ravel(), v.ravel()], 1).reshape(-1, 1, 2)
        if np.any(self.dist):
            n = cv2.undistortPointsIter(pts, self.K, self.dist, None, None,
                                        (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 40, 1e-9))
        else:
            n = cv2.undistortPoints(pts, self.K, self.dist)
        n = n.reshape(self.height, self.width, 2)
        # The iterative inverse can diverge in the far corners of strong lenses: keep only pixels whose
        # undistorted ray re-distorts back onto the same pixel (0.25 px); the rest are marked invalid.
        if np.any(self.dist):
            X = np.concatenate([n.reshape(-1, 2), np.ones((n.shape[0] * n.shape[1], 1))], 1)
            back, _ = cv2.projectPoints(X, np.zeros(3), np.zeros(3), self.K, self.dist)
            err = np.linalg.norm(back.reshape(self.height, self.width, 2) - np.stack([u, v], -1), axis=-1)
            bad = ~np.isfinite(err) | (err > 0.25)
            n[bad] = np.nan
        return n

    def mujoco_view(self, sim_scale: float = 1.0, margin_px: int = 4) -> "MujocoView":
        """A centred pinhole (fovy, size, pose) whose image contains every ray of this camera, plus
        the remap tables that resample it into this camera's (distorted) image."""
        n = self._norm_grid()
        f = float(self.K[1, 1]) * sim_scale
        ax = float(np.nanmax(np.abs(n[..., 0])))
        ay = float(np.nanmax(np.abs(n[..., 1])))
        Wr = int(2 * math.ceil(f * ax + margin_px))
        Hr = int(2 * math.ceil(f * ay + margin_px))
        fovy = math.degrees(2 * math.atan((Hr / 2.0) / f))
        valid = np.isfinite(n[..., 0])
        map_x = np.where(valid, Wr / 2.0 + f * n[..., 0] - 0.5, -10.0).astype(np.float32)
        map_y = np.where(valid, Hr / 2.0 + f * n[..., 1] - 0.5, -10.0).astype(np.float32)
        C = self.centre
        x_axis = self.R[0]          # OpenCV x (right) in sim
        y_up = -self.R[1]           # MuJoCo camera y is up = -OpenCV y
        return MujocoView(pos=C, xaxis=x_axis, yaxis=y_up, fovy=fovy, width=Wr, height=Hr,
                          map_x=map_x, map_y=map_y, valid=valid)


@dataclass
class MujocoView:
    pos: np.ndarray
    xaxis: np.ndarray
    yaxis: np.ndarray
    fovy: float
    width: int
    height: int
    map_x: np.ndarray
    map_y: np.ndarray
    valid: Optional[np.ndarray] = None

    def camera_xml(self, name: str = "twin_phone") -> str:
        f = lambda v: " ".join(f"{float(x):.7g}" for x in v)
        return (f'<camera name="{name}" pos="{f(self.pos)}" xyaxes="{f(self.xaxis)} {f(self.yaxis)}" '
                f'fovy="{self.fovy:.7g}"/>')

    def resample(self, img: np.ndarray, interpolation: Optional[int] = None) -> np.ndarray:
        import cv2
        if interpolation is None:
            interpolation = cv2.INTER_LINEAR
        return cv2.remap(img, self.map_x, self.map_y, interpolation, borderMode=cv2.BORDER_CONSTANT, borderValue=0)


class PhoneCamera(_Pinhole):
    """The phone, from capture intrinsics + table pose, expressed against the simulator world."""

    @classmethod
    def from_capture(cls, K, dist, image_size, R_table, t_table, placement: Placement,
                     out_size: Optional[tuple[int, int]] = None) -> "PhoneCamera":
        K = np.asarray(K, float).copy()
        W0, H0 = int(image_size[0]), int(image_size[1])
        if out_size is not None and (out_size[0], out_size[1]) != (W0, H0):
            sx, sy = out_size[0] / W0, out_size[1] / H0
            if abs(sx - sy) > 1e-3:
                raise ValueError("non-uniform rescale changes the camera model")
            K[0, 0] *= sx; K[0, 2] = (K[0, 2] + 0.5) * sx - 0.5
            K[1, 1] *= sy; K[1, 2] = (K[1, 2] + 0.5) * sy - 0.5
            W0, H0 = int(out_size[0]), int(out_size[1])
        Rt = np.asarray(R_table, float)
        tt = np.asarray(t_table, float).reshape(3)
        # x_cam = Rt x_table + tt ;  x_table = Rz^T (x_sim - o)
        Rz, o = placement.Rz, placement.o
        R = Rt @ Rz.T
        t = tt - R @ o
        return cls(K=K, dist=np.asarray(dist, float).reshape(-1), R=R, t=t, width=W0, height=H0)


class VirtualCamera(_Pinhole):
    """A distortion-free camera placed by us (for the top-down table twin)."""

    @classmethod
    def top_down(cls, centre_xy, span_xy, px_per_m: float, height_m: float = 1.2,
                 up_xy: Sequence[float] = (0.0, 1.0)) -> "VirtualCamera":
        """Looks straight down at ``centre_xy`` (sim); ``up_xy`` is the sim direction drawn as image-up."""
        W = int(round(span_xy[0] * px_per_m / 2) * 2)
        H = int(round(span_xy[1] * px_per_m / 2) * 2)
        f = px_per_m * height_m
        K = np.array([[f, 0, W / 2.0 - 0.5], [0, f, H / 2.0 - 0.5], [0, 0, 1.0]])
        up = np.array([up_xy[0], up_xy[1], 0.0]); up /= np.linalg.norm(up)
        z_cam = np.array([0.0, 0.0, -1.0])           # looking down
        y_cam = -up                                   # OpenCV y points down the image
        x_cam = np.cross(y_cam, z_cam)
        R = np.stack([x_cam, y_cam, z_cam])
        C = np.array([centre_xy[0], centre_xy[1], height_m])
        return cls(K=K, dist=np.zeros(5), R=R, t=-R @ C, width=W, height=H)

    def plane_homography_from(self, other: _Pinhole, z: float = 0.0) -> np.ndarray:
        """3x3 H mapping THIS camera's pixels to ``other``'s UNDISTORTED pixels for points on the
        plane z (sim). Use with cv2.warpPerspective(src_undistorted, H, size, flags=WARP_INVERSE_MAP)."""
        def plane_to_px(cam):
            # [x y 1] on plane z -> pixel:  K [r1 r2 (r3 z + t)]
            M = np.column_stack([cam.R[:, 0], cam.R[:, 1], cam.R[:, 2] * z + cam.t])
            return cam.K @ M
        A = plane_to_px(other)
        B = plane_to_px(self)
        return A @ np.linalg.inv(B)
