"""Calibrated real-camera workflow for the supported kit objects.

Real footage is reconstructed only from what was measured for that rig:

1. Intrinsics, per camera: a printed checkerboard filmed from at least six
   different angles (``calibrate_intrinsics``; Zhang's method with two radial
   distortion terms), or values the person enters from the camera maker
   (``source="user_supplied"``). The rendered demonstration cameras are never
   used for footage.
2. Pose, per camera: the same board lying flat on the table, filmed by every
   camera without moving it (``board_pose``). The board defines the table plane;
   the person measures where the board's first inner corner and its x axis sit
   in the robot workspace (``BoardPlacement``), so no table size is assumed.
3. Correspondence review: tracking labels are only proposals. The person
   confirms which track is the cup, block, marker, tray and wall
   (``Correspondence``); unconfirmed tracks are refused, not guessed.
4. Kit confirmation: the objects must be the supported kit (or measured and
   within tolerance of it), because the simulator uses the kit's geometry.

``RigCalibration.require_ready`` lists everything still missing; the pipeline
refuses footage until it is empty. Every confirmation is recorded in the
trace's evidence as a manual correction. See docs/CAMERA_CAPTURE.md.

Corner detection uses OpenCV when installed (optional extra ``camera``); the
calibration maths here is NumPy/SciPy only, and corners may also come from a
manual review (``corners_from_outer``).
"""
from __future__ import annotations

import math
import time
from typing import Literal, Optional, Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from mirror.geometry import BLOCK, CUP, MARKER, TRAY

KIT_TOLERANCE = 0.15          # measured kit dimensions may differ from the catalogue by this fraction
MAX_INTRINSIC_RMS_PX = 1.0
MAX_POSE_RMS_PX = 1.5
MIN_VIEWS = 6
MIN_COVERAGE = 0.25           # union of board outlines over the image area, for intrinsics


class CalibrationRequired(ValueError):
    """Something the real-camera workflow needs is missing or invalid."""

    def __init__(self, missing: Sequence[str]):
        self.missing = list(missing)
        super().__init__("camera footage cannot be reconstructed yet: " + "; ".join(self.missing))


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BoardSpec(_Model):
    cols: int                     # inner corners along the board's x
    rows: int                     # inner corners along the board's y
    square_m: float               # measured edge of one printed square

    def points(self) -> np.ndarray:
        """Inner corners in the board frame (z = 0), row-major from the first corner."""
        xs, ys = np.meshgrid(np.arange(self.cols), np.arange(self.rows))
        return np.stack([xs.ravel() * self.square_m, ys.ravel() * self.square_m, np.zeros(xs.size)], 1)


DEFAULT_BOARD_PATTERN = (9, 6)      # inner corners of a 10 x 7-square board; measure the printed square


class CameraIntrinsics(_Model):
    K: list[list[float]]
    dist: list[float] = Field(default_factory=lambda: [0.0, 0.0])     # k1, k2 (radial)
    image_size: tuple[int, int]                                        # (width, height)
    source: Literal["checkerboard", "user_supplied"]
    rms_px: Optional[float] = None
    n_views: int = 0
    coverage: Optional[float] = None

    def problems(self) -> list[str]:
        out = []
        if self.source == "checkerboard":
            if self.n_views < MIN_VIEWS:
                out.append(f"intrinsics from {self.n_views} board views; at least {MIN_VIEWS} are needed")
            if self.rms_px is None or self.rms_px > MAX_INTRINSIC_RMS_PX:
                out.append(f"intrinsic calibration error {self.rms_px} px exceeds {MAX_INTRINSIC_RMS_PX} px")
            if self.coverage is not None and self.coverage < MIN_COVERAGE:
                out.append(f"board views cover {100 * self.coverage:.0f}% of the image; move it into the corners")
        fx, fy = self.K[0][0], self.K[1][1]
        w, h = self.image_size
        if not (0.3 * w < fx < 5 * w and 0.3 * w < fy < 5 * w):
            out.append("focal length implausible for this image size")
        return out


class BoardPose(_Model):
    """Board on the table seen by one camera: world(board) -> camera."""
    R: list[list[float]]
    t: list[float]
    rms_px: float


class BoardPlacement(_Model):
    """Where the board's first inner corner and x axis lie in the robot
    workspace frame (metres, radians), as measured by the person."""
    x: float
    y: float
    yaw: float = 0.0
    measured_by_user: bool = False


