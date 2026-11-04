"""Build the twin media for one capture: the robot on your table, seen from your phone.

Inputs are files the capture pipeline already writes (session, take video, SAM tracks, rollout(s),
optional placement). Outputs (all display, no new measurements):
  overlay.mp4        portrait, the simulated run composited over your empty table, from your phone
  side_by_side.mp4   your hands | the robot on your table, synchronised on the pick/place events
  twin_3panel.mp4    your hands | the robot on your table | top-down table twin
  before_after.mp4   (two rollouts) the same table, before vs after the correction
  stills/*.png, plate.png, topdown_plate.png, twin.json (provenance, registration checks, timings)
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from mirror.twin import text as TX
from mirror.twin.camera import PhoneCamera, Placement, VirtualCamera
from mirror.twin.plate import FrameStream, choose_pair, clean_plate, mean_colour, read_at

CAPTION_BASE = "Approximate: one camera"
CARRY = ("lift", "transport", "place")


# ------------------------------------------------------------------ loading
def load_rollout(path):
    from mirror.contracts import Rollout
    p = Path(path)
    raw = gzip.open(p, "rt").read() if p.suffix == ".gz" else p.read_text()
    return Rollout.model_validate_json(raw)


def load_tracks(path) -> dict:
    p = Path(path)
    return json.load(gzip.open(p, "rt") if p.suffix == ".gz" else open(p))


def sha256(path, limit: Optional[int] = None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        n = 0
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
            n += len(b)
            if limit and n >= limit:
                break
    return h.hexdigest()


def placement_from(obj) -> Placement:
    if obj is None:
        raise ValueError("no placement")
    if "origin_xy_m" in obj:
        return Placement(tuple(obj["origin_xy_m"]), float(obj.get("yaw_deg", 0.0)), obj.get("source", "file"))
    if "placement" in obj:
        return placement_from(obj["placement"])
    if "R" in obj and "t" in obj:
        Rm = np.asarray(obj["R"], float)
        yaw = math.degrees(math.atan2(Rm[1, 0], Rm[0, 0]))
        t = np.asarray(obj["t"], float).reshape(-1)
        return Placement((float(t[0]), float(t[1])), yaw, obj.get("source", "file"))
    raise ValueError(f"unrecognised placement keys {sorted(obj)}")


def fit_placement(pairs_table: np.ndarray, pairs_sim: np.ndarray) -> tuple[Placement, float]:
    """2-D rigid fit table -> sim (Kabsch on x, y); returns (placement, rms m)."""
    A = np.asarray(pairs_table, float)[:, :2]
    B = np.asarray(pairs_sim, float)[:, :2]
    ca, cb = A.mean(0), B.mean(0)
    if len(A) >= 2:
        H = (A - ca).T @ (B - cb)
        U, _, Vt = np.linalg.svd(H)
        Rm = Vt.T @ U.T
        if np.linalg.det(Rm) < 0:
            Vt[-1] *= -1
            Rm = Vt.T @ U.T
    else:
        Rm = np.eye(2)
    yaw = math.degrees(math.atan2(Rm[1, 0], Rm[0, 0]))
    o = cb - Rm @ ca
    pl = Placement((float(o[0]), float(o[1])), yaw, "fitted_from_rollout")
    res = B - (A @ Rm.T + o)
    return pl, float(np.sqrt(np.mean(np.sum(res ** 2, 1))))


def moving_objects(visual) -> list[str]:
    return [b.name for b in visual.bodies if not b.static and not b.name.startswith("gripper") and "/bead" not in b.name]


def kind_of(obj_id: str) -> str:
    return obj_id.rpartition("_")[0] or obj_id


def start_rest(tr) -> Optional[np.ndarray]:
    idx = [k for k, (p, r) in enumerate(zip(tr.pos, tr.resting)) if p is not None and r]
    if not idx:
        idx = [k for k, p in enumerate(tr.pos) if p is not None]
    if not idx:
        return None
    first = idx[: max(1, min(len(idx), 5))]
    return np.median(np.array([tr.pos[k] for k in first], float), 0)


def match_objects(session, first_frame, placement: Optional[Placement], sim_ids: Sequence[str]) -> dict[str, str]:
    """sim object id -> capture track label (same proxy kind; nearest start position when ambiguous)."""
    out = {}
    by_kind: dict[str, list] = {}
    for m in session.mappings:
        if m.proxy in ("ignore", "tray", "wall"):
            continue
        by_kind.setdefault(m.proxy, []).append(m.track_label)
    rec = session.reconstruction
    for kind in sorted({kind_of(i) for i in sim_ids}):
        ids = sorted(i for i in sim_ids if kind_of(i) == kind)
        labels = list(by_kind.get(kind, []))
        if not labels:
            continue
        if len(ids) == 1 and len(labels) == 1:
            out[ids[0]] = labels[0]
            continue
        if placement is None or rec is None:
            for i, l in zip(ids, labels):
                out[i] = l
            continue
        used = set()
        for i in ids:
            p = np.asarray(first_frame.bodies[i].pos, float)
            best = None
            for l in labels:
                if l in used or l not in rec.objects:
                    continue
                q = start_rest(rec.objects[l])
                if q is None:
                    continue
                d = float(np.linalg.norm(placement.table_to_sim(q)[0, :2] - p[:2]))
                if best is None or d < best[0]:
                    best = (d, l)
            if best:
                out[i] = best[1]
                used.add(best[1])
    return out


def phase_spans(frames) -> list[tuple[str, float, float]]:
    spans = []
    for f in frames:
        if spans and spans[-1][0] == f.phase:
            spans[-1][2] = f.t
        else:
            spans.append([f.phase, f.t, f.t])
    return [tuple(s) for s in spans]


def human_pick_place(session, label: str) -> Optional[tuple[float, float]]:
    """(pick t, place t) of the carry that ends in the tray (inferred from object motion)."""
    ev = sorted([e for e in session.events if e.object == label and e.type in ("pick", "place")], key=lambda e: e.t)
    for k, e in enumerate(ev):
        if e.type == "place" and e.placed_in == "tray":
            picks = [p for p in ev[:k] if p.type == "pick"]
            if picks:
                return float(picks[-1].t), float(e.t)
    return None


# ------------------------------------------------------------------- time map
@dataclass
class TimeMap:
    """Output time tau -> sim time s = speed * tau; real (video) time T(s) piecewise linear through
    event anchors (robot grasp <-> your pick, robot release <-> your place), 1x outside them."""
    speed: float
    anchors: list                           # [(s, T, note)]
    t_video: tuple[float, float]

    def sim(self, tau: float) -> float:
        return self.speed * tau

    def real(self, s: float) -> float:
        a = self.anchors
        if not a:
            T = self.t_video[0] + s / self.speed
        elif s <= a[0][0]:
            T = a[0][1] - (a[0][0] - s) / self.speed
        elif s >= a[-1][0]:
            T = a[-1][1] + (s - a[-1][0]) / self.speed
        else:
            for (s0, T0, _), (s1, T1, _) in zip(a, a[1:]):
                if s0 <= s <= s1:
                    T = T0 + (T1 - T0) * (s - s0) / max(1e-9, s1 - s0)
                    break
        return float(min(max(T, self.t_video[0]), self.t_video[1]))

    def real_rate(self, s: float, ds: float = 0.05) -> float:
        """Your video's playback rate (x real time) at sim time s."""
        return (self.real(s + ds) - self.real(s - ds)) / (2 * ds) * self.speed


