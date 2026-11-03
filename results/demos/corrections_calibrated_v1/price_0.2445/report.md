# Correction capability demonstrations (development layouts)

Planner rotation price 0.2445 m/rad. Platform: Linux x86_64 (Linux-6.12.94+-x86_64-with-glibc2.39).

Offline interpreter (no model call), MuJoCo execution, independent checker. Development seeds only. On each unseen development layout the old specification and the new one are both executed; the old rollout is then re-checked under the new specification. Columns: old rollouts that violate the new rule; new rollouts that succeed. A relaxation is not expected to make old rollouts fail. Every rollout is reported; none is filtered. Per-layout details are in each case's summary.json.

| Case | Old run | Old run re-checked under new spec | New run | Unseen layouts: old run violates new rule | Unseen layouts: new run succeeds |
|---|---|---|---|---|---|
| object_scope | success | success | success | 0/6 | 6/6 |
| phase_scope | success | success | success | 0/6 | 6/6 |
| ordering | success | constraint_violated | success | 6/6 | 6/6 |
| ordering_start | success | constraint_violated | success | 6/6 | 6/6 |
| clearance | success | constraint_violated | success | 6/6 | 6/6 |
| multi_turn | success | constraint_violated | success | 6/6 | 6/6 |
| blocked_refusal | success | constraint_violated | planning_failed (Proven infeasible under these stated assumptions) | 3/3 | 0/3 succeed; 3/3 refused with a proof |
| ambiguous | clarify: I can keep the cup upright (while carried, once placed, or always), keep it a set distance from the wall and other obstacles, stop it touching obstacles, or change the packing order. Which of these matters for the cup? | | | | |
| unsupported | clarify: I can keep the cup a set distance from the wall and other obstacles, but not from the marker. Should it keep its distance from the obstacles instead? | | | | |
