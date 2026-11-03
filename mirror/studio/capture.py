"""Real-phone capture sessions (M2 section 6): clips, synchronisation, calibration and
user review of correspondences and events, before any specification is proposed.

The studio owns storage, validation, state and provenance; reconstruction itself is
provided by the perception/phone modules through four hooks, looked up at call time
in ``mirror.perception.phone``:

    calibrate(capture: dict) -> dict          # {"ok", "quality": {...}, "intrinsics", "pose", "scale", "errors"}
    track(capture: dict) -> dict              # {"tracks": [...], "events": [...], "coverage", "uncertain": [...]}
    reconstruct(capture: dict, review: dict) -> DemoTrace   # object-centric, evidence kind camera_video_reconstruction
    capture_requirements() -> dict            # optional: limits the phone path needs

When a hook is missing the step answers "not available on this server"; nothing is
faked. Two clips are accepted as one two-view capture only with a declared
synchronisation event in each; the studio records what was declared and what the
container metadata says, and never calls simultaneity verified on its own.
"""
from __future__ import annotations

import importlib
import time
import uuid
from typing import Any, Optional

MODES = ("two_view", "single_view")
SYNC_METHODS = ("clap", "flash", "visual_marker", "manual")
EVENT_TYPES = ("grasp", "release", "carry_start", "carry_end", "place")
EVENT_STATUS = ("confirmed", "rejected", "uncertain", "edited")
PROXIES = ("cup", "block", "marker")
DURATION_TOLERANCE_S = 3.0


class CaptureError(ValueError):
    pass


def hook(name: str):
    try:
        mod = importlib.import_module("mirror.perception.phone")
    except Exception:  # noqa: BLE001 - the phone path is optional
        return None
    fn = getattr(mod, name, None)
    return fn if callable(fn) else None


def available() -> dict:
    return {h: hook(h) is not None for h in ("calibrate", "track", "reconstruct")}


class Capture:
    def __init__(self, mode: str, notes: str = ""):
        if mode not in MODES:
            raise CaptureError(f"mode is one of {list(MODES)}")
        self.id = "cap" + uuid.uuid4().hex[:10]
        self.created_at = time.time()
        self.mode = mode
        self.notes = notes[:500]
        self.state = "awaiting_clips"   # -> clips_ready -> calibrated -> tracked -> reviewed -> proposed
        self.clips: dict[str, dict] = {}
        self.sync: Optional[dict] = None
        self.calibration: Optional[dict] = None
        self.tracking: Optional[dict] = None
        self.reviews: list[dict] = []     # append-only history of user reviews
        self.proposal: Optional[dict] = None
        self.errors: list[str] = []

    def public(self) -> dict:
        return {"id": self.id, "created_at": self.created_at, "mode": self.mode, "state": self.state,
                "notes": self.notes, "clips": self.clips, "sync": self.sync, "calibration": self.calibration,
                "tracking": self.tracking, "review": self.reviews[-1] if self.reviews else None,
                "review_history": len(self.reviews), "proposal": self.proposal, "errors": self.errors,
                "observability": ("single view: depth along the camera axis and tilt about it are not observed"
                                  if self.mode == "single_view" else "two views, synchronised by a declared event"),
                "hooks": available()}

    def to_state(self) -> dict:
        return {**self.public(), "reviews": self.reviews}

    @classmethod
    def from_state(cls, d: dict) -> "Capture":
        c = cls(d["mode"], d.get("notes", ""))
        for k in ("id", "created_at", "state", "clips", "sync", "calibration", "tracking", "proposal", "errors"):
            setattr(c, k, d.get(k, getattr(c, k)))
        c.reviews = list(d.get("reviews") or [])
        return c


