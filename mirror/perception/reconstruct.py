"""Video -> DemoTrace.

Input is a set of per-frame segmentation masks for each camera ("tracks":
camera -> label -> frame index -> mask), produced by ``track`` (SAM 3.1) or,
for development and as an upper bound, by ``tracks_from_ground_truth``.

Steps
1. Calibrate each camera from the table: the table mask in the first frame
   gives the table's four corners; with the known table size (1.2 m x 1.0 m)
   they define the table-plane homography, and with the camera intrinsics
   (assumed known, as for a calibrated phone camera) a planar pose solve
   gives the camera's rotation and position. Convention: the camera is on the
   operator's (-y) side of the table; that resolves the rectangle's symmetry.
2. Object positions per frame: the two cameras' mask centroids are
   triangulated; with one camera, the ray is intersected with the horizontal
   plane at the object's top (resting) or last known height.
3. Events: an object is carried while it moves (speed above a threshold after
   smoothing); the carry's start is the grasp, its end the release.
4. Cup tilt: angle between image vertical and the principal axis of the cup
   mask in the side view (cup plus gripper while carried), converted to a
   tilt about the camera's viewing direction. Only the in-image component of
   tilt is observable from one view; this is stated with the estimates.
5. Scene: objects from labels (kinds and sizes from the kit catalogue, the
   supported object set), positions from the first frame; tray position from
   its mask, size from the catalogue (its measured extent is reported as a
   scale check); walls from the top-view mask (line, length) and the side
   view (height, and slots as holes in the wall mask not covered by other
   tracked things); bead count from the bead mask area in the first frames.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy import ndimage

from mirror.contracts import (DemoTrace, ObstacleSpec, Pose, SceneState, SlotSpec, TraceEvent, TraceFrame)
from mirror.geometry import BLOCK, CUP, GRIPPER, MARKER, TRAY, WORLD
from mirror.perception.masks import rle_decode

KIT = {"cup": CUP.outer_half[2] * 2, "block": BLOCK.half[2] * 2, "marker": MARKER.half_length * 2}
TRAY_FLOOR = 0.006          # tray floor top above the table (kit catalogue)
ANCHOR_MAX_PX = 12.0       # px: resting objects from the top view must reproject this close in another view
RAY_GAP_MAX = 0.03         # m: largest ray-to-point distance accepted when triangulating
CARRY_MAX_XY = 0.08        # m: gripper-object table-plane distance during a carry
TABLE_FIT_MAX_PX = 10.0     # a table outline fitting worse than this (RMS px) is not used for calibration
MOVE_SPEED = 0.04          # m/s: an object moving faster than this (smoothed) is being carried
SMOOTH_S = 0.4
GRASP_LOOKBACK_S = 2.5     # s: window before motion onset searched for the gripper's lowest point


@dataclass
class Calib:
    K: np.ndarray
    R: np.ndarray
    t: np.ndarray
    corners_px: np.ndarray
    residual: float

    @property
    def center(self) -> np.ndarray:
        return -self.R.T @ self.t

    def ray(self, uv):
        d = self.R.T @ np.linalg.solve(self.K, np.array([uv[0], uv[1], 1.0]))
        return self.center, d / np.linalg.norm(d)

    def project(self, X):
        c = self.R @ np.asarray(X, float) + self.t
        p = self.K @ c
        return p[:2] / p[2]


@dataclass
class Tracks:
    """camera -> label -> frame index -> bool mask (H, W)."""
    fps: float
    n_frames: int
    size: dict                       # camera -> (width, height)
    masks: dict
    source: str = "sam-3.1"
    notes: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    static: set = field(default_factory=set)     # labels segmented once (frame 0) and held

    def get(self, cam, label, i):
        d = self.masks.get(cam, {}).get(label, {})
        m = d.get(0 if label in self.static else i)
        if m is None:
            return None
        return rle_decode(m) if isinstance(m, dict) else m


def tracks_from_ground_truth(dv, gt, labels=None) -> Tracks:
    """Oracle tracks from simulator segmentation (development / upper bound)."""
    masks = {}
    for c in dv.cameras:
        masks[c] = {}
        for lab in (labels or dv.labels):
            masks[c][lab] = {f["i"]: f["masks"][c][lab]["rle"] for f in gt["frames"] if lab in f["masks"][c]}
    return Tracks(fps=dv.fps, n_frames=dv.n_frames,
                  size={c: (ci.width, ci.height) for c, ci in dv.cameras.items()}, masks=masks,
                  source="ground-truth masks (oracle)")


# ------------------------------------------------------------ calibration


def _quad_from_mask(mask: np.ndarray) -> np.ndarray:
    """Four corners of the convex hull of a (roughly quadrilateral) mask."""
    from scipy.spatial import ConvexHull
    edge = mask & ~ndimage.binary_erosion(mask)
    ys, xs = np.nonzero(edge)
    pts = np.stack([xs, ys], 1).astype(float)
    hull = pts[ConvexHull(pts).vertices]
    poly = list(hull)

    def area(p):
        p = np.asarray(p)
        return 0.5 * abs(np.dot(p[:, 0], np.roll(p[:, 1], 1)) - np.dot(p[:, 1], np.roll(p[:, 0], 1)))
    while len(poly) > 4:
        base = area(poly)
        losses = [base - area(poly[:k] + poly[k + 1:]) for k in range(len(poly))]
        poly.pop(int(np.argmin(losses)))
    return np.asarray(poly)


def _homography(src, dst):
    A = []
    for (x, y), (u, v) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y, -u])
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y, -v])
    _, _, Vt = np.linalg.svd(np.asarray(A))
    H = Vt[-1].reshape(3, 3)
    return H / H[2, 2]


def _pose_from_h(K, H):
    B = np.linalg.solve(K, H)
    n1, n2 = np.linalg.norm(B[:, 0]), np.linalg.norm(B[:, 1])
    lam = 2.0 / (n1 + n2)
    r1, r2 = B[:, 0] * lam, B[:, 1] * lam
    t = B[:, 2] * lam
    if t[2] < 0:                     # table in front of the camera
        r1, r2, t = -r1, -r2, -t
    Rm = np.stack([r1, r2, np.cross(r1, r2)], 1)
    U, _, Vt = np.linalg.svd(Rm)
    Rm = U @ Vt
    resid = abs(n1 - n2) / (0.5 * (n1 + n2)) + abs(B[:, 0] @ B[:, 1]) / (n1 * n2)
    return Rm, t, resid


def calibrate(table_mask: np.ndarray, K: np.ndarray, table_half=WORLD.table_half[:2]) -> Calib:
    quad = _quad_from_mask(table_mask)
    # image quad in a consistent (counter-clockwise in image) order
    c = quad.mean(0)
    ang = np.arctan2(quad[:, 1] - c[1], quad[:, 0] - c[0])
    quad = quad[np.argsort(ang)]
    hx, hy = table_half
    world = np.array([[-hx, -hy], [hx, -hy], [hx, hy], [-hx, hy]])
    best = None
    for flip in (False, True):
        q = quad[::-1] if flip else quad
        for k in range(4):
            img = np.roll(q, k, axis=0)
            H = _homography(world, img)
            Rm, t, resid = _pose_from_h(K, H)
            C = -Rm.T @ t
            ok = C[2] > 0 and C[1] < 0 and np.linalg.det(Rm) > 0
            if ok and (best is None or resid < best[0]):
                best = (resid, Rm, t, img)
    if best is None:
        raise ValueError("table corners do not give a camera above the table on the operator's side")
    resid, Rm, t, img = best
    Rm, t, resid2 = _refine_slab(table_mask, np.asarray(K, float), Rm, t, table_half)
    return Calib(K=np.asarray(K, float), R=Rm, t=t, corners_px=img, residual=float(resid2))


def _rodrigues(w):
    th = np.linalg.norm(w)
    if th < 1e-12:
        return np.eye(3)
    k = w / th
    Kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(th) * Kx + (1 - math.cos(th)) * Kx @ Kx


def _seg_dist(p, a, b):
    ab = b - a
    s = np.clip(((p - a) @ ab) / max(1e-9, ab @ ab), 0, 1)
    return np.linalg.norm(p - (a + s * ab))


def _poly_dist(pts, poly):
    """Distance from each point to the closed polygon outline (vectorised _seg_dist)."""
    pts = np.asarray(pts, float)
    a = np.asarray(poly, float)
    b = np.roll(a, -1, axis=0)
    ab = b - a                                                     # (M, 2)
    den = np.maximum(1e-9, np.einsum("ij,ij->i", ab, ab))          # (M,)
    ap = pts[:, None, :] - a[None, :, :]                           # (N, M, 2)
    s_ = np.clip(np.einsum("nmk,mk->nm", ap, ab) / den, 0, 1)
    d = ap - s_[..., None] * ab[None]
    return np.sqrt(np.einsum("nmk,nmk->nm", d, d)).min(axis=1)


def _refine_slab(mask, K, Rm, t, table_half, thickness=2 * WORLD.table_half[2]):
    """Refine the pose so the projected outline of the table slab (top surface
    plus its visible side faces, ``thickness`` deep) matches the mask outline.
    For an overhead camera the side faces are invisible and this is a no-op."""
    from scipy.optimize import least_squares
    from scipy.spatial import ConvexHull
    edge = mask & ~ndimage.binary_erosion(mask)
    ys, xs = np.nonzero(edge)
    pts = np.stack([xs, ys], 1).astype(float)
    hull = pts[ConvexHull(pts).vertices]
    # sample the mask hull boundary
    samp = []
    for i in range(len(hull)):
        a, b = hull[i], hull[(i + 1) % len(hull)]
        k = max(1, int(np.linalg.norm(b - a) / 15))
        samp += [a + (b - a) * j / k for j in range(k)]
    samp = np.asarray(samp)
    hx, hy = table_half
    corners = np.array([[x, y, z] for x in (-hx, hx) for y in (-hy, hy) for z in (0.0, -thickness)])

    def proj(params):
        R_ = _rodrigues(params[:3]) @ Rm
        c = (R_ @ corners.T).T + params[3:]
        p = (K @ c.T).T
        return p[:, :2] / p[:, 2:3]

    def perimeter_samples(poly, n=40):
        seg = np.linalg.norm(np.roll(poly, -1, 0) - poly, axis=1)
        cum = np.concatenate([[0], np.cumsum(seg)])
        out = []
        for s_ in np.linspace(0, cum[-1], n, endpoint=False):
            i = min(np.searchsorted(cum, s_, side="right") - 1, len(poly) - 1)
            a, b = poly[i], poly[(i + 1) % len(poly)]
            out.append(a + (b - a) * ((s_ - cum[i]) / max(seg[i], 1e-9)))
        return np.asarray(out)

    def resid(params):
        P = proj(params)
        try:
            H = P[ConvexHull(P).vertices]
        except Exception:
            return np.full(len(samp) + 40, 1e3)
        r1 = _poly_dist(samp, H)
        r2 = _poly_dist(perimeter_samples(H), hull)
        return np.concatenate([r1, r2])

    x0 = np.concatenate([np.zeros(3), t])
    sol = least_squares(resid, x0, loss="soft_l1", f_scale=2.0, max_nfev=200)
    R_ = _rodrigues(sol.x[:3]) @ Rm
    return R_, sol.x[3:], float(np.sqrt(np.mean(sol.fun ** 2)))


def _quad_vertices(mask):
    from scipy.spatial import ConvexHull
    ys, xs = np.nonzero(mask)
    if len(xs) < 50:
        return None
    pts = np.stack([xs, ys], 1).astype(float)
    try:
        hull = list(pts[ConvexHull(pts).vertices])
    except Exception:
        return None

    def area(p):
        p = np.asarray(p)
        return 0.5 * abs(np.dot(p[:, 0], np.roll(p[:, 1], 1)) - np.dot(p[:, 1], np.roll(p[:, 0], 1)))
    while len(hull) > 4:
        base = area(hull)
        hull.pop(int(np.argmin([base - area(hull[:k] + hull[k + 1:]) for k in range(len(hull))])))
    return np.asarray(hull)


def calibrate_from_scene(K, top_cal, points3d, points2d, wall=None, side_wall_mask=None, inits=None):
    """Pose of a second camera from correspondences with geometry measured by
    an already calibrated camera: resting objects (3D centres from the top
    view, 2D mask centroids here) and, when available, the four corners of a
    wall (base line from the top view, height solved jointly).
    Multi-start least squares over camera position around the table."""
    from scipy.optimize import least_squares
    K = np.asarray(K, float)
    P3 = np.asarray(points3d, float)
    P2 = np.asarray(points2d, float)
    quad = _quad_vertices(side_wall_mask) if (wall is not None and side_wall_mask is not None) else None

    def project(R_, t_, X):
        c = (R_ @ np.asarray(X).T).T + t_
        p = (K @ c.T).T
        return p[:, :2] / p[:, 2:3]

    def wall_corners(h):
        (ra, rb) = wall                      # top-view rays through the wall's two end pixels (top face)
        a, b = _plane_z(ra, h), _plane_z(rb, h)
        return np.array([[a[0], a[1], 0.0], [b[0], b[1], 0.0], [b[0], b[1], h], [a[0], a[1], h]])

    def resid(x):
        R_ = _rodrigues(x[:3])
        t_ = x[3:6]
        r = (project(R_, t_, P3) - P2).ravel() if len(P3) else np.zeros(0)
        if quad is not None:
            C = project(R_, t_, wall_corners(x[6]))
            best = None
            for order in (quad, quad[::-1]):
                for k_ in range(4):
                    q = np.roll(order, k_, axis=0)
                    d = np.linalg.norm(C - q, axis=1)
                    if best is None or d.sum() < best.sum():
                        best = d
            r = np.concatenate([r, 0.5 * best])
        return r

    look = np.array([0.0, 0.0, 0.05])
    best = None
    grid = inits or [(az, el, d) for az in range(0, 360, 30) for el in (-15, -30, -45) for d in (1.2, 1.8, 2.5)]
    for az, el, d in grid:
        a, e = math.radians(az), math.radians(el)
        fwd = np.array([math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)])
        up = np.array([-math.sin(e) * math.cos(a), -math.sin(e) * math.sin(a), math.cos(e)])
        Rm = np.stack([np.cross(fwd, up), -up, fwd])
        pos = look - d * fwd
        t0 = -Rm @ pos
        # rotation vector of Rm
        ang = math.acos(max(-1.0, min(1.0, (np.trace(Rm) - 1) / 2)))
        if ang < 1e-6:
            w0 = np.zeros(3)
        else:
            w0 = ang / (2 * math.sin(ang)) * np.array([Rm[2, 1] - Rm[1, 2], Rm[0, 2] - Rm[2, 0], Rm[1, 0] - Rm[0, 1]])
        x0 = np.concatenate([w0, t0, [0.4]]) if quad is not None else np.concatenate([w0, t0])
        try:
            sol = least_squares(resid, x0, loss="soft_l1", f_scale=3.0, max_nfev=300)
        except Exception:
            continue
        R_ = _rodrigues(sol.x[:3])
        C = -R_.T @ sol.x[3:6]
        if C[2] <= 0:
            continue
        cost = float(np.sqrt(np.mean(sol.fun ** 2)))
        if best is None or cost < best[0]:
            best = (cost, R_, sol.x[3:6].copy(), sol.x[6] if quad is not None else None)
    if best is None:
        raise ValueError("second camera could not be calibrated from scene geometry")
    cost, R_, t_, h = best
    return Calib(K=K, R=R_, t=t_, corners_px=np.zeros((0, 2)), residual=cost), h


# ------------------------------------------------------------ geometry


def _triangulate(rays):
    """Least-squares closest point to several rays (origin, unit direction)."""
    A = np.zeros((3, 3))
    b = np.zeros(3)
    for o, d in rays:
        P = np.eye(3) - np.outer(d, d)
        A += P
        b += P @ o
    return np.linalg.solve(A, b)


def _plane_z(ray, z):
    o, d = ray
    if abs(d[2]) < 1e-9:
        return None
    s = (z - o[2]) / d[2]
    return o + s * d


def _centroid(mask):
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return np.array([xs.mean(), ys.mean()])


def _principal_angle_deg(mask) -> Optional[float]:
    """Angle (deg) between image vertical and the mask's major axis."""
    ys, xs = np.nonzero(mask)
    if len(xs) < 20:
        return None
    C = np.cov(np.stack([xs, ys]))
    w, V = np.linalg.eigh(C)
    v = V[:, np.argmax(w)]
    return math.degrees(math.atan2(abs(v[0]), abs(v[1])))


