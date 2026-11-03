"""Observability and provenance of demonstration evidence.

A demonstration trace (``DemoTrace``) stores one pose per object per frame,
because the planner, the studio and the summary all need a complete state.
Those numbers do not all mean the same thing. This module records, per value
and per frame, how each one was obtained:

* ``measured``      observed directly (a simulator state, a triangulated or
                    plane-intersected mask centroid, a side-view silhouette fit)
* ``inferred``      derived from other observations under a stated assumption
                    (an object hidden under the gripper moves with it; a resting
                    object's top-view outline matches its upright footprint)
* ``interpolated``  filled across a short gap between measured samples
* ``unobserved``    not seen; the stored value is a placeholder for display
                    and must never be used as evidence

and records where the whole trace came from (``EvidenceKind``), how the
cameras were calibrated, which model outputs it depends on and whether they
were recorded or fresh, which manual corrections were applied, and what needs
human review before the trace may support a specification.

Backward compatibility: ``DemoTrace.evidence`` is optional. A trace without it
is treated as simulator ground truth only when its ``source`` is a simulator
source; a video-derived trace without it is ``legacy_video_unannotated``, whose
tilt is unobserved (its writer stored a missing tilt as upright, so measured
and missing values cannot be told apart) and whose positions are at best
inferred. See docs/PERCEPTION.md.
"""
from __future__ import annotations

from typing import Iterable, Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

EVIDENCE_VERSION = 1

Observed = Literal["measured", "inferred", "interpolated", "unobserved"]
OBSERVED_VALUES: tuple[str, ...] = ("measured", "inferred", "interpolated", "unobserved")

EvidenceKind = Literal[
    "simulator_ground_truth",           # simulator state log (teleoperation, scripted, replay)
    "rendered_video_reconstruction",    # simulator-rendered video, reconstructed from its pixels
    "camera_video_reconstruction",      # real camera footage, calibrated, reconstructed from its pixels
    "unverified_video_reconstruction",  # video of unknown origin reconstructed with assumed calibration
    "legacy_video_unannotated",         # video-derived trace written before these annotations (derived only)
    "interface_fixture",                # kinematic key-framed motion for interface development
]
SIMULATOR_SOURCES = ("teleop_keyboard", "teleop_scripted", "replay")
VIDEO_KINDS = ("rendered_video_reconstruction", "camera_video_reconstruction",
               "unverified_video_reconstruction", "legacy_video_unannotated")

ModelOrigin = Literal["fresh_model_call", "recorded_replay", "offline_rules", "oracle_ground_truth", "unknown"]
IntrinsicsSource = Literal["simulator", "rendered_camera_model", "checkerboard", "user_supplied",
                           "assumed_rendered_model"]
ExtrinsicsSource = Literal["simulator", "table_outline_known_size", "scene_geometry", "calibration_board",
                           "user_supplied"]