ObjectRole = Literal["cup", "block", "marker", "tray", "wall", "ignore"]


class Correspondence(_Model):
    track: str                         # tracking label as produced by the tracker
    role: ObjectRole
    confirmed_by_user: bool = False
    note: str = ""


class KitConfirmation(_Model):
    confirmed_by_user: bool = False
    measured: dict[str, list[float]] = Field(default_factory=dict)   # kind -> measured full dimensions (m)


class CameraCalibration(_Model):
    name: str
    intrinsics: CameraIntrinsics
    board_pose: Optional[BoardPose] = None

    @property
    def K(self) -> np.ndarray:
        return np.asarray(self.intrinsics.K, float)


KIT_DIMS = {"cup": [2 * CUP.outer_half[0], 2 * CUP.outer_half[1], 2 * CUP.outer_half[2]],
            "block": [2 * v for v in BLOCK.half],
            "marker": [2 * MARKER.radius, 2 * MARKER.radius, 2 * MARKER.half_length],
            "tray": [2 * (TRAY.inner_half[0] + TRAY.wall_thickness), 2 * (TRAY.inner_half[1] + TRAY.wall_thickness),
                     TRAY.wall_height]}


class RigCalibration(_Model):
    cameras: dict[str, CameraCalibration]
    board: BoardSpec
    placement: Optional[BoardPlacement] = None
    correspondence: list[Correspondence] = Field(default_factory=list)
    kit: KitConfirmation = Field(default_factory=KitConfirmation)
    undistorted: bool = False          # videos were undistorted with these intrinsics before tracking

    # ---------------------------------------------------------- readiness
    def problems(self) -> list[str]:
        out = []
        if "top" not in self.cameras:
            out.append("a top camera calibration is required")
        for name, c in self.cameras.items():
            out += [f"{name}: {p}" for p in c.intrinsics.problems()]
            if c.board_pose is None:
                out.append(f"{name}: no board pose (film the board lying on the table with every camera)")
            elif c.board_pose.rms_px > MAX_POSE_RMS_PX:
                out.append(f"{name}: board pose error {c.board_pose.rms_px:.2f} px exceeds {MAX_POSE_RMS_PX} px")
            if not self.undistorted and max(abs(d) for d in c.intrinsics.dist) > 1e-3:
                out.append(f"{name}: lens distortion is not negligible; undistort the videos first "
                           "(undistort_frame) and mark the rig undistorted")
        if self.placement is None or not self.placement.measured_by_user:
            out.append("board placement in the robot workspace not measured and confirmed")
        roles = [c for c in self.correspondence if c.role != "ignore"]
        if not roles:
            out.append("no object correspondences: confirm which tracked object is the cup, block, marker and tray")
        for c in self.correspondence:
            if not c.confirmed_by_user:
                out.append(f"track '{c.track}' not confirmed by a person")
        counts = {}
        for c in roles:
            counts[c.role] = counts.get(c.role, 0) + 1
        for r_, k in counts.items():
            if k > 1 and r_ in ("tray", "wall"):
                out.append(f"{k} tracks assigned to {r_}; only one is supported")
        if not self.kit.confirmed_by_user:
            out.append("not confirmed that the objects are the supported kit (sizes from the kit catalogue)")
        for kind, dims in self.kit.measured.items():
            ref = KIT_DIMS.get(kind)
            if ref is None or len(dims) != len(ref):
                out.append(f"measured {kind}: unsupported object or wrong number of dimensions")
                continue
            if any(abs(d - r) > KIT_TOLERANCE * r for d, r in zip(sorted(dims), sorted(ref))):
                out.append(f"measured {kind} {dims} differs from the kit {ref} by more than "
                           f"{100 * KIT_TOLERANCE:.0f}%; only kit objects are supported")
        return out

    def require_ready(self) -> None:
        p = self.problems()
        if p:
            raise CalibrationRequired(p)

    # ---------------------------------------------------------- use
    def intrinsics_source(self) -> str:
        srcs = {c.intrinsics.source for c in self.cameras.values()}
        return "checkerboard" if srcs == {"checkerboard"} else "user_supplied"

    def _workspace_from_board(self) -> tuple[np.ndarray, np.ndarray]:
        p = self.placement
        c, s = math.cos(p.yaw), math.sin(p.yaw)
        Rwb = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])
        return Rwb, np.array([p.x, p.y, 0.0])

    def calibs(self) -> dict:
        """camera -> reconstruct.Calib in the robot workspace frame."""
        from mirror.perception.reconstruct import Calib
        Rwb, twb = self._workspace_from_board()
        out = {}
        for name, c in self.cameras.items():
            Rcb = np.asarray(c.board_pose.R, float)
            tcb = np.asarray(c.board_pose.t, float)
            # x_cam = Rcb x_board + tcb ; x_board = Rwb^T (x_ws - twb)
            R = Rcb @ Rwb.T
            t = tcb - R @ twb
            out[name] = Calib(K=c.K, R=R, t=t, corners_px=np.zeros((0, 2)), residual=float(c.board_pose.rms_px))
        return out

    def apply_correspondence(self, tracks):
        """Relabel tracks with the confirmed roles (cup_0, block_0, marker_0, tray_0,
        wall_0); unassigned and ignored tracks are dropped."""
        self.require_ready()
        n_by = {}
        mapping = {}
        for c in self.correspondence:
            if c.role == "ignore":
                continue
            k = n_by.get(c.role, 0)
            n_by[c.role] = k + 1
            mapping[c.track] = f"{c.role}_{k}"
        keep_extra = {"gripper", "beads"}
        new = {}
        for cam, labs in tracks.masks.items():
            new[cam] = {}
            for lab, frames in labs.items():
                if lab in mapping:
                    new[cam][mapping[lab]] = frames
                elif lab in keep_extra:
                    new[cam][lab] = frames
        tracks.masks = new
        tracks.static = {mapping.get(l, l) for l in tracks.static if l in mapping or l in keep_extra}
        tracks.notes.append("labels from the person's correspondence review")
        return tracks

    def to_workspace(self, scene):
        return scene.model_copy(update={"notes": (scene.notes + "; " if scene.notes else "")
                                        + "positions in the robot workspace from a board on the table"})

    def manual_corrections(self) -> list:
        from mirror.observability import ManualCorrection
        now = time.time()
        out = [ManualCorrection(what="identity", detail={"track": c.track, "role": c.role},
                                note="confirmed in correspondence review", t_unix=now)
               for c in self.correspondence if c.confirmed_by_user]
        if self.placement is not None:
            out.append(ManualCorrection(what="calibration", detail=self.placement.model_dump(),
                                        note="board placement measured by the person", t_unix=now))
        out.append(ManualCorrection(what="scene", detail={"kit_confirmed": self.kit.confirmed_by_user,
                                                          "measured": self.kit.measured},
                                    note="kit objects confirmed", t_unix=now))
        return out


