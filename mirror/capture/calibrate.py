"""Camera calibration for phone capture: intrinsics and distortion, camera pose
on the table plane with metric scale, a quality report with thresholds fixed
in advance, and recalibration checks.

Frames
------
Board frame B is OpenCV's ChArUco frame: origin at the board's outer top-left
corner as printed, x along the 5-square side, y down the page, z into the
table. The TABLE frame T used for reconstruction has the same origin and x
axis, y up the page and z up out of the table (right-handed, z = 0 is the
table top). Metric scale comes from the square size the person measured on
their print (``PrintCheck``), never from the nominal size alone and never from
the simulator's table.

What a planar board does and does not calibrate
------------------------------------------------
* Intrinsics (focal lengths, principal point, distortion) need several views of
  the board at different angles; a single flat view cannot separate focal
  length from distance.
* The pose of each camera relative to the table comes from the board lying flat
  on the table at the start of every take. This fixes the table plane, the
  table origin and metric scale.
* A change of zoom cannot be detected from a top-down view of the flat board
  alone (focal length and distance trade off). It is caught by lens metadata
  when the phone writes it, by the two-camera consistency check, and by the
  oblique-view focal check; otherwise the person must confirm zoom was not
  touched. The quality report says which of these checks actually ran.
"""
from __future__ import annotations

from typing import Literal, Optional, Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from mirror.capture.media import CaptureFingerprint
from mirror.capture.target import DEFAULT_TARGET, TargetSpec

# ----------------------------------------------------------------- thresholds
# Fixed before any real footage exists (design v1). Changing them requires a
# new version of the validation design, not an edit here.
THRESHOLDS = {
    "print_bar_tolerance_mm": 1.0,           # measured 50 mm bar must be within 1 mm
    "print_square_tolerance_rel": 0.02,      # measured square vs bar-implied scale must agree within 2 %
    "intr_min_views": 10,
    "intr_min_corners_per_view": 8,
    "intr_max_rms_px": 1.0,
    "intr_min_coverage": 0.6,                # fraction of a 5 x 4 image grid containing detected corners
    "intr_min_tilted_views": 3,              # views with the board tilted >= 20 deg from fronto-parallel
    "intr_tilt_deg": 20.0,
    "intr_max_focal_rel_std": 0.01,          # 1-sigma relative uncertainty of fx and fy
    "intr_max_pp_offset_rel": 0.10,          # principal point within 10 % of the image size from the centre
    "pose_min_corners": 12,
    "pose_max_rms_px": 1.5,
    "pose_max_view_angle_deg": 78.0,         # angle between the camera ray to the board and the table normal
    "pose_max_holdout_mm": 3.0,
    "pose_max_lift_error_mm": 10.0,          # camera-centre spread (0.5 px corner noise) projected to an object 20 cm above the table
    "pose_lift_reference_m": 0.20,              # board corners held out of the pose fit, error on the table plane
    "recal_max_rms_px": 2.0,                 # stored intrinsics re-used on a new take's board view
    "recal_max_focal_change_rel": 0.06,      # oblique-view focal re-estimate (single-view noise ~3 %; phone zoom steps are >= 10 %)
    "recal_focal_check_min_angle_deg": 25.0,
    "motion_max_shift_px": 2.0,              # camera bump during a take (at processing resolution)
    "drift_max_translation_mm": 5.0,         # board re-observed at the end of a take
    "drift_max_rotation_deg": 0.5,
    "multiview_max_board_error_mm": 4.0,     # board corners triangulated from two cameras vs printed geometry
}

T_FROM_B = np.diag([1.0, -1.0, -1.0])       # table frame from board frame (self-inverse)


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Check(_M):
    name: str
    value: Optional[float] = None
    threshold: Optional[float] = None
    passed: Optional[bool]                   # None = could not be evaluated (says so; never counts as passed)
    note: str = ""


