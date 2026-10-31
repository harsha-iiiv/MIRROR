# Evaluation method

This package judges rollouts from the simulator's step log. It never reads
model output or planner explanations.

## Checker (`check`)

`check(log, spec, scene)` is a pure function of the dense per-physics-step
log, the task specification and the scene.

* **Selectors** are resolved independently of the planner: a role matches
  objects of the kind bound to that role in `spec.bindings`.
* **Completion** for every object named by a confirmed goal: centre inside the
  tray's inner footprint (shrunk by `tolerances.inside_margin_m`) and above
  its floor, not held during the last 0.3 s, and linear speed below
  `tolerances.settle_speed_mps` over the last 0.3 s.
* **Upright constraints** are evaluated at every step of their phase:
  `transport` = steps where the executor's finger-contact label says the
  object is held (gaps up to 0.5 s with no other object held are bridged);
  `final` = the last 0.3 s after the object's last release; `always` = every
  step. Each violation is an interval with start, end, duration and peak tilt.
* **Forbidden contacts** use the simulator's contact records, classified by
  geom group (`obstacle`, `table_outside_tray`, `other_objects`, plus
  `tray` and `gripper`). Records are matched in both directions.
* **Clearance** uses a bounding-sphere distance to obstacle boxes (walls are
  decomposed into solid boxes around their slots).
* **Beads** retained = bead centres inside the cup's interior box (2 mm
  tolerance) at the final step. Bead loss is a metric, not a rule.
* **Status precedence**: planning_failed, execution_failed, timeout,
  constraint_violated, task_incomplete, success. Only enforced (confirmed)
  constraints can produce `constraint_violated`.
* **Re-checking** an old rollout under a newer specification: pass the newer
  rules as `extra_rules`; their violations are reported separately
  (`advisory_violations`) and never change the recorded status.

Every report lists where simulator ground truth is used (`ground_truth_use`).

## Paired evaluation (`paired_eval`)

* For each seed one scene is generated and both specification versions run
  from that identical initial state with the same planner and executor.
* **Freeze**: for the eval split, `freeze()` must have written a manifest
  (both spec hashes, sha256 over every source file under `mirror/`, seeds,
  time) before layouts are generated. `paired_eval` verifies all of it and
  refuses to run otherwise.
* **Corrected rules** = rules enforced in B with no identical enforced rule in
  A. Both conditions are measured against them (for A as advisory checks).
* **Violation-free success** = status `success` under the trial's own spec
  and no violation of the corrected rules.
* Metrics are reported separately: completion, violation-free success,
  corrected-rule violations (max tilt, time over tolerance), beads lost,
  forbidden contacts, obstacle contacts of any body (spec-independent),
  planning failures, execution failures, timeouts, planning time, simulated
  execution time, interventions (from the log; 0 unless recorded), feasible
  scenes wrongly rejected.
* **Statistics**: per-seed outcome pairs, exact two-sided McNemar test on
  violation-free success, Wilson intervals per condition, percentile bootstrap
  (resampling seeds) for paired differences, and a plain statement of what the
  sample size can establish.
* **Scope regression**: for objects not covered by the corrected rules, when
  the original run carried them tilted beyond tolerance, does the corrected
  run still do so?
* **Blocked case**: planning failures are classified by comparing the
  planner's certificate with an independent analysis (`feasibility.py`):
  proven infeasible, planner failure on a scene with a feasibility witness,
  or planner failure with unknown feasibility.

## Independent feasibility analysis

* **Infeasibility proof** (upright rules): a wall separates the object from
  the tray, extends past both workspace edges by the object's bounding radius,
  cannot be passed over within the workspace height, and none of its openings
  exceeds the object's smallest vertical extent at the allowed tilt by more
  than 0.1 mm. Only the carried object is considered (the gripper can only add
  height); the tool centre is assumed to lie within the object's bounding
  sphere; passage must be collision-free.
* **Feasibility witness**: no box obstacles and every separating wall leaves
  a gap of at least the palm length plus 2 cm at one end inside the workspace.
  This is a sufficient geometric condition, not a constructed trajectory.

Finite simulated trials are not formal verification and not evidence of
physical deployment.
