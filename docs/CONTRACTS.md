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