class PrintCheck(_M):
    """What the person measured on their printed target."""
    measured_bar_mm: float
    measured_square_mm: float
    measured_by: Literal["person", "synthetic_test"] = "person"

    def checks(self, spec: TargetSpec = DEFAULT_TARGET) -> list[Check]:
        bar_ok = abs(self.measured_bar_mm - spec.scale_bar_mm) <= THRESHOLDS["print_bar_tolerance_mm"]
        implied = spec.square_mm * self.measured_bar_mm / spec.scale_bar_mm
        rel = abs(self.measured_square_mm / implied - 1.0)
        return [
            Check(name="print_scale_bar_mm", value=self.measured_bar_mm, threshold=THRESHOLDS["print_bar_tolerance_mm"],
                  passed=bar_ok, note=f"nominal {spec.scale_bar_mm:.0f} mm; reprint at 100% if this fails"),
            Check(name="print_square_consistency_rel", value=round(rel, 4), threshold=THRESHOLDS["print_square_tolerance_rel"],
                  passed=rel <= THRESHOLDS["print_square_tolerance_rel"],
                  note="measured square vs the scale implied by the measured bar (catches misreads and non-uniform scaling)"),
        ]

    @property
    def square_mm(self) -> float:
        return self.measured_square_mm


class Detection(_M):
    image_size: tuple[int, int]              # (width, height)
    corner_ids: list[int]
    corners_px: list[tuple[float, float]]
    marker_ids: list[int] = Field(default_factory=list)
    frame_index: Optional[int] = None
    pts_s: Optional[float] = None

    @property
    def n(self) -> int:
        return len(self.corner_ids)


class Intrinsics(_M):
    K: list[list[float]]
    dist: list[float]                        # k1 k2 p1 p2 k3
    image_size: tuple[int, int]
    source: Literal["charuco_calibration", "user_supplied", "synthetic_ground_truth"]
    rms_px: Optional[float] = None
    per_view_rms_px: list[float] = Field(default_factory=list)
    n_views: int = 0
    std_fx_rel: Optional[float] = None
    std_fy_rel: Optional[float] = None
    fingerprint: Optional[CaptureFingerprint] = None
    square_mm: Optional[float] = None
    checks: list[Check] = Field(default_factory=list)
    accepted: bool = False

    @property
    def Km(self) -> np.ndarray:
        return np.asarray(self.K, float)

    @property
    def dm(self) -> np.ndarray:
        return np.asarray(self.dist, float)

    def scaled(self, width: int, height: int) -> "Intrinsics":
        """Same lens at another processing resolution (uniform scaling only)."""
        sx, sy = width / self.image_size[0], height / self.image_size[1]
        if abs(sx - sy) > 1e-3:
            raise ValueError("non-uniform rescale changes the camera model; recalibrate")
        K = self.Km.copy()
        K[0, 0] *= sx; K[0, 2] = (K[0, 2] + 0.5) * sx - 0.5
        K[1, 1] *= sy; K[1, 2] = (K[1, 2] + 0.5) * sy - 0.5
        return self.model_copy(update={"K": K.tolist(), "image_size": (width, height)})


class TablePose(_M):
    """Camera pose relative to the table frame: x_cam = R x_table + t (metres)."""
    R: list[list[float]]
    t: list[float]
    rms_px: float
    n_corners: int
    view_angle_deg: float
    distance_m: float
    holdout_mm: Optional[float] = None
    centre_sigma_mm: Optional[float] = None   # Monte Carlo spread of the camera centre for 0.5 px corner noise
    frame_index: Optional[int] = None
    pts_s: Optional[float] = None
    clip_sha256: Optional[str] = None
    checks: list[Check] = Field(default_factory=list)
    accepted: bool = False

    @property
    def Rm(self) -> np.ndarray:
        return np.asarray(self.R, float)

    @property
    def tm(self) -> np.ndarray:
        return np.asarray(self.t, float)

    @property
    def centre(self) -> np.ndarray:
        return -self.Rm.T @ self.tm


# ----------------------------------------------------------------- detection


