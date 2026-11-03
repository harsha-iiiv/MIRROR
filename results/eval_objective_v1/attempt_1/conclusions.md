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
