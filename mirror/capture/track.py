"""Offline 2D observations of real objects in phone footage.

Sources, each recorded with the observation so it can be told apart later:

* ``classical``  colour-histogram back-projection + CamShift, started from a
                 box the person draws on one frame. No model call. It loses
                 the object under a hand and says so (status ``lost``); when it
                 finds a matching blob again the observation is ``reacquired``
                 with identity uncertain until a person confirms it.
* ``tag``        a small printed ArUco tag taped to an object (optional). Gives
                 the object's pose, including tilt, whenever the tag is seen.
* ``manual``     key frames clicked by a person; frames between key frames are
                 ``interpolated``, never ``tracked``.
* ``model``      masks from a segmentation/tracking model (for example SAM
                 tracks), imported with their provenance (fresh or recorded).

Nothing here decides what an object is: labels come from the person's prompts
and are mapped to simulation proxies in ``proxy``.
"""
from __future__ import annotations

from typing import Literal, Optional, Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from mirror.capture.media import ClipMeta, iter_frames

TRACK_THRESHOLDS = {
    "lost_score": 0.35,             # back-projection mass in the window relative to the first frame
    "max_area_ratio": 3.0,          # window area change that counts as a different blob
    "reacquire_min_area_ratio": 0.3,
    "elongation_for_axis": 1.6,     # principal axis only reported when major/minor >= this
}

TAG_IDS = {95: "tag_a", 96: "tag_b", 97: "tag_c"}
TAG_SIZE_MM = 20.0


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Obs2D(_M):
    frame: int
    t: float
    status: Literal["tracked", "reacquired", "lost", "manual", "interpolated", "model", "tag"]
    cx: Optional[float] = None
    cy: Optional[float] = None
    box: Optional[tuple[float, float, float, float]] = None     # x, y, w, h
    axis_deg: Optional[float] = None       # principal axis angle in the image (deg, from +x towards +y), elongated blobs only
    elongation: Optional[float] = None
    score: Optional[float] = None
    identity_confirmed: bool = True
    tag_R: Optional[list[list[float]]] = None   # tag pose in the camera frame (tag observations only)
    tag_t: Optional[list[float]] = None

    @property
    def seen(self) -> bool:
        return self.status in ("tracked", "reacquired", "manual", "model", "tag") and self.cx is not None


class Prompt(_M):
    camera: str
    label: str                     # the person's name for the object track, e.g. "mug"
    frame: int
    box: tuple[float, float, float, float]   # x, y, w, h in pixels


class TrackSet(_M):
    camera: str
    clip_sha256: str
    source: Literal["classical", "manual", "model", "tag"]
    model: Optional[str] = None
    model_origin: Optional[Literal["fresh_model_call", "recorded_replay"]] = None
    tracks: dict[str, list[Obs2D]] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


def _hist(hsv, box):
    """Discriminative hue-saturation histogram: colours common inside the
    central part of the box and rare in the ring around it (so the table or
    background a hand-drawn box includes does not light up)."""
    import cv2
    H, W = hsv.shape[:2]
    x, y, w, h = (float(v) for v in box)
    ix0, iy0 = int(round(x + 0.2 * w)), int(round(y + 0.2 * h))
    ix1, iy1 = int(round(x + 0.8 * w)), int(round(y + 0.8 * h))
    ox0, oy0 = max(0, int(round(x - 0.5 * w))), max(0, int(round(y - 0.5 * h)))
    ox1, oy1 = min(W, int(round(x + 1.5 * w))), min(H, int(round(y + 1.5 * h)))
    valid = cv2.inRange(hsv, (0, 40, 30), (180, 255, 255))
    inner = np.zeros((H, W), np.uint8)
    inner[iy0:iy1, ix0:ix1] = 255
    ring = np.zeros((H, W), np.uint8)
    ring[oy0:oy1, ox0:ox1] = 255
    bx0, by0, bx1, by1 = int(round(x)), int(round(y)), int(round(x + w)), int(round(y + h))
    ring[by0:by1, bx0:bx1] = 0
    fg = cv2.calcHist([hsv], [0, 1], cv2.bitwise_and(inner, valid), [30, 16], [0, 180, 0, 256])
    bg = cv2.calcHist([hsv], [0, 1], cv2.bitwise_and(ring, valid), [30, 16], [0, 180, 0, 256])
    fg /= max(fg.sum(), 1.0)
    bg /= max(bg.sum(), 1.0)
    ratio = np.where(fg > 0.01, fg / (fg + bg + 1e-6), 0.0).astype(np.float32)
    return (255.0 * ratio / max(ratio.max(), 1e-6)).astype(np.float32)