def detect(image: np.ndarray, spec: TargetSpec = DEFAULT_TARGET, *, frame_index=None, pts_s=None) -> Detection:
    import cv2
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    det = cv2.aruco.CharucoDetector(spec.board())
    cc, ci, mc, mi = det.detectBoard(gray)
    h, w = gray.shape[:2]
    if ci is None or len(ci) == 0:
        return Detection(image_size=(w, h), corner_ids=[], corners_px=[],
                         marker_ids=[] if mi is None else [int(x) for x in mi.ravel()], frame_index=frame_index, pts_s=pts_s)
    return Detection(image_size=(w, h), corner_ids=[int(x) for x in ci.ravel()],
                     corners_px=[(float(p[0]), float(p[1])) for p in cc.reshape(-1, 2)],
                     marker_ids=[int(x) for x in mi.ravel()] if mi is not None else [], frame_index=frame_index, pts_s=pts_s)


def board_points(spec: TargetSpec, ids: Sequence[int], square_mm: float) -> np.ndarray:
    """Board-frame (B) 3D coordinates in metres of chessboard corner ids."""
    pts = np.asarray(spec.board(square_mm).getChessboardCorners(), float)
    return pts[np.asarray(ids, int)]


def table_points(spec: TargetSpec, ids: Sequence[int], square_mm: float) -> np.ndarray:
    return board_points(spec, ids, square_mm) @ T_FROM_B.T


# ----------------------------------------------------------------- intrinsics


def _coverage(dets: Sequence[Detection], size) -> float:
    w, h = size
    grid = np.zeros((4, 5), bool)
    for d in dets:
        for x, y in d.corners_px:
            grid[min(3, max(0, int(y / h * 4))), min(4, max(0, int(x / w * 5)))] = True
    return float(grid.mean())


def calibrate_intrinsics(dets: Sequence[Detection], spec: TargetSpec, print_check: PrintCheck,
                         fingerprint: Optional[CaptureFingerprint] = None, *, fix_k3: Optional[bool] = None) -> Intrinsics:
    import cv2
    T = THRESHOLDS
    usable = [d for d in dets if d.n >= T["intr_min_corners_per_view"]]
    if not usable:
        return Intrinsics(K=np.eye(3).tolist(), dist=[0.0] * 5, image_size=dets[0].image_size if dets else (0, 0),
                          source="charuco_calibration", n_views=0, fingerprint=fingerprint, square_mm=print_check.square_mm,
                          checks=[Check(name="intr_views", value=0, threshold=T["intr_min_views"], passed=False,
                                        note="no view showed enough board corners")] + print_check.checks(spec))
    size = usable[0].image_size
    obj = [board_points(spec, d.corner_ids, print_check.square_mm).astype(np.float32) for d in usable]
    img = [np.asarray(d.corners_px, np.float32).reshape(-1, 1, 2) for d in usable]
    if fix_k3 is None:
        fix_k3 = len(usable) < 15
    flags = cv2.CALIB_FIX_K3 if fix_k3 else 0
    rms, K, dist, rvecs, tvecs, std_int, _, per_view = cv2.calibrateCameraExtended(obj, img, size, None, None, flags=flags)
    std = std_int.ravel()
    tilts = []
    for rv in rvecs:
        R = cv2.Rodrigues(rv)[0]
        tilts.append(float(np.degrees(np.arccos(abs(R[2, 2])))))
    n_tilted = sum(a >= T["intr_tilt_deg"] for a in tilts)
    cov = _coverage(usable, size)
    w, h = size
    pp_off = max(abs(K[0, 2] - (w - 1) / 2) / w, abs(K[1, 2] - (h - 1) / 2) / h)
    sfx, sfy = float(std[0] / K[0, 0]), float(std[1] / K[1, 1])
    checks = print_check.checks(spec) + [
        Check(name="intr_views", value=len(usable), threshold=T["intr_min_views"], passed=len(usable) >= T["intr_min_views"]),
        Check(name="intr_rms_px", value=round(float(rms), 4), threshold=T["intr_max_rms_px"], passed=rms <= T["intr_max_rms_px"]),
        Check(name="intr_coverage", value=round(cov, 3), threshold=T["intr_min_coverage"], passed=cov >= T["intr_min_coverage"],
              note="fill the corners of the image with the board in some views"),
        Check(name="intr_tilted_views", value=n_tilted, threshold=T["intr_min_tilted_views"],
              passed=n_tilted >= T["intr_min_tilted_views"], note="focal length needs views tilted >= 20 degrees"),
        Check(name="intr_focal_rel_std", value=round(max(sfx, sfy), 5), threshold=T["intr_max_focal_rel_std"],
              passed=max(sfx, sfy) <= T["intr_max_focal_rel_std"]),
        Check(name="intr_principal_point_offset_rel", value=round(float(pp_off), 4), threshold=T["intr_max_pp_offset_rel"],
              passed=pp_off <= T["intr_max_pp_offset_rel"]),
    ]
    return Intrinsics(K=K.tolist(), dist=[float(x) for x in dist.ravel()[:5]], image_size=size, source="charuco_calibration",
                      rms_px=float(rms), per_view_rms_px=[float(x) for x in per_view.ravel()], n_views=len(usable),
                      std_fx_rel=sfx, std_fy_rel=sfy, fingerprint=fingerprint, square_mm=print_check.square_mm,
                      checks=checks, accepted=all(c.passed for c in checks))


