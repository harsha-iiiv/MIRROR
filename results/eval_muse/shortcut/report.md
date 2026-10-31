# MIRROR paired evaluation: shortcut

Split: **eval**, 30 paired scenes, 60 trials. Every attempt is listed below.

## Freeze

- Manifest created 2026-10-31T16:34:38.113695+00:00 (written before layouts were generated)
- Spec A `spec_demo_ref_demo_reference` v2 sha256 `7b18030c5cbbadd3`
- Spec B `spec_demo_ref_demo_reference` v3 sha256 `7ee3ac0153407424`
- Code sha256 `e77c4cec9c4d414d` over 59 source files (presentation-only paths not hashed: sim/render.py, studio/**, cli.py)
- Seeds: 50000, 50001, 50002, 50003, 50004, 50005, 50006, 50007, 50008, 50009, 50010, 50011, 50012, 50013, 50014, 50015, 50016, 50017, 50018, 50019, 50020, 50021, 50022, 50023, 50024, 50025, 50026, 50027, 50028, 50029, 50030, 50031, 50032, 50033, 50034, 50035, 50036, 50037, 50038, 50039, 50040, 50041, 50042, 50043, 50044, 50045, 50046, 50047, 50048, 50049, 50050, 50051, 50052, 50053, 50054, 50055, 50056, 50057, 50058, 50059, 60000, 60001, 60002, 60003, 60004, 60005, 60006, 60007, 60008, 60009, 60010, 60011, 60012, 60013, 60014, 60015, 60016, 60017, 60018, 60019, 60020, 60021, 60022, 60023, 60024, 60025, 60026, 60027, 60028, 60029, 50000, 50001, 50002, 50003, 50004

## Corrected rules (enforced in B, not in A)

- `c_upright_transport_cup`: upright on kind=cup during transport, params {'max_tilt_deg': 15.0}

## Metrics (reported separately, not combined)

| Metric | A: original | B: corrected |
|---|---|---|
| Trials executed in simulation | 30/30 | 30/30 |
| Task completion | 30/30 | 30/30 |
| Violation-free success | 3/30 | 30/30 |
| Trials violating corrected rules | 27 | 0 |
| Max tilt of corrected objects, mean / max (deg) | 80.7 / 89.7 | 0.2 / 0.3 |
| Time over tolerance, mean (s) | 3.73 | 0.00 |
| Beads lost, total (mean per executed trial) | 103 (3.4) | 0 (0.0) |
| Trials with forbidden contacts | 0 | 0 |
| Trials with any obstacle or floor contact (spec-independent) | 0 | 0 |
| Planning failures | 0 | 0 |
| Execution failures | 0 | 0 |
| Timeouts | 0 | 0 |
| Planning time, mean (s) | 0.088 | 0.082 |
| Execution time, mean simulated (s) | 57.2 | 53.9 |
| Human interventions | 0 | 0 |
| Feasible scenes wrongly rejected (of known feasible) | 0 of 21 | 0 of 21 |

## Paired statistics

The correction was exercised in 27 of 30 scenes (the original specification's rollout violated a corrected rule: seeds 60000, 60001, 60002, 60003, 60005, 60006, 60007, 60008, 60009, 60010, 60011, 60012, 60013, 60014, 60016, 60017, 60019, 60020, 60021, 60022, 60023, 60024, 60025, 60026, 60027, 60028, 60029). In the other scenes both versions may legitimately behave the same.

Violation-free success: A 0.10 (95% Wilson [0.03, 0.26]), B 1.00 ([0.89, 1.00]). Difference B - A +0.90, bootstrap 95% CI [0.77, 1.00].

Pairs: both 3, only A 0, only B 27, neither 0. Exact McNemar p = 1.49e-08. 27 discordant pair(s) of 30.

Corrected-rule compliance (executed and no violation): A 0.10, B 1.00, McNemar p = 1.49e-08.

Beads lost per scene: mean difference B - A -3.43, bootstrap 95% CI [-3.93, -2.90].

Previously successful scenes kept: 3 of 3 scenes where A succeeded without violating the corrected rules; 30 of 30 scenes where A succeeded under its own specification also succeeded under B.

## Scope regression

23/23 unrelated-object tilted routes kept under the corrected spec.

| seed | object | max tilt A | max tilt B | still tilted |
|---|---|---|---|---|
| 60000 | marker_0 | 89.4 | 89.4 | True |
| 60003 | marker_0 | 89.4 | 89.4 | True |
| 60004 | block_0 | 90.0 | 90.0 | True |
| 60004 | marker_0 | 89.4 | 89.4 | True |
| 60006 | marker_0 | 89.4 | 89.4 | True |
| 60007 | block_0 | 90.0 | 90.0 | True |
| 60007 | marker_0 | 89.4 | 89.4 | True |
| 60008 | block_0 | 90.0 | 90.0 | True |
| 60009 | marker_0 | 89.4 | 89.4 | True |
| 60010 | block_0 | 90.0 | 90.0 | True |
| 60011 | marker_0 | 89.4 | 89.4 | True |
| 60013 | block_0 | 90.0 | 90.0 | True |
| 60013 | marker_0 | 89.4 | 89.4 | True |
| 60014 | block_0 | 90.0 | 90.0 | True |
| 60015 | marker_0 | 89.4 | 89.4 | True |
| 60017 | block_0 | 90.0 | 90.0 | True |
| 60018 | block_0 | 90.0 | 90.0 | True |
| 60018 | marker_0 | 89.4 | 89.4 | True |
| 60020 | marker_0 | 89.4 | 89.4 | True |
| 60022 | marker_0 | 89.4 | 89.4 | True |
| 60024 | block_0 | 90.0 | 90.0 | True |
| 60025 | marker_0 | 89.4 | 89.4 | True |
| 60027 | marker_0 | 89.4 | 89.4 | True |

## Every attempt

| seed | scene | A status | A tilt | A beads lost | B status | B tilt | B beads lost | notes |
|---|---|---|---|---|---|---|---|---|
| 60000 | eval_shortcut_60000 | success | 90 | 3 | success | 0 | 0 |  |
| 60001 | eval_shortcut_60001 | success | 90 | 4 | success | 0 | 0 |  |
| 60002 | eval_shortcut_60002 | success | 90 | 2 | success | 0 | 0 |  |
| 60003 | eval_shortcut_60003 | success | 90 | 5 | success | 0 | 0 |  |
| 60004 | eval_shortcut_60004 | success | 0 | 0 | success | 0 | 0 |  |
| 60005 | eval_shortcut_60005 | success | 90 | 3 | success | 0 | 0 |  |
| 60006 | eval_shortcut_60006 | success | 90 | 4 | success | 0 | 0 |  |
| 60007 | eval_shortcut_60007 | success | 90 | 4 | success | 0 | 0 |  |
| 60008 | eval_shortcut_60008 | success | 90 | 4 | success | 0 | 0 |  |
| 60009 | eval_shortcut_60009 | success | 90 | 4 | success | 0 | 0 |  |
| 60010 | eval_shortcut_60010 | success | 90 | 5 | success | 0 | 0 |  |
| 60011 | eval_shortcut_60011 | success | 90 | 4 | success | 0 | 0 |  |
| 60012 | eval_shortcut_60012 | success | 90 | 3 | success | 0 | 0 |  |
| 60013 | eval_shortcut_60013 | success | 90 | 5 | success | 0 | 0 |  |
| 60014 | eval_shortcut_60014 | success | 90 | 3 | success | 0 | 0 |  |
| 60015 | eval_shortcut_60015 | success | 0 | 0 | success | 0 | 0 |  |
| 60016 | eval_shortcut_60016 | success | 90 | 5 | success | 0 | 0 |  |
| 60017 | eval_shortcut_60017 | success | 90 | 2 | success | 0 | 0 |  |
| 60018 | eval_shortcut_60018 | success | 0 | 0 | success | 0 | 0 |  |
| 60019 | eval_shortcut_60019 | success | 90 | 4 | success | 0 | 0 |  |
| 60020 | eval_shortcut_60020 | success | 90 | 4 | success | 0 | 0 |  |
| 60021 | eval_shortcut_60021 | success | 90 | 4 | success | 0 | 0 |  |
| 60022 | eval_shortcut_60022 | success | 90 | 3 | success | 0 | 0 |  |
| 60023 | eval_shortcut_60023 | success | 90 | 5 | success | 0 | 0 |  |
| 60024 | eval_shortcut_60024 | success | 90 | 3 | success | 0 | 0 |  |
| 60025 | eval_shortcut_60025 | success | 90 | 3 | success | 0 | 0 |  |
| 60026 | eval_shortcut_60026 | success | 90 | 4 | success | 0 | 0 |  |
| 60027 | eval_shortcut_60027 | success | 90 | 3 | success | 0 | 0 |  |
| 60028 | eval_shortcut_60028 | success | 90 | 5 | success | 0 | 0 |  |
| 60029 | eval_shortcut_60029 | success | 90 | 5 | success | 0 | 0 |  |

## Figures

![paired_tilt](paired_tilt.png)
![beads_lost](beads_lost.png)
![outcomes](outcomes.png)
![tilt_traces](tilt_traces.png)

## Where simulator ground truth is used

- object positions and orientations: simulator body state at every physics step (tilt, placement, settling)
- held object: executor's finger-contact detection logged per step (transport phase boundaries)
- bead positions: simulator body state (bead retention)
- contacts: simulator contact list, classified by geom group (forbidden contacts)
- scene geometry: SceneState dimensions (tray footprint, cup interior, obstacle boxes)

## Simplifications

- Completion uses the object's centre: inside the tray's inner footprint (minus inside_margin_m) and above its floor.
- Settled means linear speed below tolerances.settle_speed_mps over the last 0.3 s of the log; angular speed is not checked.
- Transport phase = the executor's per-step held label (finger contacts; gaps up to 0.5 s bridged) plus, between first grasp and last release, steps where the object is more than 5 mm above its resting height and touches no support.
- Bead retention counts bead centres inside the cup interior box (2 mm tolerance) at the final step.
- keep_clearance uses a bounding-sphere distance to obstacle boxes (conservative).
- Upright-route feasibility witness is a clearance condition, not a constructed trajectory; scenes with box obstacles are 'unknown'.
- The independent infeasibility proof covers any motion of a rigidly top-grasped object (tool centre on its axis, palm above its top, grasp depth 0 to the palm's lower face) with the tool centre in the workspace; it uses a 0.1 mm strictness margin (CONTACT_FREE_EPS_M). Only planner records with proven: true count as planner proofs.

Finite simulated trials; not formal verification and not evidence of physical deployment.
