# Claims and limitations

MIRROR is a simulation study. This page states what it shows, what it does
not show, and every simplification behind the results.

## What the project claims, where demonstrated

In a supported MuJoCo kit-packing task, a human correction becomes an
explicit, scoped task constraint. That constraint changes planning, the
change is visible in executed physics, and compliance with the corrected
rule improves on freshly sampled layouts, while objects the correction does
not cover keep their freedom. Evaluation numbers are in
[EVALUATION.md](EVALUATION.md) and `results/`.

## What it does not claim

* No physical robot, and no evidence of simulation-to-reality transfer.
* No understanding of human video. The demonstration is a simulated
  teleoperation trace.
* No general task learning from one example. One task family is supported.
* No new foundation model or learned robot policy.
* No formal verification or safety guarantee. Finite simulated trials are
  evidence, not proof. The only proofs are the narrow geometric
  infeasibility certificates described below.
* Not the first language-conditioned manipulation system. Prior work such as
  VoxPoser derives constraints from language. The contribution here is the
  authoring, clarification and regression-testing workflow and its evidence.

## Simplifications

### Robot and physics
* The robot is a free-flying parallel-jaw gripper on three prismatic and
  three revolute actuated joints, with no arm body. Only the gripper collides.
* Its workspace is a box (0.45 m reach in height). The wall is taller than
  this, so no route goes over it.
* Grasps are contact-based (friction between finger pads and the object),
  with no attachment constraint.

### Demonstration and interpretation
* The reference demonstration is driven by a scripted operator through the
  same velocity-command interface a person uses in the studio. It is
  labelled `teleop_scripted`.
* The interpreter reads simulator trace data (poses, grasp and release
  events), not video. Privileged simulator state is used for summarizing the
  demo, for planning and for checking, and is never presented as perception.
* Recorded model responses in `data/cassettes/` were produced with a
  development model (`gpt-5.5`, through an OpenAI-compatible endpoint), not
  Muse Spark. With a Meta Model API key, `MIRROR_LLM_MODE=record` re-records
  them with `muse-spark-1.3`.
* Rules apply per object kind, so several objects of one kind share rules.
* When a correction names no phase, an upright rule applies while the object
  is carried.
* Tilt shown in a clarification is computed from planned waypoints, assuming
  a rigid grasp; executed tilt is measured by the checker.

### Planning
* Routes are enumerated (direct, around each wall end, through each slot in
  each orientation mode), with a sampling fallback for extra obstacles.
  Collision checking uses a conservative geometric envelope of the gripper
  plus the held object.
* "No plan" means the planner found none. An infeasibility certificate is
  attached only when a geometric argument establishes it: every opening
  between the regions is lower than the minimum envelope height allowed by
  the enforced rules, and no route exists around or over the wall within the
  workspace.

### Checking
* Completion uses the object's centre (inside the tray footprint, above its
  floor), release, and settling (linear speed below tolerance for the last
  0.3 s; angular speed is not checked).
* The transport phase comes from the per-step two-finger hold signal; gaps of
  up to 0.5 s are bridged.
* Beads count as retained when their centres are inside the cup's interior
  at the last step (2 mm tolerance). Bead loss is a reported metric, not a
  rule.
* The clearance check uses a bounding sphere.
* An upright route is labelled feasible by a clearance condition, not a
  constructed path; scenes with box obstacles are marked unknown.
