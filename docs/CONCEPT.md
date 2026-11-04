# MIRROR
## Teach a simulated robot what you mean, then test its understanding

**Concept brief | 31 October 2026 | MuJoCo-only | Historical**

> This is the original proposal, kept for the record. The shipped headline is **corrections you can check** (see README). The quote below was retired: with the planner's rotation price calibrated to execution time, the cup correction changes nothing on layouts that have an upright route (docs/EVALUATION.md, objective_v1).

> Demonstrate a supported task. Inspect what the robot thinks matters. Change the environment. Correct one misunderstanding. Test whether that correction changes its behavior in new simulated worlds.

MIRROR is a robot task-authoring and testing studio, not a video-to-animation generator. Its central contribution is an inspectable loop from demonstration to task specification, clarification, physics-based execution and correction testing.

The memorable moment was proposed as: “I corrected it once, and the correction changed what it did somewhere else.” (Retired; see the note above.)

## 1. The problem

A demonstration shows what happened, but not everything that mattered.

If a person carries a container upright, is that essential or incidental? Must objects be packed in the demonstrated order, or can the robot choose another sequence? Is the destination a particular position or a relationship such as “inside this tray”?

Simply copying a trajectory preserves the demonstration's movements, not necessarily its purpose. A language model can also describe a plausible task without producing a program whose execution respects that task.

MIRROR makes those interpretations visible, lets the user resolve ambiguity, and turns corrections into explicit constraints that a planner and independent checker can consume.

## 2. The product and supported scope

The first task family is **packing a small kit of rigid objects into a tray**.

Use one simulated robot arm and gripper, a tabletop, a few known objects, a movable destination tray and obstacles. Include a container whose orientation matters. Reuse reliable motion-control infrastructure; the research claim is not learning low-level grasping from one example.

The product has five connected surfaces:

1. **Demonstration:** inspect a recorded or live demonstration.
2. **Task interpretation:** review objects, goals, required ordering, constraints and uncertain assumptions.
3. **Execution:** watch a robot attempt the task through MuJoCo physics.
4. **Correction:** edit an interpretation in natural language and inspect the resulting specification change.
5. **Testing:** compare original and corrected specifications across new layouts, including failures.

### Demonstration input

Choose and disclose the actual input mode:

- **Core build:** teleoperate the simulated gripper and record a structured demonstration trace with rendered video. This establishes task extraction from a simulated demonstration, not human-hand video understanding.
- **Optional visual extension:** record a person performing the analogous tabletop task and ground visible objects and events to known simulator assets. This requires a real visual grounding pipeline; it is not achieved by silently supplying simulator object identities to the model.

Do not advertise an input mode that the implementation does not support.

### Explicit non-goals

- Physical robot deployment or demonstrated simulation-to-reality transfer.
- General-purpose learning of arbitrary tasks from one video.
- Training a new foundation model or robot policy.
- Snap-fit assembly, deformable manipulation or broad contact-rich assembly.
- Success-looking animation detached from actual simulation state.

## 3. The demo: a confrontation, not a presentation

### “Watch me.”

Demonstrate packing the supported kit. MIRROR proposes an interpretation beside the demonstration: which objects belong in the tray, what relationships must hold, what order appears necessary and what remains uncertain.

The user confirms important requirements. Do not assume every action in the demonstration is mandatory.

### “Now your turn.”

The simulated robot executes the proposed task through the controller and MuJoCo physics. This establishes execution, but it is not the climax.

### “That could be a recording. Change the world.”

The judge rearranges objects, moves the tray and adds an obstacle while execution is stopped. MIRROR must replan from the changed scene rather than replay the demonstration's coordinates.

### “You misunderstood this.”

Use a physically validated scene where a shorter route permits tilting the container through a low-clearance passage, while keeping it upright requires a longer route around the obstacle. Confirm that the full robot and grasped object can actually execute both alternatives.

The original interpretation requires the container to be upright only when placed. The user corrects it:

> “This container must remain upright throughout transport, not just at the destination.”

Show three linked changes:

1. The task specification changes from a final-state condition to a transport-phase invariant.
2. The old trajectory is rejected by the checker under the new rule.
3. A new plan takes a visibly different route and is executed again.

A chatbot acknowledgment without changed execution does not satisfy this step.

### “Try somewhere you have not seen.”

Freeze the original and corrected specifications before generating the final evaluation layouts. Run both versions from identical initial states in each new layout. Report every attempt, not just the best rollout.