def user_supplied_intrinsics(K, dist, image_size, fingerprint=None) -> Intrinsics:
    """Manufacturer or previously validated intrinsics. Recorded as user
    supplied; the quality report lists them as not measured here."""
    return Intrinsics(K=np.asarray(K, float).tolist(), dist=[float(x) for x in (list(dist) + [0.0] * 5)[:5]],
                      image_size=tuple(image_size), source="user_supplied", fingerprint=fingerprint,
                      checks=[Check(name="intr_measured_here", passed=None, note="user supplied; not measured in this session")],
                      accepted=True)


# ----------------------------------------------------------------- table pose


def _solve(K, dist, obj, img):
    import cv2
    ok, rvec, tvec = cv2.solvePnP(obj, img, K, dist, flags=cv2.SOLVEPNP_IPPE)
    if not ok:
        ok, rvec, tvec = cv2.solvePnP(obj, img, K, dist, flags=cv2.SOLVEPNP_ITERATIVE)
    rvec, tvec = cv2.solvePnPRefineLM(obj, img, K, dist, rvec, tvec)
    return rvec, tvec


def _plane_hits(R, t, K, dist, px) -> np.ndarray:
    """Intersect pixel rays with the table plane z = 0 (table frame)."""
    from mirror.capture.geometry import pixel_rays
    o, d = pixel_rays(K, dist, R, t, px)
    s = -o[2] / d[:, 2]
    return o[None, :] + s[:, None] * d


