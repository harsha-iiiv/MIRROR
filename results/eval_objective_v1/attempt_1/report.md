# Registration objective_v1: planner objective and baselines (attempt_1)

## Conclusions (attempt_1, the only attempt)

**How much of the original benefit depends on the 0.05 objective: all of it, on these layout families.**
With the rotation price calibrated to execution time on development layouts (0.2445 m/rad), the
original specification never carries the cup on a tilted route: 0/80 primary and 0/40 shortcut
layouts, against 31/80 and 40/40 at 0.05. Its plans are identical to the corrected specification's
on all 120 layouts, so violation-free success is 80/80 and 40/40 for both and the retained benefit is
0.000 in both strata. The earlier registrations' effect does replicate on these fresh layouts when
both specifications are planned at 0.05 (primary 49/80 → 80/80, shortcut 0/40 → 40/40,
exact McNemar p = 9.3e-10 and 1.8e-12), so the difference comes from the objective, not from new
layouts.

**The sweep.** Cup tilted / violation-free (primary; shortcut): 0.05 → 31, 49/80; 40, 0/40.
0.10 → 19, 61/80; 27, 13/40. 0.20 → 2, 78/80; 5, 35/40. 0.2445, 0.40 and 1.0 → 0, 80/80; 0, 40/40.
Every tilted run lost beads (2 to 6 of 8; 124 tilted runs in the two strata). Post hoc and plan-only: at the bounds of the calibration's
bootstrap 95% CI (0.225 and 0.269) the original specification tilts the cup on 3/120 and 0/120 of
these layouts (1/90 and 0/90 on the Muse registration's layouts).

**Time.** The 0.05 objective is also slow: at the calibrated price the original specification
finishes 3.2 s (primary) and 6.5 s (shortcut) sooner on average than at 0.05, and the corrected
specification 1.7 s and 3.0 s sooner than corrected@0.05, because 0.05 also sends the block, the
marker and the empty gripper through slots with slow wrist rotations. On development layouts the
fastest cup route was never a tilted one (0/60).

**The never-tilt baseline costs almost nothing here, and fails where tilting is the only way.** On
the primary and shortcut strata it matches the corrected specification on 119/120 layouts; on one
(seed 82650) forcing the marker around the wall instead of through the slot made the run 2.0 s
slower, the only case where a harmless tilt was given up. On the 10 blocked layouts (the slot is
the only opening) never-tilt and both corrected conditions plan nothing, each with a proven
infeasibility record, while the original specification completes 10/10 at every rotation price but
spills 38 beads per condition (2 to 6 per run; the same plan at every price).

**What the correction still shows.** On these families, with a time-calibrated objective, the rule
"keep the cup upright in transport" does not change behaviour on any layout where an upright route
exists; the earlier violation-free-success gains measure the 0.05 objective as much as the
correction. The rule's remaining, measurable effect is on layouts where only a tilted route
exists: it turns a spill into a refusal with a proof. Hypotheses H1, H2 and H3 all held.

Limits: one controller and simulated timing; the price is calibrated to time only (it does not price
spill risk); one specification pair; the blocked stratum has 10 layouts.

Frozen 2026-11-03T02:28:45.345118+00:00 (commit of `freeze.json` precedes every layout); run from `8be4d2efb4c4`, attempt `attempt_1`, status finished.
Calibrated rotation price (objective of record): **0.2445 m/rad** (from development layouts only; see calibration/calibration.json).

## Stratum: primary (80 layouts)

| condition | completed | violation-free | cup tilted (planned) | beads lost (trials) | planning failures (proven) | exec time s (completed) | path cost @cal |
|---|---|---|---|---|---|---|---|
| original@0.2445 | 80/80 | 80/80 | 0 | 0 (0) | 0 (0) | 49.89 | 5.467 |
| original@0.05 | 80/80 | 49/80 | 31 | 125 (31) | 0 (0) | 53.08 | 5.994 |
| original@0.10 | 80/80 | 61/80 | 19 | 76 (19) | 0 (0) | 51.27 | 5.715 |
| original@0.20 | 80/80 | 78/80 | 2 | 10 (2) | 0 (0) | 49.90 | 5.475 |
| original@0.40 | 80/80 | 80/80 | 0 | 0 (0) | 0 (0) | 49.91 | 5.467 |
| original@1.0 | 80/80 | 80/80 | 0 | 0 (0) | 0 (0) | 49.91 | 5.467 |
| never_tilt@0.2445 | 80/80 | 80/80 | 0 | 0 (0) | 0 (0) | 49.91 | 5.467 |
| corrected@0.2445 | 80/80 | 80/80 | 0 | 0 (0) | 0 (0) | 49.89 | 5.467 |
| corrected@0.05 | 80/80 | 80/80 | 0 | 0 (0) | 0 (0) | 51.61 | 5.837 |

Violation-free success, paired (A vs B; only_a / only_b; exact McNemar p):

- corrected@cal vs original@cal: 0 / 0, diff +0.000 [+0.000, +0.000], p = 1
- corrected@0.05 vs original@0.05: 0 / 31, diff +0.388 [+0.275, +0.500], p = 9.31e-10
- corrected@cal vs original@0.05: 0 / 31, diff +0.388 [+0.275, +0.500], p = 9.31e-10
- corrected@cal vs original@0.10: 0 / 19, diff +0.237 [+0.150, +0.338], p = 3.81e-06
- corrected@cal vs original@0.20: 0 / 2, diff +0.025 [+0.000, +0.062], p = 0.5
- corrected@cal vs original@0.40: 0 / 0, diff +0.000 [+0.000, +0.000], p = 1
- corrected@cal vs original@1.0: 0 / 0, diff +0.000 [+0.000, +0.000], p = 1
- never_tilt@cal vs original@cal: 0 / 0, diff +0.000 [+0.000, +0.000], p = 1

Retained benefit at the calibrated price: 0.000 [0.000, 0.000] (gap +0.000 at cal vs +0.388 at 0.05)

Execution time, paired (seeds where both completed):

- corrected@cal - original@cal: mean +0.00 s [+0.00, +0.00] over 80 (faster 0, equal 80, slower 0)
- never_tilt@cal - corrected@cal: mean +0.03 s [+0.00, +0.08] over 80 (faster 0, equal 79, slower 1)
- never_tilt@cal - original@cal: mean +0.03 s [+0.00, +0.08] over 80 (faster 0, equal 79, slower 1)
- corrected@0.05 - original@0.05: mean -1.47 s [-1.93, -1.03] over 80 (faster 31, equal 49, slower 0)
- corrected@cal - corrected@0.05: mean -1.72 s [-2.45, -1.06] over 80 (faster 30, equal 47, slower 3)

Trade-offs (seeds):

- corrected@0.2445 vs never_tilt@0.2445: only corrected@0.2445 completes []; only never_tilt@0.2445 completes []
- original@0.2445 vs never_tilt@0.2445: only original@0.2445 completes []; only never_tilt@0.2445 completes []
- original@0.2445 vs corrected@0.2445: only original@0.2445 completes []; only corrected@0.2445 completes []
- original@1.0 vs never_tilt@0.2445: only original@1.0 completes []; only never_tilt@0.2445 completes []

Identical plans: {'original@0.2445 == original@0.05': 40, 'original@0.2445 == original@0.10': 47, 'original@0.2445 == original@0.20': 73, 'original@0.2445 == corrected@0.2445': 80, 'corrected@0.2445 == corrected@0.05': 47, 'corrected@0.2445 == never_tilt@0.2445': 79, 'original@1.0 == never_tilt@0.2445': 80}

## Stratum: shortcut (40 layouts)

| condition | completed | violation-free | cup tilted (planned) | beads lost (trials) | planning failures (proven) | exec time s (completed) | path cost @cal |
|---|---|---|---|---|---|---|---|
| original@0.2445 | 40/40 | 40/40 | 0 | 0 (0) | 0 (0) | 53.00 | 5.881 |
| original@0.05 | 40/40 | 0/40 | 40 | 153 (40) | 0 (0) | 59.47 | 6.908 |
| original@0.10 | 40/40 | 13/40 | 27 | 102 (27) | 0 (0) | 56.54 | 6.437 |
| original@0.20 | 40/40 | 35/40 | 5 | 19 (5) | 0 (0) | 53.05 | 5.904 |
| original@0.40 | 40/40 | 40/40 | 0 | 0 (0) | 0 (0) | 53.00 | 5.881 |
| original@1.0 | 40/40 | 40/40 | 0 | 0 (0) | 0 (0) | 53.00 | 5.881 |
| never_tilt@0.2445 | 40/40 | 40/40 | 0 | 0 (0) | 0 (0) | 53.00 | 5.881 |
| corrected@0.2445 | 40/40 | 40/40 | 0 | 0 (0) | 0 (0) | 53.00 | 5.881 |
| corrected@0.05 | 40/40 | 40/40 | 0 | 0 (0) | 0 (0) | 56.00 | 6.542 |

Violation-free success, paired (A vs B; only_a / only_b; exact McNemar p):

- corrected@cal vs original@cal: 0 / 0, diff +0.000 [+0.000, +0.000], p = 1
- corrected@0.05 vs original@0.05: 0 / 40, diff +1.000 [+1.000, +1.000], p = 1.82e-12
- corrected@cal vs original@0.05: 0 / 40, diff +1.000 [+1.000, +1.000], p = 1.82e-12
- corrected@cal vs original@0.10: 0 / 27, diff +0.675 [+0.525, +0.800], p = 1.49e-08
- corrected@cal vs original@0.20: 0 / 5, diff +0.125 [+0.025, +0.225], p = 0.0625
- corrected@cal vs original@0.40: 0 / 0, diff +0.000 [+0.000, +0.000], p = 1
- corrected@cal vs original@1.0: 0 / 0, diff +0.000 [+0.000, +0.000], p = 1
- never_tilt@cal vs original@cal: 0 / 0, diff +0.000 [+0.000, +0.000], p = 1

Retained benefit at the calibrated price: 0.000 [0.000, 0.000] (gap +0.000 at cal vs +1.000 at 0.05)

Execution time, paired (seeds where both completed):

- corrected@cal - original@cal: mean +0.00 s [+0.00, +0.00] over 40 (faster 0, equal 40, slower 0)
- never_tilt@cal - corrected@cal: mean +0.00 s [+0.00, +0.00] over 40 (faster 0, equal 40, slower 0)
- never_tilt@cal - original@cal: mean +0.00 s [+0.00, +0.00] over 40 (faster 0, equal 40, slower 0)
- corrected@0.05 - original@0.05: mean -3.48 s [-3.88, -3.06] over 40 (faster 39, equal 0, slower 1)
- corrected@cal - corrected@0.05: mean -3.00 s [-4.05, -1.92] over 40 (faster 26, equal 9, slower 5)

Trade-offs (seeds):

- corrected@0.2445 vs never_tilt@0.2445: only corrected@0.2445 completes []; only never_tilt@0.2445 completes []
- original@0.2445 vs never_tilt@0.2445: only original@0.2445 completes []; only never_tilt@0.2445 completes []
- original@0.2445 vs corrected@0.2445: only original@0.2445 completes []; only corrected@0.2445 completes []
- original@1.0 vs never_tilt@0.2445: only original@1.0 completes []; only never_tilt@0.2445 completes []

Identical plans: {'original@0.2445 == original@0.05': 0, 'original@0.2445 == original@0.10': 5, 'original@0.2445 == original@0.20': 31, 'original@0.2445 == corrected@0.2445': 40, 'corrected@0.2445 == corrected@0.05': 9, 'corrected@0.2445 == never_tilt@0.2445': 40, 'original@1.0 == never_tilt@0.2445': 40}

## Stratum: blocked (10 layouts)

| condition | completed | violation-free | cup tilted (planned) | beads lost (trials) | planning failures (proven) | exec time s (completed) | path cost @cal |
|---|---|---|---|---|---|---|---|
| original@0.2445 | 10/10 | 0/10 | 10 | 38 (10) | 0 (0) | 70.71 | 8.908 |
| original@0.05 | 10/10 | 0/10 | 10 | 38 (10) | 0 (0) | 70.71 | 8.908 |
| original@0.10 | 10/10 | 0/10 | 10 | 38 (10) | 0 (0) | 70.71 | 8.908 |
| original@0.20 | 10/10 | 0/10 | 10 | 38 (10) | 0 (0) | 70.71 | 8.908 |
| original@0.40 | 10/10 | 0/10 | 10 | 38 (10) | 0 (0) | 70.71 | 8.908 |
| original@1.0 | 10/10 | 0/10 | 10 | 38 (10) | 0 (0) | 70.71 | 8.908 |
| never_tilt@0.2445 | 0/10 | 0/10 | 0 | 0 (0) | 10 (10) | – | – |
| corrected@0.2445 | 0/10 | 0/10 | 0 | 0 (0) | 10 (10) | – | – |
| corrected@0.05 | 0/10 | 0/10 | 0 | 0 (0) | 10 (10) | – | – |

Violation-free success, paired (A vs B; only_a / only_b; exact McNemar p):

- corrected@cal vs original@cal: 0 / 0, diff +0.000 [+0.000, +0.000], p = 1
- corrected@0.05 vs original@0.05: 0 / 0, diff +0.000 [+0.000, +0.000], p = 1
- corrected@cal vs original@0.05: 0 / 0, diff +0.000 [+0.000, +0.000], p = 1
- corrected@cal vs original@0.10: 0 / 0, diff +0.000 [+0.000, +0.000], p = 1
- corrected@cal vs original@0.20: 0 / 0, diff +0.000 [+0.000, +0.000], p = 1
- corrected@cal vs original@0.40: 0 / 0, diff +0.000 [+0.000, +0.000], p = 1
- corrected@cal vs original@1.0: 0 / 0, diff +0.000 [+0.000, +0.000], p = 1
- never_tilt@cal vs original@cal: 0 / 0, diff +0.000 [+0.000, +0.000], p = 1

Retained benefit at the calibrated price: no benefit at 0.05 in this stratum; ratio undefined (gap +0.000 at cal vs +0.000 at 0.05)

Execution time, paired (seeds where both completed):


Trade-offs (seeds):

- corrected@0.2445 vs never_tilt@0.2445: only corrected@0.2445 completes []; only never_tilt@0.2445 completes []
- original@0.2445 vs never_tilt@0.2445: only original@0.2445 completes [82600, 82601, 82602, 82603, 82604, 82605, 82606, 82607, 82608, 82609]; only never_tilt@0.2445 completes []
- original@0.2445 vs corrected@0.2445: only original@0.2445 completes [82600, 82601, 82602, 82603, 82604, 82605, 82606, 82607, 82608, 82609]; only corrected@0.2445 completes []
- original@1.0 vs never_tilt@0.2445: only original@1.0 completes [82600, 82601, 82602, 82603, 82604, 82605, 82606, 82607, 82608, 82609]; only never_tilt@0.2445 completes []

Identical plans: {'original@0.2445 == original@0.05': 10, 'original@0.2445 == original@0.10': 10, 'original@0.2445 == original@0.20': 10, 'original@0.2445 == corrected@0.2445': 0, 'corrected@0.2445 == corrected@0.05': 0, 'corrected@0.2445 == never_tilt@0.2445': 0, 'original@1.0 == never_tilt@0.2445': 0}