def build_time_map(frames, session, obj_to_label: dict, video_dur: float, speed: Optional[float] = None) -> TimeMap:
    spans = phase_spans(frames)
    sim_end = frames[-1].t
    anchors = []
    order = []
    for verb, s0, s1 in spans:
        v, _, obj = verb.partition(":")
        if v == "grasp" and obj and obj not in order:
            order.append(obj)
    for obj in order:
        lbl = obj_to_label.get(obj)
        hp = human_pick_place(session, lbl) if lbl else None
        g = next((s0 for v, s0, s1 in spans if v == f"grasp:{obj}"), None)
        r = next((s0 for v, s0, s1 in spans if v == f"release:{obj}"), None)
        if hp is None or g is None or r is None:
            continue
        for s, T, note in ((g, hp[0], f"{obj} grasp = your pick of {lbl}"), (r, hp[1], f"{obj} release = your place of {lbl}")):
            if anchors and (s <= anchors[-1][0] or T <= anchors[-1][1]):
                continue          # order differs from yours: keep only consistent anchors
            anchors.append((float(s), float(T), note))
    if speed is None:
        human_span = (anchors[-1][1] - anchors[0][1]) if len(anchors) >= 2 else video_dur
        speed = sim_end / max(1.0, human_span + 6.0)
        speed = float(min(4.0, max(1.0, round(speed * 2) / 2)))
    return TimeMap(speed=speed, anchors=anchors, t_video=(0.0, max(0.0, video_dur - 0.04)))