def table_pose(det: Detection, intr: Intrinsics, spec: TargetSpec, print_check: PrintCheck, *,
               clip_sha256: Optional[str] = None) -> TablePose:
    """Pose of a camera relative to the table from one view of the board lying
    flat on the table."""
    import cv2
    T = THRESHOLDS
    if det.n < 4:
        return TablePose(R=np.eye(3).tolist(), t=[0, 0, 0], rms_px=float("inf"), n_corners=det.n, view_angle_deg=90.0,
                         distance_m=0.0, frame_index=det.frame_index, pts_s=det.pts_s, clip_sha256=clip_sha256,
                         checks=[Check(name="pose_corners", value=det.n, threshold=T["pose_min_corners"], passed=False,
                                       note="board not found on the table")])
    obj_t = table_points(spec, det.corner_ids, print_check.square_mm).astype(np.float64)
    img = np.asarray(det.corners_px, np.float64)
    K, dist = intr.Km, intr.dm
    rvec, tvec = _solve(K, dist, obj_t, img)
    R = cv2.Rodrigues(rvec)[0]
    proj, _ = cv2.projectPoints(obj_t, rvec, tvec, K, dist)
    rms = float(np.sqrt(np.mean(np.sum((proj.reshape(-1, 2) - img) ** 2, 1))))
    centre = -R.T @ tvec.ravel()
    board_c = obj_t.mean(0)
    ray = board_c - centre
    view_angle = float(np.degrees(np.arccos(abs(ray[2]) / np.linalg.norm(ray))))
    # hold-out: fit on even-indexed corners, measure odd-indexed corners on the plane
    hold = None
    if det.n >= 12:
        idx = np.arange(det.n)
        fit, test = idx % 2 == 0, idx % 2 == 1
        rv2, tv2 = _solve(K, dist, obj_t[fit], img[fit])
        hits = _plane_hits(cv2.Rodrigues(rv2)[0], tv2.ravel(), K, dist, img[test])
        hold = float(np.sqrt(np.mean(np.sum((hits[:, :2] - obj_t[test, :2]) ** 2, 1)))) * 1000.0
    # camera-centre uncertainty: a nearly face-on board fixes the table plane well but the camera centre poorly
    rng = np.random.default_rng(0)
    cs = []
    for _ in range(24):
        rv3, tv3 = _solve(K, dist, obj_t, img + rng.normal(0, 0.5, img.shape))
        cs.append(-cv2.Rodrigues(rv3)[0].T @ tv3.ravel())
    csig = float(np.sqrt(np.sum(np.var(np.asarray(cs), 0)))) * 1000.0
    lift_err = csig * T["pose_lift_reference_m"] / max(float(centre[2]), T["pose_lift_reference_m"])
    checks = [
        Check(name="pose_lift_error_mm", value=round(lift_err, 2), threshold=T["pose_max_lift_error_mm"],
              passed=lift_err <= T["pose_max_lift_error_mm"],
              note=f"camera centre spread {csig:.0f} mm; tilt the phone 25-35 degrees from straight down or move it closer if this fails"),
        Check(name="pose_corners", value=det.n, threshold=T["pose_min_corners"], passed=det.n >= T["pose_min_corners"]),
        Check(name="pose_rms_px", value=round(rms, 4), threshold=T["pose_max_rms_px"], passed=rms <= T["pose_max_rms_px"]),
        Check(name="pose_view_angle_deg", value=round(view_angle, 2), threshold=T["pose_max_view_angle_deg"],
              passed=view_angle <= T["pose_max_view_angle_deg"], note="raise the camera if the table is seen too obliquely"),
        Check(name="pose_holdout_mm", value=None if hold is None else round(hold, 3), threshold=T["pose_max_holdout_mm"],
              passed=None if hold is None else hold <= T["pose_max_holdout_mm"],
              note="corners not used in the fit, error on the table plane" if hold is not None else "too few corners for a hold-out"),
    ]
    if not intr.accepted:
        checks.append(Check(name="intrinsics_accepted", passed=False, note="intrinsics failed their own checks"))
    return TablePose(R=R.tolist(), t=[float(x) for x in tvec.ravel()], rms_px=rms, n_corners=det.n, view_angle_deg=view_angle,
                     distance_m=float(np.linalg.norm(ray)), holdout_mm=hold, centre_sigma_mm=csig, frame_index=det.frame_index, pts_s=det.pts_s,
                     clip_sha256=clip_sha256, checks=checks, accepted=all(c.passed is not False for c in checks) and det.n >= 4)


# ----------------------------------------------------------------- recalibration


