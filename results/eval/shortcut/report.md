# MIRROR paired evaluation: shortcut

Split: **eval**, 30 paired scenes, 60 trials. Every attempt is listed below.

## Freeze

- Manifest created 2026-10-31T13:50:32.746526+00:00 (written before layouts were generated)
- Spec A `spec_demo_ref_demo_reference` v2 sha256 `546ca656ea2952f2`
- Spec B `spec_demo_ref_demo_reference` v3 sha256 `baafa260a6834153`
- Code sha256 `b498c4b41664c589` over 65 source files
- Seeds: 30000, 30001, 30002, 30003, 30004, 30005, 30006, 30007, 30008, 30009, 30010, 30011, 30012, 30013, 30014, 30015, 30016, 30017, 30018, 30019, 30020, 30021, 30022, 30023, 30024, 30025, 30026, 30027, 30028, 30029, 30030, 30031, 30032, 30033, 30034, 30035, 30036, 30037, 30038, 30039, 30040, 30041, 30042, 30043, 30044, 30045, 30046, 30047, 30048, 30049, 30050, 30051, 30052, 30053, 30054, 30055, 30056, 30057, 30058, 30059, 40000, 40001, 40002, 40003, 40004, 40005, 40006, 40007, 40008, 40009, 40010, 40011, 40012, 40013, 40014, 40015, 40016, 40017, 40018, 40019, 40020, 40021, 40022, 40023, 40024, 40025, 40026, 40027, 40028, 40029, 30000, 30001, 30002, 30003, 30004

## Corrected rules (enforced in B, not in A)

- `c_upright_transport_cup`: upright on kind=cup during transport, params {'max_tilt_deg': 15.0}

## Metrics (reported separately, not combined)

| Metric | A: original | B: corrected |
|---|---|---|
| Trials executed in simulation | 30/30 | 30/30 |
| Task completion | 30/30 | 30/30 |
| Violation-free success | 4/30 | 30/30 |
| Trials violating corrected rules | 26 | 0 |
| Max tilt of corrected objects, mean / max (deg) | 77.7 / 89.8 | 0.2 / 0.3 |
| Time over tolerance, mean (s) | 3.60 | 0.00 |
| Beads lost, total (mean per executed trial) | 106 (3.5) | 0 (0.0) |
| Trials with forbidden contacts | 0 | 0 |
| Trials with any obstacle or floor contact (spec-independent) | 0 | 0 |
| Planning failures | 0 | 0 |
| Execution failures | 0 | 0 |
| Timeouts | 0 | 0 |
| Planning time, mean (s) | 0.983 | 0.944 |
| Execution time, mean simulated (s) | 57.9 | 55.4 |
| Human interventions | 0 | 0 |
| Feasible scenes wrongly rejected (of known feasible) | 0 of 17 | 0 of 17 |

## Paired statistics

