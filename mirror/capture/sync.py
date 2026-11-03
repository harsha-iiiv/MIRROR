"""Two-camera synchronisation from observable events.

Triangulation is only meaningful for frames of the SAME action taken at the
SAME moment. Phones start recording at different times and their timestamps
share no clock, so the two clips are aligned by events both cameras see:

* ``card``  the sync card (marker 90) is shown to both phones and then turned
            face down (or shown again). Primary method.
* ``light`` the room light is switched off and on: a step in mean brightness.
* ``audio`` a clap in each phone's audio track (cross-check only; audio and
            video clocks inside one phone file are aligned by the container).

Every clip keeps its original frame timestamps. ``align`` matches events of
the same kind and direction, fits ``t_side = a + b * t_top`` (b = 1 unless
there are at least two events far enough apart to measure clock drift), and
reports the residual. Clips that share no sync event are refused: they may be
two separate performances, and triangulating them would invent geometry.
"""
from __future__ import annotations

from typing import Literal, Optional, Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from mirror.capture.media import ClipMeta, audio_envelope, iter_frames
from mirror.capture.target import DEFAULT_TARGET, TargetSpec

SYNC_THRESHOLDS = {
    "min_stable_frames": 2,          # card state must hold this many frames on each side of a transition
    "light_step_min": 18.0,          # grey levels, frame-to-frame mean brightness step
    "match_tolerance_s": 0.12,       # events agree within this after alignment
    "max_residual_frames": 1.0,      # alignment residual (in top-camera frame intervals) to allow triangulation
    "min_drift_baseline_s": 10.0,    # events this far apart are needed to estimate drift
    "max_abs_drift": 2e-3,           # |b - 1|; larger means mismatched or edited clips
    "min_matched_events": 2,         # one event can always be paired; two with matching spacing identify the same take
}


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SyncEvent(_M):
    kind: Literal["card", "light", "audio"]
    direction: Literal["up", "down"]          # card shown / hidden, light on / off, audio onset (up)
    t: float                                  # seconds on the clip's own timeline (original pts)
    uncertainty_s: float                      # half the gap between the frames that bracket it
    frame_before: Optional[int] = None
    frame_after: Optional[int] = None


class SyncResult(_M):
    ok: bool
    method: Optional[str] = None
    offset_s: Optional[float] = None          # t_side = offset_s + scale * t_top
    scale: float = 1.0
    drift_estimated: bool = False
    matched: list[tuple[float, float]] = Field(default_factory=list)
    residual_s: Optional[float] = None        # RMS of the fit residuals (None if not measurable: one event)
    uncertainty_s: Optional[float] = None     # residual combined with event bracketing
    residual_frames: Optional[float] = None
    overlap_s: Optional[float] = None
    reasons: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def to_side(self, t_top):
        return self.offset_s + self.scale * np.asarray(t_top, float)

    def to_top(self, t_side):
        return (np.asarray(t_side, float) - self.offset_s) / self.scale


def _transitions(times: Sequence[float], frames: Sequence[int], state: Sequence[bool], kind: str, k: int) -> list[SyncEvent]:
    out = []
    s = np.asarray(state, bool)
    for i in range(k, len(s) - k + 1):
        if s[i] != s[i - 1] and np.all(s[i - k:i] == s[i - 1]) and np.all(s[i:i + k] == s[i]):
            out.append(SyncEvent(kind=kind, direction="up" if s[i] else "down", t=0.5 * (times[i - 1] + times[i]),
                                 uncertainty_s=0.5 * (times[i] - times[i - 1]), frame_before=frames[i - 1], frame_after=frames[i]))
    return out


