"""The phone-capture record and its offline pipeline.

``build_session`` runs calibration, synchronisation, tracking (offline:
person-drawn boxes + classical tracking, optional tags, optional imported
model tracks), reconstruction and event detection, and collects every review
item. ``CaptureSession.gate`` says whether a task specification may be
proposed from it: not while any blocking ("abstain") item is unresolved.

Evidence kind:
  camera_video_reconstruction   real phone footage, calibration accepted, all
                                blocking items resolved by a person
  unverified_camera_video       real footage that has not passed the gate
  synthetic_test_render         rendered test data (never evidence about phones)
"""
from __future__ import annotations

import datetime as _dt
import json
import uuid
from pathlib import Path
from typing import Literal, Optional, Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from mirror.capture import calibrate as C
from mirror.capture import events as EV
from mirror.capture import media as M
from mirror.capture import proxy as PX
from mirror.capture import reconstruct as RC
from mirror.capture import sync as Y
from mirror.capture import track as TR
from mirror.capture.target import DEFAULT_TARGET, TargetSpec

SESSION_VERSION = 1


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClipRecord(_M):
    role: str                         # "lens:top", "take:top", ...
    name: str                         # file name only (no directories are stored)
    sha256: str
    n_frames: int
    duration_s: Optional[float]
    variable_frame_rate: bool
    first_pts_s: float
    fingerprint: M.CaptureFingerprint


class ReviewItem(_M):
    code: str
    severity: Literal["abstain", "review"]
    message: str
    object: Optional[str] = None
    event: Optional[str] = None
    camera: Optional[str] = None
    resolved: bool = False
    resolution: Optional[str] = None


