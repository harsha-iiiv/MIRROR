"""Shared data contracts for MIRROR.

Every subsystem (simulation, scenes, interpretation, planning, evaluation,
studio) exchanges data only through the models in this module. They are the
integration boundary: change them deliberately, bump CONTRACT_VERSION, and
update docs/CONTRACTS.md in the same change.

Conventions (apply everywhere):
  * SI units: metres, kilograms, seconds. Angles in radians unless a field
    name ends in ``_deg``.
  * World frame: right-handed, +z up. The table top surface is the plane
    z = 0. Gravity is -9.81 m/s^2 along z.
  * Quaternions are (w, x, y, z), the MuJoCo convention, unit norm.
  * "Tilt" of an object is the angle between the object's local +z axis and
    world +z, in degrees, in [0, 180].
  * Object identity: instance ids (``cup_0``) are scene-specific. Task
    specifications refer to objects through selectors (kind and/or role), so
    a specification transfers to new layouts with different instances.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "1.0"

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]  # (w, x, y, z)

IDENTITY_QUAT: Quat = (1.0, 0.0, 0.0, 0.0)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


class Pose(_Model):
    pos: Vec3
    quat: Quat = IDENTITY_QUAT


# --------------------------------------------------------------------------
# Scene description (input to the simulator, the planner and the checker)
# --------------------------------------------------------------------------

ObjectKind = Literal["cup", "block", "marker"]


class ObjectSpec(_Model):
    """A movable rigid object on the table.

    Geometry by kind (``size`` keys):
      cup:    outer_half=(hx, hy, hz), wall (thickness). Open top. Its local +z
              points out of the opening. ``beads`` small spheres start inside.
      block:  half=(hx, hy, hz)
      marker: radius, half_length (a capped cylinder standing on its end;
              local +z along its axis)
    ``pose`` is the pose of the object's geometric centre.
    """

    id: str
    kind: ObjectKind
    size: dict[str, Any]
    mass: float
    pose: Pose
    beads: int = 0
    bead_radius: float = 0.0075
    rgba: Optional[tuple[float, float, float, float]] = None


class TraySpec(_Model):
    """Open box that acts as a packing destination. ``pose`` is the centre of
    the tray floor's top surface. ``inner_half`` = (hx, hy) of the usable floor."""

    id: str
    pose: Pose
    inner_half: tuple[float, float]
    wall_height: float = 0.03
    wall_thickness: float = 0.008


class SlotSpec(_Model):
    """A rectangular through-opening in a wall, in the wall's local frame:
    y_min..y_max along the wall, z_min..z_max above the table."""

    y_min: float
    y_max: float
    z_min: float
    z_max: float


class ObstacleSpec(_Model):
    """Static obstacle.

    kind == "box": ``half`` = (hx, hy, hz), ``pose`` is the box centre.
    kind == "wall": an axis-aligned vertical wall whose local frame has x along
      the wall normal and y along its length. ``pose`` is the centre of the
      wall's footprint on the table (z = 0). Fields: ``length`` (along local
      y), ``height``, ``thickness`` (along local x), and optional ``slots``
      cut through it.
    """

    id: str
    kind: Literal["box", "wall"]
    pose: Pose
    half: Optional[Vec3] = None
    length: Optional[float] = None
    height: Optional[float] = None
    thickness: Optional[float] = None
    slots: list[SlotSpec] = Field(default_factory=list)


class Workspace(_Model):
    """Reachable box for the gripper frame origin (the robot's joint limits)."""

    x_range: tuple[float, float]
    y_range: tuple[float, float]
    z_range: tuple[float, float]


class SceneState(_Model):
    scene_id: str
    seed: Optional[int] = None
    family: str = "kit_packing"
    split: Optional[Literal["demo", "dev", "eval", "blocked", "custom"]] = None
    objects: list[ObjectSpec]
    trays: list[TraySpec]
    obstacles: list[ObstacleSpec] = Field(default_factory=list)
    workspace: Workspace
    gripper_start: Pose
    notes: str = ""


# --------------------------------------------------------------------------
# Demonstration
# --------------------------------------------------------------------------


class TraceFrame(_Model):
    t: float
    gripper: Pose
    gripper_width: float  # metres between finger pads
    objects: dict[str, Pose]  # object id -> pose


