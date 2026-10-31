"""Evaluation report types (local to the evaluation package)."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from mirror.contracts import RolloutReport, ViolationInterval


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FreezeManifest(_M):
    created_at: str                      # ISO-8601 UTC
    created_unix: float
    split: str
    seeds: list[int]
    spec_a: dict[str, Any]               # spec_id, version, sha256
    spec_b: dict[str, Any]
    code_sha256: str                     # over mirror/**/*.py
    code_files: int
    code_exclude: list[str] = Field(default_factory=list)   # presentation-only paths left out of the hash
    config: dict[str, Any] = Field(default_factory=dict)
    kind: str = "paired"                 # "paired" | "final" | "rehearsal"
    family: str = "mixed"                # single-stratum manifests
    strata: list[dict[str, Any]] = Field(default_factory=list)   # name, role, split, family, seeds
    spec_provenance: dict[str, Any] = Field(default_factory=dict)
    file_hashes: dict[str, Any] = Field(default_factory=dict)    # group -> {sha256, files}
    versions: dict[str, str] = Field(default_factory=dict)       # python, mujoco, numpy, ...
    ledger_at_freeze: dict[str, Any] = Field(default_factory=dict)


class TrialRecord(_M):
    condition: str
    report: RolloutReport
    violation_free_success: bool         # completion and no violation of the corrected rules
    corrected_violations: list[ViolationInterval] = Field(default_factory=list)
    corrected_max_tilt_deg: float = 0.0  # over objects under the corrected rules, in their phase
    corrected_over_tol_s: float = 0.0
    beads_total: int = 0
    beads_lost: int = 0
    forbidden_contacts: int = 0
    obstacle_contacts: list[dict[str, Any]] = Field(default_factory=list)  # any body touching an obstacle, spec-independent
    failure_class: str = ""              # for planning failures
    feasibility: str = ""                # proven_infeasible | feasible_witness | unknown
    scope_objects_tilted: dict[str, float] = Field(default_factory=dict)  # unrelated object -> max transport tilt
    interventions: int = 0
    tilt_trace: dict[str, list[list[float]]] = Field(default_factory=dict)  # object -> [[t, tilt], ...]
    log_path: Optional[str] = None
    rollout_path: Optional[str] = None


class PairedTrial(_M):
    seed: int
    scene_id: str
    scene_sha256: str
    a: TrialRecord
    b: TrialRecord


class ConditionMetrics(_M):
    condition: str
    n: int
    completion: int
    violation_free_success: int
    corrected_rule_violations: int
    executed: int                        # trials that reached the simulator
    max_tilt_deg_mean: Optional[float]   # None when no trial was executed
    max_tilt_deg_max: Optional[float]
    over_tol_s_mean: Optional[float]
    beads_lost_total: int
    beads_lost_mean: Optional[float]
    forbidden_contact_trials: int
    obstacle_contact_trials: int
    status_counts: dict[str, int]
    planning_failures: int
    execution_failures: int
    timeouts: int
    planning_time_s_mean: float
    execution_time_s_mean: Optional[float]
    interventions: int
    feasible_scenes_wrongly_rejected: int
    feasible_scenes_known: int


class EvalReport(_M):
    title: str = "MIRROR paired evaluation"
    split: str
    manifest: FreezeManifest
    corrected_rules: list[dict[str, Any]]
    scope_objects: list[str]
    config: dict[str, Any]
    trials: list[PairedTrial]
    metrics: dict[str, ConditionMetrics]
    paired: dict[str, Any]
    scope: dict[str, Any]
    blocked: list[dict[str, Any]] = Field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    wall_time_s: float = 0.0
    notes: list[str] = Field(default_factory=list)
    simplifications: list[str] = Field(default_factory=list)


class FinalReport(_M):
    """Pre-registered evaluation: every stratum from one freeze manifest."""
    title: str = "MIRROR pre-registered evaluation"
    manifest: FreezeManifest
    spec_provenance: dict[str, Any] = Field(default_factory=dict)
    strata: dict[str, EvalReport]
    roles: dict[str, str] = Field(default_factory=dict)         # stratum name -> role
    blocked_reference: list[dict[str, Any]] = Field(default_factory=list)
    ledger: dict[str, Any] = Field(default_factory=dict)        # eval-layout ledger entries for frozen seeds
    rollout_selection: dict[str, Any] = Field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""
    wall_time_s: float = 0.0
    notes: list[str] = Field(default_factory=list)