def recalibration_reasons(intr: Intrinsics, fingerprint: Optional[CaptureFingerprint], det: Optional[Detection],
                          spec: TargetSpec, print_check: PrintCheck) -> tuple[list[str], list[Check]]:
    """Reasons the stored intrinsics may no longer apply to a new take, plus
    the checks that ran (a check that could not run is listed with passed=None)."""
    import cv2
    T = THRESHOLDS
    reasons, checks = [], []
    if intr.fingerprint is not None and fingerprint is not None:
        diffs = intr.fingerprint.differences(fingerprint)
        reasons += diffs
        checks.append(Check(name="recal_fingerprint", passed=not diffs, note="; ".join(diffs) or "same resolution, rotation, device and lens metadata"))
        if not intr.fingerprint.lens_tags or not fingerprint.lens_tags:
            checks.append(Check(name="recal_lens_metadata", passed=None,
                                note="clip carries no lens/zoom metadata; zoom change cannot be read from metadata"))
    else:
        checks.append(Check(name="recal_fingerprint", passed=None, note="no fingerprint to compare"))
    if (fingerprint is not None) and tuple(intr.image_size) != (fingerprint.width, fingerprint.height):
        msg = f"take resolution {fingerprint.width}x{fingerprint.height} differs from calibration {intr.image_size[0]}x{intr.image_size[1]}"
        if msg not in reasons:
            reasons.append(msg)
    if det is None or det.n < 6:
        checks.append(Check(name="recal_board_reprojection", passed=None, note="board not seen at the start of the take"))
        reasons.append("board not seen at the start of the take: camera pose for this take cannot be measured")
        return reasons, checks
    obj = table_points(spec, det.corner_ids, print_check.square_mm)
    img = np.asarray(det.corners_px, np.float64)
    rvec, tvec = _solve(intr.Km, intr.dm, obj, img)
    proj, _ = cv2.projectPoints(obj, rvec, tvec, intr.Km, intr.dm)
    rms = float(np.sqrt(np.mean(np.sum((proj.reshape(-1, 2) - img) ** 2, 1))))
    ok = rms <= T["recal_max_rms_px"]
    checks.append(Check(name="recal_board_reprojection_px", value=round(rms, 3), threshold=T["recal_max_rms_px"], passed=ok))
    if not ok:
        reasons.append(f"board reprojection with stored intrinsics {rms:.2f} px > {T['recal_max_rms_px']} px (lens, zoom or focus changed)")
    R = cv2.Rodrigues(rvec)[0]
    angle = float(np.degrees(np.arccos(abs(R[2, 2]))))
    if angle >= T["recal_focal_check_min_angle_deg"] and det.n >= 12:
        K0 = intr.Km.copy()
        flags = (cv2.CALIB_USE_INTRINSIC_GUESS | cv2.CALIB_FIX_PRINCIPAL_POINT | cv2.CALIB_FIX_ASPECT_RATIO |
                 cv2.CALIB_FIX_K1 | cv2.CALIB_FIX_K2 | cv2.CALIB_FIX_K3 | cv2.CALIB_ZERO_TANGENT_DIST)
        f0 = float(intr.Km[0, 0])
        _, K1, *_ = cv2.calibrateCamera([obj.astype(np.float32)], [img.astype(np.float32).reshape(-1, 1, 2)],
                                        tuple(intr.image_size), K0, intr.dm.copy(), flags=flags)
        rel = abs(float(K1[0, 0]) / f0 - 1.0)   # K0 is updated in place by OpenCV; compare with the stored value
        ok = rel <= T["recal_max_focal_change_rel"]
        checks.append(Check(name="recal_focal_change_rel", value=round(float(rel), 4), threshold=T["recal_max_focal_change_rel"], passed=ok,
                            note=f"oblique board view ({angle:.0f} deg)"))
        if not ok:
            reasons.append(f"focal length re-estimated {100 * rel:.1f}% different: zoom changed")
    else:
        checks.append(Check(name="recal_focal_change_rel", passed=None,
                            note=f"board seen nearly face-on ({angle:.0f} deg); zoom cannot be checked from this view"))
    return reasons, checks


def camera_shift_px(frame_a: np.ndarray, frame_b: np.ndarray, *, border: float = 0.18) -> float:
    """Global image shift between two frames of a fixed camera, measured on the
    image border band (where the demonstration rarely happens) by phase correlation."""
    import cv2

    def prep(f):
        g = f if f.ndim == 2 else cv2.cvtColor(f, cv2.COLOR_RGB2GRAY)
        g = g.astype(np.float32)
        h, w = g.shape
        m = np.zeros_like(g)
        bh, bw = int(h * border), int(w * border)
        m[:bh] = 1; m[-bh:] = 1; m[:, :bw] = 1; m[:, -bw:] = 1
        return (g - g[m > 0].mean()) * m

    a, b = prep(frame_a), prep(frame_b)
    win = cv2.createHanningWindow(a.shape[::-1], cv2.CV_32F)
    (dx, dy), _ = cv2.phaseCorrelate(a, b, win)
    return float(np.hypot(dx, dy))


def pose_drift(a: TablePose, b: TablePose) -> tuple[float, float]:
    """Translation (mm) and rotation (deg) between two poses of one camera."""
    dR = a.Rm @ b.Rm.T
    ang = float(np.degrees(np.arccos(np.clip((np.trace(dR) - 1) / 2, -1, 1))))
    return float(np.linalg.norm(a.centre - b.centre) * 1000.0), ang