def _axis_angle_to(mask, ref) -> Optional[float]:
    """Angle (deg, 0..90) between the mask's major axis and image direction ``ref``."""
    ys, xs = np.nonzero(mask)
    if len(xs) < 20:
        return None
    C = np.cov(np.stack([xs, ys]))
    w, V = np.linalg.eigh(C)
    v = V[:, np.argmax(w)]
    r = np.asarray(ref, float) / (np.linalg.norm(ref) or 1.0)
    return math.degrees(math.acos(min(1.0, abs(float(v @ r)))))


def _min_rect_yaw(mask) -> Optional[float]:
    """Image-plane angle (rad, mod pi/2) of the minimum-area rectangle around a mask."""
    ys, xs = np.nonzero(mask)
    if len(xs) < 10:
        return None
    pts = np.stack([xs, ys], 1).astype(float)
    best = None
    for a in np.radians(np.arange(0, 90, 1.0)):
        c, s_ = math.cos(a), math.sin(a)
        q = pts @ np.array([[c, -s_], [s_, c]])
        area = np.ptp(q[:, 0]) * np.ptp(q[:, 1])
        if best is None or area < best[0]:
            best = (area, a)
    return best[1]


def _box_corners(half, yaw, tilt_deg, phi_deg):
    hx, hy, hz = half
    C = np.array([[x, y, z] for x in (-hx, hx) for y in (-hy, hy) for z in (-hz, hz)])
    Rz = np.array([[math.cos(yaw), -math.sin(yaw), 0], [math.sin(yaw), math.cos(yaw), 0], [0, 0, 1]])
    ax = np.array([math.cos(math.radians(phi_deg)), math.sin(math.radians(phi_deg)), 0.0])
    Rt = _rodrigues(ax * math.radians(tilt_deg))
    return (Rt @ Rz @ C.T).T


def _silhouette_iou(mask_crop, occl_crop, poly, off):
    from PIL import Image, ImageDraw
    h, w = mask_crop.shape
    im = Image.new("1", (w, h), 0)
    ImageDraw.Draw(im).polygon([tuple(p - off) for p in poly], fill=1)
    sil = np.asarray(im, dtype=bool)
    valid = ~occl_crop
    a, b = sil & valid, mask_crop & valid
    u = (a | b).sum()
    return float((a & b).sum() / u) if u else 0.0


