"""Case files for protocol v3.

A case is one authoring episode: a scene, a base specification and one or
more turns. Each turn is what an author says (typed, or spoken by a real or
synthetic speaker), the adjudicated gold result of that turn, and, separately,
the structured edit an independent editor wrote for the manual baseline.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

Category = Literal["positive", "negative", "ambiguous", "unsupported", "infeasible"]
Outcome = Literal["apply", "asks", "rejects"]
Expect = Literal["compliant_completion", "proven_infeasible", "no_plan_ok", "not_executed"]
KINDS = ("cup", "block", "marker")


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuleEdit(_M):
    """One rule in the structured editor: type, object (or "all"), phase, params."""
    type: Literal["upright", "forbidden_contact", "keep_clearance"]
    object: Literal["cup", "block", "marker", "all"]
    phase: Literal["transport", "final", "always"]
    max_tilt_deg: Optional[float] = None
    with_groups: Optional[list[Literal["obstacle", "table_outside_tray", "other_objects"]]] = None
    min_m: Optional[float] = None
    status: Literal["confirmed", "rejected"] = "confirmed"

    @model_validator(mode="after")
    def _params(self):
        need = {"upright": "max_tilt_deg", "forbidden_contact": "with_groups", "keep_clearance": "min_m"}[self.type]
        if self.status == "confirmed" and getattr(self, need) is None and self.type != "upright":
            raise ValueError(f"{self.type} rule needs {need}")
        return self


class OrderingEdit(_M):
    before: Literal["cup", "block", "marker"]
    after: Literal["cup", "block", "marker"]
    status: Literal["confirmed", "rejected"] = "confirmed"


class GoalEdit(_M):
    object: Literal["cup", "block", "marker"]
    status: Literal["confirmed", "rejected"]


class StructuredEdit(_M):
    """What a person enters in a structured specification editor."""
    rules: list[RuleEdit] = Field(default_factory=list)
    orderings: list[OrderingEdit] = Field(default_factory=list)
    goals: list[GoalEdit] = Field(default_factory=list)

    def empty(self) -> bool:
        return not (self.rules or self.orderings or self.goals)


class Speech(_M):
    audio: str                                   # path relative to the case file, or absolute
    speaker: Literal["real", "synthetic"]        # real people are never mixed with synthetic voices
    speaker_id: str                              # pseudonymous (S01) or engine + voice for synthetic
    consent_ref: Optional[str] = None            # required for real speakers

    @model_validator(mode="after")
    def _consent(self):
        if self.speaker == "real" and not self.consent_ref:
            raise ValueError("a real speaker needs a consent reference")
        return self


class Gold(_M):
    outcome: Outcome
    edit: Optional[StructuredEdit] = None        # for "apply": the adjudicated change
    # Other edits the adjudicators accept as meaning the same thing (scored as correct,
    # reported separately as "acceptable alternative").
    alternatives: list[StructuredEdit] = Field(default_factory=list)
    adjudicators: list[str] = Field(default_factory=list)    # pseudonymous ids; empty for team fixtures

    @model_validator(mode="after")
    def _edit(self):
        if self.outcome == "apply" and (self.edit is None or self.edit.empty()):
            raise ValueError("gold outcome 'apply' needs a non-empty edit")
        if self.outcome != "apply" and self.edit is not None and not self.edit.empty():
            raise ValueError("gold outcome 'asks'/'rejects' must not change the specification")
        return self


class ManualEdit(_M):
    """The manual baseline for one turn, written by an editor who is not the
    author and did not see the gold. ``edit`` None means the editor declined
    to change the specification (for example, judged the request unsupported)."""
    editor_id: str
    edit: Optional[StructuredEdit] = None
    seconds: Optional[float] = None              # editor's own measured time, if recorded


class Turn(_M):
    text: str
    kind: Literal["correction", "clarification_answer"] = "correction"
    speech: Optional[Speech] = None
    gold: Gold
    manual: Optional[ManualEdit] = None


class SceneRef(_M):
    kind: Literal["reference", "layout"]
    name: Optional[Literal["demo", "shortcut", "blocked"]] = None     # reference scenes
    split: Optional[Literal["dev", "eval", "blocked"]] = None         # sampled layouts
    family: Literal["mixed", "shortcut"] = "mixed"
    seed: Optional[int] = None

    @model_validator(mode="after")
    def _shape(self):
        if self.kind == "reference" and not self.name:
            raise ValueError("reference scene needs a name")
        if self.kind == "layout" and (self.split is None or self.seed is None):
            raise ValueError("layout scene needs split and seed")
        return self

    def key(self) -> str:
        return f"ref_{self.name}" if self.kind == "reference" else f"{self.split}_{self.family}_{self.seed}"


Base = Literal["reference_reviewed", "reference_unreviewed", "cup_transport_confirmed",
               "block_strict", "ordering_confirmed", "all_transport_upright"]
Track = Literal["authoring", "language"]


class Case(_M):
    id: str
    category: Category
    author_id: str
    track: Track = "authoring"                   # language: authoring-only, scored semantically
    scope_tags: list[str] = Field(default_factory=list)
    scene: SceneRef
    base: Base = "reference_reviewed"
    turns: list[Turn]
    expect_execution: Expect = "compliant_completion"
    notes: str = ""

    @model_validator(mode="after")
    def _turns(self):
        if not self.turns:
            raise ValueError("a case needs at least one turn")
        if self.track == "language" and self.expect_execution != "not_executed":
            raise ValueError("language-understanding cases are scored without execution")
        return self


class CaseFile(_M):
    protocol: Literal["mirror-authoring-v3"]
    split: Literal["dev", "holdout"]
    synthetic_or_team_written: bool              # True for development cases written by the project team
    description: str = ""
    cases: list[Case]

    @model_validator(mode="after")
    def _unique(self):
        ids = [c.id for c in self.cases]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"duplicate case ids: {dup}")
        return self