End with a blocked case. The robot should stop rather than violate the rule. Distinguish “the planner found no valid plan” from a mathematically proven impossibility.


## 4. The stronger differentiator: active clarification

MIRROR should not depend entirely on the user noticing a mistake.

It can propose two candidate behaviors that both satisfy the current specification and ask:

> “Would both of these still count as doing the task correctly?”

One candidate might carry the container upright; the other might tilt it and restore its orientation at the destination. The user's answer reveals the missing distinction.

This is clarification, not mind reading. A candidate cannot be certified as violating an unstated intention until the human establishes that intention. Once confirmed, the rule becomes an executable constraint and a regression test.

The differentiator is **inspectable intent, targeted clarification and tested correction transfer**, not merely controlling a robot through language.

## 5. System architecture

```text
Demonstration trace and/or supported visual observations
                         ↓
              Meta model interpretation
                         ↓
       Proposed TaskSpec with explicit uncertainty
                         ↓
            Human confirmation or correction
                         ↓
             Validated, versioned TaskSpec
                         ↓
         Task planner and motion planner/controller
                         ↓
              MuJoCo physics execution
                         ↓
       Independent trajectory and goal checking
                         ↓
          Evidence, failures and clarification
```

### Task specification

Represent the task independently of any one layout. Include:

- Stable object identities or roles, grounded to the current scene.
- Goal relationships, such as required objects being inside the selected tray.
- Required ordering only where confirmed.
- Phase-specific constraints, such as an orientation bound during transport.
- Forbidden contacts and permitted contacts, rather than treating every contact as a collision failure.
- User-visible tolerances, unresolved assumptions and provenance for each inferred or corrected rule.
- Version identifiers linking the specification to its demonstrations and corrections.

Do not encode the task solely as fixed coordinates or copied waypoints. Controllers and motion primitives may be predefined; task interpretation and correction must actually feed their planning constraints.

### Model responsibility

Use an accessible, event-provided Meta model for substantive interpretation, uncertainty identification and natural-language-to-constraint compilation. Validate the output against a supported schema before planning.

Verify the selected model's actual input support and API access. If it accepts images but not native video, use timestamped sampled frames and describe that accurately. Segmentation is optional and should be added only when it serves the visual grounding path.

The model must not directly emit unchecked actuator commands or be the sole judge of success. A polished narration around a hardcoded tilt toggle is not the intended contribution.

### Planning and execution

Use a task planner to select supported actions and a motion planner/controller to realize them under the confirmed constraints. Keep this machinery the same in the original and corrected conditions.

Execution must use simulated robot actuation and object dynamics. No object teleportation, forced final poses or per-layout scripted successes during a rollout. Resets between trials are allowed and must be separate from execution.

If a simplified grasp attachment is used instead of contact-based grasping, disclose it explicitly and narrow the claims accordingly.

### Independent evaluation

The checker reads simulator trajectories, not the model's explanation. Evaluate relevant conditions at each recorded physics step, with explicit tolerances, and store violation timing and magnitude.

Simulator ground truth may be used for planning, control and evaluation, but record exactly where it is used. Do not represent privileged object poses as model-derived visual perception. Finite simulation tests do not constitute formal verification or physical safety guarantees.

## 6. Components and interfaces

| Component | Responsibility | Main output |
|---|---|---|
| Integration | Shared interfaces, reproducibility and the working end-to-end path | Integrated runnable system |
| Simulation and control | Robot model, scenes, teleoperation, actuation, contacts and reset behavior | Reliable MuJoCo execution environment |
| Interpretation and compiler | Demonstration interpretation, uncertainty, clarification and scoped corrections | Validated, versioned task specifications |
| Planning | Task sequencing, collision-aware motion and constraint-aware replanning | Plans with explicit success/failure status |
| Evaluation | Paired trials, independent monitors, scope regressions and failure reports | Reproducible evidence bundle |
| Studio | Demonstration playback, live simulation, specification diffs and results | One coherent task-authoring interface |

Agree on shared contracts before independent implementation: **DemoTrace, SceneState, TaskSpec, PlanResult and RolloutReport**. Specify units, coordinate frames, identifiers, constraint semantics and failure states.

Build one thin end-to-end path early. Integrate continuously instead of producing disconnected subsystem demos. Deepen reliability and evaluation before expanding the task catalogue.


## 7. Evaluation that makes the claim credible