# ------------------------------------------------------------------ maths ---


def _normalise(p):
    p = np.asarray(p, float)
    c = p.mean(0)
    s = math.sqrt(2) / max(1e-12, np.mean(np.linalg.norm(p - c, axis=1)))
    T = np.array([[s, 0, -s * c[0]], [0, s, -s * c[1]], [0, 0, 1]])
    return T


def homography(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """DLT with Hartley normalisation; src, dst (N, 2)."""
    Ts, Td = _normalise(src), _normalise(dst)
    sh = (Ts @ np.c_[src, np.ones(len(src))].T).T
    dh = (Td @ np.c_[dst, np.ones(len(dst))].T).T
    A = []
    for (x, y, _), (u, v, _) in zip(sh, dh):
        A.append([-x, -y, -1, 0, 0, 0, u * x, u * y, u])
        A.append([0, 0, 0, -x, -y, -1, v * x, v * y, v])
    _, _, Vt = np.linalg.svd(np.asarray(A))
    H = Vt[-1].reshape(3, 3)
    H = np.linalg.inv(Td) @ H @ Ts
    return H / H[2, 2]


def _rodrigues(w):
    th = np.linalg.norm(w)
    if th < 1e-12:
        return np.eye(3)
    k = w / th
    Kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(th) * Kx + (1 - math.cos(th)) * Kx @ Kx


def _rotvec(R):
    ang = math.acos(max(-1.0, min(1.0, (np.trace(R) - 1) / 2)))
    if ang < 1e-9:
        return np.zeros(3)
    return ang / (2 * math.sin(ang)) * np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])


def project(K, dist, R, t, X):
    Xc = (R @ np.asarray(X, float).T).T + t
    x = Xc[:, :2] / Xc[:, 2:3]
    r2 = (x ** 2).sum(1, keepdims=True)
    k1, k2 = (list(dist) + [0.0, 0.0])[:2]
    x = x * (1 + k1 * r2 + k2 * r2 ** 2)
    return (np.asarray(K)[:2, :2] @ x.T).T + np.asarray(K)[:2, 2]