def card_states(meta: ClipMeta, spec: TargetSpec = DEFAULT_TARGET, *, step: int = 1, scale: float = 1.0):
    """Per-frame visibility of the sync marker (times, frames, visible)."""
    import cv2
    det = cv2.aruco.ArucoDetector(spec.dictionary_obj(), cv2.aruco.DetectorParameters())
    ts, fs, vis = [], [], []
    for i, t, f in iter_frames(meta, step=step, gray=True):
        g = f if scale == 1.0 else cv2.resize(f, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        _, ids, _ = det.detectMarkers(g)
        ts.append(t); fs.append(i)
        vis.append(ids is not None and spec.sync_marker_id in ids.ravel())
    return ts, fs, vis


def scan(meta: ClipMeta, spec: TargetSpec = DEFAULT_TARGET):
    """One decoding pass: (card states, brightness series) for card_events / light_events."""
    import cv2
    det = cv2.aruco.ArucoDetector(spec.dictionary_obj(), cv2.aruco.DetectorParameters())
    ts, fs, vis, br = [], [], [], []
    for i, t, f in iter_frames(meta, gray=True):
        _, ids, _ = det.detectMarkers(f)
        ts.append(t); fs.append(i)
        vis.append(ids is not None and spec.sync_marker_id in ids.ravel())
        br.append(float(f[::4, ::4].mean()))
    return (ts, fs, vis), (ts, fs, br)


def card_events(meta: ClipMeta, spec: TargetSpec = DEFAULT_TARGET, *, states=None) -> list[SyncEvent]:
    ts, fs, vis = states if states is not None else card_states(meta, spec)
    return _transitions(ts, fs, vis, "card", SYNC_THRESHOLDS["min_stable_frames"])


def brightness_series(meta: ClipMeta, step: int = 1):
    ts, fs, b = [], [], []
    for i, t, f in iter_frames(meta, step=step, gray=True):
        ts.append(t); fs.append(i); b.append(float(f[::4, ::4].mean()))
    return ts, fs, b


def light_events(meta: ClipMeta, *, series=None) -> list[SyncEvent]:
    ts, fs, b = series if series is not None else brightness_series(meta)
    b = np.asarray(b)
    if len(b) < 3:
        return []
    d = np.diff(b)
    thr = max(SYNC_THRESHOLDS["light_step_min"], 6.0 * float(np.median(np.abs(d - np.median(d)))) + 1e-9)
    out = []
    for i in np.where(np.abs(d) >= thr)[0]:
        if out and out[-1].frame_after == fs[i]:
            continue
        out.append(SyncEvent(kind="light", direction="up" if d[i] > 0 else "down", t=0.5 * (ts[i] + ts[i + 1]),
                             uncertainty_s=0.5 * (ts[i + 1] - ts[i]), frame_before=fs[i], frame_after=fs[i + 1]))
    return out


def audio_events(path, *, rate: int = 4000, min_gap_s: float = 0.5) -> list[SyncEvent]:
    env = audio_envelope(path, rate)
    if env is None:
        return []
    x, r = env
    win = max(1, int(0.005 * r))
    e = np.convolve(np.abs(x), np.ones(win) / win, mode="same")
    floor = np.median(e) + 1e-6
    peak = e.max()
    if peak < 8 * floor:
        return []
    above = np.where(e > max(8 * floor, 0.35 * peak))[0]
    out, last = [], -1e9
    for i in above:
        t = i / r
        if t - last >= min_gap_s:
            out.append(SyncEvent(kind="audio", direction="up", t=float(t), uncertainty_s=1.0 / r + 0.0025))
        last = t
    return out


def align(top: Sequence[SyncEvent], side: Sequence[SyncEvent], *, top_meta: Optional[ClipMeta] = None,
          side_meta: Optional[ClipMeta] = None) -> SyncResult:
    T = SYNC_THRESHOLDS
    if not top or not side:
        which = "top" if not top else "side"
        return SyncResult(ok=False, reasons=[f"no sync event detected in the {which} clip; refusing to triangulate "
                                             "(the clips may be separate takes)"])
    best = None
    for a in top:
        for b in side:
            if (a.kind, a.direction) != (b.kind, b.direction):
                continue
            off = b.t - a.t
            pairs = []
            used = set()
            for x in top:
                cands = [(abs((y.t - x.t) - off), j) for j, y in enumerate(side)
                         if (y.kind, y.direction) == (x.kind, x.direction) and j not in used]
                if cands:
                    err, j = min(cands)
                    # allow the largest acceptable clock drift to accumulate with time from the anchor event
                    if err <= T["match_tolerance_s"] + T["max_abs_drift"] * abs(x.t - a.t):
                        used.add(j)
                        pairs.append((x, side[j]))
            score = (len(pairs), -sum(abs((y.t - x.t) - off) for x, y in pairs))
            if best is None or score > best[0]:
                best = (score, pairs)
    if best is None or not best[1]:
        return SyncResult(ok=False, reasons=["the clips show no sync event of the same kind in both views; "
                                             "refusing to triangulate (they may be separate takes)"])
    pairs = best[1]
    notes, reasons = [], []
    kinds_used = {(x.kind, x.direction) for x, _ in pairs}
    extra_top = [x for x in top if (x.kind, x.direction) in kinds_used and all(x is not p for p, _ in pairs)]
    extra_side = [y for y in side if (y.kind, y.direction) in kinds_used and all(y is not q for _, q in pairs)]
    if extra_top or extra_side:
        reasons.append(f"sync events do not correspond: {len(extra_top)} top and {len(extra_side)} side events have no "
                       "partner at the same relative time (separate takes, or an event missed by one camera)")
    if len(pairs) < T["min_matched_events"]:
        reasons.append("fewer than two matched sync events: a single event cannot tell one take from two separate "
                       "performances; show the sync card at the start AND at the end of the take")
    ta = np.array([x.t for x, _ in pairs])
    tb = np.array([y.t for _, y in pairs])
    unc = float(np.sqrt(np.mean([x.uncertainty_s ** 2 + y.uncertainty_s ** 2 for x, y in pairs])))
    drift = len(pairs) >= 2 and (ta.max() - ta.min()) >= T["min_drift_baseline_s"]
    if drift:
        b, a = np.polyfit(ta, tb, 1)
        if abs(b - 1.0) > T["max_abs_drift"]:
            reasons.append(f"clock rate mismatch {abs(b - 1) * 1e3:.2f} ms/s: clips edited, slowed or not the same take")
    else:
        b, a = 1.0, float(np.mean(tb - ta))
    res = tb - (a + b * ta)
    n_free = 2 if drift else 1
    residual = float(np.sqrt(np.sum(res ** 2) / max(1, len(pairs) - n_free))) if len(pairs) > n_free else None
    if residual is None:
        notes.append("only one matched sync event (or events too close together): the residual cannot be measured; "
                     "record a second sync event at the end of the take")
    total = float(np.hypot(residual or 0.0, unc))
    frame_dt = None
    if top_meta is not None and top_meta.n_frames > 1:
        frame_dt = float(np.median(np.diff(top_meta.pts_s)))
    res_frames = None if frame_dt is None else total / frame_dt
    if res_frames is not None and res_frames > T["max_residual_frames"]:
        reasons.append(f"sync uncertainty {total * 1000:.0f} ms is more than {T['max_residual_frames']} frame interval")
    overlap = None
    if top_meta is not None and side_meta is not None:
        s0, s1 = (np.asarray([side_meta.pts_s[0], side_meta.pts_s[-1]]) - a) / b
        overlap = float(min(top_meta.pts_s[-1], s1) - max(top_meta.pts_s[0], s0))
        if overlap <= 0:
            reasons.append("after alignment the clips do not overlap in time")
    kinds = sorted({x.kind for x, _ in pairs})
    return SyncResult(ok=not reasons, method="+".join(kinds), offset_s=float(a), scale=float(b), drift_estimated=bool(drift),
                      matched=[(float(x.t), float(y.t)) for x, y in pairs], residual_s=residual, uncertainty_s=total,
                      residual_frames=res_frames, overlap_s=overlap, reasons=reasons, notes=notes)
