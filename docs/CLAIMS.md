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
* Grasps are contact-based (the jaw closes at 12 N per finger; friction
  holds the object), with no attachment constraint. Gravity compensation is
  applied to the robot's bodies only, not to the held object.
* The executor stops at every waypoint, with speed and acceleration limits.
  Physics timestep 0.004 s.
* Bead contacts are not logged; bead loss is measured from bead positions.
* Layout validity at sampling time uses a conservative 2-D route search at
  carrying height, not a motion plan.

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
* Rules apply per object kind; a rule covers every object in the scene that
  matches its selector.
* The reference correction confirms a rule that the interpreter had already
  proposed (as an open question) from the demonstration. The correction's
  role is to settle that question and scope it, not to invent the rule from
  nothing.
* The offline rules lean toward "likely required" for the cup's upright
  carry because the scene describes the cup as holding beads. That is scene
  knowledge, not evidence from the demonstration.
* In `mirror story` and in `mirror run` without `--spec`, the review step is
  scripted: it confirms the goals and "cup upright at the destination".
* When a correction names no phase, an upright rule applies while the object
  is carried.
* Tilt shown in a clarification is computed from planned waypoints, assuming
  a rigid grasp; executed tilt is measured by the checker.

### Correction compiler and its benchmark
* Natural-language corrections are compiled by the model into intent-level
  operations, then checked by deterministic code (schema, specification
  consistency and scope) before anything is applied.
* After the correction benchmark showed the model applying corrections about
  objects that are not in the scene ("bowl", "jar") to the cup, a
  deterministic validation layer was added. It runs after the model and asks
  which scene object was meant whenever a correction names a known object
  noun that matches nothing in the scene. Because it was added after those
  failures were seen, the benchmark reports interpreter output alone and
  interpreter plus validation layer as separate rows, computed from the same
  recorded model outputs. The layer recognises only nouns in its fixed
  lexicon, so it narrows but does not remove the risk of a correction being
  applied to the wrong object.
* Benchmark cases are single sentences applied to one base specification in
  one scene. Gold answers follow the documented policies (for example, a rule
  with no phase applies while the object is carried). The prompt was revised
  twice after results were seen; the blind first-revision numbers remain the
  headline and later rows are labelled post-change
  (`results/corrections_benchmark_summary.md`).

### Planning
* Routes are enumerated (direct, around each wall end, through each slot in
  each orientation mode), with a sampling fallback for extra obstacles.
  Collision checking uses a conservative geometric envelope of the gripper
  plus the held object.
* Only top grasps of upright objects are supported, and the grasp is
  treated as rigid while planning. The marker's collision shape is a square
  prism around its cylinder (conservative).
* Orientation modes are a fixed set (upright, ±30°, ±45°, ±60°, ±90° about the
  jaw axis). A slot is crossed in a straight horizontal pass at a fixed
  orientation; the gripper rotates only while stationary. With a tilt limit
  between modes the planner may find no route where a partly tilted path
  exists, and it says so.
* If an unmoved object blocks a route, the planner retries other object
  orders.
* "No plan" means the planner found none. A proof (`proven: true`) is
  attached only when a geometric bound shows no motion within the workspace
  can carry the object across under the enforced rules, under stated
  assumptions (rigid top grasp within a depth range). A `planner_model`
  record (`proven: false`) covers only the planner's own way of crossing a
  slot and is not a proof.
* Forbidden contacts are avoided through safety margins (10 mm in open space,
  3 mm near contact) rather than a separate check during planning.
* The empty gripper may also tilt through a slot on its way back; rules
  apply to objects.

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

### Studio and static site
* Live runs in the studio use development showcase layouts (dev seeds
  9000–9099) and are labelled as such. Pre-registered evaluation results are
  only loaded, never regenerated, by the studio.
* Maps of loaded evaluation trials are sketches rebuilt from each rollout's
  recorded geometry. The tilt chart uses about 200 samples per run.
* The static site stores poses at 1 mm and 0.001 quaternion resolution.
  Bodies that have not moved are omitted from playback frames, and long
  rollouts may be thinned for playback; every thinning step is noted in the
  report. Metrics always come from the full-rate step log.
* Tall static walls are drawn translucent so the robot stays visible.
