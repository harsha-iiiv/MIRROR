# Architecture

MIRROR is five packages joined by one set of data contracts
(`mirror/contracts.py`), one dense step log (`mirror/statelog.py`) and one
set of shared dimensions (`mirror/geometry.py`). `mirror/pipeline.py`
composes them into a `Session` used by the command line and the studio.

```text
DemoTrace ──► interpret.summarize_demo ──► DemoSummary (deterministic)
                     │
                     ▼
              interpret.propose_spec ──► TaskSpec v1 (everything "proposed")
                     │
     human review ───┤  confirm_items / compile_correction / apply_answer
                     ▼
              TaskSpec vN (validated, versioned, with history)
                     │
                     ▼
              plan.plan(spec, scene) ──► PlanResult (routes, steps, certificate)
                     │
                     ▼
              sim.SimEnv.execute ──► StateLog (every physics step) + frames
                     │
                     ▼
              evaluate.check(log, spec, scene) ──► RolloutReport
```

## Responsibilities and boundaries

| Package | Owns | Never does |
|---|---|---|
| `scenes` | Reference scenes and seeded layout sampling with geometric validity checks; the evaluation ledger | Look at outcomes |
| `sim` | MuJoCo model generation, the executor, teleoperation, rendering | Set object poses during execution; decide success |
| `interpret` | Demo summary, model client, proposal, correction compiler, clarification | Emit actuator commands; judge success |
| `plan` | Grounding, task order, grasps, placements, routes per orientation mode, certificates | Read model output beyond the validated spec |
| `evaluate` | Checker over the step log, trials, paired evaluation, freeze, reports | Read plans or model explanations to grade |
| `studio` | Interface over a `Session` | Change any verdict |

## Where simulator ground truth is used

Ground truth (exact object poses, contacts) is used to summarize the
demonstration, to plan, and to check. It is never presented as perception.
Every `RolloutReport` lists the ground-truth uses in `ground_truth_use`.

## The model's role

The model turns the demonstration summary into a proposed specification,
compiles natural-language corrections into scoped specification operations,
and phrases clarifications. Its output is validated against the schema and
against the measured demonstration (a proposal cannot claim a rule the
demonstration broke) before anything is planned. Identifiers and statuses
are assigned by code. The model never moves the robot and never grades a
rollout.

Model access goes through an OpenAI-compatible client. The default target is
Meta Model API (`muse-spark-1.3`). Modes: `live`, `record`, `replay`
(recorded responses keyed by a hash of model, messages and schema) and
`offline` (deterministic rules). Every specification version records which
interpreter produced it.

## Planning