class TraceEvent(_Model):
    t: float
    type: Literal["grasp", "release", "contact", "note"]
    object_id: Optional[str] = None
    detail: dict[str, Any] = Field(default_factory=dict)


class DemoTrace(_Model):
    demo_id: str
    scene: SceneState
    source: Literal["teleop_keyboard", "teleop_scripted", "replay", "video_grounded"]
    source_note: str = ""
    sample_hz: float
    frames: list[TraceFrame]
    events: list[TraceEvent] = Field(default_factory=list)
    video_path: Optional[str] = None


# --------------------------------------------------------------------------
# Task specification
# --------------------------------------------------------------------------


class ObjectSelector(_Model):
    """Matches scene objects. All given fields must match. ``ids`` is only for
    scene-bound use (tests, debugging); specifications meant to transfer use
    kind/role."""

    kind: Optional[ObjectKind] = None
    role: Optional[str] = None
    ids: Optional[list[str]] = None


class ObjectBinding(_Model):
    """A role the task cares about, e.g. role="container", kind="cup"."""

    role: str
    kind: ObjectKind
    required: bool = True
    count: int = 1


class Status(str, Enum):
    proposed = "proposed"  # inferred, awaiting human decision
    confirmed = "confirmed"  # human confirmed; enforced
    rejected = "rejected"  # human rejected; not enforced


class Provenance(_Model):
    source: Literal["demo_inference", "user_correction", "clarification", "default", "manual"]
    evidence: str = ""
    version_introduced: int = 1
    model: Optional[str] = None


class Goal(_Model):
    id: str
    type: Literal["inside"]  # object rests inside the destination tray
    object: ObjectSelector
    target: str = "destination"  # tray role; bound to a tray at grounding time
    status: Status = Status.confirmed
    provenance: Provenance


class OrderingConstraint(_Model):
    id: str
    before: ObjectSelector
    after: ObjectSelector
    status: Status = Status.proposed
    provenance: Provenance


Phase = Literal["final", "transport", "always"]


class Constraint(_Model):
    """An executable rule.

    type == "upright": tilt(object) <= params["max_tilt_deg"] during ``phase``.
      phase "final": after release, once settled.
      phase "transport": from grasp until release of that object.
      phase "always": every recorded step of the rollout.
    type == "forbidden_contact": object(s) matching ``applies_to`` must not
      touch ``params["with"]`` (list of geom group names: "obstacle",
      "table_outside_tray", "other_objects") during ``phase``.
    type == "keep_clearance": distance from applies_to to obstacles stays
      >= params["min_m"] during phase.
    """

    id: str
    type: Literal["upright", "forbidden_contact", "keep_clearance"]
    applies_to: ObjectSelector
    phase: Phase
    params: dict[str, Any] = Field(default_factory=dict)
    status: Status = Status.proposed
    provenance: Provenance
    description: str = ""


class Uncertainty(_Model):
    """An open question the interpreter could not settle from the demo."""

    id: str
    question: str
    options: list[str] = Field(default_factory=list)
    related_constraint: Optional[str] = None  # constraint id it would confirm/reject
    status: Literal["open", "resolved"] = "open"
    resolution: Optional[str] = None


class CorrectionRecord(_Model):
    version: int  # the spec version this correction produced
    text: str
    kind: Literal["correction", "clarification_answer", "confirmation"]
    diff: list["SpecOp"] = Field(default_factory=list)
    model: Optional[str] = None


class SpecOp(_Model):
    op: Literal["add_constraint", "modify_constraint", "remove_constraint",
                "set_status", "add_goal", "remove_goal", "add_ordering",
                "remove_ordering", "resolve_uncertainty"]
    target_id: Optional[str] = None
    value: Optional[dict[str, Any]] = None
    rationale: str = ""


