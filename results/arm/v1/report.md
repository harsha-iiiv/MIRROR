# Arm backend vs baseline gripper (development layouts)

Commit b5b234b, MuJoCo 3.17.0, Python 3.12.11, Darwin arm64. Simulation only (MuJoCo). No hardware. Development layouts, not an evaluation. Physics results differ slightly between platforms (about 1e-4 m); compare runs only within one platform.

| backend | scene | spec | status | routes (transport) | cup beads | v1 re-checked under v2 | plan s | episode s |
|---|---|---|---|---|---|---|---|---|
| gripper | ref_demo | v1 | success | around:wall_0:+y | 8/8 | success | 0.1 | 1.7 |
| gripper | ref_demo | v2 | success | around:wall_0:+y | 8/8 |  | 0.1 | 1.6 |
| gripper | ref_shortcut | v1 | success | through_slot:wall_0:0:pitched+90, through_slot:wall_0:0:pitched-90, around:wall_0:+y | 6/8 | constraint_violated | 0.1 | 1.9 |
| gripper | ref_shortcut | v2 | success | around:wall_0:+y, through_slot:wall_0:0:pitched-90 | 8/8 |  | 0.1 | 2.1 |
| gripper | ref_blocked | v1 | success | through_slot:wall_0:0:pitched+90, through_slot:wall_0:0:pitched-90 | 6/8 | constraint_violated | 0.1 | 2.2 |
| gripper | ref_blocked | v2 | planning_failed (proven) | - | - |  | 0.1 | 0.1 |
| gripper | dev_300 | v1 | success | through_slot:wall_0:0:pitched-90, around:wall_0:-y | 5/8 | constraint_violated | 0.1 | 2.0 |
| gripper | dev_300 | v2 | success | around:wall_0:-y, through_slot:wall_0:0:pitched-90 | 8/8 |  | 0.1 | 1.9 |
| gripper | dev_301 | v1 | success | through_slot:wall_0:0:pitched+90, around:wall_0:-y | 6/8 | constraint_violated | 0.1 | 2.1 |
| gripper | dev_301 | v2 | success | around:wall_0:-y, through_slot:wall_0:0:pitched+90 | 8/8 |  | 0.1 | 1.8 |
| gripper | dev_302 | v1 | success | around:wall_0:-y, through_slot:wall_0:0:pitched+90 | 8/8 | success | 0.1 | 1.7 |
| gripper | dev_302 | v2 | success | around:wall_0:-y, through_slot:wall_0:0:pitched+90 | 8/8 |  | 0.1 | 1.7 |
| gripper | dev_303 | v1 | success | through_slot:wall_0:0:pitched-90, through_slot:wall_0:0:pitched+90 | 4/8 | constraint_violated | 0.1 | 1.8 |
| gripper | dev_303 | v2 | success | around:wall_0:-y, through_slot:wall_0:0:pitched+90, through_slot:wall_0:0:pitched-90 | 8/8 |  | 0.1 | 1.8 |
| gripper | dev_304 | v1 | success | around:wall_0:+y, through_slot:wall_0:0:pitched-90 | 8/8 | success | 0.1 | 1.7 |
| gripper | dev_304 | v2 | success | around:wall_0:+y, through_slot:wall_0:0:pitched-90 | 8/8 |  | 0.1 | 1.6 |
| gripper | dev_305 | v1 | success | around:wall_0:-y | 8/8 | success | 0.2 | 1.6 |
| gripper | dev_305 | v2 | success | around:wall_0:-y | 8/8 |  | 0.1 | 1.6 |
| gripper | dev_306 | v1 | success | through_slot:wall_0:0:pitched-90, around:wall_0:-y | 5/8 | constraint_violated | 0.1 | 1.8 |
| gripper | dev_306 | v2 | success | around:wall_0:-y | 8/8 |  | 0.1 | 1.6 |
| gripper | dev_307 | v1 | success | direct, around:wall_0:+y | 8/8 | success | 0.1 | 1.3 |
| gripper | dev_307 | v2 | success | direct, around:wall_0:+y | 8/8 |  | 0.1 | 1.3 |
| gripper | dev_308 | v1 | success | through_slot:wall_0:0:pitched-90, around:wall_0:-y | 3/8 | constraint_violated | 0.1 | 1.6 |
| gripper | dev_308 | v2 | success | around:wall_0:-y | 8/8 |  | 0.1 | 1.6 |
| gripper | dev_309 | v1 | success | through_slot:wall_0:0:pitched-90, around:wall_0:+y | 5/8 | constraint_violated | 0.1 | 2.1 |
| gripper | dev_309 | v2 | success | around:wall_0:+y, through_slot:wall_0:0:pitched-90 | 8/8 |  | 0.1 | 1.8 |
| arm | ref_demo | v1 | success | around:wall_0:+y | 8/8 | success | 0.4 | 2.1 |
| arm | ref_demo | v2 | success | around:wall_0:+y | 8/8 |  | 0.4 | 2.2 |
| arm | ref_shortcut | v1 | success | around:wall_0:+y | 8/8 | success | 1.2 | 3.3 |
| arm | ref_shortcut | v2 | success | around:wall_0:+y | 8/8 |  | 2.4 | 4.6 |
| arm | ref_blocked | v1 | planning_failed (not proven: arm_motion_planning_failure) | - | - |  | 1.2 | 1.2 |
| arm | ref_blocked | v2 | planning_failed (proven) | - | - |  | 0.1 | 0.1 |
| arm | dev_300 | v1 | success | around:wall_0:-y | 8/8 | success | 3.1 | 5.3 |
| arm | dev_300 | v2 | success | around:wall_0:-y | 8/8 |  | 3.4 | 5.8 |
| arm | dev_301 | v1 | success | around:wall_0:-y | 8/8 | success | 1.6 | 3.1 |
| arm | dev_301 | v2 | success | around:wall_0:-y | 8/8 |  | 2.7 | 4.3 |
| arm | dev_302 | v1 | success | around:wall_0:-y | 8/8 | success | 2.1 | 3.7 |
| arm | dev_302 | v2 | success | around:wall_0:-y | 8/8 |  | 2.1 | 3.8 |
| arm | dev_303 | v1 | success | around:wall_0:-y | 8/8 | success | 1.9 | 3.9 |
| arm | dev_303 | v2 | success | around:wall_0:-y | 8/8 |  | 2.8 | 4.7 |
| arm | dev_304 | v1 | success | around:wall_0:+y | 8/8 | success | 2.4 | 4.1 |
| arm | dev_304 | v2 | success | around:wall_0:+y | 8/8 |  | 2.0 | 3.6 |
| arm | dev_305 | v1 | success | around:wall_0:-y | 8/8 | success | 4.2 | 5.7 |
| arm | dev_305 | v2 | success | around:wall_0:-y | 8/8 |  | 4.4 | 5.9 |
| arm | dev_306 | v1 | success | around:wall_0:-y | 8/8 | success | 1.8 | 3.3 |
| arm | dev_306 | v2 | success | around:wall_0:-y | 8/8 |  | 4.6 | 6.2 |
| arm | dev_307 | v1 | success | direct, around:wall_0:+y | 8/8 | success | 1.5 | 3.5 |
| arm | dev_307 | v2 | success | direct, around:wall_0:+y | 8/8 |  | 1.6 | 3.6 |
| arm | dev_308 | v1 | success | around:wall_0:-y | 8/8 | success | 1.9 | 3.7 |
| arm | dev_308 | v2 | success | around:wall_0:-y | 8/8 |  | 0.6 | 2.3 |
| arm | dev_309 | v1 | success | around:wall_0:+y | 8/8 | success | 2.1 | 3.7 |
| arm | dev_309 | v2 | success | around:wall_0:+y | 8/8 |  | 2.6 | 4.3 |
