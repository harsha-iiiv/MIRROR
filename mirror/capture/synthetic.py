"""Synthetic renders for testing the phone-capture path.

SYNTHETIC: everything produced here is drawn by a small rasteriser from known
cameras and known object motion. It exists so the capture code can be tested
against exact ground truth. It is never evidence about real phone footage, and
every file it writes says so in its name or metadata (``synthetic: true``).

The camera model is OpenCV's pinhole model with radial/tangential distortion:
an ideal pinhole image is rasterised first and then warped with the
distortion, so tests exercise the same model the calibration estimates.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

import numpy as np

from mirror.capture.target import DEFAULT_TARGET, TargetSpec, board_bitmap, marker_cells

SYNTHETIC_NOTE = "SYNTHETIC render for tests; not real footage"


def rodrigues(rvec) -> np.ndarray:
    import cv2
    return cv2.Rodrigues(np.asarray(rvec, float).reshape(3))[0]


def look_at(eye, target, up=(0, 0, 1)) -> tuple[np.ndarray, np.ndarray]:
    """World->camera rotation R and translation t (x_cam = R x_world + t) for an
    OpenCV camera (x right, y down, z forward) at ``eye`` looking at ``target``."""
    eye, target, up = (np.asarray(v, float) for v in (eye, target, up))
    z = target - eye
    z /= np.linalg.norm(z)
    x = np.cross(z, up)
    if np.linalg.norm(x) < 1e-9:
        x = np.cross(z, (0, 1, 0))
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    R = np.stack([x, y, z])
    return R, -R @ eye


@dataclass
class SynthCamera:
    name: str
    width: int
    height: int
    K: np.ndarray
    dist: np.ndarray
    R: np.ndarray      # world (table frame, z up) -> camera
    t: np.ndarray

    @staticmethod
    def make(name, width, height, hfov_deg, eye, target, dist=(0.0, 0.0, 0.0, 0.0, 0.0), roll_deg=0.0):
        f = 0.5 * width / np.tan(np.radians(hfov_deg) / 2)
        K = np.array([[f, 0, width / 2 - 0.5 + 3.0], [0, f, height / 2 - 0.5 - 2.0], [0, 0, 1.0]])
        R, t = look_at(eye, target)
        if roll_deg:
            c, s = np.cos(np.radians(roll_deg)), np.sin(np.radians(roll_deg))
            Rz = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])
            R, t = Rz @ R, Rz @ t
        return SynthCamera(name, width, height, K, np.asarray(dist, float), R, t)

    def project_ideal(self, X) -> tuple[np.ndarray, np.ndarray]:
        X = np.asarray(X, float).reshape(-1, 3)
        Xc = X @ self.R.T + self.t
        z = Xc[:, 2]
        uv = (Xc[:, :2] / z[:, None]) @ self.K[:2, :2].T + self.K[:2, 2]
        return uv, z

    def project(self, X) -> np.ndarray:
        import cv2
        X = np.asarray(X, float).reshape(-1, 3)
        rvec = cv2.Rodrigues(self.R)[0]
        uv, _ = cv2.projectPoints(X, rvec, self.t, self.K, self.dist)
        return uv.reshape(-1, 2)

    def distort_image(self, ideal: np.ndarray) -> np.ndarray:
        import cv2
        if not np.any(self.dist):
            return ideal
        key = (self.width, self.height, tuple(self.dist), tuple(self.K.ravel()))
        maps = _MAP_CACHE.get(key)
        if maps is None:
            ys, xs = np.mgrid[0:self.height, 0:self.width].astype(np.float32)
            pts = np.stack([xs.ravel(), ys.ravel()], 1).reshape(-1, 1, 2)
            und = cv2.undistortPoints(pts, self.K, self.dist, P=self.K).reshape(self.height, self.width, 2)
            maps = (und[..., 0].astype(np.float32), und[..., 1].astype(np.float32))
            _MAP_CACHE[key] = maps
        return cv2.remap(ideal, maps[0], maps[1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


_MAP_CACHE: dict = {}


# ------------------------------------------------------------------ primitives


@dataclass
class Prim:
    """A convex-faced solid: faces are lists of 3D vertices in its local frame."""
    name: str
    faces: list
    color: tuple
    pose_fn: Callable[[float], tuple]   # t -> (pos(3), R(3x3)); None pose = hidden
    texture: Optional[np.ndarray] = None  # for single-face planar textured prims (the board, the sync card)


def box_faces(hx, hy, hz):
    v = np.array([[sx * hx, sy * hy, sz * hz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)])
    idx = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    return [v[list(f)] for f in idx]


def cylinder_faces(r, hz, n=20):
    a = np.linspace(0, 2 * np.pi, n, endpoint=False)
    ring = np.stack([r * np.cos(a), r * np.sin(a)], 1)
    faces = []
    for i in range(n):
        j = (i + 1) % n
        faces.append(np.array([[*ring[i], -hz], [*ring[j], -hz], [*ring[j], hz], [*ring[i], hz]]))
    faces.append(np.array([[x, y, hz] for x, y in ring]))
    faces.append(np.array([[x, y, -hz] for x, y in ring[::-1]]))
    return faces


def tilt_R(tilt_deg: float, axis=(1.0, 0.0, 0.0), yaw_deg: float = 0.0) -> np.ndarray:
    ax = np.asarray(axis, float)
    ax = ax / np.linalg.norm(ax)
    R = rodrigues(ax * np.radians(tilt_deg))
    c, s = np.cos(np.radians(yaw_deg)), np.sin(np.radians(yaw_deg))
    return R @ np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])


def static_pose(pos, R=None):
    R = np.eye(3) if R is None else R
    return lambda t: (np.asarray(pos, float), R)


def board_prim(spec: TargetSpec = DEFAULT_TARGET, origin=(0.0, 0.0), visible=lambda t: True, square_mm=None) -> Prim:
    """The printed board lying flat on the table. Table frame: board x along +x,
    board y (down the page) along -y, so the board's printed top-left corner is
    at ``origin`` and the board extends to +x and -y."""
    sq = (square_mm or spec.square_mm) / 1000.0
    w, h = spec.squares_x * sq, spec.squares_y * sq
    face = np.array([[0, 0, 0], [w, 0, 0], [w, -h, 0], [0, -h, 0]], float)
    tex = board_bitmap(spec, px_per_mm=8.0)
    o = np.array([origin[0], origin[1], 0.0005])
    return Prim("board", [face], (255, 255, 255), lambda t: (o, np.eye(3)) if visible(t) else None, texture=tex)


def sync_card_prim(spec: TargetSpec, center, normal_toward, visible: Callable[[float], bool], size_m=0.08) -> Prim:
    cells = marker_cells(spec, spec.sync_marker_id, spec.sync_marker_mm)
    tex = np.kron(np.where(cells, 0, 255).astype(np.uint8), np.ones((24, 24), np.uint8))
    tex = np.pad(tex, 24, constant_values=255)
    s = size_m * tex.shape[0] / (cells.shape[0] * 24) / 2
    n = np.asarray(normal_toward, float)
    n /= np.linalg.norm(n)
    up = np.array([0, 0, 1.0]) if abs(n[2]) < 0.9 else np.array([0, 1.0, 0])
    x = np.cross(up, n)
    x /= np.linalg.norm(x)
    y = np.cross(n, x)
    c = np.asarray(center, float)
    face = np.array([c - s * x + s * y, c + s * x + s * y, c + s * x - s * y, c - s * x - s * y])
    return Prim("sync_card", [face], (255, 255, 255), lambda t: (np.zeros(3), np.eye(3)) if visible(t) else None, texture=tex)


# ------------------------------------------------------------------ rasteriser


def _shade(color, normal_world, light=(0.3, -0.5, 0.8)):
    l = np.asarray(light, float)
    l /= np.linalg.norm(l)
    k = 0.55 + 0.45 * max(0.0, float(np.dot(normal_world, l)))
    return tuple(int(min(255, c * k)) for c in color)


def render(cam: SynthCamera, prims: Sequence[Prim], t: float, *, background=None, gain: float = 1.0,
           noise: float = 2.0, seed: int = 0) -> np.ndarray:
    """RGB uint8 frame of the scene at time t from ``cam`` (synthetic)."""
    import cv2
    H, W = cam.height, cam.width
    if background is None:
        background = table_background(cam)
    img = background.copy()
    polys = []
    for p in prims:
        pose = p.pose_fn(t)
        if pose is None:
            continue
        pos, R = pose
        for f in p.faces:
            Xw = f @ R.T + pos
            uv, z = cam.project_ideal(Xw)
            if np.any(z <= 0.01):
                continue
            nrm = np.cross(Xw[1] - Xw[0], Xw[2] - Xw[0])
            if np.linalg.norm(nrm) < 1e-12:
                continue
            nrm /= np.linalg.norm(nrm)
            centre = Xw.mean(0)
            cam_pos = -cam.R.T @ cam.t
            if p.texture is None and np.dot(nrm, cam_pos - centre) <= 0:
                continue  # back face
            depth = float(np.linalg.norm(centre - cam_pos))
            polys.append((depth, p, uv, nrm))
    for depth, p, uv, nrm in sorted(polys, key=lambda q: -q[0]):
        if p.texture is not None:
            th, tw = p.texture.shape[:2]
            src = np.float32([[0, 0], [tw, 0], [tw, th], [0, th]])
            Hm = cv2.getPerspectiveTransform(src, np.float32(uv))
            tex = p.texture if p.texture.ndim == 3 else np.repeat(p.texture[..., None], 3, 2)
            warped = cv2.warpPerspective(tex, Hm, (W, H), flags=cv2.INTER_AREA)
            mask = cv2.warpPerspective(np.full((th, tw), 255, np.uint8), Hm, (W, H), flags=cv2.INTER_NEAREST)
            img[mask > 0] = warped[mask > 0]
        else:
            cv2.fillConvexPoly(img, np.round(uv * 16).astype(np.int32), _shade(p.color, nrm), cv2.LINE_AA, shift=4)
    img = cam.distort_image(img)
    if gain != 1.0:
        img = np.clip(img.astype(np.float32) * gain, 0, 255).astype(np.uint8)
    if noise:
        rng = np.random.default_rng(seed)
        img = np.clip(img.astype(np.int16) + rng.normal(0, noise, img.shape).astype(np.int16), 0, 255).astype(np.uint8)
    return img


def table_background(cam: SynthCamera, seed: int = 3) -> np.ndarray:
    """Wood-ish table plane (large, untextured beyond it) seen by ``cam``."""
    import cv2
    rng = np.random.default_rng(seed)
    tex = (rng.normal(0, 1, (64, 64)))
    tex = cv2.resize(tex, (512, 512), interpolation=cv2.INTER_CUBIC)
    base = np.stack([150 + 18 * tex, 118 + 14 * tex, 86 + 10 * tex], -1).clip(0, 255).astype(np.uint8)
    img = np.full((cam.height, cam.width, 3), (205, 208, 212), np.uint8)
    half = 0.9
    corners = np.array([[-half, half, 0], [half, half, 0], [half, -half, 0], [-half, -half, 0]], float)
    uv, z = cam.project_ideal(corners)
    if np.all(z > 0):
        src = np.float32([[0, 0], [512, 0], [512, 512], [0, 512]])
        Hm = cv2.getPerspectiveTransform(src, np.float32(uv))
        warped = cv2.warpPerspective(base, Hm, (cam.width, cam.height))
        mask = cv2.warpPerspective(np.full((512, 512), 255, np.uint8), Hm, (cam.width, cam.height))
        img[mask > 0] = warped[mask > 0]
    return img


# ------------------------------------------------------------------ video I/O


def frame_times(n: int, fps: float, *, jitter: float = 0.0, start: float = 0.0) -> list[float]:
    """Clip timestamps used by ``write_video``: k/fps + jitter*sin(1.7k) + start
    (jitter < 0.3/fps keeps them increasing; non-zero jitter = variable frame rate)."""
    return [start + k / fps + jitter * np.sin(1.7 * k) for k in range(n)]


def write_video(frames: Sequence[np.ndarray], path, *, fps: float, jitter: float = 0.0, start: float = 0.0,
                crf: int = 18) -> Path:
    """Encode RGB frames so that frame k has presentation time frame_times(...)[k]
    exactly (to the 1/90000 s container clock)."""
    path = Path(path)
    ff = shutil.which("ffmpeg")
    if ff is None:
        raise RuntimeError("ffmpeg is required")
    h, w = frames[0].shape[:2]
    vf = f"settb=1/90000,setpts=(N/{fps!r}+{jitter!r}*sin(N*1.7))/TB"
    cmd = [ff, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", repr(fps), "-i", "-",
           "-vf", vf, "-fps_mode", "passthrough", "-enc_time_base:v", "1:90000", "-c:v", "libx264", "-bf", "0", "-pix_fmt", "yuv420p", "-crf", str(crf),
           "-video_track_timescale", "90000", "-output_ts_offset", f"{start:.6f}",
           "-metadata", "comment=" + SYNTHETIC_NOTE, str(path)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    for f in frames:
        p.stdin.write(np.ascontiguousarray(f).tobytes())
    p.stdin.close()
    err = p.stderr.read()
    if p.wait(timeout=300) != 0:
        raise RuntimeError("ffmpeg failed: " + err.decode()[-300:])
    return path


# ------------------------------------------------------------------ scenario
# A synthetic take of the supported kitting task: a person moves the mug into
# the tray. SYNTHETIC: rendered, not filmed.

MUG = dict(r=0.040, hz=0.0475, color=(200, 40, 40))
BOX = dict(half=(0.025, 0.020, 0.0175), color=(40, 70, 200))
PEN = dict(half=(0.07, 0.006, 0.006), color=(30, 160, 60))
TRAY = dict(center=(0.25, 0.04), inner_half=(0.12, 0.09), wall=0.03, color=(150, 150, 150))
BOOK = dict(center=(0.40, -0.12), half=(0.075, 0.015, 0.11), color=(220, 190, 60))
HAND_COLOR = (225, 175, 140)


def _smooth01(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


@dataclass
class TakePlan:
    """World-time plan of one synthetic take (seconds from the top phone's start)."""
    duration: float = 6.0
    board_until: float = 0.9
    sync_windows: tuple = ((1.1, 1.5), (5.2, 5.6))
    pick: float = 2.0                 # mug starts to rise
    place: float = 4.0                # mug back on a surface
    start_xy: tuple = (-0.17, -0.06)
    end_xy: tuple = (0.25, 0.04)
    lift: float = 0.16
    tilt_deg: float = 0.0             # peak tilt during the carry
    mug_tag: bool = False             # a printed tag on the mug's side (faces the side camera)
    hand_occludes: bool = True
    light_dip: Optional[tuple] = None  # (t0, t1) room light off

    def mug_pose(self, t):
        a = _smooth01((t - self.pick) / (self.place - self.pick))
        xy = (1 - a) * np.asarray(self.start_xy) + a * np.asarray(self.end_xy)
        up = _smooth01((t - self.pick) / 0.4) * (1 - _smooth01((t - (self.place - 0.4)) / 0.4))
        z = MUG["hz"] + self.lift * up
        tilt = self.tilt_deg * np.sin(np.pi * np.clip((t - self.pick) / (self.place - self.pick), 0, 1))
        return np.array([xy[0], xy[1], z]), tilt_R(tilt, axis=(0.0, 1.0, 0.0))

    def moving(self, t):
        return self.pick < t < self.place


def scene_prims(plan: TakePlan, spec: TargetSpec = DEFAULT_TARGET, *, sync_center=(-0.15, -0.20, 0.15),
                sync_normal=(0.0, -0.6, 0.8)) -> list:
    prims = []
    prims.append(board_prim(spec, origin=(-0.075, 0.105), visible=lambda t: t < plan.board_until))
    tx, ty = TRAY["center"]
    hx, hy = TRAY["inner_half"]
    w = 0.008
    floor = Prim("tray_floor", box_faces(hx + w, hy + w, 0.003), TRAY["color"], static_pose((tx, ty, 0.003)))
    walls = [Prim(f"tray_wall{k}", box_faces(*h), TRAY["color"], static_pose(p)) for k, (h, p) in enumerate([
        ((hx + w, w / 2, TRAY["wall"] / 2), (tx, ty + hy + w / 2, TRAY["wall"] / 2)),
        ((hx + w, w / 2, TRAY["wall"] / 2), (tx, ty - hy - w / 2, TRAY["wall"] / 2)),
        ((w / 2, hy, TRAY["wall"] / 2), (tx + hx + w / 2, ty, TRAY["wall"] / 2)),
        ((w / 2, hy, TRAY["wall"] / 2), (tx - hx - w / 2, ty, TRAY["wall"] / 2))])]
    prims += [floor] + walls
    bx, by = BOOK["center"]
    prims.append(Prim("book", box_faces(*BOOK["half"]), BOOK["color"], static_pose((bx, by, BOOK["half"][2]))))
    prims.append(Prim("box", box_faces(*BOX["half"]), BOX["color"], static_pose((-0.20, 0.12, BOX["half"][2]))))
    prims.append(Prim("pen", box_faces(*PEN["half"]), PEN["color"], static_pose((0.02, 0.22, PEN["half"][2]))))
    prims.append(Prim("mug", cylinder_faces(MUG["r"], MUG["hz"]), MUG["color"], lambda t: plan.mug_pose(t)))
    if plan.mug_tag:
        from mirror.capture.track import TAG_SIZE_MM
        cells = marker_cells(spec, 95, TAG_SIZE_MM)
        tex = np.pad(np.kron(np.where(cells, 0, 255).astype(np.uint8), np.ones((16, 16), np.uint8)), 16, constant_values=255)
        s = TAG_SIZE_MM / 1000.0 / 2 * tex.shape[0] / (cells.shape[0] * 16)
        y = -MUG["r"] - 0.0015
        face = np.array([[-s, y, s], [s, y, s], [s, y, -s], [-s, y, -s]])
        prims.append(Prim("mug_tag", [face], (255, 255, 255), lambda t: plan.mug_pose(t), texture=tex))
    if plan.hand_occludes:
        def hand(t):
            if not (plan.pick - 0.3 < t < plan.place + 0.3):
                return None
            p, R = plan.mug_pose(min(max(t, plan.pick), plan.place))
            return p + np.array([0.0, 0.0, MUG["hz"] + 0.03]), np.eye(3)
        prims.append(Prim("hand", box_faces(0.045, 0.035, 0.02), HAND_COLOR, hand))
    for k, (a, b) in enumerate(plan.sync_windows):
        prims.append(sync_card_prim(spec, sync_center, sync_normal, visible=(lambda a=a, b=b: (lambda t: a <= t < b))()))
    return prims


def default_cameras(width=960, height=540) -> dict:
    return {
        "top": SynthCamera.make("top", width, height, 70, (0.0, -0.20, 0.74), (0.02, 0.0, 0.0), dist=(0.06, -0.12, 0.0, 0.0, 0.0)),
        "side": SynthCamera.make("side", width, height, 62, (0.0, -0.56, 0.40), (0.03, 0.0, 0.03), dist=(-0.04, 0.05, 0.0, 0.0, 0.0)),
    }


def render_take(out_dir, plan: TakePlan, *, cams: Optional[dict] = None, fps=None, start_offsets=None,
                spec: TargetSpec = DEFAULT_TARGET, gain: float = 1.0, other_plan: Optional[TakePlan] = None,
                jitter: Optional[dict] = None) -> dict:
    """Render a synthetic take. ``start_offsets[c]`` is the world time at which
    camera c started recording (its clip time 0). ``other_plan`` renders the
    side camera from a DIFFERENT performance (for the separate-takes test)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cams = cams or default_cameras()
    fps = fps or {"top": 20.0, "side": 15.0}
    start_offsets = start_offsets or {"top": 0.0, "side": -0.37}
    jitter = {"top": 0.0, "side": 0.006} if jitter is None else jitter
    files = {}
    for name, cam in cams.items():
        p = other_plan if (other_plan is not None and name != "top") else plan
        prims = scene_prims(p, spec)
        bg = table_background(cam)
        t0 = start_offsets[name]
        times = frame_times(int((p.duration - t0) * fps[name]), fps[name], jitter=jitter.get(name, 0.0))
        frames = []
        for k, tc in enumerate(times):
            tw = tc + t0
            g = gain
            if p.light_dip and p.light_dip[0] <= tw < p.light_dip[1]:
                g *= 0.35
            frames.append(render(cam, prims, tw, background=bg, gain=g, seed=k + 17 * len(name)))
        files[name] = str(write_video(frames, out / f"synthetic_{name}.mp4", fps=fps[name], jitter=jitter.get(name, 0.0)))
    truth = {"synthetic": True, "note": SYNTHETIC_NOTE, "start_offsets": start_offsets, "fps": fps, "jitter": jitter,
             "plan": {k: (list(v) if isinstance(v, tuple) else v) for k, v in plan.__dict__.items()},
             "cameras": {n: {"K": c.K.tolist(), "dist": c.dist.tolist(), "R": c.R.tolist(), "t": c.t.tolist(),
                             "size": [c.width, c.height]} for n, c in cams.items()},
             "board_origin_xy": [-0.075, 0.105], "files": files}
    (out / "synthetic_truth.json").write_text(json.dumps(truth, indent=1))
    return truth


def render_lens_clip(out_path, cam: SynthCamera, *, n_views=16, spec: TargetSpec = DEFAULT_TARGET, seed=0, fps=2.0):
    """A clip of the board waved in front of ``cam`` (camera fixed, board moving):
    rendered by moving a virtual camera with ``cam``'s intrinsics around a fixed board."""
    rng = np.random.default_rng(seed)
    board = board_prim(spec, origin=(-0.075, 0.105))
    frames = []
    for k in range(n_views):
        az = rng.uniform(0, 2 * np.pi)
        el = rng.uniform(40, 88)
        r = rng.uniform(0.32, 0.5)
        tgt = np.array([rng.uniform(-0.05, 0.05), rng.uniform(-0.06, 0.06), 0])
        eye = tgt + r * np.array([np.cos(np.radians(el)) * np.cos(az), np.cos(np.radians(el)) * np.sin(az), np.sin(np.radians(el))])
        c = SynthCamera.make(cam.name, cam.width, cam.height, 60, eye, tgt, roll_deg=rng.uniform(-40, 40))
        c.K, c.dist = cam.K.copy(), cam.dist.copy()
        # aim so the board lands in different image regions
        shift = np.array([rng.uniform(-0.3, 0.3) * cam.width, rng.uniform(-0.3, 0.3) * cam.height])
        c.K = c.K.copy()
        frames.append(_shifted_render(c, board, shift, k))
    return write_video(frames, out_path, fps=fps)


def _shifted_render(c: SynthCamera, board: Prim, shift, k):
    """Render with the board appearing offset in the image while keeping the
    true intrinsics: rotate the camera slightly instead of moving the principal point."""
    fx = c.K[0, 0]
    yaw = np.arctan2(-shift[0], fx)
    pitch = np.arctan2(-shift[1], fx)
    Rx = rodrigues((pitch, 0, 0))
    Ry = rodrigues((0, yaw, 0))
    c2 = SynthCamera(c.name, c.width, c.height, c.K, c.dist, Ry @ Rx @ c.R, Ry @ Rx @ c.t)
    return render(c2, [board], 0.0, seed=k)


def truth_prompt(truth: dict, camera: str, label: str, center, half, frame: int = 0, shrink_px: float = 3.0):
    """Box around an object's projection in the TRUE camera (stands in for the
    box a person draws; synthetic tests only)."""
    from mirror.capture.geometry import project
    from mirror.capture.track import Prompt
    cam = truth["cameras"][camera]
    c = np.asarray(center, float)
    pts = np.array([c + np.array([sx * half[0], sy * half[1], sz * half[2]]) for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)])
    uv = project(cam["K"], cam["dist"], cam["R"], cam["t"], pts)
    x0, y0 = uv.min(0)
    x1, y1 = uv.max(0)
    return Prompt(camera=camera, label=label, frame=frame,
                  box=(float(x0 + shrink_px), float(y0 + shrink_px), float(x1 - x0 - 2 * shrink_px), float(y1 - y0 - 2 * shrink_px)))


def truth_table_xyz(world_xyz, truth: dict) -> np.ndarray:
    """World (scene) coordinates -> table frame (origin at the board's printed top-left corner)."""
    o = truth["board_origin_xy"]
    return np.asarray(world_xyz, float) - np.array([o[0], o[1], 0.0])