def tilt_by_silhouette(mask, occluder, cal, X, yaw_world, half=CUP.outer_half, shift_px: int = 6,
                       min_visible: float = 0.8):
    """Cup tilt (deg) whose projected box silhouette best matches the side-view
    mask. Pixels covered by ``occluder`` are ignored in both. A small image
    shift absorbs the position estimate's error. Coarse grid then refine."""
    from scipy.spatial import ConvexHull
    ys, xs = np.nonzero(mask)
    if len(xs) < 30:
        return None
    pad = 12 + shift_px
    x0, y0 = max(0, xs.min() - pad), max(0, ys.min() - pad)
    x1, y1 = xs.max() + pad, ys.max() + pad
    mc = mask[y0:y1, x0:x1]
    oc = occluder[y0:y1, x0:x1] if occluder is not None else np.zeros_like(mc)
    off = np.array([x0, y0])

    def hull(th, ph):
        P = np.array([cal.project(X + c) for c in _box_corners(half, yaw_world, th, ph)])
        return P[ConvexHull(P).vertices]

    def best_shift(H, rng):
        best = (-1.0, np.zeros(2))
        for dx in rng:
            for dy in rng:
                sc = _silhouette_iou(mc, oc, H + (dx, dy), off)
                if sc > best[0]:
                    best = (sc, np.array([dx, dy], float))
        return best
    # Too little of the cup visible (occluded or cut off): no estimate.
    from PIL import Image, ImageDraw
    H0 = hull(0.0, 0.0)
    im = Image.new("1", (mask.shape[1], mask.shape[0]), 0)
    ImageDraw.Draw(im).polygon([tuple(p) for p in H0], fill=1)
    sil = np.asarray(im, dtype=bool)
    occl_full = occluder if occluder is not None else np.zeros_like(mask)
    vis = (sil & ~occl_full).sum() / max(1, sil.sum())
    if vis < min_visible or mask.sum() < 0.8 * (sil & ~occl_full).sum():
        return None
    _, sh = best_shift(H0, range(-shift_px, shift_px + 1, 2))
    best = (_silhouette_iou(mc, oc, H0 + sh, off), 0.0, 0.0)
    for th in range(10, 91, 10):
        for ph in range(0, 360, 45):
            sc = _silhouette_iou(mc, oc, hull(th, ph) + sh, off)
            if sc > best[0]:
                best = (sc, th, ph)
    _, th0, ph0 = best
    for th in np.arange(max(0, th0 - 8), min(90, th0 + 8) + 0.1, 2.0):
        for ph in np.arange(ph0 - 30, ph0 + 31, 10.0):
            H = hull(float(th), float(ph))
            sc, sh2 = best_shift(H, (-2, 0, 2))
            if sc > best[0]:
                best = (sc, float(th), float(ph))
    return best[1]


def _inside(p, poly) -> bool:
    x, y = float(p[0]), float(p[1])
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _tray_polygon(tracks, calib, top, tray_label="tray_0"):
    """Table-plane outline of the tray (top view, frame 0), or None."""
    from scipy.spatial import ConvexHull
    m = tracks.get(top, tray_label, 0)
    if m is None or not m.any() or top not in calib:
        return None
    ys, xs = np.nonzero(m)
    sel = np.linspace(0, len(xs) - 1, min(len(xs), 2000)).astype(int)
    P = np.array([_plane_z(calib[top].ray((xs[j], ys[j])), TRAY.wall_height + TRAY_FLOOR)[:2] for j in sel])
    try:
        return P[ConvexHull(P).vertices]
    except Exception:
        return None


def _smooth(x, k):
    if len(x) < 3 or k < 2:
        return x
    return ndimage.uniform_filter1d(x, size=k, axis=0, mode="nearest")


def _yaw_quat(yaw):
    return (math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2))


def _tilt_quat(yaw, tilt_deg, axis_xy=(0.0, 1.0)):
    """yaw about z, then a tilt about a horizontal axis."""
    a = math.radians(tilt_deg) / 2
    ax = np.array([axis_xy[0], axis_xy[1], 0.0])
    ax /= np.linalg.norm(ax) or 1.0
    qt = (math.cos(a), *(math.sin(a) * ax))
    qy = _yaw_quat(yaw)
    w1, x1, y1, z1 = qt
    w2, x2, y2, z2 = qy
    return (w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2, w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2, w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2)


# ------------------------------------------------------------ main


@dataclass
class Estimates:
    t: np.ndarray
    objects: dict                 # id -> (N, 3) positions (nan where not found)
    found: dict                   # id -> (N,) bool
    tilt_deg: dict                # cup id -> (N,) in-image tilt from the side view (nan if unknown)
    gripper: np.ndarray
    events: list
    calib: dict
    scene_checks: dict
    # observability (mirror.observability): per-frame status of each estimated value
    pos_status: dict = field(default_factory=dict)      # label -> (N,) "measured"|"inferred"|"unobserved"
    tilt_status: dict = field(default_factory=dict)     # cup label -> (N,) "measured"|"unobserved"
    gripper_status: Optional[np.ndarray] = None         # (N,) "measured"|"inferred"|"unobserved"
    rest_tilt: dict = field(default_factory=dict)       # label -> {"start"|"end": inference dict}
    identity: dict = field(default_factory=dict)        # label -> {"identity": ..., "note": ...}
    calibration: list = field(default_factory=list)     # CalibrationRecord-shaped dicts
    review: list = field(default_factory=list)          # ReviewItem-shaped dicts



# ------------------------------------------------------------ observability helpers

# Resting orientation from the top view: the object's outline is compared with
# the projected silhouettes of its catalogue shape standing upright and lying
# on its side. Only "standing" vs "lying" is resolvable this way; small tilts
# are not (about REST_TILT_SIGMA_DEG), and an upside-down object looks the
# same as an upright one from above.
REST_IOU_MIN = 0.55          # the upright silhouette must match the outline at least this well
REST_IOU_MARGIN = 0.10       # ... and better than any lying hypothesis by this margin
REST_TILT_SIGMA_DEG = 15.0
REST_WINDOW_S = 1.0


def _shape_half(kind):
    if kind == "cup":
        return tuple(CUP.outer_half)
    if kind == "block":
        return tuple(BLOCK.half)
    return (MARKER.radius, MARKER.radius, MARKER.half_length)


def _poly_mask_iou(mask, poly):
    from PIL import Image, ImageDraw
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return 0.0
    x0 = int(max(0, min(xs.min(), poly[:, 0].min()) - 2))
    y0 = int(max(0, min(ys.min(), poly[:, 1].min()) - 2))
    x1 = int(min(mask.shape[1], max(xs.max(), poly[:, 0].max()) + 3))
    y1 = int(min(mask.shape[0], max(ys.max(), poly[:, 1].max()) + 3))
    if x1 <= x0 or y1 <= y0:
        return 0.0
    im = Image.new("1", (x1 - x0, y1 - y0), 0)
    ImageDraw.Draw(im).polygon([(float(p[0] - x0), float(p[1] - y0)) for p in poly], fill=1)
    sil = np.asarray(im, dtype=bool)
    m = mask[y0:y1, x0:x1]
    u = (sil | m).sum()
    return float((sil & m).sum() / u) if u else 0.0


def _silhouette(cal, center, half, yaw, lying):
    from scipy.spatial import ConvexHull
    hx, hy, hz = half
    if lying:                      # local z (the axis) horizontal, along yaw
        dims = (hz, hy, hx)
    else:
        dims = (hx, hy, hz)
    C = np.array([[x, y, z] for x in (-dims[0], dims[0]) for y in (-dims[1], dims[1]) for z in (-dims[2], dims[2])])
    Rz = np.array([[math.cos(yaw), -math.sin(yaw), 0], [math.sin(yaw), math.cos(yaw), 0], [0, 0, 1]])
    P = np.array([cal.project(center + Rz @ c) for c in C])
    try:
        return P[ConvexHull(P).vertices]
    except Exception:
        return None


def rest_tilt_from_top(mask, cal, xy, kind, *, plane_z=0.0):
    """Standing-vs-lying inference for a resting object from its top-view mask.

    Returns {"status": "inferred"|"unobserved", "upright": bool|None, "iou_upright",
    "iou_lying", "basis"}. A cube is symmetric (every face looks alike), so its
    tilt is never inferred."""
    if kind == "block" and len(set(np.round(BLOCK.half, 6))) == 1:
        return dict(status="unobserved", upright=None, basis="a cube looks the same on every face")
    if mask is None or mask.sum() < 30:
        return dict(status="unobserved", upright=None, basis="not visible from above")
    half = _shape_half(kind)
    best_up, best_ly = 0.0, 0.0
    for yaw in np.radians(np.arange(0, 180, 7.5)):
        up = _silhouette(cal, np.array([xy[0], xy[1], plane_z + half[2]]), half, yaw, False)
        ly = _silhouette(cal, np.array([xy[0], xy[1], plane_z + half[0]]), half, yaw, True)
        if up is not None:
            best_up = max(best_up, _poly_mask_iou(mask, up))
        if ly is not None:
            best_ly = max(best_ly, _poly_mask_iou(mask, ly))
    out = dict(iou_upright=round(best_up, 3), iou_lying=round(best_ly, 3))
    if best_up >= REST_IOU_MIN and best_up - best_ly >= REST_IOU_MARGIN:
        return dict(out, status="inferred", upright=True,
                    basis=f"top-view outline matches the upright shape (IoU {best_up:.2f} vs {best_ly:.2f} lying)")
    if best_ly >= REST_IOU_MIN and best_ly - best_up >= REST_IOU_MARGIN:
        return dict(out, status="inferred", upright=False,
                    basis=f"top-view outline matches the shape lying on its side (IoU {best_ly:.2f} vs {best_up:.2f})")
    return dict(out, status="unobserved", upright=None,
                basis=f"top-view outline does not decide standing vs lying (IoU {best_up:.2f} / {best_ly:.2f})")