class TaskSpec(_Model):
    spec_id: str
    version: int = 1
    parent_version: Optional[int] = None
    task_family: Literal["kit_packing"] = "kit_packing"
    summary: str = ""
    bindings: list[ObjectBinding]
    goals: list[Goal]
    ordering: list[OrderingConstraint] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    uncertainties: list[Uncertainty] = Field(default_factory=list)
    tolerances: dict[str, float] = Field(default_factory=lambda: {
        "settle_speed_mps": 0.01, "inside_margin_m": 0.0})
    demo_id: Optional[str] = None
    history: list[CorrectionRecord] = Field(default_factory=list)
    interpreter: Optional[str] = None  # model id or "offline-rules"

    def enforced_constraints(self) -> list[Constraint]:
        return [c for c in self.constraints if c.status == Status.confirmed]


CorrectionRecord.model_rebuild()


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


class PlanStep(_Model):
    kind: Literal["move", "approach", "grasp", "lift", "transport", "place",
                  "release", "retreat"]
    object_id: Optional[str] = None
    waypoints: list[Pose] = Field(default_factory=list)  # gripper-frame targets
    gripper_width: Optional[float] = None  # command for grasp/release steps
    max_speed: float = 0.25  # m/s cap for this segment
    note: str = ""


class RouteOption(_Model):
    label: str  # e.g. "through_slot", "around_+y", "direct"
    object_id: Optional[str] = None
    feasible: bool
    cost: Optional[float] = None
    rejected_reason: Optional[str] = None  # e.g. "violates c_upright_transport"


class PlanResult(_Model):
    status: Literal["ok", "no_plan", "invalid_spec", "error"]
    reason: str = ""
    spec_version: Optional[int] = None
    scene_id: Optional[str] = None
    steps: list[PlanStep] = Field(default_factory=list)
    object_order: list[str] = Field(default_factory=list)
    routes: list[RouteOption] = Field(default_factory=list)
    planning_time_s: float = 0.0
    planner: str = ""
    infeasibility: Optional[dict[str, Any]] = None  # certificate if proven, else heuristic note


# --------------------------------------------------------------------------
# Rollouts and evaluation
# --------------------------------------------------------------------------


class VisualGeom(_Model):
    type: Literal["box", "sphere", "cylinder", "capsule", "plane", "mesh"]
    size: tuple[float, ...]  # MuJoCo geom size semantics
    pos: Vec3  # in body frame
    quat: Quat
    rgba: tuple[float, float, float, float]
    name: Optional[str] = None


class VisualBody(_Model):
    name: str
    static: bool
    geoms: list[VisualGeom]


class VisualScene(_Model):
    """Renderer-agnostic description of every body for playback viewers."""

    bodies: list[VisualBody]


class RolloutFrame(_Model):
    t: float
    bodies: dict[str, Pose]  # non-static body name -> world pose
    gripper_width: float
    phase: str = ""  # e.g. "transport:cup_0"


class ViolationInterval(_Model):
    constraint_id: str
    object_id: str
    t_start: float
    t_end: float
    peak: float  # peak magnitude (degrees for upright, metres for clearance)
    detail: str = ""


class ObjectOutcome(_Model):
    object_id: str
    in_target: bool
    released: bool
    settled: bool
    final_tilt_deg: float
    max_transport_tilt_deg: float
    beads_total: int = 0
    beads_in_container: int = 0  # at end of rollout


class RolloutReport(_Model):
    rollout_id: str
    scene_id: str
    spec_id: str
    spec_version: int
    condition: str = ""  # e.g. "original" / "corrected"
    status: Literal["success", "task_incomplete", "constraint_violated",
                    "planning_failed", "execution_failed", "timeout"]
    plan_status: str = ""
    plan_reason: str = ""
    route_labels: list[str] = Field(default_factory=list)
    outcomes: list[ObjectOutcome] = Field(default_factory=list)
    violations: list[ViolationInterval] = Field(default_factory=list)
    forbidden_contacts: list[dict[str, Any]] = Field(default_factory=list)
    completion: bool = False
    planning_time_s: float = 0.0
    execution_time_s: float = 0.0  # simulated seconds
    wall_time_s: float = 0.0
    physics_steps: int = 0
    checker: str = ""
    ground_truth_use: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class Rollout(_Model):
    """Report plus playback data (frames downsampled for viewing; the checker
    evaluates every physics step online, not these frames)."""

    report: RolloutReport
    visual: Optional[VisualScene] = None
    frame_hz: float = 30.0
    frames: list[RolloutFrame] = Field(default_factory=list)