def _axis(bp, win):
    import cv2
    x, y, w, h = win
    sub = bp[y:y + h, x:x + w].astype(np.float64)
    m = cv2.moments(sub)
    if m["m00"] <= 1e-6:
        return None, None
    mu20, mu02, mu11 = m["mu20"] / m["m00"], m["mu02"] / m["m00"], m["mu11"] / m["m00"]
    ang = 0.5 * np.degrees(np.arctan2(2 * mu11, mu20 - mu02))
    common = np.sqrt(4 * mu11 ** 2 + (mu20 - mu02) ** 2)
    l1, l2 = (mu20 + mu02 + common) / 2, (mu20 + mu02 - common) / 2
    el = float(np.sqrt(l1 / max(l2, 1e-9)))
    return float(ang), el


def classical_track(meta: ClipMeta, prompts: Sequence[Prompt], *, camera: str, step: int = 1) -> TrackSet:
    """Track every prompted object through the clip, forward from its prompt frame
    (frames before the prompt are reported ``lost``)."""
    import cv2
    T = TRACK_THRESHOLDS
    prompts = [p for p in prompts if p.camera == camera]
    state = {}
    out = {p.label: [] for p in prompts}
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 10, 1)
    for i, t, f in iter_frames(meta, step=step):
        hsv = cv2.cvtColor(f, cv2.COLOR_RGB2HSV)
        H, W = f.shape[:2]
        for p in prompts:
            if i < p.frame:
                out[p.label].append(Obs2D(frame=i, t=t, status="lost"))
                continue
            if p.label not in state:
                hist = _hist(hsv, p.box)
                bp = cv2.calcBackProject([hsv], [0, 1], hist, [0, 180, 0, 256], 1)
                x, y, w, h = (int(round(v)) for v in p.box)
                ref = float(bp[y:y + h, x:x + w].sum()) + 1e-6
                state[p.label] = {"hist": hist, "win": (x, y, w, h), "ref": ref, "area": w * h, "lost": False}
            s = state[p.label]
            bp = cv2.calcBackProject([hsv], [0, 1], s["hist"], [0, 180, 0, 256], 1)
            status = "tracked"
            if s["lost"]:
                # search the whole frame for a blob of similar size
                _, th = cv2.threshold(bp, 60, 255, cv2.THRESH_BINARY)
                n, lab, stats, cents = cv2.connectedComponentsWithStats(th)
                best = None
                for k in range(1, n):
                    a = stats[k, cv2.CC_STAT_AREA]
                    bx, by, bw, bh = stats[k, :4]
                    if bw * bh < T["reacquire_min_area_ratio"] * s["area"] or bw * bh > T["max_area_ratio"] * s["area"]:
                        continue
                    if best is None or a > best[0]:
                        best = (a, (int(bx), int(by), int(bw), int(bh)))
                if best is None:
                    out[p.label].append(Obs2D(frame=i, t=t, status="lost", identity_confirmed=False))
                    continue
                s["win"], s["lost"] = best[1], False
                status = "reacquired"
                s["unconfirmed"] = True
            rot, win = cv2.CamShift(bp, s["win"], crit)
            x, y, w, h = win
            score = float(bp[y:y + h, x:x + w].sum()) / s["ref"] if w > 0 and h > 0 else 0.0
            area = w * h
            if (w <= 1 or h <= 1 or score < T["lost_score"] or area > T["max_area_ratio"] * s["area"]
                    or area < s["area"] / T["max_area_ratio"]):
                s["lost"] = True
                out[p.label].append(Obs2D(frame=i, t=t, status="lost", score=score, identity_confirmed=False))
                continue
            s["win"] = (x, y, w, h)
            ang, el = _axis(bp, (x, y, w, h))
            if el is not None and el < T["elongation_for_axis"]:
                ang = None
            m = cv2.moments(bp[y:y + h, x:x + w].astype(np.float64))
            cx, cy = (x + m["m10"] / m["m00"], y + m["m01"] / m["m00"]) if m["m00"] > 0 else (x + w / 2, y + h / 2)
            out[p.label].append(Obs2D(frame=i, t=t, status="reacquired" if s.get("unconfirmed") else "tracked",
                                      cx=float(cx), cy=float(cy), box=(float(x), float(y), float(w), float(h)),
                                      axis_deg=ang, elongation=el, score=score,
                                      identity_confirmed=not s.get("unconfirmed", False)))
    return TrackSet(camera=camera, clip_sha256=meta.sha256, source="classical", tracks=out,
                    notes=["colour back-projection + CamShift from person-drawn boxes; no model call"])


def confirm_identity(ts: TrackSet, label: str, from_frame: int) -> TrackSet:
    """A person confirmed that the reacquired track from ``from_frame`` on is the same object."""
    obs = ts.tracks[label]
    for k, o in enumerate(obs):
        if o.frame >= from_frame and o.seen:
            obs[k] = o.model_copy(update={"identity_confirmed": True, "status": "tracked" if o.status == "reacquired" else o.status})
    return ts