# ------------------------------------------------------------------ geometry
def wall_planes(visual) -> list[tuple[str, np.ndarray, np.ndarray]]:
    from mirror.sim.rotations import quat_to_mat  # type: ignore
    out = []
    for b in visual.bodies:
        if not b.static:
            continue
        for g in b.geoms:
            if g.name and (g.name.startswith("wall") or "/seg" in g.name) and g.type == "box":
                Rm = quat_to_mat(g.quat)
                out.append((g.name.split("/")[0], np.asarray(g.pos, float), np.asarray(Rm, float)[:, 0]))
    return out


def tray_box_points(visual) -> list[np.ndarray]:
    from mirror.sim.rotations import quat_to_mat  # type: ignore
    pts = []
    for b in visual.bodies:
        if b.static and b.name.startswith("tray"):
            for g in b.geoms:
                Rm = np.asarray(quat_to_mat(g.quat), float)
                s = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], float) * np.asarray(g.size)
                pts.append(np.asarray(g.pos) + s @ Rm.T)
    return pts


def silhouette(cam, boxes_pts: list[np.ndarray], size) -> np.ndarray:
    import cv2
    m = np.zeros((size[1], size[0]), np.uint8)
    for P in boxes_pts:
        uv = cam.project(P)
        if np.all(np.isfinite(uv)):
            cv2.fillConvexPoly(m, cv2.convexHull(np.int32(np.round(uv))), 1)
    return m > 0


def iou(a, b) -> float:
    u = (a | b).sum()
    return float((a & b).sum() / u) if u else 0.0