def _pose_from_h(K, H):
    B = np.linalg.solve(K, H)
    lam = 2.0 / (np.linalg.norm(B[:, 0]) + np.linalg.norm(B[:, 1]))
    r1, r2, t = B[:, 0] * lam, B[:, 1] * lam, B[:, 2] * lam
    if t[2] < 0:
        r1, r2, t = -r1, -r2, -t
    R = np.stack([r1, r2, np.cross(r1, r2)], 1)
    U, _, Vt = np.linalg.svd(R)
    return U @ Vt, t


def calibrate_intrinsics(views: Sequence[np.ndarray], board: BoardSpec, image_size: tuple[int, int]) -> CameraIntrinsics:
    """Zhang's method from detected inner corners (one (cols*rows, 2) array per view)."""
    from scipy.optimize import least_squares
    obj = board.points()
    Hs = [homography(obj[:, :2], np.asarray(v, float)) for v in views]
    if len(Hs) < 3:
        raise CalibrationRequired([f"{len(Hs)} board views; at least {MIN_VIEWS} are needed"])

    def vij(H, i, j):
        h = H.T
        return np.array([h[i, 0] * h[j, 0], h[i, 0] * h[j, 1] + h[i, 1] * h[j, 0], h[i, 1] * h[j, 1],
                         h[i, 2] * h[j, 0] + h[i, 0] * h[j, 2], h[i, 2] * h[j, 1] + h[i, 1] * h[j, 2],
                         h[i, 2] * h[j, 2]])
    V = []
    for H in Hs:
        V.append(vij(H, 0, 1))
        V.append(vij(H, 0, 0) - vij(H, 1, 1))
    V.append([0, 1, 0, 0, 0, 0])               # zero skew
    _, _, Vt = np.linalg.svd(np.asarray(V))
    b = Vt[-1]
    B11, B12, B22, B13, B23, B33 = b
    if B11 < 0:
        B11, B12, B22, B13, B23, B33 = -b
    v0 = (B12 * B13 - B11 * B23) / (B11 * B22 - B12 ** 2)
    lam = B33 - (B13 ** 2 + v0 * (B12 * B13 - B11 * B23)) / B11
    fx = math.sqrt(abs(lam / B11))
    fy = math.sqrt(abs(lam * B11 / (B11 * B22 - B12 ** 2)))
    u0 = -B13 * fx ** 2 / lam
    K0 = np.array([[fx, 0, u0], [0, fy, v0], [0, 0, 1.0]])
    poses = [_pose_from_h(K0, H) for H in Hs]
    x0 = [fx, fy, u0, v0, 0.0, 0.0]
    for R, t in poses:
        x0 += list(_rotvec(R)) + list(t)

    def resid(x):
        K = np.array([[x[0], 0, x[2]], [0, x[1], x[3]], [0, 0, 1.0]])
        out = []
        for k, v in enumerate(views):
            p = x[6 + 6 * k: 12 + 6 * k]
            out.append((project(K, x[4:6], _rodrigues(p[:3]), p[3:], obj) - v).ravel())
        return np.concatenate(out)
    sol = least_squares(resid, np.asarray(x0, float), method="lm", max_nfev=2000)
    x = sol.x
    rms = float(np.sqrt(np.mean(sol.fun.reshape(-1, 2) ** 2) * 2))
    K = [[float(x[0]), 0.0, float(x[2])], [0.0, float(x[1]), float(x[3])], [0.0, 0.0, 1.0]]
    return CameraIntrinsics(K=K, dist=[float(x[4]), float(x[5])], image_size=tuple(image_size),
                            source="checkerboard", rms_px=round(rms, 4), n_views=len(views),
                            coverage=round(board_coverage(views, image_size), 3))


def board_coverage(views, image_size) -> float:
    from PIL import Image, ImageDraw
    from scipy.spatial import ConvexHull
    w, h = image_size
    im = Image.new("1", (w, h), 0)
    d = ImageDraw.Draw(im)
    for v in views:
        v = np.asarray(v, float)
        hull = v[ConvexHull(v).vertices]
        d.polygon([tuple(p) for p in hull], fill=1)
    return float(np.asarray(im, bool).mean())


