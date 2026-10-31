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
  between the first grasp and the last release, steps where the object is more
  than 5 mm above its resting height and touches no support also count (a
  disagreement with the held label is noted); `final` = the last 0.3 s after
  the object's last release; `always` = every step. Each violation is an interval with start, end, duration and peak tilt.
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
  rules as `extra_rules`; their violations go to
  `RolloutReport.advisory_violations` and never change the recorded status.
* **Required objects** are those named by `spec.active_goals()` (goals are
  pursued unless rejected).

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

## Pre-registered evaluation (`final.py`)

* Strata, fixed before any freeze: **primary** 60 eval seeds (30000-30059),
  standard sampler, family `mixed`; **shortcut** 30 eval seeds (40000-40029),
  family `shortcut` (a wall slot near the straight cup-to-tray line, defined by
  geometry at sampling time, never by outcomes); **blocked** 5 seeds
  (30000-30004) of the blocked split. All are reported side by side; nothing is
  filtered by outcome. Seeds 10000-10059 and 20000-20029 were observed during
  development and are retired; freezing them is refused.
* **Ledger**: the layout sampler appends every evaluation-split layout it
  generates to `results/eval/ledger.jsonl`. `freeze_final` refuses if any entry
  already names a frozen seed; `run_final` refuses if an entry predates the
  freeze, embeds all entries for frozen seeds in the report, and says so
  plainly if the writer recorded nothing (then the check is vacuous).
* The manifest also hashes `scripts/`, `data/demos` and `data/cassettes` and
  records the Python, NumPy, SciPy and MuJoCo versions; any change refuses the run.
* **Rollout subset** committed with the final report: every seed where either
  condition carried a corrected-rule object above the rule's tolerance, did not
  succeed or touched an obstacle; every blocked seed; every 5th seed of each
  stratum. All rollouts are staged under results/tmp; metrics cover every seed.
* Specifications: `evaluation_specs()` replays recorded model responses on the
  pinned reference demonstration (proposal with all goals and
  `c_upright_final_cup` confirmed; then the reference correction) and refuses
  to continue unless the offline interpreter yields identical enforced
  constraints and active goals.
* `freeze_final()` writes one manifest covering every stratum (refusing if a
  stratum's family cannot be generated); `run_final()` verifies specs, code
  hash and each stratum before generating its layouts, and stops if the code
  changes mid-run. `scripts/run_eval.py freeze-final` / `final` drive it;
  `--rehearsal` uses development seeds and cannot write to results/eval.

## Playback export

`--save-rollouts` writes `<out>/[<stratum>/]rollouts/seed<k>_<condition>.json`,
a valid `Rollout` with frames at 15 Hz. Numbers are rounded to 4 decimals and a
body is listed in a frame only when it has moved since its last listed pose
(first and last frames list every body): an absent body keeps its last pose
(`expand_frames` restores full frames). Over the 400 KB budget, bead bodies are
listed on every 2nd/4th/8th frame, then poses are rounded to 3 decimals, and as
a last resort the frame rate is halved. Every applied step is recorded in the
report notes prefixed `playback:`. The checker never uses these frames.

## Independent feasibility analysis

* **Infeasibility proof** (`feasibility.py`, scope `any_motion_rigid_top_grasp`):
  separate code from the planner's certificate, same kind of argument. In any
  rigid top grasp (tool centre on the object's axis, palm above its top, grasp
  depth between 0 and the palm's lower face) at tilt <= alpha, when the tool
  centre crosses the wall's mid-plane two body points on that plane (one in the
  palm, one on the object's bottom face) differ in height by at least
  (u_top + H - g_max) cos(alpha). The wall extends past the workspace at both
  ends and above it, so both points must lie in openings; if that bound
  exceeds the openings' total vertical span by more than CONTACT_FREE_EPS_M
  (0.1 mm), no motion in the model reaches the tray. A randomized test checks
  the bound against an independent numerical construction of the witnesses.
  It makes no claim when no valid witness exists (for the cup, tilt above
  about 17 degrees).
* Only planner records with `proven: true` count as planner proofs; records
  with `scope: planner_model, proven: false` are planner failures.
* **Feasibility witness**: no box obstacles and every separating wall leaves
  a gap of at least the palm length plus 2 cm at one end inside the workspace.
  This is a sufficient geometric condition, not a constructed trajectory.

Finite simulated trials are not formal verification and not evidence of
physical deployment.
