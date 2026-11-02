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
cost or rejection reason.

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

## Checking

`evaluate.check` is a pure function of the step log, the specification and
the scene. Because logs are kept, any recorded rollout can be re-checked
against a later specification version; violations of rules that version did
not enforce are reported separately as `advisory_violations`.

## Evaluation layers

Three parts of the pipeline are evaluated separately, then composed:

| Layer | Input → output | Where it is measured |
|---|---|---|
| Perception | demonstration video → `DemoTrace` | Video and robustness registrations (rendered simulator video only) |
| Authoring | demonstration summary + human corrections → validated `TaskSpec` versions | Correction-compiler benchmark (single sentences, one scene); protocol v3 (prepared, not run) |
| Execution | fixed `TaskSpec` + new layout → plan, physics, checker verdict | First and second registrations (one fixed specification pair on new layouts) |

The pre-registered evaluations exercise only the execution layer with a fixed
specification pair. Protocol v3 (`mirror/evaluate/protocol_v3/`,
[protocols/v3_authoring.md](protocols/v3_authoring.md)) measures authoring and
runs each authored specification through the same planner, simulator and
checker, so the composition of authoring and execution is measured on the same
cases. Its modules are new and do not import or alter the frozen evaluation
runners.