def board_pose(corners: np.ndarray, board: BoardSpec, intr: CameraIntrinsics) -> BoardPose:
    """Pose of the board (lying on the table) in one camera, from its inner corners."""
    from scipy.optimize import least_squares
    obj = board.points()
    K = np.asarray(intr.K, float)
    pts = np.asarray(corners, float)
    # undistort the observed points (fixed-point iteration) for the closed-form start
    Ki = np.linalg.inv(K)
    xn = (Ki @ np.c_[pts, np.ones(len(pts))].T).T[:, :2]
    xu = xn.copy()
    k1, k2 = (list(intr.dist) + [0.0, 0.0])[:2]
    for _ in range(10):
        r2 = (xu ** 2).sum(1, keepdims=True)
        xu = xn / (1 + k1 * r2 + k2 * r2 ** 2)
    pu = (K[:2, :2] @ xu.T).T + K[:2, 2]
    R0, t0 = _pose_from_h(K, homography(obj[:, :2], pu))

    def resid(x):
        return (project(K, intr.dist, _rodrigues(x[:3]), x[3:], obj) - pts).ravel()
    sol = least_squares(resid, np.r_[_rotvec(R0), t0], method="lm", max_nfev=500)
    rms = float(np.sqrt(np.mean(sol.fun.reshape(-1, 2) ** 2) * 2))
    return BoardPose(R=_rodrigues(sol.x[:3]).tolist(), t=sol.x[3:].tolist(), rms_px=round(rms, 4))


def corners_from_outer(outer_px: np.ndarray, board: BoardSpec) -> np.ndarray:
    """Inner corners predicted from the four outermost inner corners a person
    clicked (first corner, along x, opposite, along y), for footage where the
    board is not detected automatically. Ignores lens distortion."""
    s = board.square_m
    src = np.array([[0, 0], [(board.cols - 1) * s, 0], [(board.cols - 1) * s, (board.rows - 1) * s],
                    [0, (board.rows - 1) * s]])
    H = homography(src, np.asarray(outer_px, float))
    P = board.points()[:, :2]
    q = (H @ np.c_[P, np.ones(len(P))].T).T
    return q[:, :2] / q[:, 2:3]


def detect_board(image: np.ndarray, board: BoardSpec) -> Optional[np.ndarray]:
    """Inner corners (cols*rows, 2) in board order, or None. Needs OpenCV
    (optional extra ``camera``)."""
    try:
        import cv2
    except ImportError as exc:
        raise ImportError("board detection needs OpenCV: install the 'camera' extra "
                          "(pip install 'mirror[camera]'), or click the board's outer corners") from exc
    gray = image if image.ndim == 2 else cv2.cvtColor(np.ascontiguousarray(image), cv2.COLOR_RGB2GRAY)
    ok, c = cv2.findChessboardCorners(gray, (board.cols, board.rows),
                                      flags=cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE)
    if not ok:
        return None
    c = cv2.cornerSubPix(gray, c, (5, 5), (-1, -1),
                         (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, 1e-3))
    c = c.reshape(-1, 2).astype(float)
    # OpenCV may return the grid starting from either end; keep the first corner nearest the image top-left
    if np.linalg.norm(c[0]) > np.linalg.norm(c[-1]):
        c = c[::-1].copy()
    return c


def undistort_frame(image: np.ndarray, intr: CameraIntrinsics) -> np.ndarray:
    """Remove radial distortion (same K): output pixel (u, v) samples the input at
    its distorted location."""
    from scipy import ndimage
    K = np.asarray(intr.K, float)
    h, w = image.shape[:2]
    vv, uu = np.mgrid[0:h, 0:w].astype(float)
    x = (uu - K[0, 2]) / K[0, 0]
    y = (vv - K[1, 2]) / K[1, 1]
    r2 = x * x + y * y
    k1, k2 = (list(intr.dist) + [0.0, 0.0])[:2]
    f = 1 + k1 * r2 + k2 * r2 * r2
    ud = x * f * K[0, 0] + K[0, 2]
    vd = y * f * K[1, 1] + K[1, 2]
    if image.ndim == 2:
        return ndimage.map_coordinates(image, [vd, ud], order=1, mode="nearest").astype(image.dtype)
    return np.stack([ndimage.map_coordinates(image[..., k], [vd, ud], order=1, mode="nearest")
                     for k in range(image.shape[2])], -1).astype(image.dtype)