# ------------------------------------------------------------------- writing
class Mp4:
    def __init__(self, path, size, fps=30.0, crf=20):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.size = size
        self.proc = subprocess.Popen(
            [shutil.which("ffmpeg"), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{size[0]}x{size[1]}",
             "-r", f"{fps}", "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", str(self.path)], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.n = 0

    def write(self, img: np.ndarray):
        assert img.shape[1] == self.size[0] and img.shape[0] == self.size[1], (img.shape, self.size)
        self.proc.stdin.write(np.ascontiguousarray(img, np.uint8).tobytes())
        self.n += 1

    def close(self):
        self.proc.stdin.close()
        err = self.proc.stderr.read().decode(errors="replace")
        rc = self.proc.wait(timeout=600)
        if rc != 0:
            raise RuntimeError(f"ffmpeg failed for {self.path}: {err[-500:]}")


def resize(img, size):
    import cv2
    if img.shape[1] == size[0] and img.shape[0] == size[1]:
        return img
    interp = cv2.INTER_AREA if img.shape[1] > size[0] else cv2.INTER_LINEAR
    return cv2.resize(img, size, interpolation=interp)


# --------------------------------------------------------------------- build
@dataclass
class TwinJob:
    session: Path
    video: Path
    tracks: Optional[Path]
    rollouts: list                          # [(label, path)]
    out: Path
    placement: Optional[dict] = None
    camera: str = "top"
    size: tuple = (540, 960)                # phone-view output (portrait, <= 720p)
    speed: Optional[float] = None
    fps: float = 30.0
    max_seconds: Optional[float] = None     # smoke tests: truncate the output
    labels: list = field(default_factory=list)   # extra honesty labels shown on screen
    title: str = "MIRROR"
    correction: Optional[dict] = None       # {text, diff:[{target_id}], old_run_recheck|recheck:{violations:[...]}}
    after_labels: list = field(default_factory=list)   # labels for the before/after film only


def run(job: TwinJob, log=print) -> dict:
    import cv2
    from mirror.capture.session import CaptureSession
    from mirror.sim.render import _Playback, _tilt_deg, _cup_beads_in, phase_words, object_words, tilt_rgba
    from mirror.twin.render import TwinRenderer, Occluder, recoloured

    t_start = time.time()
    timings = {}
    out = Path(job.out)
    (out / "stills").mkdir(parents=True, exist_ok=True)
    W, H = job.size

    # -- capture
    s = CaptureSession.load(job.session)
    cam_key = job.camera if job.camera in s.intrinsics else sorted(s.intrinsics)[0]
    intr, pose = s.intrinsics[cam_key], s.poses[cam_key]
    ok, blocking = s.gate()
    labels = []
    if s.synthetic:
        labels.append("Synthetic capture")
    else:
        labels.append("Real phone footage")
    labels.append("One camera" if len(s.intrinsics) == 1 else f"{len(s.intrinsics)} cameras")
    codes = [b.code for b in blocking]
    if "recalibration_required" in codes or (s.recalibration and len(s.recalibration)):
        labels.append("Calibration reuse flagged")
    labels += list(job.labels)
    caption = CAPTION_BASE + ("" if len(s.intrinsics) == 1 else "s") + \
        (", calibration reuse flagged" if "Calibration reuse flagged" in labels else "") + \
        ". Simulated gripper and proxies, not your objects."

    rolls = [(lbl, load_rollout(p)) for lbl, p in job.rollouts]
    base_label, base = rolls[0]
    visual, frames = base.visual, list(base.frames)
    sim_ids = moving_objects(visual)

    # -- placement (table -> sim)
    fit_rms = None
    if job.placement is not None:
        pl = placement_from(job.placement)
    elif s.placement is not None and s.placement.source != "default_centred":
        pl = Placement(tuple(s.placement.origin_xy_m), s.placement.yaw_deg, s.placement.source)
    else:
        pl = None
    rec = s.reconstruction
    if pl is None or True:
        # fit (or verify) against the rollout's first frame: same proxies, same start positions
        prov = match_objects(s, frames[0], pl, sim_ids)
        A, B = [], []
        for oid, lbl in prov.items():
            q = start_rest(rec.objects[lbl]) if rec and lbl in rec.objects else None
            if q is not None:
                A.append(q)
                B.append(frames[0].bodies[oid].pos)
        if len(A) >= 2:
            fitted, fit_rms = fit_placement(np.array(A), np.array(B))
            if pl is None:
                pl = fitted
            else:
                Bp = pl.table_to_sim(np.array(A))[:, :2]
                fit_rms = float(np.sqrt(np.mean(np.sum((Bp - np.array(B)[:, :2]) ** 2, 1))))
        elif pl is None:
            pl = Placement(tuple(s.placement.origin_xy_m), s.placement.yaw_deg, s.placement.source)
    obj_to_label = match_objects(s, frames[0], pl, sim_ids)
    log(f"placement {pl} fit_rms={fit_rms} objects {obj_to_label}")

    cam = PhoneCamera.from_capture(intr.K, intr.dist, intr.image_size, pose.R, pose.t, pl, out_size=(W, H))

    # -- masks
    tracks = load_tracks(job.tracks) if job.tracks else None
    tfps = float(tracks.get("fps", 30.0)) if tracks else 30.0

    def mask_at(label: str, t: float) -> Optional[np.ndarray]:
        from mirror.perception.masks import rle_decode
        if not tracks:
            return None
        mm = tracks["masks"].get(cam_key, {}).get(label)
        if not mm:
            return None
        keys = np.array(sorted(int(k) for k in mm))
        k = int(keys[np.argmin(np.abs(keys - t * tfps))])
        m = rle_decode(mm[str(k)])
        return cv2.resize(m.astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST) > 0

    video_dur = s.clips[0].duration_s if s.clips and s.clips[0].duration_s else 60.0
    # -- plate
    t0 = time.time()
    picks = [human_pick_place(s, l) for l in obj_to_label.values()]
    picks = [p for p in picks if p]
    all_ev = [e for e in s.events if e.object in obj_to_label.values()]
    first_touch = min([e.t_lo for e in all_ev if e.type == "pick"], default=(min(p[0] for p in picks) if picks else 1.0))
    last_place = max([e.t_hi for e in all_ev if e.type == "place"], default=(max(p[1] for p in picks) if picks else video_dur - 1))
    sw = (max(0.0, first_touch - 2.5), max(0.05, first_touch - 0.05))
    ew = (min(video_dur - 0.2, last_place + 0.5), max(min(video_dur - 0.2, last_place + 0.5) + 0.1, video_dur - 0.1))
    small = (135, 240)
    avoid = np.zeros((small[1], small[0]), bool)
    start_masks = {}
    for lbl in obj_to_label.values():
        for tt in (sw[1], ew[1]):
            m = mask_at(lbl, tt)
            if m is not None:
                avoid |= cv2.resize(m.astype(np.uint8), small, interpolation=cv2.INTER_NEAREST) > 0
    avoid = cv2.dilate(avoid.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    ts, te, agree = choose_pair(job.video, sw, ew, avoid, step=0.1)
    for lbl in obj_to_label.values():
        m = mask_at(lbl, ts)
        if m is not None:
            start_masks[lbl] = m
    plate = clean_plate(job.video, ts, te, start_masks, (W, H))
    plate.agreement = agree
    start_img = read_at(job.video, ts, (W, H))
    cv2.imwrite(str(out / "plate.png"), cv2.cvtColor(plate.image, cv2.COLOR_RGB2BGR))
    timings["plate_s"] = round(time.time() - t0, 1)
    log(f"plate start {ts:.2f}s end {te:.2f}s agreement {agree:.1f} gain {plate.gain}")

    # -- colours: proxies take their real object's colour (display only)
    colours, colour_src = {}, {}
    for oid, lbl in obj_to_label.items():
        m = start_masks.get(lbl)
        c = mean_colour(start_img, m) if m is not None else None
        if c is not None:
            colours[oid] = tuple(min(1.0, x * 0.92) for x in c)   # sim lighting is brighter than the room
            colour_src[oid] = [round(x, 3) for x in c]

    # -- occluders: real walls (their SAM mask) hide the robot behind them
    occluders = []
    wall_labels = [m.track_label for m in s.mappings if m.proxy == "wall"]
    planes = wall_planes(visual)
    for (wid, p, n), lbl in zip(planes, wall_labels):
        m = mask_at(lbl, ts)
        if m is not None:
            occluders.append(Occluder(mask=cv2.dilate(m.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0,
                                      point=p, normal=n, label=lbl))

    # -- registration check: simulated tray vs your tray (image IoU)
    reg = {}
    tray_lbl = next((m.track_label for m in s.mappings if m.proxy == "tray"), None)
    tm = mask_at(tray_lbl, ts) if tray_lbl else None
    if tm is not None:
        sil = silhouette(cam, tray_box_points(visual), (W, H))
        reg["tray_iou"] = round(iou(sil, tm), 3)
        ys, xs = np.nonzero(tm); ys2, xs2 = np.nonzero(sil)
        if len(xs) and len(xs2):
            reg["tray_centroid_offset_px"] = [round(float(xs2.mean() - xs.mean()), 1), round(float(ys2.mean() - ys.mean()), 1)]
    for oid, lbl in obj_to_label.items():
        m = start_masks.get(lbl)
        if m is None:
            continue
        p = np.asarray(frames[0].bodies[oid].pos, float)
        uv = cam.project([[p[0], p[1], 0.0]])[0]
        ys, xs = np.nonzero(m)
        bottom = np.array([xs.mean(), np.percentile(ys, 85)])
        reg.setdefault("object_base_offset_px", {})[lbl] = round(float(np.linalg.norm(uv - bottom)), 1)
    reg["note"] = ("Image-space agreement between the simulator's placement and your footage at "
                   f"{W}x{H}; not a 3-D accuracy measurement.")

    # -- time map
    tmap = build_time_map(frames, s, obj_to_label, video_dur, job.speed)
    log(f"time map speed x{tmap.speed} anchors {[(round(a, 2), round(b, 2)) for a, b, _ in tmap.anchors]}")

    # -- top-down twin camera
    tray_pts = np.concatenate(tray_box_points(visual)) if tray_box_points(visual) else np.zeros((0, 3))
    obj_pts = np.array([frames[0].bodies[o].pos for o in sim_ids] + [f.bodies[o].pos for f in frames[-1:] for o in sim_ids if o in f.bodies])
    wall_pts = []
    for wid, p, n in planes:
        wall_pts.append(p)
    allp = np.concatenate([tray_pts[:, :2], obj_pts[:, :2]] + ([np.array(wall_pts)[:, :2]] if wall_pts else []))
    lo, hi = allp.min(0) - 0.07, allp.max(0) + 0.07
    # image-up direction of the phone, on the table
    c_px = np.array([[W / 2, H / 2], [W / 2, H / 2 - 40]])
    from mirror.capture.geometry import pixel_rays, ray_plane
    Ct, d = pixel_rays(cam.K, cam.dist, cam.R, cam.t, c_px)
    hits = [ray_plane(Ct, dd, 0.0) for dd in d]
    up = hits[1][:2] - hits[0][:2]
    up = up / max(1e-9, np.linalg.norm(up))
    # snap to the nearest sim axis direction so the twin reads as a plan
    ang = math.atan2(up[1], up[0]); ang = round(ang / (math.pi / 2)) * (math.pi / 2)
    up = np.array([math.cos(ang), math.sin(ang)])
    right = np.array([up[1], -up[0]])
    corners = np.array([[lo[0], lo[1]], [lo[0], hi[1]], [hi[0], lo[1]], [hi[0], hi[1]]])
    span_r = np.ptp(corners @ right); span_u = np.ptp(corners @ up)
    PW, PH = 492, 672
    ppm = min(PW / span_r, (PH - 60) / span_u)
    vc = VirtualCamera.top_down(((lo + hi) / 2), (PW / ppm, PH / ppm), ppm, up_xy=up)
    Hm = vc.plane_homography_from(cam, z=0.0)
    und = cv2.undistort(plate.image, cam.K, cam.dist)
    td_plate = cv2.warpPerspective(und, Hm, (vc.width, vc.height), flags=cv2.WARP_INVERSE_MAP | cv2.INTER_LINEAR,
                                   borderMode=cv2.BORDER_CONSTANT, borderValue=(28, 28, 30))
    cv2.imwrite(str(out / "topdown_plate.png"), cv2.cvtColor(td_plate, cv2.COLOR_RGB2BGR))

    # -- renderers
    vis = recoloured(visual, colours)
    tr_phone = TwinRenderer(vis, cam)
    tr_top = TwinRenderer(vis, vc, shadows=True)

    def trails_on(img, playback, camera, faded=False, width=2):
        for obj, segs in playback.trails.items():
            for seg in segs:
                if len(seg) < 2:
                    continue
                P = np.array([p for p, _ in seg])
                uv = camera.project(P)
                for k in range(len(seg) - 1):
                    col = seg[k + 1][1]
                    c = tuple(int(255 * x) for x in col[:3])
                    if faded:
                        c = (150, 150, 158)
                    a, b = uv[k], uv[k + 1]
                    if np.all(np.isfinite([a, b])):
                        cv2.line(img, tuple(np.int32(a)), tuple(np.int32(b)), c, width, cv2.LINE_AA)
        return img

    def status_lines(f, s_now):
        lines = [phase_words(f.phase)]
        bi = _cup_beads_in(f, visual)
        obj = f.phase.partition(":")[2]
        if obj and obj in f.bodies and f.phase.partition(":")[0] in CARRY:
            lines.append(f"{object_words(obj)} tilt {_tilt_deg(f.bodies[obj].quat):.0f}°")
        if bi is not None:
            lines.append(f"beads in cup {bi[0]}/{bi[1]}")
        return lines

    # -- main render (base rollout): overlay, side-by-side, 3-panel
    t0 = time.time()
    sim_end = frames[-1].t - frames[0].t
    n_out = int(sim_end / tmap.speed * job.fps) + int(1.0 * job.fps)
    if job.max_seconds:
        n_out = min(n_out, int(job.max_seconds * job.fps))
    pb = _Playback(frames)
    fs = FrameStream(job.video, (W, H), start_s=0.0, pts=s.clips[0].__dict__.get("pts_s") if s.clips else None,
                     fps=(s.clips[0].n_frames / s.clips[0].duration_s) if s.clips and s.clips[0].duration_s else 30.0)
    CW, CH = 1280, 720
    head = 48
    pw, ph = 378, 672
    w_ov = Mp4(out / "overlay.mp4", (W, H), job.fps)
    w_sbs = Mp4(out / "side_by_side.mp4", (2 * pw + 3 * 8, CH), job.fps)
    w_3p = Mp4(out / "twin_3panel.mp4", (CW, CH), job.fps)
    still_times = sorted({round(a[0], 2) for a in tmap.anchors} | {round(sim_end * 0.02, 2), round(sim_end - 0.1, 2)})
    stills = []
    hidden_max = 0
    rates = []
    try:
        for i in range(n_out):
            tau = i / job.fps
            s_now = min(sim_end, tmap.sim(tau))
            f = pb.at(frames[0].t + s_now)
            T = tmap.real(s_now)
            rate = tmap.real_rate(s_now)
            rates.append(rate)
            hands = fs.at(T)
            # phone view
            base_img = trails_on(plate.image.copy(), pb, cam)
            ar, info = tr_phone.composite(base_img, f, occluders=occluders)
            hidden_max = max(hidden_max, info["hidden_px"])
            # top-down twin
            td_base = trails_on(td_plate.copy(), pb, vc, width=2)
            td, _ = tr_top.composite(td_base, f, shadow_strength=0.6)
            st = status_lines(f, s_now)
            # overlay (portrait)
            ov = TX.panel_label(ar, "The robot on your table", f"seen from your phone · sim ×{tmap.speed:g}", st,
                                caption, labels)
            w_ov.write(ov)
            # panels
            hp = TX.panel_label(resize(hands, (pw, ph)), "Your hands", f"your video · ×{rate:.2f}" if abs(rate - 1) > 0.05 else "your video · real time",
                                [f"t = {T:4.1f} s"], None, None, small=True)
            rp = TX.panel_label(resize(ar, (pw, ph)), "The robot on your table", f"simulated · ×{tmap.speed:g}", st, None, None, small=True)
            tdp = TX.topdown_panel(td, (492, ph), "Table twin", "top-down, rectified from your phone", ppm)
            sbs = TX.canvas([hp, rp], (2 * pw + 3 * 8, CH), head, job.title + " · your table", caption)
            w_sbs.write(sbs)
            three = TX.canvas([hp, rp, tdp], (CW, CH), head, job.title + " · your table, your phone, the robot", caption)
            w_3p.write(three)
            if any(abs(s_now - x) < 0.5 * tmap.speed / job.fps for x in still_times):
                pth = out / "stills" / f"twin_{s_now:06.2f}.png"
                cv2.imwrite(str(pth), cv2.cvtColor(three, cv2.COLOR_RGB2BGR))
                cv2.imwrite(str(out / "stills" / f"overlay_{s_now:06.2f}.png"), cv2.cvtColor(ov, cv2.COLOR_RGB2BGR))
                stills.append(pth.name)
    finally:
        fs.close()
        for w in (w_ov, w_sbs, w_3p):
            w.close()
    timings["main_render_s"] = round(time.time() - t0, 1)
    timings["main_frames"] = n_out

    # -- before / after (two rollouts on the same table)
    ba = None
    if len(rolls) >= 2:
        t0 = time.time()
        (la, ra), (lb, rb) = rolls[0], rolls[1]
        fa, fb = list(ra.frames), list(rb.frames)
        trb = TwinRenderer(recoloured(rb.visual, colours), cam)
        tdb = TwinRenderer(recoloured(rb.visual, colours), vc)
        da, db = fa[-1].t - fa[0].t, fb[-1].t - fb[0].t
        sp = tmap.speed
        n_ba = int(max(da, db) / sp * job.fps) + int(1.5 * job.fps)
        if job.max_seconds:
            n_ba = min(n_ba, int(job.max_seconds * job.fps))
        pa, pbb = _Playback(fa), _Playback(fb)
        corr = job.correction or {}
        rule_text = corr.get("text") or corr.get("compile_text") or ""
        rc = corr.get("old_run_recheck") or corr.get("recheck") or {}
        viol = [v for v in (rc.get("violations") or []) if isinstance(v, dict)]
        added = {d.get("target_id") for d in corr.get("diff") or [] if isinstance(d, dict)}
        after_rules = [x for x in rb.report.model_dump(mode="json").get("rule_results") or [] if x.get("rule_id") in added]

        def before_lines(f, sn):
            ln = status_lines(f, sn)
            now = [v for v in viol if v.get("t_start") is not None and v["t_start"] - 0.05 <= sn <= v["t_end"] + 0.05]
            if now:
                v = now[0]
                ln.append(f"re-checked: {object_words(v.get('object_id', ''))} {100 * float(v['peak']):.1f} cm from the divider")
            elif viol:
                ln.append(f"re-checked under the new rule: {len(viol)} violation" + ("s" if len(viol) > 1 else ""))
            return ln

        def after_lines(f, sn):
            ln = status_lines(f, sn)
            for x in after_rules:
                ln.append(f"new rule: {x.get('verdict')} (checked from the run)")
            return ln
        bw = Mp4(out / "before_after.mp4", (CW, CH), job.fps)
        try:
            for i in range(n_ba):
                sn = i / job.fps * sp
                f1 = pa.at(fa[0].t + min(da, sn)); f2 = pbb.at(fb[0].t + min(db, sn))
                a1, _ = tr_phone.composite(trails_on(plate.image.copy(), pa, cam), f1, occluders=occluders)
                a2, _ = trb.composite(trails_on(plate.image.copy(), pbb, cam), f2, occluders=occluders)
                tdimg = trails_on(td_plate.copy(), pa, vc, faded=True)
                tdimg = trails_on(tdimg, pbb, vc, width=3)
                tdimg, _ = tdb.composite(tdimg, f2, shadow_strength=0.6)
                p1 = TX.panel_label(resize(a1, (pw, ph)), la.capitalize(), f"simulated · ×{sp:g}", before_lines(f1, sn), None, None, small=True)
                p2 = TX.panel_label(resize(a2, (pw, ph)), lb.capitalize(), (rule_text[:44] + "…") if len(rule_text) > 45 else (rule_text or f"simulated · ×{sp:g}"),
                                    after_lines(f2, sn), None, None, small=True)
                p3 = TX.topdown_panel(tdimg, (492, ph), "Table twin", f"grey: {la} · colour: {lb}", ppm)
                cap2 = caption + ("" if not job.after_labels else " " + " · ".join(job.after_labels) + ".")
                img = TX.canvas([p1, p2, p3], (CW, CH), head, job.title + f" · {la} vs {lb}, on your table", cap2)
                bw.write(img)
                if i in (int(n_ba * 0.5), n_ba - 1):
                    cv2.imwrite(str(out / "stills" / f"before_after_{i:05d}.png"), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
        finally:
            bw.close()
            trb.close(); tdb.close()
        timings["before_after_s"] = round(time.time() - t0, 1)
        ba = {"before": la, "after": lb, "frames": n_ba}
    tr_phone.close(); tr_top.close()

    manifest = {
        "kind": "mirror.twin/v1",
        "created_unix": round(time.time(), 1),
        "inputs": {"session": str(job.session), "session_id": s.session_id, "video": str(job.video),
                   "video_sha256": s.clips[0].sha256 if s.clips else None, "tracks": str(job.tracks) if job.tracks else None,
                   "rollouts": {lbl: str(p) for lbl, p in job.rollouts}, "camera": cam_key},
        "labels": labels, "caption": caption,
        "gate": {"passed": ok, "blocking": codes},
        "placement": {"origin_xy_m": list(pl.origin_xy), "yaw_deg": pl.yaw_deg, "source": pl.source,
                      "fit_rms_m_vs_rollout_start": None if fit_rms is None else round(fit_rms, 4)},
        "objects": obj_to_label, "proxy_colours_from_footage": colour_src,
        "plate": {"start_s": round(plate.start_s, 2), "end_s": round(plate.end_s, 2), "agreement_grey": round(plate.agreement, 1),
                  "gain": plate.gain, "filled_px": plate.filled,
                  "method": "start frame; each moved object's region replaced by the end frame (fixed phone), exposure-matched"},
        "occluders": [o.label for o in occluders], "occluded_px_max": hidden_max,
        "registration": reg,
        "time_map": {"sim_speed": tmap.speed, "anchors": [{"sim_s": round(a, 2), "video_s": round(b, 2), "what": n} for a, b, n in tmap.anchors],
                     "your_video_rate_min": round(min(rates), 2) if rates else None, "your_video_rate_max": round(max(rates), 2) if rates else None,
                     "note": "Your video is re-timed between matching events so the robot and your hands pick and place together; rates are shown on screen."},
        "topdown": {"px_per_m": round(ppm, 1), "size": [vc.width, vc.height],
                    "note": "Plane-to-plane homography of the table surface (z = 0). Anything above the table (tray walls, the divider, your objects) is smeared in this view."},
        "files": {"overlay": "overlay.mp4", "side_by_side": "side_by_side.mp4", "twin_3panel": "twin_3panel.mp4",
                  "before_after": "before_after.mp4" if ba else None, "plate": "plate.png", "topdown_plate": "topdown_plate.png",
                  "stills": sorted(os.listdir(out / "stills"))},
        "before_after": ba if not ba else {**ba, "rule": job.correction.get("text") if job.correction else None,
                                            "after_labels": job.after_labels},
        "timings_s": {**timings, "total_s": round(time.time() - t_start, 1)},
        "honesty": ["Display only: no new measurement is made here.",
                    "Proxies are simulator stand-ins sized from your measurements; their colours are sampled from your footage.",
                    "The robot is a simulated free-floating gripper; it never touched your table.",
                    "Registration is limited by one camera and the capture's calibration labels."],
    }
    (out / "twin.json").write_text(json.dumps(manifest, indent=1))
    return manifest
