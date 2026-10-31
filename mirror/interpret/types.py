"""Types owned by the interpretation package.

``DemoSummary`` and ``Clarification`` are referenced by docs/CONTRACTS.md but
are produced and consumed only through ``mirror.interpret``; they live here so
the shared contract module stays small.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from mirror.contracts import ObjectKind, Pose, SpecOp


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# Demonstration summary (deterministic, no model involved)
# --------------------------------------------------------------------------


class ObjectSummary(_Model):
    object_id: str
    kind: ObjectKind
    moved: bool
    grasp_times: list[float] = Field(default_factory=list)
    release_times: list[float] = Field(default_factory=list)
    start_pose: Pose
    end_pose: Pose
    start_tilt_deg: float
    end_tilt_deg: float
    ended_in_tray: Optional[str] = None
    carry_duration_s: float = 0.0
    carry_max_tilt_deg: Optional[float] = None
    carry_mean_tilt_deg: Optional[float] = None
    carry_path_length_m: float = 0.0
    carry_max_height_m: Optional[float] = None
    min_obstacle_distance_m: Optional[float] = None
    nearest_obstacle: Optional[str] = None
    wall_crossings: list[str] = Field(default_factory=list)  # e.g. "wall_0:around_+y"


class Regularity(_Model):
    """Something the demonstration shows consistently. Whether it is a
    requirement or incidental is exactly what interpretation must decide."""

    id: str
    kind: Literal["carry_upright", "final_upright", "order", "inside", "route",
                  "clearance", "carry_tilted"]
    object_kind: Optional[ObjectKind] = None
    statement: str
    value: Optional[float] = None


class DemoSummary(_Model):
    demo_id: str
    source: str
    duration_s: float
    event_source: Literal["events", "motion"]
    order: list[str]  # object ids in the order they were first grasped
    objects: list[ObjectSummary]
    trays: list[str]
    obstacles: list[dict[str, Any]]
    regularities: list[Regularity]
    notes: list[str] = Field(default_factory=list)

    def object(self, object_id: str) -> ObjectSummary:
        for o in self.objects:
            if o.object_id == object_id:
                return o
        raise KeyError(object_id)


# --------------------------------------------------------------------------
# Active clarification
# --------------------------------------------------------------------------


class CandidateDigest(_Model):
    index: int
    label: str  # "A", "B", ...
    object_order: list[str]
    routes: list[str]
    planned_max_tilt_deg: dict[str, float]  # object id -> max tilt while carried (planned)
    gripper_path_length_m: float
    plan_status: str


class Difference(_Model):
    feature: Literal["carry_tilt", "order", "route"]
    object_kind: Optional[ObjectKind] = None
    object_id: Optional[str] = None
    values: list[Any]  # one per candidate, same order as candidates
    statement: str


class Clarification(_Model):
    clarification_id: str
    spec_id: str
    spec_version: int
    scene_id: str
    question: str
    candidates: list[CandidateDigest]
    differences: list[Difference]
    options: list[str]
    if_yes: list[SpecOp] = Field(default_factory=list)  # both count
    if_no: list[SpecOp] = Field(default_factory=list)   # only the demo-like one counts
    note: str = ""


# --------------------------------------------------------------------------
# Outcomes that are not a new specification version
# --------------------------------------------------------------------------


class InterpretError(ValueError):
    """Model or rule output could not be turned into a valid specification."""


class CorrectionRejected(InterpretError):
    """The text does not describe a supported change to the task."""


class NeedsClarification(InterpretError):
    """The input is ambiguous; ask the human instead of guessing."""

    def __init__(self, question: str, options: Optional[list[str]] = None,
                 reason: str = "", interpretation: str = ""):
        super().__init__(question)
        self.question = question
        self.options = list(options or [])
        self.reason = reason
        self.interpretation = interpretation

    def as_dict(self) -> dict[str, Any]:
        return {"question": self.question, "options": self.options,
                "reason": self.reason, "interpretation": self.interpretation}
