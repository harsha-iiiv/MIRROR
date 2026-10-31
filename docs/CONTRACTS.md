# Module contracts

Data types live in `mirror/contracts.py`, the dense step log in
`mirror/statelog.py`, and shared dimensions in `mirror/geometry.py`. This page
fixes the public functions each package exposes so the packages can be built
and tested independently and then composed by `mirror/pipeline.py`.

Units, frames and quaternion order are defined at the top of
`mirror/contracts.py` and apply everywhere.

## The reference world

* Table top at z = 0. Movable kit: one **cup** (open top, eight beads inside),
  one **block**, one **marker** (standing cylinder). One **tray** is the
  destination.
* A tall **wall** (0.50 m, taller than the gripper's 0.45 m reach) separates
  the start region (x < 0) from the destination region (x > 0). The wall may
  have a **slot** whose height (0.09 to 0.19 m) admits the cup only when it is
  carried on its side. Going around the end of the wall keeps the cup upright
  but is longer.
* The robot is a six-axis free-flying parallel-jaw gripper (no arm body). All
  motion is produced by position actuators and contact physics.
* Carrying the cup on its side through the slot spills the beads. That is a
  physical consequence, not a scripted effect, and it is what makes the
  "upright throughout transport" correction meaningful.

Reference scenes (from `mirror.scenes`):

| name | purpose |
|---|---|
| `demo` | demonstration layout; wall without a slot, the operator goes around it |
| `shortcut` | the "change the world" layout: the tray moves and the wall gains a slot, so the shortest route tilts the cup |
| `blocked` | the slot is the only connection between regions; carrying the cup upright is geometrically impossible |

## `mirror.scenes` (owner: simulation)

```python
reference_scene(name: str) -> SceneState            # "demo" | "shortcut" | "blocked"
sample_layout(seed: int, split: str) -> SceneState   # split in {"dev", "eval"}
EVAL_SEED_BASE = 10_000                              # eval seeds never used in development
```

Sampled layouts vary object positions, tray position, wall extent and side,
slot position and presence, and optional extra box obstacles. Every sampled
layout must have at least one collision-free route for every object under
the unconstrained specification (validated at sampling time) unless the split
is "blocked".

## `mirror.sim` (owner: simulation)

```python
build_model(scene: SceneState) -> tuple[mujoco.MjModel, SceneIndex]
class SimEnv:
    def __init__(self, scene: SceneState, *, seed: int = 0): ...
    def reset(self) -> None
    def visual_scene(self) -> VisualScene
    def execute(self, plan: PlanResult, *, frame_hz: float = 30.0,
                max_sim_time: float = 120.0) -> ExecutionResult
    # ExecutionResult: .log (StateLog, every physics step), .frames (list[RolloutFrame]),
    #                  .status ("completed" | "execution_failed" | "timeout"), .reason, .sim_time
record_scripted_demo(scene: SceneState, script: str = "reference") -> DemoTrace
class Teleop:  # incremental keyboard/joystick teleoperation for the studio
    def __init__(self, scene: SceneState): ...
    def command(self, dpos: Vec3, drot: Vec3, grip: float | None) -> None
    def advance(self, seconds: float) -> RolloutFrame
    def finish(self) -> DemoTrace
```

The executor tracks plan waypoints with bounded speed, closes the jaw on
grasp steps, opens it on release steps, and never sets object poses. Resets
between trials are allowed and are separate from execution.

## `mirror.interpret` (owner: interpretation)

```python
summarize_demo(trace: DemoTrace) -> DemoSummary        # deterministic features: per-object events, tilt stats, order
propose_spec(trace: DemoTrace, client: LLMClient) -> TaskSpec
compile_correction(spec: TaskSpec, text: str, client: LLMClient,
                   context: dict | None = None) -> tuple[TaskSpec, CorrectionRecord]
make_clarification(spec, scene, candidates: list[PlanResult]) -> Clarification
apply_answer(spec, clarification, answer: str, client) -> tuple[TaskSpec, CorrectionRecord]
validate_spec(spec: TaskSpec) -> list[str]             # empty list means valid
LLMClient.from_env()  # MIRROR_LLM_BASE_URL (default https://api.meta.ai/v1),
                      # MIRROR_LLM_MODEL (default muse-spark-1.3), MODEL_API_KEY;
                      # MIRROR_LLM_MODE = live | replay | record | offline
```

Model output is always validated against the schema before planning. The
model never emits actuator commands and never judges success.

## `mirror.plan` (owner: planning)

```python
ground(spec: TaskSpec, scene: SceneState) -> Grounding  # role -> object ids, destination tray id
plan(spec: TaskSpec, scene: SceneState, *, prefer: str | None = None) -> PlanResult
candidate_plans(spec, scene, k: int = 2) -> list[PlanResult]  # distinct behaviours, all valid under spec
```

Only `spec.enforced_constraints()` restrict planning. The same planner and
configuration are used for every specification version. A plan with no valid
route returns `status="no_plan"` with the rejected routes listed; an
infeasibility certificate is attached only when it is actually proven.

## `mirror.evaluate` (owner: evaluation)

```python
check(log: StateLog, spec: TaskSpec, scene: SceneState) -> RolloutReport
run_trial(scene, spec, *, condition: str) -> Rollout          # plan -> execute -> check
paired_eval(spec_a, spec_b, seeds: list[int], split="eval") -> EvalReport
write_report(report: EvalReport, out_dir: Path) -> None       # JSON + Markdown + figures
```

The checker reads only the step log and the scene, never model output.

## `mirror.studio` (owner: studio)

FastAPI app at `mirror.studio.app:app`, static frontend in `web/`, and
`scripts/export_static.py` that writes a self-contained static site of
precomputed sessions.

## `mirror.pipeline` (owner: integration)

`Session` composes the packages for the studio and the CLI: load or record a
demo, interpret, confirm, plan, execute, change the scene, correct,
re-plan, and run the paired evaluation.

## Conventions fixed during integration

**Status semantics.** Everything the interpreter infers starts as `proposed`;
only a human statement (review, correction, clarification answer) makes it
`confirmed` or `rejected`. Constraints are enforced only when `confirmed`
(`TaskSpec.enforced_constraints()`). Goals are pursued unless `rejected`
(`TaskSpec.active_goals()`), so a fresh proposal is plannable.

**Identifiers** are assigned by code, never by the model: goals
`g_inside_<kind>`, constraints `c_<type>_<phase>_<kind|all>` (for example
`c_upright_transport_cup`), ordering `o_<a>_before_<b>`, questions
`u_<related id>`.

**Types defined outside contracts.py.** `SceneIndex`, `ExecutionResult`
(`mirror.sim`); `DemoSummary`, `Clarification` (`mirror.interpret.types`);
`EvalReport`, `FreezeManifest`, `PairedTrial`, `TrialRecord`
(`mirror.evaluate.models`); `Grounding`, `PlannerConfig` (`mirror.plan`).

**Asking instead of guessing.** `compile_correction` and `apply_answer` raise
`NeedsClarification` (unclear referent, object not in the scene,
contradiction, unclear answer) or `CorrectionRejected` (unsupported or
redundant request). The specification is unchanged in both cases. Pass
`context={"scene": scene, "focus_kind": kind}`.

**Robot wrist.** R = Rz(yaw) · Rx(roll) · Ry(pitch); pitch is about the jaw's
closing axis (gripper local y). Limits: yaw ±270°, roll ±80°, pitch ±135°.
Pitch +90° lays a top-grasped cup on its side with its opening toward +x.

**Executor.** Trapezoidal velocity per waypoint segment (linear cap
min(step.max_speed, 0.40 m/s), 1.0 m/s²; angular 1.2 rad/s, 4 rad/s²), stopping
at every waypoint. A grasp step closes the jaw (0.10 m/s, 12 N per finger)
until it stops and then requires the named object to touch both fingers. A
release step opens to `gripper_width` (default fully open). Physics timestep
0.004 s.

**Phase labels** in `StateLog.phase` and `RolloutFrame.phase`:
`<kind>:<object_id>`, then `settle` after the last step (until every object is
below 0.01 m/s for 0.3 s, at most 3 s). An object is `held` only while it
touches both finger geoms.

**Execution failures.** `execution_failed` for a failed grasp, a drop (no
two-finger hold for 0.15 s between grasp and release), sustained tracking
error (> 20 mm or 0.25 rad for 0.3 s; the reason names the obstacle in
contact), a waypoint outside the workspace (never clamped) or an unreachable
orientation; `timeout` past `max_sim_time`.

**Contacts.** One record per (step, object, other) with the maximum depth.
`object_id` is a movable object id or `gripper`; `other` is an object id,
`table`, `floor`, `tray:<id>`, `obstacle:<id>` or `gripper`. A floor plane 0.75 m below the table catches anything that falls off. Object-object contacts are
recorded in both directions; bead contacts are not recorded.

**Scenes.** Ids `ref_demo`, `ref_shortcut`, `ref_blocked`, `<split>_<seed>`.
`sample_layout` accepts `dev`, `eval` and `blocked`; dev seeds are below
`EVAL_SEED_BASE` and eval seeds at or above it (enforced). `validate_layout`
and `route_exists` are exported. Every evaluation-split layout generated at a
seed at or above `EVAL_SEED_BASE` is appended to `results/eval/ledger.jsonl`
(redirect with `MIRROR_EVAL_LEDGER`).

**Route labels** are carried in transport step notes as `route=<label>; ...`,
for example `route=through_slot:wall_0:0:pitched+90` or `route=around:wall_0:+y`.