Identity = Literal["simulator", "assumed_from_label", "confirmed_by_user", "ambiguous", "not_found"]
Severity = Literal["abstain", "review", "info"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


class Span(_Model):
    """Frames ``start``..``end`` (inclusive, trace frame indices) share one status."""

    status: Observed
    start: int
    end: int
    sigma: Optional[float] = None     # 1-sigma uncertainty in the quantity's unit (m or deg), when known
    basis: str = ""


class CalibrationRecord(_Model):
    camera: str
    intrinsics_source: IntrinsicsSource
    extrinsics_source: ExtrinsicsSource
    table_size_source: Literal["kit_catalogue", "user_measured", "not_used"] = "not_used"
    residual_px: Optional[float] = None
    valid: bool = True
    assumptions: list[str] = Field(default_factory=list)


class ModelArtifact(_Model):
    role: Literal["segmentation", "grounding", "video_observation", "transcription", "specification", "other"]
    model: str
    origin: ModelOrigin
    calls: int = 0
    keys: list[str] = Field(default_factory=list)
    note: str = ""


class ManualCorrection(_Model):
    what: Literal["identity", "calibration", "position", "event", "scene", "other"]
    detail: dict = Field(default_factory=dict)
    note: str = ""
    t_unix: Optional[float] = None


class ReviewItem(_Model):
    """Something a person must look at. ``abstain``: the trace must not support
    a specification until it is resolved; ``review``: show it with the proposal."""

    code: str
    severity: Severity
    message: str
    object_id: Optional[str] = None


class ObjectEvidence(_Model):
    object_id: str
    label: Optional[str] = None
    identity: Identity = "simulator"
    identity_note: str = ""
    position: list[Span] = Field(default_factory=list)
    tilt: list[Span] = Field(default_factory=list)


class TraceEvidence(_Model):
    version: int = EVIDENCE_VERSION
    kind: EvidenceKind
    cameras: list[str] = Field(default_factory=list)
    media: dict[str, str] = Field(default_factory=dict)          # camera -> content sha256
    calibration: list[CalibrationRecord] = Field(default_factory=list)
    objects: dict[str, ObjectEvidence] = Field(default_factory=dict)
    gripper_position: list[Span] = Field(default_factory=list)
    gripper_width: list[Span] = Field(default_factory=list)
    events_basis: str = ""
    models: list[ModelArtifact] = Field(default_factory=list)
    manual_corrections: list[ManualCorrection] = Field(default_factory=list)
    review: list[ReviewItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------ spans ---


def spans_from_statuses(statuses: Sequence[str], *, sigma: Optional[dict] = None,
                        basis: Optional[dict] = None) -> list[Span]:
    """Run-length encode a per-frame status list. ``sigma``/``basis``: status -> value."""
    out: list[Span] = []
    for i, s in enumerate(statuses):
        if s not in OBSERVED_VALUES:
            raise ValueError(f"unknown observability status {s!r}")
        if out and out[-1].status == s and out[-1].end == i - 1:
            out[-1].end = i
        else:
            out.append(Span(status=s, start=i, end=i, sigma=(sigma or {}).get(s), basis=(basis or {}).get(s, "")))
    return out


def uniform(status: str, n: int, *, sigma: Optional[float] = None, basis: str = "") -> list[Span]:
    return [Span(status=status, start=0, end=max(0, n - 1), sigma=sigma, basis=basis)] if n > 0 else []


def status_at(spans: Iterable[Span], i: int) -> str:
    for s in spans:
        if s.start <= i <= s.end:
            return s.status
    return "unobserved"


def statuses(spans: Iterable[Span], n: int) -> list[str]:
    out = ["unobserved"] * n
    for s in spans:
        for i in range(max(0, s.start), min(n - 1, s.end) + 1):
            out[i] = s.status
    return out


def coverage(spans: Iterable[Span], frames: Iterable[int], accept: Sequence[str] = ("measured",)) -> Optional[float]:
    """Fraction of ``frames`` whose status is in ``accept`` (None when no frames)."""
    spans = list(spans)
    fr = list(frames)
    if not fr:
        return None
    return sum(1 for i in fr if status_at(spans, i) in accept) / len(fr)


# --------------------------------------------------------------- resolving ---


def evidence_kind(trace) -> str:
    ev = getattr(trace, "evidence", None)
    if ev is not None:
        return ev.kind
    return "simulator_ground_truth" if trace.source in SIMULATOR_SOURCES else "legacy_video_unannotated"


LEGACY_TILT_BASIS = ("legacy video trace: a missing tilt was stored as upright, so measured and missing "
                     "values cannot be told apart")
LEGACY_POS_BASIS = "legacy video trace: measured, held and filled positions cannot be told apart"


def simulator_evidence(trace, *, kind: str = "simulator_ground_truth", note: str = "") -> TraceEvidence:
    n = len(trace.frames)
    basis = "simulator state" if kind == "simulator_ground_truth" else "kinematic fixture"
    objs = {o.id: ObjectEvidence(object_id=o.id, identity="simulator", position=uniform("measured", n, basis=basis),
                                 tilt=uniform("measured", n, basis=basis)) for o in trace.scene.objects}
    return TraceEvidence(kind=kind, objects=objs, gripper_position=uniform("measured", n, basis=basis),
                         gripper_width=uniform("measured", n, basis=basis),
                         events_basis="simulator contact and gripper state" if kind == "simulator_ground_truth"
                         else "fixture key frames", notes=[note] if note else [])


def resolve_evidence(trace) -> TraceEvidence:
    """The trace's evidence, or what may honestly be assumed when it has none."""
    ev = getattr(trace, "evidence", None)
    if ev is not None:
        return ev
    if trace.source in SIMULATOR_SOURCES:
        return simulator_evidence(trace)
    n = len(trace.frames)
    objs = {o.id: ObjectEvidence(object_id=o.id, identity="assumed_from_label",
                                 identity_note="legacy video trace: identity taken from tracking labels",
                                 position=uniform("inferred", n, basis=LEGACY_POS_BASIS),
                                 tilt=uniform("unobserved", n, basis=LEGACY_TILT_BASIS))
            for o in trace.scene.objects}
    return TraceEvidence(
        kind="legacy_video_unannotated", objects=objs,
        gripper_position=uniform("inferred", n, basis=LEGACY_POS_BASIS),
        gripper_width=uniform("inferred", n, basis="not observed in video: closed while an object is carried"),
        events_basis="inferred from object motion in video",
        review=[ReviewItem(code="legacy_unannotated", severity="review",
                           message=("This video-derived trace predates observability annotations: its tilt values "
                                    "are not evidence and its positions may include held or filled values."))])


def abstains(ev: TraceEvidence) -> list[ReviewItem]:
    return [r for r in ev.review if r.severity == "abstain"]


# ------------------------------------------------------------- user text ---

# One plain description per kind, for interfaces and reports (the web track owns the UI text).
ABOUT_WORDING: dict[str, str] = {
    "simulator_ground_truth": ("Recorded simulator trace: gripper and object poses logged by the physics "
                               "simulator. Not video, and not a person."),
    "rendered_video_reconstruction": ("Simulator-rendered video reconstructed from pixels: the video was rendered "
                                      "from a simulated demonstration, and the motion was rebuilt from the video "
                                      "frames alone. Not footage of a person."),
    "camera_video_reconstruction": ("Real camera footage reconstructed from pixels, with a calibrated camera and "
                                    "object identities confirmed by a person."),
    "unverified_video_reconstruction": ("Video reconstructed with assumed camera calibration; positions, sizes and "
                                        "tilts are unverified."),
    "legacy_video_unannotated": ("Video reconstruction recorded before observability annotations; tilt values "
                                 "in it are not evidence."),
    "interface_fixture": "Interface fixture: key-framed motion for development, not a physics simulation.",
}

MODEL_ORIGIN_WORDING: dict[str, str] = {
    "fresh_model_call": "fresh model call",
    "recorded_replay": "recorded model output (replayed, no network)",
    "offline_rules": "offline rules (deterministic, no model)",
    "oracle_ground_truth": "simulator ground truth (oracle, development only)",
    "unknown": "origin not recorded",
}