The correction was exercised in 26 of 30 scenes (the original specification's rollout violated a corrected rule: seeds 40001, 40002, 40003, 40004, 40005, 40006, 40007, 40008, 40009, 40010, 40011, 40012, 40014, 40016, 40017, 40018, 40019, 40021, 40022, 40023, 40024, 40025, 40026, 40027, 40028, 40029). In the other scenes both versions may legitimately behave the same.

Violation-free success: A 0.13 (95% Wilson [0.05, 0.30]), B 1.00 ([0.89, 1.00]). Difference B - A +0.87, bootstrap 95% CI [0.73, 0.97].

Pairs: both 4, only A 0, only B 26, neither 0. Exact McNemar p = 2.98e-08. 26 discordant pair(s) of 30.

Corrected-rule compliance (executed and no violation): A 0.13, B 1.00, McNemar p = 2.98e-08.

Beads lost per scene: mean difference B - A -3.53, bootstrap 95% CI [-4.13, -2.90].

Previously successful scenes kept: 4 of 4 scenes where A succeeded without violating the corrected rules; 30 of 30 scenes where A succeeded under its own specification also succeeded under B.

## Scope regression

25/25 unrelated-object tilted routes kept under the corrected spec.

| seed | object | max tilt A | max tilt B | still tilted |
|---|---|---|---|---|
| 40000 | marker_0 | 89.4 | 89.4 | True |
| 40001 | block_0 | 90.0 | 90.0 | True |
| 40002 | marker_0 | 89.5 | 89.4 | True |
| 40003 | marker_0 | 89.5 | 89.5 | True |
| 40009 | marker_0 | 89.4 | 89.4 | True |
| 40013 | block_0 | 90.0 | 90.0 | True |
| 40014 | block_0 | 90.0 | 90.0 | True |
| 40014 | marker_0 | 89.4 | 89.4 | True |
| 40015 | block_0 | 90.0 | 90.0 | True |
| 40015 | marker_0 | 89.4 | 89.4 | True |
| 40016 | block_0 | 90.0 | 90.0 | True |
| 40016 | marker_0 | 89.4 | 89.4 | True |
| 40017 | marker_0 | 89.4 | 89.4 | True |
| 40019 | block_0 | 90.0 | 90.0 | True |
| 40019 | marker_0 | 89.4 | 89.4 | True |
| 40020 | block_0 | 90.0 | 90.0 | True |
| 40021 | block_0 | 90.0 | 90.0 | True |
| 40023 | block_0 | 90.0 | 90.0 | True |
| 40023 | marker_0 | 89.4 | 89.4 | True |
| 40025 | block_0 | 90.0 | 90.0 | True |
| 40025 | marker_0 | 89.4 | 89.4 | True |
| 40027 | block_0 | 90.0 | 90.0 | True |
| 40027 | marker_0 | 89.4 | 89.4 | True |
| 40028 | marker_0 | 89.4 | 89.4 | True |
| 40029 | marker_0 | 89.4 | 89.5 | True |

## Every attempt

| seed | scene | A status | A tilt | A beads lost | B status | B tilt | B beads lost | notes |
|---|---|---|---|---|---|---|---|---|
| 40000 | eval_shortcut_40000 | success | 0 | 0 | success | 0 | 0 |  |
| 40001 | eval_shortcut_40001 | success | 90 | 5 | success | 0 | 0 |  |
| 40002 | eval_shortcut_40002 | success | 90 | 4 | success | 0 | 0 |  |
| 40003 | eval_shortcut_40003 | success | 90 | 5 | success | 0 | 0 |  |
| 40004 | eval_shortcut_40004 | success | 90 | 4 | success | 0 | 0 |  |
| 40005 | eval_shortcut_40005 | success | 90 | 6 | success | 0 | 0 |  |
| 40006 | eval_shortcut_40006 | success | 90 | 3 | success | 0 | 0 |  |
| 40007 | eval_shortcut_40007 | success | 90 | 2 | success | 0 | 0 |  |
| 40008 | eval_shortcut_40008 | success | 90 | 6 | success | 0 | 0 |  |
| 40009 | eval_shortcut_40009 | success | 90 | 3 | success | 0 | 0 |  |
| 40010 | eval_shortcut_40010 | success | 90 | 6 | success | 0 | 0 |  |
| 40011 | eval_shortcut_40011 | success | 90 | 4 | success | 0 | 0 |  |
| 40012 | eval_shortcut_40012 | success | 90 | 4 | success | 0 | 0 |  |
| 40013 | eval_shortcut_40013 | success | 0 | 0 | success | 0 | 0 |  |
| 40014 | eval_shortcut_40014 | success | 90 | 3 | success | 0 | 0 |  |
| 40015 | eval_shortcut_40015 | success | 0 | 0 | success | 0 | 0 |  |
| 40016 | eval_shortcut_40016 | success | 90 | 3 | success | 0 | 0 |  |
| 40017 | eval_shortcut_40017 | success | 90 | 5 | success | 0 | 0 |  |
| 40018 | eval_shortcut_40018 | success | 90 | 4 | success | 0 | 0 |  |
| 40019 | eval_shortcut_40019 | success | 90 | 5 | success | 0 | 0 |  |
| 40020 | eval_shortcut_40020 | success | 0 | 0 | success | 0 | 0 |  |
| 40021 | eval_shortcut_40021 | success | 90 | 5 | success | 0 | 0 |  |
| 40022 | eval_shortcut_40022 | success | 90 | 3 | success | 0 | 0 |  |
| 40023 | eval_shortcut_40023 | success | 90 | 5 | success | 0 | 0 |  |
| 40024 | eval_shortcut_40024 | success | 90 | 4 | success | 0 | 0 |  |
| 40025 | eval_shortcut_40025 | success | 90 | 4 | success | 0 | 0 |  |
| 40026 | eval_shortcut_40026 | success | 90 | 3 | success | 0 | 0 |  |
| 40027 | eval_shortcut_40027 | success | 90 | 2 | success | 0 | 0 |  |
| 40028 | eval_shortcut_40028 | success | 90 | 4 | success | 0 | 0 |  |
| 40029 | eval_shortcut_40029 | success | 90 | 4 | success | 0 | 0 |  |

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