def set_clips(cap: Capture, uploads: dict[str, dict], sync: dict) -> None:
    """``uploads``: role -> public upload record (validated by uploads.py)."""
    need = ("top", "side", "calibration") if cap.mode == "two_view" else ("single", "calibration")
    missing = [r for r in need if r not in uploads]
    if missing:
        raise CaptureError(f"{cap.mode} needs clips {list(need)}; missing {missing}")
    extra = set(uploads) - set(need)
    if extra:
        raise CaptureError(f"unexpected clip roles {sorted(extra)}")
    method = (sync or {}).get("method")
    if cap.mode == "two_view":
        if method not in SYNC_METHODS:
            raise CaptureError(f"two views need a synchronisation event: method one of {list(SYNC_METHODS)}")
        try:
            t_top, t_side = float(sync["top_t"]), float(sync["side_t"])
        except (KeyError, TypeError, ValueError):
            raise CaptureError("give the synchronisation event time in each clip (top_t, side_t, seconds)")
        for role, t in (("top", t_top), ("side", t_side)):
            if not 0 <= t <= float(uploads[role].get("duration") or 0):
                raise CaptureError(f"sync time {t} s is outside the {role} clip")
        dt = abs(float(uploads["top"]["duration"]) - float(uploads["side"]["duration"]))
        if dt > DURATION_TOLERANCE_S + abs(t_top - t_side):
            raise CaptureError(f"the two clips differ by {dt:.1f} s in length; they do not look like one take "
                               "recorded on two phones. Record both phones during the same action.")
        cap.sync = {"method": method, "top_t": t_top, "side_t": t_side, "offset_s": round(t_side - t_top, 3),
                    "status": "declared", "note": "declared by the person capturing; not verified by the studio"}
    else:
        cap.sync = None
    cap.clips = {role: {k: v for k, v in u.items() if k not in ("path", "used_by")} for role, u in uploads.items()}
    cap.state = "clips_ready"


def check_review(cap: Capture, review: dict, scene_kinds=PROXIES) -> dict:
    """Validate a user review of correspondences and events; returns the normalised review."""
    if cap.state not in ("tracked", "reviewed"):
        raise CaptureError("review comes after tracking")
    tracks = {str(t.get("id")) for t in (cap.tracking or {}).get("tracks", [])}
    corr = []
    for c in review.get("correspondence") or []:
        tid = str(c.get("track_id"))
        if tid not in tracks:
            raise CaptureError(f"unknown track {tid}")
        proxy = c.get("proxy")
        if proxy is not None and proxy not in scene_kinds:
            raise CaptureError(f"proxy is one of {list(scene_kinds)} or null (not a task object)")
        dims = c.get("dimensions_m")
        if dims is not None and (not isinstance(dims, list) or not all(0.005 <= float(x) <= 0.5 for x in dims)):
            raise CaptureError("dimensions_m is a list of 0.005-0.5 m values")
        corr.append({"track_id": tid, "proxy": proxy, "label": str(c.get("label") or "")[:80],
                     "dimensions_m": dims, "dimensions_source": c.get("dimensions_source") or ("manual" if dims else None),
                     "unsupported_features": [str(x)[:80] for x in (c.get("unsupported_features") or [])][:5],
                     "assumed": {k: c.get("assumed", {}).get(k) for k in ("mass_kg", "friction", "contents")
                                 if c.get("assumed", {}).get(k) is not None}})
    proxies = [c["proxy"] for c in corr if c["proxy"]]
    if len(proxies) != len(set(proxies)):
        raise CaptureError("each simulation proxy maps to one real object")
    events = []
    for e in review.get("events") or []:
        if e.get("type") not in EVENT_TYPES or e.get("status") not in EVENT_STATUS:
            raise CaptureError(f"event type one of {list(EVENT_TYPES)}, status one of {list(EVENT_STATUS)}")
        t0, t1 = float(e.get("t_start", -1)), float(e.get("t_end", e.get("t_start", -1)))
        if t0 < 0 or t1 < t0:
            raise CaptureError("event interval must have 0 <= t_start <= t_end")
        events.append({"track_id": str(e.get("track_id")), "type": e["type"], "t_start": t0, "t_end": t1,
                       "status": e["status"], "observed_as": "human hand (inferred interval, not robot contact)"})
    return {"at": time.time(), "correspondence": corr, "events": events, "by": "user"}
