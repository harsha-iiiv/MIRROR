# MIRROR paired evaluation: primary

Split: **eval**, 60 paired scenes, 120 trials. Every attempt is listed below.

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
| Trials executed in simulation | 60/60 | 60/60 |
| Task completion | 60/60 | 60/60 |
| Violation-free success | 27/60 | 60/60 |
| Trials violating corrected rules | 33 | 0 |
| Max tilt of corrected objects, mean / max (deg) | 49.4 / 89.8 | 0.2 / 0.3 |
| Time over tolerance, mean (s) | 2.28 | 0.00 |
| Beads lost, total (mean per executed trial) | 119 (2.0) | 0 (0.0) |
| Trials with forbidden contacts | 0 | 0 |
| Trials with any obstacle or floor contact (spec-independent) | 0 | 0 |
| Planning failures | 0 | 0 |
| Execution failures | 0 | 0 |
| Timeouts | 0 | 0 |
| Planning time, mean (s) | 0.780 | 0.756 |
| Execution time, mean simulated (s) | 54.0 | 51.8 |
| Human interventions | 0 | 0 |
| Feasible scenes wrongly rejected (of known feasible) | 0 of 33 | 0 of 33 |

## Paired statistics

The correction was exercised in 33 of 60 scenes (the original specification's rollout violated a corrected rule: seeds 30002, 30004, 30006, 30008, 30010, 30012, 30015, 30017, 30018, 30019, 30020, 30021, 30022, 30026, 30027, 30028, 30029, 30030, 30031, 30034, 30040, 30043, 30045, 30046, 30047, 30048, 30050, 30051, 30052, 30053, 30055, 30057, 30058). In the other scenes both versions may legitimately behave the same.

Violation-free success: A 0.45 (95% Wilson [0.33, 0.58]), B 1.00 ([0.94, 1.00]). Difference B - A +0.55, bootstrap 95% CI [0.42, 0.68].

Pairs: both 27, only A 0, only B 33, neither 0. Exact McNemar p = 2.328e-10. 33 discordant pair(s) of 60.

Corrected-rule compliance (executed and no violation): A 0.45, B 1.00, McNemar p = 2.328e-10.

Beads lost per scene: mean difference B - A -1.98, bootstrap 95% CI [-2.48, -1.50].

Previously successful scenes kept: 27 of 27 scenes where A succeeded without violating the corrected rules; 60 of 60 scenes where A succeeded under its own specification also succeeded under B.

## Scope regression

32/32 unrelated-object tilted routes kept under the corrected spec.

| seed | object | max tilt A | max tilt B | still tilted |
|---|---|---|---|---|
| 30002 | marker_0 | 89.4 | 89.4 | True |
| 30005 | marker_0 | 89.4 | 89.4 | True |
| 30006 | block_0 | 90.0 | 90.0 | True |
| 30010 | marker_0 | 89.4 | 89.4 | True |
| 30012 | block_0 | 90.0 | 90.0 | True |
| 30012 | marker_0 | 89.4 | 89.4 | True |
| 30014 | block_0 | 90.0 | 90.0 | True |
| 30014 | marker_0 | 89.4 | 89.4 | True |
| 30015 | block_0 | 90.0 | 90.0 | True |
| 30015 | marker_0 | 89.4 | 89.5 | True |
| 30017 | marker_0 | 89.4 | 89.4 | True |
| 30019 | block_0 | 90.0 | 90.0 | True |
| 30020 | block_0 | 90.0 | 90.0 | True |
| 30020 | marker_0 | 89.4 | 89.4 | True |
| 30022 | block_0 | 90.0 | 90.0 | True |
| 30022 | marker_0 | 89.4 | 89.4 | True |
| 30026 | marker_0 | 89.4 | 89.5 | True |
| 30028 | block_0 | 90.0 | 90.0 | True |
| 30031 | marker_0 | 89.4 | 89.4 | True |
| 30034 | block_0 | 90.0 | 90.0 | True |
| 30034 | marker_0 | 89.4 | 89.4 | True |
| 30036 | marker_0 | 89.4 | 89.4 | True |
| 30037 | marker_0 | 89.4 | 89.4 | True |
| 30043 | marker_0 | 89.4 | 89.4 | True |
| 30044 | marker_0 | 89.4 | 89.4 | True |
| 30045 | marker_0 | 89.5 | 89.5 | True |
| 30047 | block_0 | 90.0 | 90.0 | True |
| 30048 | marker_0 | 89.4 | 89.4 | True |
| 30050 | marker_0 | 89.4 | 89.4 | True |
| 30053 | marker_0 | 89.4 | 89.4 | True |
| 30056 | block_0 | 90.0 | 90.0 | True |
| 30059 | block_0 | 90.0 | 90.0 | True |

## Every attempt

| seed | scene | A status | A tilt | A beads lost | B status | B tilt | B beads lost | notes |
|---|---|---|---|---|---|---|---|---|
| 30000 | eval_30000 | success | 0 | 0 | success | 0 | 0 |  |
| 30001 | eval_30001 | success | 0 | 0 | success | 0 | 0 |  |
| 30002 | eval_30002 | success | 90 | 5 | success | 0 | 0 |  |
| 30003 | eval_30003 | success | 0 | 0 | success | 0 | 0 |  |
| 30004 | eval_30004 | success | 90 | 4 | success | 0 | 0 |  |
| 30005 | eval_30005 | success | 0 | 0 | success | 0 | 0 |  |
| 30006 | eval_30006 | success | 90 | 2 | success | 0 | 0 |  |
| 30007 | eval_30007 | success | 0 | 0 | success | 0 | 0 |  |
| 30008 | eval_30008 | success | 90 | 3 | success | 0 | 0 |  |
| 30009 | eval_30009 | success | 0 | 0 | success | 0 | 0 |  |
| 30010 | eval_30010 | success | 90 | 6 | success | 0 | 0 |  |
| 30011 | eval_30011 | success | 0 | 0 | success | 0 | 0 |  |
| 30012 | eval_30012 | success | 90 | 3 | success | 0 | 0 |  |
| 30013 | eval_30013 | success | 0 | 0 | success | 0 | 0 |  |
| 30014 | eval_30014 | success | 0 | 0 | success | 0 | 0 |  |
| 30015 | eval_30015 | success | 90 | 4 | success | 0 | 0 |  |
| 30016 | eval_30016 | success | 0 | 0 | success | 0 | 0 |  |
| 30017 | eval_30017 | success | 90 | 3 | success | 0 | 0 |  |
| 30018 | eval_30018 | success | 90 | 4 | success | 0 | 0 |  |
| 30019 | eval_30019 | success | 90 | 5 | success | 0 | 0 |  |
| 30020 | eval_30020 | success | 90 | 3 | success | 0 | 0 |  |
| 30021 | eval_30021 | success | 90 | 3 | success | 0 | 0 |  |
| 30022 | eval_30022 | success | 90 | 4 | success | 0 | 0 |  |
| 30023 | eval_30023 | success | 0 | 0 | success | 0 | 0 |  |
| 30024 | eval_30024 | success | 0 | 0 | success | 0 | 0 |  |
| 30025 | eval_30025 | success | 0 | 0 | success | 0 | 0 |  |
| 30026 | eval_30026 | success | 90 | 3 | success | 0 | 0 |  |
| 30027 | eval_30027 | success | 90 | 3 | success | 0 | 0 |  |
| 30028 | eval_30028 | success | 90 | 3 | success | 0 | 0 |  |
| 30029 | eval_30029 | success | 90 | 3 | success | 0 | 0 |  |
| 30030 | eval_30030 | success | 90 | 4 | success | 0 | 0 |  |
| 30031 | eval_30031 | success | 90 | 4 | success | 0 | 0 |  |
| 30032 | eval_30032 | success | 0 | 0 | success | 0 | 0 |  |
| 30033 | eval_30033 | success | 0 | 0 | success | 0 | 0 |  |
| 30034 | eval_30034 | success | 90 | 3 | success | 0 | 0 |  |
| 30035 | eval_30035 | success | 0 | 0 | success | 0 | 0 |  |
| 30036 | eval_30036 | success | 0 | 0 | success | 0 | 0 |  |
| 30037 | eval_30037 | success | 0 | 0 | success | 0 | 0 |  |
| 30038 | eval_30038 | success | 0 | 0 | success | 0 | 0 |  |
| 30039 | eval_30039 | success | 0 | 0 | success | 0 | 0 |  |
| 30040 | eval_30040 | success | 90 | 3 | success | 0 | 0 |  |
| 30041 | eval_30041 | success | 0 | 0 | success | 0 | 0 |  |
| 30042 | eval_30042 | success | 0 | 0 | success | 0 | 0 |  |
| 30043 | eval_30043 | success | 90 | 3 | success | 0 | 0 |  |
| 30044 | eval_30044 | success | 0 | 0 | success | 0 | 0 |  |
| 30045 | eval_30045 | success | 90 | 3 | success | 0 | 0 |  |
| 30046 | eval_30046 | success | 90 | 3 | success | 0 | 0 |  |
| 30047 | eval_30047 | success | 90 | 4 | success | 0 | 0 |  |
| 30048 | eval_30048 | success | 90 | 6 | success | 0 | 0 |  |
| 30049 | eval_30049 | success | 0 | 0 | success | 0 | 0 |  |
| 30050 | eval_30050 | success | 90 | 4 | success | 0 | 0 |  |
| 30051 | eval_30051 | success | 90 | 5 | success | 0 | 0 |  |
| 30052 | eval_30052 | success | 90 | 3 | success | 0 | 0 |  |
| 30053 | eval_30053 | success | 90 | 2 | success | 0 | 0 |  |
| 30054 | eval_30054 | success | 0 | 0 | success | 0 | 0 |  |
| 30055 | eval_30055 | success | 90 | 3 | success | 0 | 0 |  |
| 30056 | eval_30056 | success | 0 | 0 | success | 0 | 0 |  |
| 30057 | eval_30057 | success | 90 | 4 | success | 0 | 0 |  |
| 30058 | eval_30058 | success | 90 | 4 | success | 0 | 0 |  |
| 30059 | eval_30059 | success | 0 | 0 | success | 0 | 0 |  |

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
