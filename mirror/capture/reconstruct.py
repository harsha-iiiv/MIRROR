"""Object trajectories in the table frame from calibrated phone footage.

Per object and per reference-camera frame the result records the position,
its status and uncertainty, the tilt, and where each value came from.

Status vocabulary (shared with the rest of MIRROR):
  measured      observed in this frame by a calibrated view (two-view
                triangulation, or a single view while the object rests on
                the table at a known height)
  inferred      computed with an assumption that this footage cannot check
                (single view while the object is lifted: the height is
                assumed, so the position carries the parallax uncertainty)
  interpolated  not observed in this frame; filled between observations at
                most ``MAX_FILL_S`` apart
  unobserved    no value. Never replaced by a default.

Tilt:
  * a printed tag on the object gives the full tilt relative to its resting
    pose at the start (kind ``full``);
  * an elongated silhouette in the side view gives only the tilt component in
    that camera's image plane (kind ``lower_bound``: the true tilt is at least
    this);
  * the top view alone cannot observe tilt: status ``unobserved``.
"""
from __future__ import annotations

from typing import Literal, Optional, Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from mirror.capture.calibrate import Intrinsics, TablePose
from mirror.capture.geometry import pixel_rays, project, ray_plane, triangulate
from mirror.capture.proxy import ObjectMapping
from mirror.capture.sync import SyncResult
from mirror.capture.track import Obs2D, TrackSet

MAX_FILL_S = 0.5
REST_SPEED_M_S = 0.015
REST_MIN_S = 0.4
LIFT_RANGE_M = 0.25                 # possible carry height above rest, for single-view parallax bounds
MAX_VIEW_DISAGREEMENT_M = 0.025
PIXEL_SIGMA = 1.5                   # assumed centroid noise for uncertainty propagation (px)
TAG_MAX_OBLIQUITY_DEG = 60.0        # tag views more oblique than this are not used
TAG_AMBIGUITY_RATIO = 1.5
TAG_MAX_STEP_DEG = 30.0             # a tag pose further than this from the previous one starts a new chain
TAG_MOUNT_TOLERANCE_DEG = 25.0      # at rest the tag's 'up' must be within this of vertical (else remount it)           # without a previous pose, accept the better planar solution only if clearly better
PARTIAL_SIGMA_M = 0.03             # extra uncertainty when the object is partly hidden
PLANE_MAX_RAY_ANGLE_DEG = 45.0      # single-view table-plane positions only from steep (top-like) views
PIXEL_MOTION_PX_S = 60.0            # 2D speed that counts as moving in an image (sustained over 2 frame pairs)