def manual_track(meta: ClipMeta, keyframes: dict[str, dict[int, tuple[float, float]]], *, camera: str,
                 max_gap_s: float = 0.6) -> TrackSet:
    """Person-clicked object centres at key frames; frames between two key frames
    closer than ``max_gap_s`` are interpolated, others are lost."""
    out = {}
    for label, kf in keyframes.items():
        keys = sorted(kf)
        obs = []
        for i, t in enumerate(meta.pts_s):
            if i in kf:
                obs.append(Obs2D(frame=i, t=t, status="manual", cx=kf[i][0], cy=kf[i][1]))
                continue
            lo = max([k for k in keys if k < i], default=None)
            hi = min([k for k in keys if k > i], default=None)
            if lo is not None and hi is not None and meta.pts_s[hi] - meta.pts_s[lo] <= max_gap_s:
                a = (t - meta.pts_s[lo]) / (meta.pts_s[hi] - meta.pts_s[lo])
                p = (1 - a) * np.asarray(kf[lo]) + a * np.asarray(kf[hi])
                obs.append(Obs2D(frame=i, t=t, status="interpolated", cx=float(p[0]), cy=float(p[1])))
            else:
                obs.append(Obs2D(frame=i, t=t, status="lost"))
        out[label] = obs
    return TrackSet(camera=camera, clip_sha256=meta.sha256, source="manual", tracks=out,
                    notes=["person-clicked key frames; in-between frames interpolated"])


def tag_track(meta: ClipMeta, K, dist, *, camera: str, tag_labels: dict[int, str], tag_size_mm: float = TAG_SIZE_MM,
              dictionary=None, step: int = 1) -> TrackSet:
    """Pose of printed tags (ids 95-97) taped to objects, per frame."""
    import cv2
    from mirror.capture.target import DEFAULT_TARGET
    d = dictionary or DEFAULT_TARGET.dictionary_obj()
    det = cv2.aruco.ArucoDetector(d, cv2.aruco.DetectorParameters())
    s = tag_size_mm / 1000.0
    obj = np.array([[-s / 2, s / 2, 0], [s / 2, s / 2, 0], [s / 2, -s / 2, 0], [-s / 2, -s / 2, 0]], float)
    out = {lab: [] for lab in tag_labels.values()}
    for i, t, f in iter_frames(meta, step=step, gray=True):
        corners, ids, _ = det.detectMarkers(f)
        seen = {}
        if ids is not None:
            for c, k in zip(corners, ids.ravel()):
                if int(k) in tag_labels:
                    seen[int(k)] = c.reshape(4, 2)
        for k, lab in tag_labels.items():
            if k not in seen:
                out[lab].append(Obs2D(frame=i, t=t, status="lost"))
                continue
            c = seen[k]
            ok, rvec, tvec = cv2.solvePnP(obj, c.astype(np.float64), np.asarray(K, float), np.asarray(dist, float),
                                          flags=cv2.SOLVEPNP_IPPE_SQUARE)
            if not ok:
                out[lab].append(Obs2D(frame=i, t=t, status="lost"))
                continue
            R = cv2.Rodrigues(rvec)[0]
            cx, cy = c.mean(0)
            out[lab].append(Obs2D(frame=i, t=t, status="tag", cx=float(cx), cy=float(cy), tag_R=R.tolist(),
                                  tag_t=[float(x) for x in tvec.ravel()]))
    return TrackSet(camera=camera, clip_sha256=meta.sha256, source="tag", tracks=out,
                    notes=[f"printed {tag_size_mm:.0f} mm ArUco tags; pose by planar PnP"])


def from_masks(meta: ClipMeta, masks: dict[str, dict[int, np.ndarray]], *, camera: str, model: str,
               model_origin: Literal["fresh_model_call", "recorded_replay"]) -> TrackSet:
    """Import per-frame boolean masks from a segmentation model (e.g. SAM tracks
    decoded with mirror.perception.masks.rle_decode). Frame indices are the
    clip's own decoded frame indices."""
    out = {}
    for label, fr in masks.items():
        obs = []
        for i, t in enumerate(meta.pts_s):
            m = fr.get(i)
            if m is None or not np.any(m):
                obs.append(Obs2D(frame=i, t=t, status="lost"))
                continue
            ys, xs = np.nonzero(m)
            x0, y0, x1, y1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
            cx, cy = float(xs.mean()), float(ys.mean())
            cov = np.cov(np.stack([xs, ys]).astype(float)) if len(xs) > 2 else None
            ang = el = None
            if cov is not None:
                w_, v_ = np.linalg.eigh(cov)
                el = float(np.sqrt(w_[1] / max(w_[0], 1e-9)))
                ang = float(np.degrees(np.arctan2(v_[1, 1], v_[0, 1]))) if el >= TRACK_THRESHOLDS["elongation_for_axis"] else None
            obs.append(Obs2D(frame=i, t=t, status="model", cx=cx, cy=cy, box=(float(x0), float(y0), float(x1 - x0), float(y1 - y0)),
                             axis_deg=ang, elongation=el))
        out[label] = obs
    return TrackSet(camera=camera, clip_sha256=meta.sha256, source="model", model=model, model_origin=model_origin, tracks=out)