Use a development layout to expose the misunderstanding and obtain the correction. Then freeze both task versions and the planning/control code before sampling the final test layouts.

### Paired comparison

For every held-out initial scene, compare:

- **Original specification:** the goal condition without the transport-phase correction.
- **Corrected specification:** the same task with the confirmed, scoped transport constraint.

Use identical initial states and the same planner/controller configuration. Publish seeds and configuration, and report the number of trials. Do not tune the correction against the final evaluation set.

### Metrics

Report these separately rather than hiding them in one success score:

- Task completion: required kit objects correctly placed, released and settled.
- Corrected-constraint violations, including maximum container tilt and duration beyond tolerance.
- Forbidden-contact violations.
- Planning failures, execution failures and timeouts.
- Replanning time and execution time.
- Human interventions after the correction.
- Rejections of scenes that are actually feasible, where feasibility is independently established.

### Necessary test cases

1. New object and destination positions within the supported task family.
2. Obstacles that require a genuine route change.
3. Original and corrected versions evaluated from the same scene state.
4. An unrelated object that is still allowed to rotate, proving the correction is not globally overapplied.
5. Previously successful cases that remain successful after the correction.
6. A deliberately blocked case with an independently justified incompatibility, plus ordinary planner failures labeled as such.

A scene that the current planner cannot solve is not automatically an impossible scene. Preserve that distinction in both the results and the UI.

## 8. Studio design

Keep the interface focused on cause and effect:

- Main view: the actual MuJoCo rollout.
- Demonstration timeline: what the user showed.
- Interpretation panel: goals, constraints and unresolved questions.
- Correction diff: exactly which rule changed and why.
- Evidence view: paired rollouts, metrics and failures with replayable seeds.

The signature interaction is selecting a misunderstood behavior, correcting its meaning and seeing the resulting trajectory change. A grid of fresh evaluation worlds should make transfer visible without concealing unsuccessful runs.

Export the task specification, correction history, scene configuration, seeds and rollout reports so the result is inspectable rather than only watchable.

## 9. Acceptance criteria

The concept is demonstrated only if all of the following work:

- A supported demonstration produces a user-reviewable task specification.
- Changed geometry produces replanning, not coordinate replay.
- A natural-language correction produces a real, scoped specification change.
- The changed specification affects executed behavior, not just labels or narration.
- Independent checks detect the old behavior's violation of the corrected rule.
- Fresh paired trials reveal both improvements and remaining failures.
- A scope regression confirms that unrelated behavior is not unnecessarily constrained.
- Execution failures and missing plans remain visible instead of being converted into success claims.

These are proposed acceptance criteria, not claims that the system already exists or has achieved particular results.

## 10. Positioning and honest claims

**Positioning:** a simulation-native robot task-authoring and testing studio for people who need to specify what a robot should accomplish, not prescribe every motion.

**Potential users to validate:** robotics developers, simulation researchers and teams authoring manipulation workflows. These are proposed users, not evidence of customer demand.

**Defensible claim if demonstrated:** “A human correction becomes an explicit task constraint that changes planning and improves compliance across new layouts in a supported MuJoCo manipulation environment.”

**Claims to avoid:** general physical-world understanding, training a robot from one example, guaranteed safety, arbitrary task generalization, real-world deployment or guaranteed hackathon success.

Language-conditioned manipulation and constraint-aware planning already exist. The proposed value is the complete authoring, ambiguity-resolution and regression-testing workflow, supported by evidence.

### Short pitch

MIRROR turns a demonstration into a task specification you can inspect and correct. It executes the task in MuJoCo, exposes ambiguous interpretations, and tests whether one correction changes robot behavior across new layouts. The product is not a robot animation. It is an interface for teaching a simulated robot what counts as doing the task correctly.

## References and context

- [Meta Global AI Developer Hackathon](https://cerebralvalley.ai/e/meta-ai-developer-hackathon): public event context; robotics appears under Push the Frontier. Exact participant rules and model capabilities still require verification.
- [Meta event page](https://dev.meta.ai/events/global-hackathon).
- [MuJoCo documentation](https://mujoco.readthedocs.io/): simulation framework.
- [VoxPoser](https://voxposer.github.io/): relevant prior work on language-derived affordances, constraints and robot motion planning. MIRROR should not claim to be the first language-controlled robot.

---

**Project scope:** MuJoCo only. **Core proof:** a confirmed correction changes executed behavior in fresh simulated environments.