Status = Literal["measured", "inferred", "interpolated", "unobserved"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CameraModel(_M):
    name: str
    intrinsics: Intrinsics
    pose: TablePose


class ObjectTrajectory(_M):
    label: str
    proxy: str
    t: list[float]
    pos: list[Optional[tuple[float, float, float]]]
    pos_status: list[Status]
    pos_sigma_m: list[Optional[float]]
    pos_basis: list[str]
    tilt_deg: list[Optional[float]]
    tilt_status: list[Status]
    tilt_kind: list[Optional[Literal["full", "lower_bound"]]]
    identity_uncertain: list[bool]
    views: list[list[str]]
    triangulation_residual_m: list[Optional[float]]
    resting: list[Optional[bool]] = Field(default_factory=list)
    moving_2d: list[Optional[bool]] = Field(default_factory=list)   # any view saw the object move (None: no view saw it)
    notes: list[str] = Field(default_factory=list)

    def coverage(self, statuses=("measured",)) -> float:
        return float(np.mean([s in statuses for s in self.pos_status])) if self.pos_status else 0.0

    def tilt_coverage(self) -> float:
        return float(np.mean([s == "measured" for s in self.tilt_status])) if self.tilt_status else 0.0


class Reconstruction(_M):
    mode: Literal["two_view", "single_view"]
    reference_camera: str
    cameras: list[str]
    sync: Optional[SyncResult] = None
    objects: dict[str, ObjectTrajectory]
    notes: list[str] = Field(default_factory=list)


def _obs_at(obs: Sequence[Obs2D], times: np.ndarray, t: float, dt: float) -> Optional[tuple[np.ndarray, Obs2D, bool]]:
    """Pixel observation at time t (nearest within half a frame, else linear
    between bracketing seen frames at most 1.5 frames apart)."""
    if not len(times):
        return None
    k = int(np.searchsorted(times, t))
    cands = [j for j in (k - 1, k) if 0 <= j < len(obs)]
    near = min(cands, key=lambda j: abs(times[j] - t))
    o = obs[near]
    if abs(times[near] - t) <= 0.5 * dt and o.seen:
        return np.array([o.cx, o.cy]), o, False
    lo, hi = k - 1, k
    if 0 <= lo and hi < len(obs) and obs[lo].seen and obs[hi].seen and times[hi] - times[lo] <= 1.5 * dt:
        a = (t - times[lo]) / (times[hi] - times[lo])
        p = (1 - a) * np.array([obs[lo].cx, obs[lo].cy]) + a * np.array([obs[hi].cx, obs[hi].cy])
        return p, obs[lo] if a < 0.5 else obs[hi], True
    return None


def _merge(sets: Sequence[TrackSet], label: str) -> list[Obs2D]:
    """Per frame, prefer manual > model > classical (tag sets are used for tilt only)."""
    order = {"manual": 0, "model": 1, "classical": 2}
    cands = [s for s in sets if s.source in order and label in s.tracks]
    if not cands:
        return []
    cands.sort(key=lambda s: order[s.source])
    base = [o for o in cands[0].tracks[label]]
    for s in cands[1:]:
        for k, o in enumerate(s.tracks[label]):
            if k < len(base) and not base[k].seen and o.seen:
                base[k] = o
    return base


def _tilt_from_axis(cam: CameraModel, X: np.ndarray, axis_deg: float) -> float:
    """Tilt component (deg) in the image plane: angle between the observed
    silhouette axis and the image direction of world vertical at X."""
    K, d, R, t = cam.intrinsics.Km, cam.intrinsics.dm, cam.pose.Rm, cam.pose.tm
    a, b = project(K, d, R, t, np.stack([X, X + np.array([0, 0, 0.05])]))
    v = b - a
    up = np.degrees(np.arctan2(v[1], v[0]))
    diff = abs(((axis_deg - up) + 90.0) % 180.0 - 90.0)
    return float(diff)


def reconstruct(cams: dict[str, CameraModel], tracks: dict[str, Sequence[TrackSet]], mappings: Sequence[ObjectMapping], *,
                sync: Optional[SyncResult] = None, reference: str = "top") -> Reconstruction:
    names = list(cams)
    if reference not in cams:
        reference = names[0]
    two_view = len(names) >= 2 and sync is not None and sync.ok
    notes = []
    if len(names) >= 2 and not two_view:
        notes.append("two cameras given but not synchronised: positions are single-view only; no triangulation")
    others = [c for c in names if c != reference] if two_view else []
    # reference timeline
    ref_sets = tracks.get(reference, [])
    ref_times = None
    for s in ref_sets:
        for obs in s.tracks.values():
            ref_times = np.array([o.t for o in obs])
            break
        if ref_times is not None:
            break
    if ref_times is None:
        raise ValueError("no tracks for the reference camera")
    dt_ref = float(np.median(np.diff(ref_times))) if len(ref_times) > 1 else 1 / 30
    out = {}
    for m in mappings:
        if m.proxy == "ignore" or not m.confirmed_by_person:
            continue
        h, hsrc = m.height_m()
        z_rest = (h or 0.0) / 2.0 if m.proxy != "tray" else 0.0
        per_cam = {c: _merge(tracks.get(c, []), m.track_label) for c in names}
        per_times = {c: np.array([o.t for o in per_cam[c]]) for c in names}
        per_dt = {c: float(np.median(np.diff(per_times[c]))) if len(per_times[c]) > 1 else dt_ref for c in names}
        tags = {c: next((s.tracks[m.track_label] for s in tracks.get(c, []) if s.source == "tag" and m.track_label in s.tracks), None)
                for c in names}
        N = len(ref_times)
        pos = [None] * N; st = ["unobserved"] * N; sig = [None] * N; basis = [""] * N
        idu = [False] * N; views = [[] for _ in range(N)]; resid = [None] * N
        for i, t in enumerate(ref_times):
            rays, used, unc, partial = [], [], False, []
            for c in [reference] + others:
                tc = t if c == reference else float(sync.to_side(t))
                got = _obs_at(per_cam[c], per_times[c], tc, per_dt[c])
                if got is None:
                    continue
                px, o, _ = got
                cm = cams[c]
                origin, d = pixel_rays(cm.intrinsics.Km, cm.intrinsics.dm, cm.pose.Rm, cm.pose.tm, px[None])
                rays.append((origin, d[0], cm))
                used.append(c)
                unc = unc or not o.identity_confirmed
                if o.partial:
                    partial.append(c)
            if not rays:
                continue
            idu[i] = unc
            views[i] = used
            if len(rays) >= 2:
                X, r = triangulate([o for o, _, _ in rays], [d for _, d, _ in rays])
                resid[i] = r
                pos[i] = tuple(float(v) for v in X)
                dist = np.mean([np.linalg.norm(X - o) for o, _, _ in rays])
                f = np.mean([cm.intrinsics.Km[0, 0] for _, _, cm in rays])
                # camera-centre uncertainty matters in proportion to the height above the table
                csig = max((cm.pose.centre_sigma_mm or 0.0) for _, _, cm in rays) / 1000.0
                hfrac = max(0.0, X[2]) / max(np.mean([cm.pose.centre[2] for _, _, cm in rays]), 1e-6)
                sig[i] = float(np.sqrt(r ** 2 + (PIXEL_SIGMA * dist / f) ** 2 + (csig * hfrac) ** 2))
                if r > MAX_VIEW_DISAGREEMENT_M:
                    st[i] = "inferred"
                    basis[i] = f"views disagree by {r * 100:.1f} cm (identity or sync problem)"
                elif partial:
                    st[i] = "inferred"
                    basis[i] = "two-view triangulation, but partly hidden in " + ", ".join(partial) + " (centroid biased)"
                    sig[i] = float(sig[i] + PARTIAL_SIGMA_M)
                else:
                    st[i] = "measured"
                    basis[i] = "two-view triangulation of object centroids"
            else:
                o, d, cm = rays[0]
                if np.degrees(np.arccos(min(1.0, abs(d[2])))) > PLANE_MAX_RAY_ANGLE_DEG:
                    views[i] = used
                    continue  # an oblique single view cannot place the object on the table
                X = ray_plane(o, d, z_rest)
                if not np.all(np.isfinite(X)):
                    continue
                pos[i] = tuple(float(v) for v in X)
                st[i] = "measured_pending" if not partial else "partial_pending"  # resolved after rest detection
                basis[i] = f"single view {used[0]} on the plane z = {z_rest * 100:.1f} cm ({hsrc or 'unknown'} height)"
        # rest detection from positions
        P = np.array([p if p is not None else (np.nan,) * 3 for p in pos], float)
        rest = _resting(ref_times, P)
        for i in range(N):
            if st[i] == "partial_pending":
                st[i] = "inferred"
                cm = cams[views[i][0]]
                sig[i] = float(PARTIAL_SIGMA_M + PIXEL_SIGMA * np.linalg.norm(np.asarray(pos[i]) - cm.pose.centre) / cm.intrinsics.Km[0, 0])
                basis[i] += "; partly hidden (centroid biased)"
                continue
            if st[i] == "measured_pending":
                cm = cams[views[i][0]]
                if rest[i]:
                    st[i] = "measured"
                    dist = np.linalg.norm(np.asarray(pos[i]) - cm.pose.centre)
                    sig[i] = float(PIXEL_SIGMA * dist / cm.intrinsics.Km[0, 0])
                else:
                    st[i] = "inferred"
                    c = cm.pose.centre
                    X = np.asarray(pos[i])
                    # lifting by up to LIFT_RANGE_M moves the plane hit along the ray's horizontal direction
                    horiz = np.linalg.norm((X - c)[:2]) / max(c[2] - X[2], 1e-6)
                    sig[i] = float(LIFT_RANGE_M * horiz / 2 + PIXEL_SIGMA * np.linalg.norm(X - c) / cm.intrinsics.Km[0, 0])
                    basis[i] += "; object not at rest, height assumed (single view cannot measure it)"
        # fill short gaps
        seen = [k for k in range(N) if st[k] in ("measured", "inferred")]
        for a, b in zip(seen, seen[1:]):
            if b - a > 1 and ref_times[b] - ref_times[a] <= MAX_FILL_S:
                for k in range(a + 1, b):
                    w = (ref_times[k] - ref_times[a]) / (ref_times[b] - ref_times[a])
                    pos[k] = tuple(float(v) for v in (1 - w) * np.asarray(pos[a]) + w * np.asarray(pos[b]))
                    st[k] = "interpolated"
                    sig[k] = float(max(sig[a] or 0, sig[b] or 0) + 0.5 * np.linalg.norm(np.subtract(pos[b], pos[a])))
                    basis[k] = "interpolated between observations"
        # tilt
        tilt = [None] * N; tst = ["unobserved"] * N; tkind = [None] * N
        tag_ref = None
        prev = None
        for i, t in enumerate(ref_times):
            cands = []
            for c in names:
                tg = tags.get(c)
                if tg is None:
                    continue
                tc = t if c == reference else (float(sync.to_side(t)) if two_view else None)
                if tc is None:
                    continue
                tt = np.array([o.t for o in tg])
                j = int(np.argmin(np.abs(tt - tc)))
                o = tg[j]
                if abs(tt[j] - tc) > 0.5 * per_dt.get(c, dt_ref) or o.status != "tag":
                    continue
                sols = [np.asarray(o.tag_R)] + ([np.asarray(o.tag_R_alt)] if o.tag_R_alt is not None else [])
                tv = np.asarray(o.tag_t)
                obliq = float(np.degrees(np.arccos(abs(sols[0][:, 2] @ (tv / np.linalg.norm(tv))))))
                if obliq > TAG_MAX_OBLIQUITY_DEG:
                    continue
                Rw = [cams[c].pose.Rm.T @ Rs for Rs in sols]
                cands.append((obliq, Rw, o.tag_err))
            best = None
            if cands:
                obliq, Rw, err = min(cands, key=lambda x: x[0])
                if prev is not None:
                    near = min(Rw, key=lambda R: np.linalg.norm(R - prev))
                    if np.degrees(np.arccos(np.clip((np.trace(near @ prev.T) - 1) / 2, -1, 1))) <= TAG_MAX_STEP_DEG:
                        best = near
                if best is None and rest[i]:
                    # documented mounting: the tag's printed 'up' points to the mug's opening, so at rest it points up
                    up = [float(np.degrees(np.arccos(np.clip(R[2, 1], -1, 1)))) for R in Rw]
                    k = int(np.argmin(up))
                    if up[k] <= TAG_MOUNT_TOLERANCE_DEG:
                        best = Rw[k]
                if best is None and prev is None and len(Rw) > 1 and err and err[1] > TAG_AMBIGUITY_RATIO * max(err[0], 1e-3) \
                        and tag_ref is not None:
                    best = Rw[0]
            if best is None:
                prev = None
            if best is not None:
                prev = best
                if tag_ref is None and rest[i]:
                    tag_ref = best
                if tag_ref is not None:
                    Rrel = best @ tag_ref.T
                    tilt[i] = float(np.degrees(np.arccos(np.clip(Rrel[2, 2], -1, 1))))
                    tst[i] = "measured"; tkind[i] = "full"
                    continue
            if pos[i] is None or st[i] == "unobserved":
                continue
            for c in [x for x in names if x != "top"]:
                if c not in views[i]:
                    continue
                tc = t if c == reference else float(sync.to_side(t))
                got = _obs_at(per_cam[c], per_times[c], tc, per_dt[c])
                if got is None or got[1].axis_deg is None:
                    continue
                tilt[i] = _tilt_from_axis(cams[c], np.asarray(pos[i]), got[1].axis_deg)
                tst[i] = "measured"; tkind[i] = "lower_bound"
                break
        # 2D motion seen in any view (object-centric evidence that does not need 3D)
        mv = [None] * N
        for c in names:
            ob = per_cam[c]
            if not ob:
                continue
            tt = per_times[c]
            for i, t in enumerate(ref_times):
                tc = t if c == reference else (float(sync.to_side(t)) if two_view else None)
                if tc is None:
                    continue
                j = int(np.searchsorted(tt, tc))
                a, b = j - 1, j
                if a < 0 or b >= len(ob) or not (ob[a].seen and ob[b].seen) or tt[b] - tt[a] > 1.5 * per_dt[c]:
                    continue
                if any(ob[q].partial for q in (a, b)):
                    # partly hidden: a hand can shift the centroid by up to about half the object, so only a
                    # displacement larger than the object's own image size over half a second counts as motion
                    def _disp(t_from, t_to):
                        qa = int(np.clip(np.searchsorted(tt, t_from), 0, len(ob) - 1))
                        qb = int(np.clip(np.searchsorted(tt, t_to), 0, len(ob) - 1))
                        if qa == qb or not (ob[qa].seen and ob[qb].seen) or ob[qa].box is None:
                            return None, None
                        return (float(np.hypot(ob[qb].cx - ob[qa].cx, ob[qb].cy - ob[qa].cy)),
                                float(np.hypot(ob[qa].box[2], ob[qa].box[3])))
                    d_past, size = _disp(tc - 0.4, tc)
                    d_next, _ = _disp(tc, tc + 0.4)
                    if d_past is None or d_next is None or not (d_past > size and d_next > size):
                        continue
                    moving = True
                else:
                    if b + 1 >= len(ob) or not ob[b + 1].seen or a - 1 < 0 or not ob[a - 1].seen:
                        continue
                    if any(ob[q].partial for q in (a - 1, b + 1)):
                        continue
                    sp = [np.hypot(ob[q + 1].cx - ob[q].cx, ob[q + 1].cy - ob[q].cy) / max(tt[q + 1] - tt[q], 1e-6)
                          for q in (a - 1, a, b)]
                    moving = bool(min(sp[0:2]) > PIXEL_MOTION_PX_S or min(sp[1:3]) > PIXEL_MOTION_PX_S)
                    if not moving and max(sp) > PIXEL_MOTION_PX_S:
                        continue  # a single jump (tracker re-centring) is not evidence either way
                mv[i] = moving if mv[i] is None else (mv[i] or moving)
        tnotes = []
        if not any(s == "measured" for s in tst):
            tnotes.append("tilt never observed: no tag and no elongated silhouette in a side view")
        out[m.track_label] = ObjectTrajectory(
            label=m.track_label, proxy=m.proxy, t=[float(x) for x in ref_times], pos=pos,
            pos_status=[s if not s.endswith("_pending") else "unobserved" for s in st], pos_sigma_m=sig, pos_basis=basis,
            tilt_deg=tilt, tilt_status=tst, tilt_kind=tkind, identity_uncertain=idu, views=views,
            triangulation_residual_m=resid, resting=[None if np.isnan(P[k, 0]) else bool(rest[k]) for k in range(N)],
            moving_2d=mv,
            notes=tnotes)
    return Reconstruction(mode="two_view" if two_view else "single_view", reference_camera=reference, cameras=names,
                          sync=sync, objects=out, notes=notes)


def _resting(t: np.ndarray, P: np.ndarray) -> np.ndarray:
    """Frames where the object moves slower than REST_SPEED for REST_MIN_S around them."""
    N = len(t)
    rest = np.zeros(N, bool)
    ok = ~np.isnan(P[:, 0])
    for i in range(N):
        if not ok[i]:
            continue
        w = (np.abs(t - t[i]) <= REST_MIN_S / 2) & ok
        idx = np.where(w)[0]
        if len(idx) < 2 or t[idx[-1]] - t[idx[0]] < 0.6 * REST_MIN_S:
            continue
        span = np.linalg.norm(P[idx, :2].max(0) - P[idx, :2].min(0))
        rest[i] = span / max(t[idx[-1]] - t[idx[0]], 1e-6) < REST_SPEED_M_S * 2 and span < 0.01
    return rest