def _rest_tilt_window(tracks, calib, top, lab, kind, pos, frames, bead_label, tray_poly):
    """Majority of per-frame standing/lying inferences over ``frames``."""
    votes = []
    for i in frames:
        m = tracks.get(top, lab, i)
        if m is None or not m.any() or np.isnan(pos[i][0]):
            continue
        if kind == "cup":
            bead = tracks.get(top, bead_label, i)
            if bead is not None and bead.any():
                ys, xs = np.nonzero(m)
                box = np.zeros_like(m)
                box[max(0, ys.min() - 3):ys.max() + 4, max(0, xs.min() - 3):xs.max() + 4] = True
                m = m | (bead & box)
            m = ndimage.binary_fill_holes(m)
        z0 = TRAY_FLOOR if (tray_poly is not None and _inside(pos[i][:2], tray_poly)) else 0.0
        votes.append(rest_tilt_from_top(m, calib[top], pos[i][:2], kind, plane_z=z0))
    if not votes:
        return dict(status="unobserved", upright=None, basis="not seen from above while resting", frames=0)
    ups = [v for v in votes if v.get("upright") is True]
    lys = [v for v in votes if v.get("upright") is False]
    n = len(votes)
    res = None
    if len(ups) >= 0.6 * n:
        res = dict(ups[len(ups) // 2], frames=n, agree=len(ups))
    elif len(lys) >= 0.6 * n:
        res = dict(lys[len(lys) // 2], frames=n, agree=len(lys))
    else:
        bases = {v.get("basis") for v in votes if v.get("upright") is None}
        why = (bases.pop() if len(bases) == 1 and not ups and not lys and "cube" in next(iter(bases), "")
               else f"top-view outline inconclusive ({len(ups)} standing, {len(lys)} lying of {n} frames)")
        res = dict(status="unobserved", upright=None, frames=n, basis=why)
    if res.get("upright") is True and kind == "cup":
        bead_inside = False
        for i in frames:
            bead = tracks.get(top, bead_label, i)
            mm = tracks.get(top, lab, i)
            if bead is not None and mm is not None and mm.any() and bead.any():
                if (ndimage.binary_fill_holes(mm | (bead & ndimage.binary_dilation(mm, iterations=3)))
                        & bead).sum() >= 10:
                    bead_inside = True
                    break
        if not bead_inside:
            res = dict(status="unobserved", upright=None, frames=n,
                       basis="standing, but upright and upside-down look alike from above (no beads seen inside)")
        else:
            res["basis"] += "; beads visible inside, so the opening faces up"
    elif res.get("upright") is True and kind == "marker":
        res["basis"] += "; standing on one end (which end is up cannot be seen from above)"
    return res


def _resting_anchors(tracks, top_cal, top, i0=0):
    """3D centres of the kit objects at the start (resting, top view)."""
    out = {}
    for lab in tracks.masks.get(top, {}):
        kind = next((k for k in KIT if k in lab), None)
        m = tracks.get(top, lab, i0)
        if kind is None or m is None or not m.any():
            continue
        X = _plane_z(top_cal.ray(_centroid(m)), KIT[kind])
        if X is not None:
            out[lab] = X - np.array([0, 0, KIT[kind] / 2])
    return out


def _anchor_error(cal, anchors, tracks, cam, i0=0):
    errs = []
    for lab, X in anchors.items():
        m = tracks.get(cam, lab, i0)
        if m is None or not m.any():
            continue
        errs.append(np.linalg.norm(cal.project(X) - _centroid(m)))
    return float(np.median(errs)) if len(errs) >= 2 else None


def _check_side_tray(tracks, calib, top, tray_label="tray_0"):
    """Drop a non-top tray mask that does not overlap the tray as measured in
    the top view (e.g. a grounding box on the floor)."""
    poly = _tray_polygon(tracks, calib, top, tray_label)
    for c in list(tracks.masks):
        if c == top or c not in calib or tray_label not in tracks.masks[c]:
            continue
        m = tracks.get(c, tray_label, 0)
        ok = False
        if poly is not None and m is not None and m.any():
            from PIL import Image, ImageDraw
            P = np.array([calib[c].project(np.array([x, y, TRAY.wall_height])) for x, y in poly])
            im = Image.new("1", (m.shape[1], m.shape[0]), 0)
            ImageDraw.Draw(im).polygon([tuple(p) for p in P], fill=1)
            proj = np.asarray(im, dtype=bool)
            u = (proj | m).sum()
            ok = u > 0 and (proj & m).sum() / u > 0.3
        if not ok:
            tracks.masks[c].pop(tray_label, None)
            tracks.notes.append(f"{c}: tray mask inconsistent with the top view, not used")


def _calibrate_second(tracks, top_cal, top, cam, Kc, table_label, gripper_label, i0=0):
    kinds = {l: k for l in tracks.masks.get(top, {}) for k in KIT if k in l}
    P3, P2 = [], []
    for lab, kind in kinds.items():
        mt, ms = tracks.get(top, lab, i0), tracks.get(cam, lab, i0)
        if mt is None or ms is None or not mt.any() or not ms.any():
            continue
        X = _plane_z(top_cal.ray(_centroid(mt)), KIT[kind])
        if X is None:
            continue
        P3.append(X - np.array([0, 0, KIT[kind] / 2]))
        P2.append(_centroid(ms))
    wall, wmask = None, None
    for lab in tracks.masks.get(top, {}):
        if lab.startswith("wall"):
            mt, ms = tracks.get(top, lab, i0), tracks.get(cam, lab, i0)
            if mt is None or ms is None or not mt.any() or not ms.any():
                continue
            ys, xs = np.nonzero(mt)
            pts = np.stack([xs, ys], 1).astype(float)
            c0 = pts.mean(0)
            w, V = np.linalg.eigh(np.cov((pts - c0).T))
            ax = V[:, np.argmax(w)]
            proj = (pts - c0) @ ax
            ea, eb = pts[np.argmin(proj)], pts[np.argmax(proj)]
            H_, W_ = mt.shape
            # ends cut by the image border are not wall ends
            if min(ea[0], ea[1], eb[0], eb[1]) <= 1 or ea[0] >= W_ - 2 or eb[0] >= W_ - 2 \
                    or ea[1] >= H_ - 2 or eb[1] >= H_ - 2:
                continue
            wall, wmask = (top_cal.ray(ea), top_cal.ray(eb)), ms
            break
    if len(P3) + (4 if wall is not None else 0) < 4:
        raise ValueError("not enough correspondences")
    return calibrate_from_scene(Kc, top_cal, P3, P2, wall=wall, side_wall_mask=wmask)


def _label_kinds(labels, prompts):
    kinds = {}
    for lab in labels:
        p = (prompts or {}).get(lab, lab).lower()
        for k in ("cup", "block", "marker"):
            if k in lab or k in p:
                kinds[lab] = k
    return kinds


def estimate(tracks: Tracks, K: dict, *, object_labels=None, prompts=None,
             gripper_label="gripper", table_label="table", calib_frame: int = 0,
             tilt_every: int = 1, bead_label: str = "beads", calib_override: Optional[dict] = None,
             intrinsics_source: str = "assumed_rendered_model", table_half=None,
             event_timing: str = "gripper_lowest") -> Estimates:
    """``calib_override``: camera -> Calib from a calibration board (real footage);
    the table outline and the kit table size are then not used. Otherwise each
    camera is calibrated from the table outline with ``table_half`` (default:
    the kit table, recorded as an assumption). ``intrinsics_source`` records
    where ``K`` came from (mirror.observability.IntrinsicsSource).
    ``event_timing``: "gripper_lowest" (grasp and release at the gripper's lowest
    point around each carry, seen as its smallest top-view outline) or "motion"
    (the object's motion onset and the gripper rising afterwards; the timing of
    results recorded before this option existed)."""
    cams = list(tracks.masks)
    calib, calib_choice, hyps = {}, {}, {}
    table_half_ = tuple(table_half) if table_half is not None else tuple(WORLD.table_half[:2])
    if calib_override:
        cams_cal = [c for c in cams if c in calib_override]
    else:
        cams_cal = cams
    for c in ([] if calib_override else cams):
        cands = [l for l in tracks.masks[c] if l == table_label or l.startswith(table_label + "~")]
        hyps[c] = []
        for lab in cands:
            tm = None
            for i in range(calib_frame, min(tracks.n_frames, calib_frame + 30)):
                tm = tracks.get(c, lab, i)
                if tm is not None and tm.any():
                    break
            if tm is None or tm.sum() < 500:
                continue
            try:
                hyps[c].append((calibrate(ndimage.binary_fill_holes(tm), np.asarray(K[c], float),
                                          table_half=table_half_), lab))
            except (ValueError, np.linalg.LinAlgError):
                continue
    if calib_override:
        for c in cams_cal:
            calib[c], calib_choice[c] = calib_override[c], "calibration board"
        top = "top" if "top" in calib else (cams_cal[0] if cams_cal else None)
        if top is None:
            raise ValueError("no calibrated camera")
    else:
        top = "top" if hyps.get("top") else next((c for c in cams if hyps.get(c)), None)
        if top is None:
            raise ValueError("no camera could be calibrated from the table")
        calib[top], calib_choice[top] = min(hyps[top], key=lambda h: h[0].residual)
    anchors = _resting_anchors(tracks, calib[top], top, calib_frame)
    for c in cams:
        if c == top or calib_override:
            continue
        best = None
        for cal, lab in hyps.get(c, []):
            if cal.residual > TABLE_FIT_MAX_PX:
                continue
            err = _anchor_error(cal, anchors, tracks, c, calib_frame)
            if err is not None and err <= ANCHOR_MAX_PX and (best is None or err < best[0]):
                best = (err, cal, lab)
        if best is not None:
            calib[c], calib_choice[c] = best[1], best[2]
            continue
        try:
            calib[c], _h = _calibrate_second(tracks, calib[top], top, c, K[c], table_label, gripper_label)
            err = _anchor_error(calib[c], anchors, tracks, c, calib_frame)
            if err is not None and err > ANCHOR_MAX_PX:
                calib.pop(c)
                tracks.notes.append(f"{c}: no consistent calibration (anchor error {err:.1f} px); camera not used")
            else:
                calib_choice[c] = "scene geometry (resting objects + wall)"
        except ValueError:
            tracks.notes.append(f"{c}: could not be calibrated; camera not used")
    _check_side_tray(tracks, calib, top)
    review: list = []
    calib_records = []
    for c in cams:
        if c not in calib:
            calib_records.append(dict(camera=c, intrinsics_source=intrinsics_source, extrinsics_source="scene_geometry",
                                      valid=False, assumptions=["camera could not be calibrated; not used"]))
            continue
        choice = calib_choice.get(c, "")
        if calib_override:
            ext, tsize, assume = "calibration_board", "not_used", []
        elif choice.startswith(table_label):
            ext, tsize = "table_outline_known_size", "kit_catalogue"
            assume = [f"table size {2 * table_half_[0]:.2f} m x {2 * table_half_[1]:.2f} m (kit catalogue)"]
        else:
            ext, tsize = "scene_geometry", "kit_catalogue"
            assume = ["object sizes from the kit catalogue", "pose solved against the calibrated top camera"]
        if intrinsics_source in ("assumed_rendered_model", "rendered_camera_model"):
            assume.append("intrinsics of the rendered demonstration camera")
        calib_records.append(dict(camera=c, intrinsics_source=intrinsics_source, extrinsics_source=ext,
                                  table_size_source=tsize, residual_px=round(float(calib[c].residual), 2),
                                  valid=bool(calib[c].residual <= TABLE_FIT_MAX_PX) if ext != "scene_geometry" else True,
                                  assumptions=assume))
    if intrinsics_source == "assumed_rendered_model":
        review.append(dict(code="calibration_assumed", severity="abstain",
                           message=("Camera intrinsics, table size and object sizes were taken from the simulated "
                                    "demonstration set-up without confirmation. Confirm the video is "
                                    "simulator-rendered, or calibrate the camera (docs/CAMERA_CAPTURE.md).")))
    if not any(c != top for c in calib):
        review.append(dict(code="single_view", severity="review",
                           message="Only one calibrated camera: object tilt cannot be measured."))
    labels = object_labels or sorted({lab for c in cams for lab in tracks.masks[c]
                                      if not lab.startswith(table_label)} - {gripper_label})
    kinds = _label_kinds(labels, prompts)
    obj_labels = [l for l in labels if l in kinds]
    n = tracks.n_frames
    t = np.arange(n) / tracks.fps
    pos = {l: np.full((n, 3), np.nan) for l in obj_labels}
    pstat = {l: np.full(n, "unobserved", dtype=object) for l in obj_labels}
    gstat = np.full(n, "unobserved", dtype=object)
    found = {l: np.zeros(n, bool) for l in obj_labels}
    single = {l: np.zeros(n, bool) for l in obj_labels}
    g_tri = np.zeros(n, bool)
    g_ray = {}
    top_ray = {l: {} for l in obj_labels}
    g_area = np.full(n, np.nan)
    tilt = {l: np.full(n, np.nan) for l in obj_labels if kinds[l] == "cup"}
    grip = np.full((n, 3), np.nan)
    last_z = {l: KIT[kinds[l]] for l in obj_labels}
    for i in range(n):
        for lab in obj_labels + [gripper_label]:
            rays = []
            for c, cal in calib.items():
                m = tracks.get(c, lab, i)
                cen = _centroid(m) if m is not None else None
                if cen is not None:
                    rays.append((c, cal.ray(cen)))
            if not rays:
                continue
            if lab == gripper_label:
                mg = tracks.get(top, lab, i)
                if mg is not None:
                    g_area[i] = float(mg.sum())
            if len(rays) >= 2:
                X = _triangulate([r for _, r in rays])
                # cameras disagree (a mask on the wrong thing in one view): trust the top view
                gap = max(np.linalg.norm(np.cross(X - o, d)) for _, (o, d) in rays)
                if gap > RAY_GAP_MAX:
                    rays = [r for r in rays if r[0] == top] or rays[:1]
            if len(rays) >= 2:
                pass
            else:
                zt = last_z.get(lab, 0.15)
                X = _plane_z(rays[0][1], zt)
                if X is None:
                    continue
                if lab in kinds:
                    X = X - np.array([0, 0, KIT[kinds[lab]] / 2])   # top surface -> centre
            if lab == gripper_label:
                grip[i] = X
                g_tri[i] = len(rays) >= 2
                gstat[i] = "measured" if len(rays) >= 2 else "inferred"
                top_r = [r for c_, r in rays if c_ == top]
                if top_r:
                    g_ray[i] = top_r[0]
                continue
            pos[lab][i] = X
            found[lab][i] = True
            pstat[lab][i] = "measured" if len(rays) >= 2 else "inferred"
            if len(rays) == 1:
                single[lab][i] = True
                top_ray[lab][i] = rays[0][1]
            last_z[lab] = X[2] + KIT[kinds[lab]] / 2
        side = next((c for c in calib if c != top), None)
        for lab in tilt:
            if side is None or np.isnan(pos[lab][i][0]):
                continue
            m = tracks.get(side, lab, i)
            if m is None or not m.any():
                continue
            X = pos[lab][i]
            yaw = 0.0
            mt = tracks.get(top, lab, i)
            if mt is not None and mt.any():
                a = _min_rect_yaw(mt)
                if a is not None:
                    # image angle -> world yaw through the top camera's x axis direction
                    u0 = calib[top].project(X)
                    ux = calib[top].project(X + np.array([0.05, 0, 0])) - u0
                    yaw = (a - math.atan2(ux[1], ux[0])) % (math.pi / 2)
            if (i % tilt_every) == 0:
                bead = tracks.get(side, bead_label, i)
                sil = m | bead if bead is not None else m
                sil = ndimage.binary_fill_holes(sil)
                occ = np.zeros_like(sil)
                touching = False
                ring = ndimage.binary_dilation(sil, iterations=3) & ~sil
                for other in tracks.masks.get(side, {}):
                    if other in (lab, bead_label) or other.startswith(table_label):
                        continue
                    om = tracks.get(side, other, i)
                    if om is not None:
                        occ |= ndimage.binary_dilation(om, iterations=1)
                        # partly hidden behind another thing (not the gripper holding it): unreliable
                        if other != gripper_label and (ring & om).sum() > 10:
                            touching = True
                if not touching:
                    tilt[lab][i] = tilt_by_silhouette(sil & ~occ, occ, calib[side], X, -yaw)
    # Gripper seen only from above: depth along the top camera's ray from its
    # apparent size (area ~ 1/depth^2), scaled on frames where it was triangulated.
    ref = [np.linalg.norm(grip[i] - g_ray[i][0]) * math.sqrt(g_area[i]) for i in range(n)
           if g_tri[i] and i in g_ray and not np.isnan(g_area[i]) and g_area[i] > 50]
    if len(ref) >= 5:
        kd = float(np.median(ref))
        for i in range(n):
            if not g_tri[i] and i in g_ray and not np.isnan(g_area[i]) and g_area[i] > 50:
                o, d = g_ray[i]
                grip[i] = o + d * (kd / math.sqrt(g_area[i]))
                gstat[i] = "inferred"
    k = max(1, int(round(SMOOTH_S * tracks.fps)))
    for lab, series in tilt.items():          # tilt changes smoothly: drop samples its neighbours do not support
        ok0 = np.flatnonzero(~np.isnan(series))
        w1 = int(tracks.fps)
        keep = series.copy()
        for j in ok0:
            nb = ok0[(np.abs(ok0 - j) <= w1) & (ok0 != j)]
            if len(nb) < 3 or abs(series[j] - np.median(series[nb])) > 10.0:
                keep[j] = np.nan
        series = keep
        tilt[lab] = series
        ok = ~np.isnan(series)
        if ok.sum() >= 3:
            filt = ndimage.median_filter(np.where(ok, series, np.interp(np.arange(n), np.flatnonzero(ok), series[ok])),
                                         size=max(3, int(2 * tracks.fps) | 1), mode="nearest")
            tilt[lab] = np.where(ok, filt, np.nan)
    tray_poly = _tray_polygon(tracks, calib, top)
    idx = np.arange(n)
    G = grip.copy()
    gok = ~np.isnan(G[:, 0])
    if gok.sum() >= 3:
        for d in range(3):
            G[:, d] = np.interp(idx, idx[gok], G[gok, d])
        G = _smooth(G, k)
    A = g_area.copy()
    aok = ~np.isnan(A)
    if aok.sum() >= 3:
        A = _smooth(np.interp(idx, idx[aok], A[aok]), k)

    def carries(P):
        v = np.r_[0.0, np.linalg.norm(np.diff(P, axis=0), axis=1) * tracks.fps]
        v = _smooth(v, k)
        lab_seg, nseg = ndimage.label(v > MOVE_SPEED)
        segs = []
        for s_ in range(1, nseg + 1):
            ii = np.flatnonzero(lab_seg == s_)
            if np.linalg.norm(P[ii[-1]] - P[ii[0]]) > 0.03 and len(ii) > tracks.fps * 0.5:
                # a carry moves the object to a new resting place
                w1 = max(1, int(tracks.fps))
                before = np.median(P[max(0, ii[0] - w1):ii[0] + 1], axis=0)
                after_ = np.median(P[ii[-1]:min(n, ii[-1] + w1 + 1)], axis=0)
                if np.linalg.norm(after_ - before) < 0.05:
                    continue
                # carried objects move with the gripper: it must be close above them
                if gok.sum() >= 3:
                    dxy = np.median(np.linalg.norm(G[ii, :2] - P[ii, :2], axis=1))
                    if dxy > CARRY_MAX_XY:
                        continue
                segs.append((ii[0], ii[-1]))
        merged = []
        for a, b in segs:
            if merged and a - merged[-1][1] < tracks.fps * 1.5:
                merged[-1] = (merged[-1][0], b)
            else:
                merged.append((a, b))
        return merged

    def filled(lab):
        P = pos[lab].copy()
        ok = found[lab]
        for d in range(3):
            P[:, d] = np.interp(idx, idx[ok], P[ok, d])
        return _smooth(P, k)

    events = []
    for lab in obj_labels:
        ok = found[lab]
        if ok.sum() < 3:
            continue
        segs = carries(filled(lab))
        carried = np.zeros(n, bool)
        for a, b in segs:
            carried[a:b + 1] = True
        # Seen by one camera and not carried: the object rests on the table or in the tray,
        # so intersect the top-view ray with the plane of its top surface there.
        h = KIT[kinds[lab]]
        for i in np.flatnonzero(single[lab] & ~carried):
            ray = top_ray[lab].get(i)
            if ray is None:
                continue
            X = _plane_z(ray, h)
            if X is not None and tray_poly is not None and _inside(X[:2], tray_poly):
                X = _plane_z(ray, h + TRAY_FLOOR)
            if X is not None:
                pos[lab][i] = X - np.array([0, 0, h / 2])
                pstat[lab][i] = "measured"      # top-view ray at the resting height
        # Carried and hidden under the gripper (small top-view mask, no second view):
        # the object moves with the gripper, at the offset measured when the carry began.
        areas = np.array([float(tracks.get(top, lab, i).sum()) if tracks.get(top, lab, i) is not None else 0.0
                          for i in range(n)])
        rest_area = np.median(areas[~carried & (areas > 0)]) if np.any(~carried & (areas > 0)) else 0.0
        if gok.sum() >= 3 and rest_area > 0:
            for a, b in segs:
                seen = [i for i in range(a, b + 1) if areas[i] >= 0.5 * rest_area and not np.isnan(pos[lab][i][0])]
                if not seen:
                    continue
                off = np.median(np.array([pos[lab][i] - G[i] for i in seen[:max(3, int(tracks.fps))]]), axis=0)
                for i in range(a, b + 1):
                    if areas[i] < 0.5 * rest_area and single[lab][i] | np.isnan(pos[lab][i][0]):
                        pos[lab][i] = G[i] + off
                        found[lab][i] = True
                        pstat[lab][i] = "inferred"   # hidden under the gripper: moves with it
        ok = found[lab]
        P = filled(lab)
        segs = carries(P)
        for a, b in segs:
            rel = b
            grasp_i = a
            basis = "object motion onset"
            if aok.sum() >= 3:
                # fingers opening or the gripper rising changes its outline from above
                # the gripper is lowest (smallest from above) when it lets go
                w_ = int(tracks.fps * 8.0)
                j0 = b + int(np.argmin(A[b:b + w_ + 1]))
                ref = A[j0]
                after = np.flatnonzero(A[j0:] > ref * 1.06)
                if event_timing == "gripper_lowest":
                    rel = j0
                    # grasp: the gripper's lowest point (smallest from above) just before the object moves
                    wg = int(round(tracks.fps * GRASP_LOOKBACK_S))
                    lo = max(0, a - wg)
                    grasp_i = lo + int(np.argmin(A[lo:a + 1]))
                    basis = "gripper lowest point (top-view outline smallest) around the object's motion"
                elif len(after) and j0 - b + after[0] < tracks.fps * 6:
                    rel = j0 + after[0]
            elif gok.sum() >= 3:
                d0 = np.linalg.norm(G[b] - P[b])
                after = np.flatnonzero(np.linalg.norm(G[b:] - P[b], axis=1) > d0 + 0.015)
                if len(after) and after[0] < tracks.fps * 6:
                    rel = b + after[0]
            events.append({"type": "grasp", "object": lab, "t": float(t[grasp_i]), "basis": basis})
            events.append({"type": "release", "object": lab, "t": float(t[rel]), "basis": basis})
        pos[lab] = np.where(ok[:, None], P, np.nan)
    events.sort(key=lambda e: e["t"])
    for lab in obj_labels:
        pstat[lab][~found[lab]] = "unobserved"
    tstat = {lab: np.where(np.isnan(series), "unobserved", "measured").astype(object) for lab, series in tilt.items()}
    # resting orientation (standing vs lying) from the top view, before the first grasp and after the last release
    rest = {}
    w_ = max(1, int(round(REST_WINDOW_S * tracks.fps)))
    for lab in obj_labels:
        evs = [e for e in events if e["object"] == lab]
        g0 = min((e["t"] for e in evs if e["type"] == "grasp"), default=None)
        r1 = max((e["t"] for e in evs if e["type"] == "release"), default=None)
        i_g = int(np.searchsorted(t, g0)) if g0 is not None else n
        i_r = int(np.searchsorted(t, r1 + 0.5)) if r1 is not None else 0
        start_fr = list(range(calib_frame, min(i_g, calib_frame + w_)))
        end_fr = list(range(max(i_r, n - w_), n)) if r1 is not None else []
        rest[lab] = {"start": _rest_tilt_window(tracks, calib, top, lab, kinds[lab], pos[lab], start_fr, bead_label,
                                                tray_poly),
                     "end": (_rest_tilt_window(tracks, calib, top, lab, kinds[lab], pos[lab], end_fr, bead_label,
                                               tray_poly) if end_fr else None)}
    # identity
    identity = {}
    by_kind: dict = {}
    for lab in obj_labels:
        by_kind.setdefault(kinds[lab], []).append(lab)
    for lab in obj_labels:
        fr = float(found[lab].mean()) if n else 0.0
        how = "label" if kinds[lab] in lab else "grounding prompt"
        note = f"kind '{kinds[lab]}' from the tracking {how}; found in {100 * fr:.0f}% of frames"
        ident = "assumed_from_label"
        if not found[lab].any():
            ident = "not_found"
            review.append(dict(code="object_not_found", severity="abstain", object_id=lab,
                               message=f"{lab} was never found in the video."))
        elif len(by_kind[kinds[lab]]) > 1:
            ident = "ambiguous"
            review.append(dict(code="identity_ambiguous", severity="review", object_id=lab,
                               message=f"Several tracks are labelled {kinds[lab]}: confirm which is which."))
        elif fr < 0.5:
            review.append(dict(code="low_coverage", severity="review", object_id=lab,
                               message=f"{lab} was found in only {100 * fr:.0f}% of frames."))
        identity[lab] = dict(identity=ident, note=note)
    for lab in [l for l in labels if l not in kinds and not l.startswith("wall") and not l.startswith("tray")
                and l != bead_label]:
        review.append(dict(code="unknown_object", severity="review", object_id=lab,
                           message=f"Tracked label '{lab}' is not a supported kit object; it was ignored."))
    return Estimates(t=t, objects=pos, found=found, tilt_deg=tilt, gripper=grip, events=events,
                     calib=calib, scene_checks={"kinds": kinds, "calibration_mask": calib_choice,
                                                "calibration_residual_px": {c: round(v.residual, 2)
                                                                            for c, v in calib.items()}},
                     pos_status=pstat, tilt_status=tstat, gripper_status=gstat, rest_tilt=rest,
                     identity=identity, calibration=calib_records, review=review)


# ------------------------------------------------------------ scene


def _wall_from_masks(tracks, calib, label, top, side, i0=0, foreground=()):
    m = tracks.get(top, label, i0)
    if m is None or not m.any():
        return None
    h = WORLD.wall_height
    cal = calib[top]
    ys, xs = np.nonzero(m)
    sel = np.linspace(0, len(xs) - 1, min(len(xs), 3000)).astype(int)
    for _ in range(3):
        P = np.array([_plane_z(cal.ray((xs[j], ys[j])), h)[:2] for j in sel])
        ctr = P.mean(0)
        w, V = np.linalg.eigh(np.cov((P - ctr).T))
        axis = V[:, np.argmax(w)]
        along = (P - ctr) @ axis
        length = float(along.max() - along.min())
        ctr = ctr + axis * 0.5 * (along.max() + along.min())
        normal = np.array([-axis[1], axis[0]])
        if side is not None and side in calib:
            ms = tracks.get(side, label, i0)
            if ms is not None and ms.any():
                cs = calib[side]
                yy, xx = np.nonzero(ms)
                top_rows = {}
                for x_, y_ in zip(xx, yy):
                    if x_ not in top_rows or y_ < top_rows[x_]:
                        top_rows[x_] = y_
                zs = []
                for x_, y_ in top_rows.items():
                    o, d = cs.ray((x_, y_))
                    den = d[:2] @ normal
                    if abs(den) < 1e-6:
                        continue
                    s = ((ctr - o[:2]) @ normal) / den
                    zs.append((o + s * d)[2])
                if zs:
                    h = float(np.percentile(zs, 95))
    if side is not None and side in calib:
        ms = tracks.get(side, label, i0)
        if ms is not None and ms.any():
            cs = calib[side]
            yy, xx = np.nonzero(ms)
            sel2 = np.linspace(0, len(xx) - 1, min(len(xx), 3000)).astype(int)
            al = []
            for j in sel2:
                o, d = cs.ray((xx[j], yy[j]))
                den = d[:2] @ normal
                if abs(den) < 1e-6:
                    continue
                s_ = ((ctr - o[:2]) @ normal) / den
                X = o + s_ * d
                if -0.01 <= X[2] <= h + 0.02:
                    al.append((X[:2] - ctr) @ axis)
            if al:
                lo = min(-length / 2, float(np.percentile(al, 0.5)))
                hi = max(length / 2, float(np.percentile(al, 99.5)))
                ctr = ctr + axis * 0.5 * (lo + hi)
                length = hi - lo
    yaw = math.atan2(axis[1], axis[0]) - math.pi / 2     # wall local y along its length
    slots = []
    if side is not None and side in calib:
        ms = tracks.get(side, label, i0)
        if ms is not None and ms.any():
            filled = ndimage.binary_fill_holes(ms)
            holes = filled & ~ms
            for other in tracks.masks.get(side, {}):
                if other != label and other in foreground:
                    om = tracks.get(side, other, i0)
                    if om is not None:
                        holes &= ~ndimage.binary_dilation(om, iterations=2)
            lab_h, nh = ndimage.label(holes)
            cs = calib[side]
            for k in range(1, nh + 1):
                yy, xx = np.nonzero(lab_h == k)
                if len(xx) < 150:
                    continue
                pts = []
                for x_, y_ in zip(xx[::7], yy[::7]):
                    o, d = cs.ray((x_, y_))
                    den = d[:2] @ normal
                    if abs(den) < 1e-6:
                        continue
                    s = ((ctr - o[:2]) @ normal) / den
                    X = o + s * d
                    pts.append(((X[:2] - ctr) @ axis, X[2]))
                if len(pts) < 10:
                    continue
                pts = np.array(pts)
                fill = len(xx) / max(1, (xx.max() - xx.min() + 1) * (yy.max() - yy.min() + 1))
                if fill < 0.6:
                    continue
                slots.append(SlotSpec(y_min=float(pts[:, 0].min()), y_max=float(pts[:, 0].max()),
                                      z_min=float(max(0.0, pts[:, 1].min())), z_max=float(pts[:, 1].max())))
    return dict(center=ctr, yaw=yaw, length=length, height=h, slots=slots)


def scene_from_video(tracks: Tracks, est: Estimates, *, scene_id: str, prompts=None,
                     tray_label="tray_0", wall_labels=("wall_0",), bead_label="beads") -> tuple[SceneState, dict]:
    """A SceneState built only from the video estimates and the kit catalogue."""
    from mirror.scenes.objects import make_block, make_cup, make_marker, make_tray, yaw_quat
    from mirror.scenes.sampling import default_workspace
    calib = est.calib
    top = "top" if "top" in calib else next(iter(calib))
    side = next((c for c in calib if c != top), None)
    checks = {}
    objects = []
    kinds = est.scene_checks["kinds"]
    for lab, kind in kinds.items():
        P = est.objects[lab]
        ok = np.flatnonzero(est.found[lab])
        if len(ok) == 0:
            continue
        x, y = np.nanmedian(P[ok[:max(1, int(tracks.fps))], :2], axis=0)
        oid = lab if "_" in lab and lab.split("_")[-1].isdigit() else f"{kind}_0"
        obj = {"cup": make_cup, "block": make_block, "marker": make_marker}[kind](oid, float(x), float(y))
        objects.append(obj)
    # beads: area of the bead mask (first second, top view) / area of one bead seen from above
    cal = calib[top]
    cups = [o for o in objects if o.kind == "cup"]
    for o in cups:
        areas = []
        for i in range(0, min(tracks.n_frames, int(tracks.fps))):
            m = tracks.get(top, bead_label, i)
            if m is not None:
                areas.append(int(m.sum()))
        n_beads = 0
        if areas:
            p0 = np.array(o.pose.pos) + np.array([0, 0, -CUP.outer_half[2] + 2 * CUP.bead_radius])
            u0 = cal.project(p0)
            u1 = cal.project(p0 + np.array([CUP.bead_radius, 0, 0]))
            r_px = float(np.linalg.norm(u1 - u0))
            one = math.pi * r_px ** 2
            n_beads = int(round(np.median(areas) / one)) if one > 0 else 0
        if "ball_count" in (tracks.extra or {}):
            n_beads = int(tracks.extra["ball_count"])
            checks["beads_source"] = "grounding count (visible balls)"
        o.beads = max(0, n_beads)
        checks["beads_estimated"] = o.beads
    trays = []
    tm = tracks.get(top, tray_label, 0)
    if tm is not None and tm.any():
        ys, xs = np.nonzero(tm)
        P = np.array([_plane_z(cal.ray((x_, y_)), TRAY.wall_height + 0.006)[:2]
                      for x_, y_ in zip(xs[::5], ys[::5])])
        ctr = P.mean(0)
        w, V = np.linalg.eigh(np.cov((P - ctr).T))
        ax = V[:, np.argmax(w)]
        yaw = math.atan2(ax[1], ax[0])
        yaw = (yaw + math.pi / 2) % math.pi - math.pi / 2
        ext = np.abs((P - ctr) @ V)
        checks["tray_extent_m"] = [float(2 * np.percentile(ext[:, np.argmax(w)], 99)),
                                   float(2 * np.percentile(ext[:, np.argmin(w)], 99))]
        checks["tray_catalogue_outer_m"] = [2 * (TRAY.inner_half[0] + TRAY.wall_thickness),
                                            2 * (TRAY.inner_half[1] + TRAY.wall_thickness)]
        trays.append(make_tray(tray_label, float(ctr[0]), float(ctr[1]), yaw))
    obstacles = []
    for wl in wall_labels:
        fg = set(kinds) | {"gripper", bead_label}
        w_ = _wall_from_masks(tracks, calib, wl, top, side, foreground=fg)
        if w_ is None:
            continue
        obstacles.append(ObstacleSpec(id=wl, kind="wall",
                                      pose=Pose(pos=(float(w_["center"][0]), float(w_["center"][1]), 0.0),
                                                quat=yaw_quat(w_["yaw"])),
                                      length=w_["length"], height=w_["height"],
                                      thickness=WORLD.wall_thickness, slots=w_["slots"]))
        checks[f"{wl}_height_m"] = w_["height"]
    g0 = est.gripper[np.flatnonzero(~np.isnan(est.gripper[:, 0]))[0]] if np.any(~np.isnan(est.gripper[:, 0])) \
        else np.array([0.0, 0.0, 0.3])
    scene = SceneState(scene_id=scene_id, split="custom", objects=objects, trays=trays, obstacles=obstacles,
                       workspace=default_workspace(),
                       gripper_start=Pose(pos=(float(g0[0]), float(g0[1]), float(g0[2]))),
                       notes="reconstructed from video (positions measured; sizes from the kit catalogue)")
    return scene, checks


INTERP_MAX_S = 1.0          # gaps between measured positions up to this long are interpolated
TILT_INTERP_MAX_S = 0.6     # ... and between measured tilts (inside one carry)


def _gap_fill(status: np.ndarray, values: np.ndarray, t: np.ndarray, max_gap_s: float, good=("measured", "inferred")):
    """Linear interpolation across short runs of non-``good`` frames bounded by good
    frames on both sides. Returns (values, status) copies; filled frames get
    status "interpolated"."""
    st = status.copy()
    v = values.copy()
    ok = np.isin(st, good)
    idx = np.flatnonzero(ok)
    for a, b in zip(idx, idx[1:]):
        if b - a > 1 and t[b] - t[a] <= max_gap_s:
            for j in range(a + 1, b):
                w = (t[j] - t[a]) / (t[b] - t[a])
                v[j] = (1 - w) * v[a] + w * v[b]
                st[j] = "interpolated"
    return v, st


def _carry_mask(est: Estimates, lab: str) -> np.ndarray:
    evs = sorted((e for e in est.events if e["object"] == lab), key=lambda e: e["t"])
    m = np.zeros(len(est.t), bool)
    start = None
    for e in evs:
        if e["type"] == "grasp" and start is None:
            start = e["t"]
        elif e["type"] == "release" and start is not None:
            m |= (est.t >= start) & (est.t <= e["t"])
            start = None
    if start is not None:
        m |= est.t >= start
    return m


def evidence_from_estimates(tracks: Tracks, est: Estimates, ids: dict, *, kind: Optional[str] = None,
                            media: Optional[dict] = None, models: Optional[list] = None):
    """Per-frame observability and provenance for the trace built from ``est``.
    Returns (TraceEvidence, positions, quats) where positions/quats are the
    per-label arrays written into the trace (placeholders where unobserved)."""
    from mirror.observability import (CalibrationRecord, ModelArtifact, ObjectEvidence, ReviewItem, TraceEvidence,
                                      spans_from_statuses)
    n = len(est.t)
    t = est.t
    if kind is None:
        srcs = {c.get("intrinsics_source") for c in est.calibration if c.get("valid", True)}
        if est.scene_checks.get("calibration_mask") and all(v == "calibration board" for v in
                                                           est.scene_checks["calibration_mask"].values()) \
                and srcs <= {"checkerboard", "user_supplied"}:
            kind = "camera_video_reconstruction"
        elif srcs == {"rendered_camera_model"}:
            kind = "rendered_video_reconstruction"
        else:
            kind = "unverified_video_reconstruction"
    kinds = est.scene_checks["kinds"]
    positions, quats, objs = {}, {}, {}
    review = [ReviewItem(**r) for r in est.review]
    pos_sigma = {"measured": 0.01, "inferred": 0.02, "interpolated": 0.02}
    for lab in kinds:
        if lab not in ids:
            continue
        P = np.asarray(est.objects[lab], float)
        st = np.asarray(est.pos_status.get(lab, np.where(np.isnan(P[:, 0]), "unobserved", "measured")), dtype=object)
        P, st = _gap_fill(st, P, t, INTERP_MAX_S)
        # beyond short gaps: hold the nearest known position for display only
        known = np.flatnonzero(~np.isnan(P[:, 0]))
        if len(known):
            for j in range(n):
                if np.isnan(P[j, 0]):
                    k = known[np.searchsorted(known, j, side="right") - 1] if j > known[0] else known[0]
                    P[j] = P[k]
                    st[j] = "unobserved"
        positions[lab] = P
        # tilt
        carried = _carry_mask(est, lab)
        tilt = np.full(n, np.nan)
        tst = np.full(n, "unobserved", dtype=object)
        tbasis = {"unobserved": "not observed (the stored orientation is a display placeholder)"}
        if lab in est.tilt_deg:
            tilt = np.asarray(est.tilt_deg[lab], float).copy()
            tst = np.asarray(est.tilt_status.get(lab, np.where(np.isnan(tilt), "unobserved", "measured")), dtype=object)
            # bridge short gaps only inside one carry
            filled, fst = _gap_fill(tst, tilt, t, TILT_INTERP_MAX_S, good=("measured",))
            same = np.ones(n, bool)
            for j in np.flatnonzero(fst == "interpolated"):
                same[j] = carried[j]
            sel = (fst == "interpolated") & same
            tilt[sel], tst[sel] = filled[sel], "interpolated"
            tbasis["measured"] = "side-view silhouette fit (in-image component of tilt)"
            tbasis["interpolated"] = f"interpolated across a gap of at most {TILT_INTERP_MAX_S:g} s"
        rest = est.rest_tilt.get(lab, {})
        i_first = int(np.argmax(carried)) if carried.any() else n
        i_last = int(n - 1 - np.argmax(carried[::-1])) if carried.any() else -1
        for key, rng in (("start", range(0, i_first)), ("end", range(i_last + 1, n) if carried.any() else range(0))):
            inf = rest.get(key)
            if not inf or inf.get("status") != "inferred":
                continue
            for j in rng:
                if tst[j] == "unobserved":
                    tst[j] = "inferred"
                    tilt[j] = 0.0 if inf.get("upright") else 90.0
            tbasis["inferred"] = (tbasis.get("inferred", "") + ("; " if tbasis.get("inferred") else "")
                                  + f"{key}: {inf.get('basis', '')}")
        q = np.zeros((n, 4))
        q[:, 0] = 1.0
        for j in range(n):
            if tst[j] != "unobserved" and not np.isnan(tilt[j]):
                q[j] = _tilt_quat(0.0, float(tilt[j]))
        quats[lab] = q
        tsig = {"measured": 5.0, "interpolated": 8.0, "inferred": REST_TILT_SIGMA_DEG}
        ident = est.identity.get(lab, {})
        objs[ids[lab]] = ObjectEvidence(
            object_id=ids[lab], label=lab, identity=ident.get("identity", "assumed_from_label"),
            identity_note=ident.get("note", ""),
            position=spans_from_statuses(list(st), sigma=pos_sigma, basis={
                "measured": "mask centroid (two views triangulated, or the top-view ray at the resting height)",
                "inferred": "one view with an assumed height, or hidden under the gripper and moved with it",
                "interpolated": f"interpolated across a gap of at most {INTERP_MAX_S:g} s",
                "unobserved": "not seen: nearest known position held for display"}),
            tilt=spans_from_statuses(list(tst), sigma=tsig, basis=tbasis))
        if carried.any() and lab in est.tilt_deg:
            cov = float(np.mean(np.isin(tst[carried], ("measured", "interpolated"))))
            if cov < 0.8:
                review.append(ReviewItem(code="tilt_unobserved", severity="review", object_id=ids[lab],
                                         message=(f"{kinds[lab]} tilt was observed on {100 * cov:.0f}% of the "
                                                  "time it was carried; the rest is unknown, not upright.")))
    gst = np.asarray(est.gripper_status if est.gripper_status is not None
                     else np.where(np.isnan(est.gripper[:, 0]), "unobserved", "measured"), dtype=object)
    G, gst = _gap_fill(gst, np.asarray(est.gripper, float), t, INTERP_MAX_S)
    positions["__gripper__"] = G
    ev = TraceEvidence(
        kind=kind, cameras=sorted(tracks.masks), media=dict(media or {}),
        calibration=[CalibrationRecord(**c) for c in est.calibration], objects=objs,
        gripper_position=spans_from_statuses(list(gst), basis={
            "measured": "two views triangulated", "inferred": "one view (depth from apparent size or assumed height)",
            "interpolated": f"interpolated across a gap of at most {INTERP_MAX_S:g} s",
            "unobserved": "not seen: last known position held for display"}),
        gripper_width=spans_from_statuses(["inferred"] * n, basis={
            "inferred": "not observed: closed while an object is carried, open otherwise"}),
        events_basis=("grasp and release inferred from object motion between resting places; release timing "
                      "refined from the gripper's top-view outline"),
        models=[ModelArtifact(**m) for m in (models or [])], review=review,
        notes=[f"tracks: {tracks.source}"] + [x for x in (tracks.notes or []) if not x.startswith("{")])
    return ev, positions, quats


def trace_from_estimates(tracks: Tracks, est: Estimates, scene: SceneState, *, demo_id: str,
                         video_path: Optional[str] = None, kind: Optional[str] = None,
                         media: Optional[dict] = None, models: Optional[list] = None) -> DemoTrace:
    """``kind``: mirror.observability.EvidenceKind; by default derived from how
    the cameras were calibrated (assumed intrinsics -> unverified)."""
    kinds = est.scene_checks["kinds"]
    ids = {lab: (lab if any(o.id == lab for o in scene.objects) else f"{kinds[lab]}_0") for lab in kinds}
    evidence, positions, quats = evidence_from_estimates(tracks, est, ids, kind=kind, media=media, models=models)
    held = {}
    for e in est.events:
        held.setdefault(e["object"], []).append(e)
    frames = []
    G = positions["__gripper__"]
    for i, ti in enumerate(est.t):
        objs = {}
        for lab, P in positions.items():
            if lab not in ids or np.isnan(P[i][0]):
                continue
            objs[ids[lab]] = Pose(pos=tuple(float(v) for v in P[i]), quat=tuple(float(v) for v in quats[lab][i]))
        g = G[i]
        if np.isnan(g[0]):
            prev = [j for j in range(i) if not np.isnan(G[j][0])]
            g = G[prev[-1]] if prev else np.array(scene.gripper_start.pos)
        carrying = any(e0["t"] <= ti <= e1["t"] for lab, evs in held.items()
                       for e0, e1 in zip(evs[0::2], evs[1::2]))
        frames.append(TraceFrame(t=float(ti), gripper=Pose(pos=tuple(float(v) for v in g)),
                                 gripper_width=0.0 if carrying else GRIPPER.max_opening, objects=objs))
    events = [TraceEvent(t=e["t"], type=e["type"], object_id=ids.get(e["object"], e["object"]),
                         detail={"from": "video", "observability": "inferred",
                                 "basis": e.get("basis", "object motion")}) for e in est.events]
    what = {"rendered_video_reconstruction": "simulator-rendered video",
            "camera_video_reconstruction": "calibrated camera footage",
            "unverified_video_reconstruction": "video with assumed camera calibration"}.get(evidence.kind, "video")
    note = (f"Reconstructed from {what} with {tracks.source}: positions from mask centroids (two views "
            "triangulated, or one view at a known height), carry events from object motion, cup tilt from the "
            "side-view silhouette (in-image component only). Values not observed are marked unobserved in the "
            "trace's evidence and are display placeholders, not measurements. Gripper width is not observed.")
    return DemoTrace(demo_id=demo_id, scene=scene, source="video_grounded", source_note=note,
                     sample_hz=tracks.fps, frames=frames, events=events, video_path=video_path, evidence=evidence)