class CaptureSession(_M):
    version: int = SESSION_VERSION
    session_id: str = Field(default_factory=lambda: "cap_" + uuid.uuid4().hex[:12])
    created_at: str = Field(default_factory=lambda: _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"))
    synthetic: bool = False
    evidence_kind: Literal["camera_video_reconstruction", "unverified_camera_video", "synthetic_test_render"] = "unverified_camera_video"
    target: TargetSpec = DEFAULT_TARGET
    print_check: Optional[C.PrintCheck] = None
    clips: list[ClipRecord] = Field(default_factory=list)
    intrinsics: dict[str, C.Intrinsics] = Field(default_factory=dict)
    poses: dict[str, C.TablePose] = Field(default_factory=dict)
    recalibration: dict[str, dict] = Field(default_factory=dict)
    camera_shift_px: dict[str, float] = Field(default_factory=dict)
    sync: Optional[Y.SyncResult] = None
    mode: Literal["two_view", "single_view"] = "single_view"
    track_sources: dict[str, list[dict]] = Field(default_factory=dict)
    mappings: list[PX.ObjectMapping] = Field(default_factory=list)
    placement: PX.WorkspacePlacement = Field(default_factory=PX.WorkspacePlacement)
    tray: Optional[EV.TrayRegion] = None
    reconstruction: Optional[RC.Reconstruction] = None
    events: list[EV.HumanEvent] = Field(default_factory=list)
    corrections: list[EV.Correction] = Field(default_factory=list)
    review: list[ReviewItem] = Field(default_factory=list)
    model_calls: list[dict] = Field(default_factory=list)      # offline pipeline: always empty
    notes: list[str] = Field(default_factory=list)

    def gate(self) -> tuple[bool, list[ReviewItem]]:
        blocking = [r for r in self.review if r.severity == "abstain" and not r.resolved]
        return (not blocking), blocking

    def finalise_kind(self) -> None:
        if self.synthetic:
            self.evidence_kind = "synthetic_test_render"
        else:
            ok, _ = self.gate()
            self.evidence_kind = "camera_video_reconstruction" if ok else "unverified_camera_video"

    def save(self, path) -> Path:
        Path(path).write_text(self.model_dump_json(indent=1))
        return Path(path)

    @staticmethod
    def load(path) -> "CaptureSession":
        return CaptureSession.model_validate_json(Path(path).read_text())


def _clip_record(role: str, m: M.ClipMeta) -> ClipRecord:
    return ClipRecord(role=role, name=Path(m.path).name, sha256=m.sha256, n_frames=m.n_frames, duration_s=m.duration_s,
                      variable_frame_rate=m.variable_frame_rate, first_pts_s=m.pts_s[0], fingerprint=m.fingerprint)


def lens_detections(meta: M.ClipMeta, spec: TargetSpec, *, max_views: int = 30) -> list[C.Detection]:
    """Board detections from a lens-calibration clip, sampled evenly."""
    step = max(1, meta.n_frames // max_views)
    dets = []
    for i, t, f in M.iter_frames(meta, step=step, gray=True):
        d = C.detect(f, spec, frame_index=i, pts_s=t)
        if d.n >= C.THRESHOLDS["intr_min_corners_per_view"]:
            dets.append(d)
    return dets


def first_board(meta: M.ClipMeta, spec: TargetSpec, *, search_s: float = 3.0) -> Optional[C.Detection]:
    """Best board view in the first seconds of a take (board lying on the table)."""
    best = None
    for i, t, f in M.iter_frames(meta, gray=True):
        if t - meta.pts_s[0] > search_s:
            break
        d = C.detect(f, spec, frame_index=i, pts_s=t)
        if best is None or d.n > best.n:
            best = d
    return best


def last_board(meta: M.ClipMeta, spec: TargetSpec, *, search_s: float = 3.0) -> Optional[C.Detection]:
    best = None
    t_end = meta.pts_s[-1]
    start = next((k for k, t in enumerate(meta.pts_s) if t >= t_end - search_s), 0)
    for i, t, f in M.iter_frames(meta, start=start, gray=True):
        d = C.detect(f, spec, frame_index=i, pts_s=t)
        if d.n >= 12 and (best is None or d.n > best.n):
            best = d
    return best


def build_session(*, take: dict[str, str], lens: dict[str, str] | None = None, intrinsics: dict[str, C.Intrinsics] | None = None,
                  print_check: C.PrintCheck, prompts: Sequence[TR.Prompt], mappings: Sequence[PX.ObjectMapping],
                  spec: TargetSpec = DEFAULT_TARGET, tags: dict[int, str] | None = None,
                  model_tracks: dict[str, TR.TrackSet] | None = None, tray: Optional[EV.TrayRegion] = None,
                  placement: Optional[PX.WorkspacePlacement] = None, synthetic: bool = False,
                  corrections: Sequence[EV.Correction] = (), identity_confirmations: Sequence[tuple[str, str, int]] = (),
                  manual_keyframes: dict[str, dict[str, dict[int, tuple[float, float]]]] | None = None,
                  tracker: Literal["classical", "manual", "model"] = "classical") -> CaptureSession:
    """Offline pipeline. ``take`` maps camera name ("top", "side") to the take
    clip; ``lens`` maps camera name to its lens-calibration clip (or pass
    ``intrinsics``). ``identity_confirmations`` are (camera, label, from_frame)
    confirmations a person gave for reacquired tracks."""
    s = CaptureSession(synthetic=synthetic, target=spec, print_check=print_check, mappings=list(mappings),
                       placement=placement or PX.WorkspacePlacement(), corrections=list(corrections))
    review: list[ReviewItem] = []
    for chk in print_check.checks(spec):
        if chk.passed is False:
            review.append(ReviewItem(code="print_scale_invalid", severity="abstain",
                                     message=f"Printed target check failed ({chk.name} = {chk.value}); reprint at 100% and measure again."))
    metas = {}
    for cam, path in take.items():
        m = M.probe(path)
        metas[cam] = m
        s.clips.append(_clip_record(f"take:{cam}", m))
    cams = {}
    for cam, m in metas.items():
        intr = (intrinsics or {}).get(cam)
        if intr is None and lens and cam in lens:
            lm = M.probe(lens[cam])
            s.clips.append(_clip_record(f"lens:{cam}", lm))
            intr = C.calibrate_intrinsics(lens_detections(lm, spec), spec, print_check, lm.fingerprint)
        if intr is None:
            review.append(ReviewItem(code="intrinsics_missing", severity="abstain", camera=cam,
                                     message=f"No lens calibration for the {cam} phone. Film the board waved in front of it."))
            continue
        s.intrinsics[cam] = intr
        if not intr.accepted:
            failed = [f"{c.name} = {c.value} (limit {c.threshold})" for c in intr.checks if c.passed is False]
            review.append(ReviewItem(code="calibration_invalid", severity="abstain", camera=cam,
                                     message=f"{cam} lens calibration rejected: " + "; ".join(failed)))
        d0 = first_board(m, spec)
        reasons, checks = C.recalibration_reasons(intr, m.fingerprint, d0, spec, print_check)
        s.recalibration[cam] = {"reasons": reasons, "checks": [c.model_dump() for c in checks]}
        if reasons:
            review.append(ReviewItem(code="recalibration_required", severity="abstain", camera=cam,
                                     message=f"{cam}: " + "; ".join(reasons)))
        if d0 is None or d0.n < 4:
            continue
        pose = C.table_pose(d0, intr, spec, print_check, clip_sha256=m.sha256)
        s.poses[cam] = pose
        if not pose.accepted:
            failed = [f"{c.name} = {c.value} (limit {c.threshold})" for c in pose.checks if c.passed is False]
            review.append(ReviewItem(code="pose_invalid", severity="abstain", camera=cam,
                                     message=f"{cam} camera pose on the table rejected: " + "; ".join(failed)))
        # camera bump during the take: compare a frame after the board is gone with the last frame
        try:
            k0 = min(m.n_frames - 1, (d0.frame_index or 0) + int(2.0 / max(np.median(np.diff(m.pts_s)), 1e-3)))
            shift = C.camera_shift_px(M.read_frame(m, k0), M.read_frame(m, m.n_frames - 1))
            s.camera_shift_px[cam] = shift
            if shift > C.THRESHOLDS["motion_max_shift_px"] * max(1.0, m.width / 960):
                review.append(ReviewItem(code="camera_moved", severity="abstain", camera=cam,
                                         message=f"The {cam} phone moved by about {shift:.1f} px during the take; recalibrate and refilm."))
        except M.ClipError:
            pass
        d1 = last_board(m, spec)
        if d1 is not None and d1.frame_index != d0.frame_index:
            p1 = C.table_pose(d1, intr, spec, print_check)
            dmm, ddeg = C.pose_drift(pose, p1)
            s.recalibration[cam]["end_drift_mm_deg"] = [dmm, ddeg]
            if dmm > C.THRESHOLDS["drift_max_translation_mm"] or ddeg > C.THRESHOLDS["drift_max_rotation_deg"]:
                review.append(ReviewItem(code="camera_moved", severity="abstain", camera=cam,
                                         message=f"Board seen at the end of the take moved {dmm:.1f} mm / {ddeg:.2f} deg: the {cam} phone moved."))
        cams[cam] = RC.CameraModel(name=cam, intrinsics=intr, pose=pose)
    # synchronisation
    sync = None
    if len(metas) >= 2 and "top" in metas and len(cams) >= 2:
        other = next(c for c in metas if c != "top")
        evs = {}
        for c in ("top", other):
            cards, light = Y.scan(metas[c], spec)
            evs[c] = Y.card_events(metas[c], spec, states=cards) + Y.light_events(metas[c], series=light)
        ev_top, ev_oth = evs["top"], evs[other]
        sync = Y.align(ev_top, ev_oth, top_meta=metas["top"], side_meta=metas[other])
        if not sync.ok:
            review.append(ReviewItem(code="sync_refused", severity="review",
                                     message="Not triangulating: " + "; ".join(sync.reasons) + ". Using single-view positions."))
    s.sync = sync
    # tracking
    tracks = {}
    for cam, m in metas.items():
        sets = []
        if tracker == "manual":
            if manual_keyframes and cam in manual_keyframes:
                sets.append(TR.manual_track(m, manual_keyframes[cam], camera=cam))
        elif tracker == "classical" and any(p.camera == cam for p in prompts):
            ts = TR.classical_track(m, prompts, camera=cam)
            for c2, lab, frm in identity_confirmations:
                if c2 == cam and lab in ts.tracks:
                    TR.confirm_identity(ts, lab, frm)
            sets.append(ts)
        if model_tracks and cam in model_tracks and tracker in ("classical", "model"):
            sets.append(model_tracks[cam])
            if model_tracks[cam].model_origin:
                s.model_calls.append({"camera": cam, "model": model_tracks[cam].model, "origin": model_tracks[cam].model_origin})
        if tags and cam in s.intrinsics:
            sets.append(TR.tag_track(m, s.intrinsics[cam].Km, s.intrinsics[cam].dm, camera=cam, tag_labels=tags))
        tracks[cam] = sets
        s.track_sources[cam] = [{"source": x.source, "model": x.model, "model_origin": x.model_origin, "notes": x.notes} for x in sets]
    for item in PX.review_items(list(mappings)):
        review.append(ReviewItem(**item))
    usable = {c: cams[c] for c in cams if c in s.poses}
    if not usable:
        review.append(ReviewItem(code="no_calibrated_camera", severity="abstain",
                                 message="No camera has an accepted pose on the table; nothing can be reconstructed."))
        s.review = review
        s.finalise_kind()
        return s
    rec = RC.reconstruct(usable, {c: tracks[c] for c in usable}, list(mappings), sync=sync,
                         reference="top" if "top" in usable else next(iter(usable)))
    s.reconstruction = rec
    s.mode = rec.mode
    if rec.mode == "single_view":
        review.append(ReviewItem(code="single_view", severity="review",
                                 message="Single view: positions are on the table plane only; heights while lifted are assumed "
                                         "and tilt is not observed unless a tag is visible."))
    for lab, tr in rec.objects.items():
        dis = [r for r in tr.triangulation_residual_m if r is not None]
        if dis and np.mean([r > RC.MAX_VIEW_DISAGREEMENT_M for r in dis]) > 0.2:
            review.append(ReviewItem(code="views_disagree", severity="abstain", object=lab,
                                     message=f"The two views disagree about the {lab} in {100 * np.mean([r > RC.MAX_VIEW_DISAGREEMENT_M for r in dis]):.0f}% "
                                             "of frames: wrong identity, bad sync, or separate takes."))
        if all(st != "measured" for st in tr.tilt_status):
            review.append(ReviewItem(code="tilt_unobserved", severity="review", object=lab,
                                     message=f"The {lab}'s tilt was never observed; MIRROR will ask rather than assume it stayed upright."))
    tray_region = tray
    if tray_region is None:
        tray_map = next((mm for mm in mappings if mm.proxy == "tray" and mm.confirmed_by_person), None)
        if tray_map and tray_map.track_label in rec.objects:
            tt = rec.objects[tray_map.track_label]
            pts = [p for p, st in zip(tt.pos, tt.pos_status) if p is not None and st == "measured"]
            if pts:
                w = tray_map.real_dims.get("width"); dd = tray_map.real_dims.get("depth")
                half = ((w.value_m / 2) if w else PX.SUPPORTED["tray"]["proxy_dims_m"][0] / 2,
                        (dd.value_m / 2) if dd else PX.SUPPORTED["tray"]["proxy_dims_m"][1] / 2)
                c = np.median(np.array(pts), 0)
                tray_region = EV.TrayRegion(center_xy=(float(c[0]), float(c[1])), inner_half=half, yaw_deg=0.0,
                                            source="assumed_aligned_with_board",
                                            footprint_source="measured_ruler" if (w and dd) else "assumed_proxy")
    review += tray_review(tray_region, mappings)
    s.tray = tray_region
    evs = []
    for lab, tr in rec.objects.items():
        if tr.proxy in ("tray", "wall"):
            continue
        evs += EV.detect_events(tr, tray_region)
    s.events = EV.apply_corrections(evs, s.corrections)
    for item in EV.review_items(s.events, {k: v for k, v in rec.objects.items() if v.proxy not in ("tray", "wall")}):
        if item["code"] == "identity_uncertain":
            continue  # handled through identity confirmations below
        review.append(ReviewItem(**item))
    for lab, tr in rec.objects.items():
        if any(tr.identity_uncertain):
            review.append(ReviewItem(code="identity_uncertain", severity="abstain", object=lab,
                                     message=f"The {lab} track was lost and found again; confirm it is the same object."))
    s.notes.append(f"tracker: {tracker}")
    s.review = review
    s.finalise_kind()
    return s


TRAY_HALF_RANGE_M = (0.03, 0.30)   # inside half-width/half-depth the simulated tray accepts


def tray_review(tray: Optional[EV.TrayRegion], mappings: list[PX.ObjectMapping]) -> list[ReviewItem]:
    """The tray is the task's destination: without it there is no packing task to propose."""
    if tray is None:
        mapped = any(m.proxy == "tray" and m.confirmed_by_person for m in mappings)
        why = ("the tray is mapped but was never located at rest in the footage"
               if mapped else "no object is mapped to the tray and no tray region was entered")
        return [ReviewItem(code="tray_not_mapped", severity="abstain", object=None,
                           message=f"Map the tray: {why}. MIRROR needs one destination tray before it can propose a task. "
                                   "Draw a box on the tray in each camera and confirm it as the tray (with its measured inside "
                                   "width and depth), or enter the tray region (centre and inside half-sizes on the board).")]
    out = []
    if not all(TRAY_HALF_RANGE_M[0] <= h <= TRAY_HALF_RANGE_M[1] for h in tray.inner_half):
        out.append(ReviewItem(code="tray_size_unsupported", severity="abstain", object=None,
                              message=f"Tray inside {2 * tray.inner_half[0] * 100:.0f} x {2 * tray.inner_half[1] * 100:.0f} cm is "
                                      f"outside the supported {2 * TRAY_HALF_RANGE_M[0] * 100:.0f}-{2 * TRAY_HALF_RANGE_M[1] * 100:.0f} cm "
                                      "per side; check the measurement."))
    if tray.footprint_source == "assumed_proxy":
        out.append(ReviewItem(code="tray_footprint_assumed", severity="review", object=None,
                              message="The tray's inside width and depth were not measured; the simulated tray uses the "
                                      "catalogue size. Measure them with a ruler for a faithful destination."))
    return out


def summary_lines(s: CaptureSession) -> list[str]:
    """Plain-language summary for the person reviewing the capture."""
    ok, blocking = s.gate()
    out = [f"Capture {s.session_id} ({s.evidence_kind.replace('_', ' ')}), mode {s.mode.replace('_', '-')}."]
    if s.synthetic:
        out.append("SYNTHETIC test render: not real footage.")
    for cam, intr in s.intrinsics.items():
        out.append(f"{cam} lens: {'accepted' if intr.accepted else 'REJECTED'}, reprojection "
                   f"{intr.rms_px:.2f} px over {intr.n_views} views." if intr.rms_px is not None else f"{cam} lens: {intr.source}.")
    for cam, p in s.poses.items():
        out.append(f"{cam} pose on the table: {'accepted' if p.accepted else 'REJECTED'}, {p.rms_px:.2f} px, "
                   f"hold-out {p.holdout_mm if p.holdout_mm is not None else float('nan'):.1f} mm, {p.distance_m * 100:.0f} cm away.")
    if s.sync is not None:
        out.append("Sync: " + (f"{s.sync.method}, offset {s.sync.offset_s:.3f} s, uncertainty {1000 * (s.sync.uncertainty_s or 0):.0f} ms"
                               if s.sync.ok else "refused (" + "; ".join(s.sync.reasons) + ")"))
    if s.reconstruction:
        for lab, tr in s.reconstruction.objects.items():
            out.append(f"{lab} -> {tr.proxy}: located {100 * tr.coverage(('measured',)):.0f}% measured, "
                       f"{100 * tr.coverage(('inferred',)):.0f}% inferred, {100 * tr.coverage(('interpolated',)):.0f}% interpolated; "
                       f"tilt observed {100 * tr.tilt_coverage():.0f}%.")
    if s.tray is not None:
        t = s.tray
        out.append(f"Destination tray: inside {2 * t.inner_half[0] * 100:.1f} x {2 * t.inner_half[1] * 100:.1f} cm "
                   f"({t.footprint_source.replace('_', ' ')}), centre ({t.center_xy[0] * 100:.1f}, {t.center_xy[1] * 100:.1f}) cm "
                   f"on the board ({t.source.replace('_', ' ')}).")
    else:
        out.append("Destination tray: not mapped.")
    for e in s.events:
        out.append(f"{e.object} {e.type} at {e.t:.2f} s (between {e.t_lo:.2f} and {e.t_hi:.2f} s){', in ' + e.placed_in if e.placed_in else ''}"
                   f" [{e.status}; {e.label}].")
    for m in s.mappings:
        out.append(m.fidelity_statement())
    out.append("Ready to propose a task specification." if ok else
               f"Not ready: {len(blocking)} item(s) need a person's review.")
    for r in s.review:
        if not r.resolved:
            out.append(f"- [{r.severity}] {r.message}")
    return out