For each object the planner enumerates candidate transport routes: direct,
around each wall end, and through each slot in every orientation mode that
fits (upright, pitched ±30°, ±45°, ±60°, ±90° about the jaw axis), plus a
sampling fallback for extra obstacles. Collision checks use a conservative
envelope of the gripper plus the held object. Confirmed upright rules remove
orientation modes whose tilt exceeds the limit, for the matching objects
only. The cheapest feasible route wins; every candidate is recorded with its
cost or rejection reason. Cost is path length plus a rotation price per radian
of gripper rotation (`PlannerConfig.rotation_penalty`, default 0.05 m/rad, used by
the first two registrations). That price decides how often a tilted slot route
beats an upright detour when no rule forbids tilting. Calibrated to execution
time on development layouts it is 0.2445 m/rad, and at that price the
uncorrected specification takes no tilted cup route on 120 new layouts; see
[Planner objective and baselines](EVALUATION.md#planner-objective-and-baselines-objective_v1).
A never-tilt baseline is a planner wrapper that adds upright-transport rules to a
planning copy of the specification.

When nothing is feasible the planner returns `no_plan`. It attaches a proof
(`proven: true`, `scope: any_motion`) only when a geometric bound shows that
no motion within the workspace can carry the object across the wall under
the enforced rules. A weaker record (`proven: false`, `scope: planner_model`)
covers only the planner's own way of crossing a slot and is labelled as not a
proof.

## Execution

The robot is a free-flying parallel-jaw gripper on three prismatic and three
revolute position-controlled joints, plus two finger joints. The executor
interpolates waypoints with trapezoidal velocity profiles and writes only
actuator commands. Grasps are friction grasps. Every physics step (0.004 s)
is logged: object and gripper poses, jaw width, which object both fingers
touch, the plan phase, bead positions and contacts.

### Arm backend (optional)

`mirror.arm` is a second execution backend, selected with `--backend arm` or
`mirror.backends.get_backend("arm")`. It takes the same plan and turns each TCP
segment into a validated joint path on a simulated UR10e or UR5e (inverse
kinematics, joint limits, discontinuities, arm-link and self collisions), then
executes it with joint servos. Grasp, drop and settle detection and the step log
are the baseline's, so the checker reads both backends the same way. When no
plan can be executed it reports `arm_motion_planning_failure`, which is never a
proof; a planner certificate passes through unchanged. A fixed-base arm cannot
pass the wall slot with the tool horizontal, so slot routes are always rejected
and the spill-then-correct story occurs only on the gripper baseline. Mount and
tool were tuned on development seeds 100-107, excluded from the development
report (`results/arm/v1`, which names its physics platform). Vendored models
and their BSD-3-Clause licences live in `mirror/assets/`. Every evaluation
result uses the gripper baseline.

## Checking

`evaluate.check` is a pure function of the step log, the specification and
the scene. Because logs are kept, any recorded rollout can be re-checked
against a later specification version; violations of rules that version did
not enforce are reported separately as `advisory_violations`. Ordering rules are
monitored from execution evidence (grasp start, placement completion) and
reported per rule in `report.rule_results`; a confirmed rule the evidence cannot
settle makes the status `unverified`, never success. Clearance is measured
against the solid shapes. Rules, phases and which component honours each are in
one support matrix (`mirror.rules_support`, [RULES.md](RULES.md)).

## Evidence and observability

Every `DemoTrace` can carry `evidence`: per frame, whether each object's position
and tilt (and the gripper) were measured, inferred, interpolated or unobserved;
the evidence kind (simulator ground truth, rendered video reconstructed from
pixels, calibrated camera footage, unverified video, legacy unannotated video,
interface fixture); camera calibration records; the origin of each model output
(fresh call, recorded replay, offline rules, oracle); and review items that make
the system ask instead of propose (`mirror/observability.py`). Unobserved values
are display placeholders, never evidence: unobserved tilt is unknown, not upright.

## Real-phone capture

`mirror/capture` is a phone capture path for the supported kitting task: a
printable ChArUco target with checked physical dimensions, per-phone lens
calibration and per-take table pose with metric scale from the measured print,
two-phone synchronisation from shared observable events on original timestamps
(clips that do not share them are refused), a single-view mode with reduced
observability, person-reviewed mapping of real objects to simulation proxies, and
object-centric pick and place intervals for human-hand demonstrations (inferred,
never labelled as robot contact). Tested on synthetic renders only; real-footage
validation awaits footage ([CAMERA_CAPTURE.md](CAMERA_CAPTURE.md),
[REAL_FOOTAGE.md](REAL_FOOTAGE.md)).

## Evaluation layers

Three parts of the pipeline are evaluated separately, then composed:

| Layer | Input → output | Where it is measured |
|---|---|---|
| Perception | demonstration video → `DemoTrace` | Video and robustness registrations (rendered simulator video only) |
| Authoring | demonstration summary + human corrections → validated `TaskSpec` versions | Correction-compiler benchmark (single sentences, one scene); protocol v3 (prepared, not run) |
| Execution | fixed `TaskSpec` + new layout → plan, physics, checker verdict | First and second registrations (one fixed specification pair on new layouts, rotation price 0.05 m/rad); objective_v1 (same pair, price swept and calibrated, never-tilt baseline) |

The layout evaluations exercise only the execution layer with a fixed
specification pair; the agreement check in `mirror/evaluate/specs.py` (replayed
model specification must equal the offline-rule specification) is a
deterministic regression check, so they say nothing about the model's
contribution. Protocol v3 (`mirror/evaluate/protocol_v3/`,
[protocols/v3_authoring.md](protocols/v3_authoring.md)) measures authoring and
runs each authored specification through the same planner, simulator and
checker, so the composition of authoring and execution is measured on the same
cases. Its modules are new and do not import or alter the frozen evaluation
runners.
