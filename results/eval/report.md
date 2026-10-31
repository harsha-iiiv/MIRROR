# MIRROR pre-registered evaluation

> Every attempt in every stratum is reported; nothing is filtered by outcome.

## Pre-registration

- Freeze manifest written 2026-10-31T13:50:32.746526+00:00, before any of these layouts was generated.
- Code sha256 `b498c4b41664c589` over 65 source files.
- Spec A (original) `spec_demo_ref_demo_reference` v2 `546ca656ea2952f2`; spec B (corrected) v3 `baafa260a6834153`.

- Specifications: replay of recorded model responses on the pinned reference demonstration; interpreter `gpt-5.5-2026-04-23`; offline interpreter agrees: True.
- Correction text: "This container must remain upright throughout transport, not just at the destination."

- Also frozen: scripts `f9420b45f770` (3 files); data/demos `ce178018db38` (1 files); data/cassettes `cc02ce78ccfa` (238 files).
- Runtime: python 3.11.18, numpy 2.4.6, mujoco 3.17.0, scipy 1.17.1.

| stratum | role | split | family | seeds | n |
|---|---|---|---|---|---|
| primary | primary | eval | mixed | 30000-30059 | 60 |
| shortcut | stratum | eval | shortcut | 40000-40029 | 30 |
| blocked | blocked | blocked | mixed | 30000-30004 | 5 |

## Results side by side

| Metric | primary: A / B | shortcut: A / B | blocked: A / B |
|---|---|---|---|
| Scenes | 60 / 60 | 30 / 30 | 5 / 5 |
| Executed in simulation | 60 / 60 | 30 / 30 | 5 / 0 |
| Task completion | 60 / 60 | 30 / 30 | 5 / 0 |
| Violation-free success | 27 / 60 | 4 / 30 | 0 / 0 |
| Trials violating corrected rule | 33 / 0 | 26 / 0 | 5 / 0 |
| Max corrected-object tilt (deg) | 89.8 / 0.3 | 89.8 / 0.3 | 89.7 / n/a |
| Beads lost (total) | 119 / 0 | 106 / 0 | 22 / 0 |
| Obstacle- or floor-contact trials | 0 / 0 | 0 / 0 | 0 / 0 |
| Planning failures | 0 / 0 | 0 / 0 | 0 / 5 |
| Execution failures | 0 / 0 | 0 / 0 | 0 / 0 |
| Timeouts | 0 / 0 | 0 / 0 | 0 / 0 |
| Feasible scenes wrongly rejected | 0/33 / 0/33 | 0/17 / 0/17 | 0/0 / 0/0 |

## Paired statistics per stratum

**primary** (primary): correction exercised in 33/60 scenes. Violation-free success A 0.45, B 1.00; difference +0.55 (bootstrap 95% CI [0.42, 0.68]); pairs only-A 0, only-B 33; exact McNemar p = 2.328e-10. 33 discordant pair(s) of 60. Scope: 32/32 unrelated-object tilted routes kept under the corrected spec. [Details](primary/report.md)

**shortcut** (stratum): correction exercised in 26/30 scenes. Violation-free success A 0.13, B 1.00; difference +0.87 (bootstrap 95% CI [0.73, 0.97]); pairs only-A 0, only-B 26; exact McNemar p = 2.98e-08. 26 discordant pair(s) of 30. Scope: 25/25 unrelated-object tilted routes kept under the corrected spec. [Details](shortcut/report.md)

**blocked** (blocked): correction exercised in 5/5 scenes. Violation-free success A 0.00, B 0.00; difference +0.00 (bootstrap 95% CI [0.00, 0.00]); pairs only-A 0, only-B 0; exact McNemar p = 1. 0 discordant pair(s) of 5; exact McNemar needs at least 6 discordant pairs all in one direction to reach p < 0.05, so this sample cannot establish significance either way. Scope: no unrelated object took a tilted route in the original runs; scope not exercised. [Details](blocked/report.md)

## Blocked stratum (blocked)

| seed | A status | B status | B classification | independent analysis (B) |
|---|---|---|---|---|
| 30000 | success | planning_failed | proven_infeasible | proven_infeasible |
| 30001 | success | planning_failed | proven_infeasible | proven_infeasible |
| 30002 | success | planning_failed | proven_infeasible | proven_infeasible |
| 30003 | success | planning_failed | proven_infeasible | proven_infeasible |
| 30004 | success | planning_failed | proven_infeasible | proven_infeasible |

## Blocked reference scene

| condition | status | classification | independent |
|---|---|---|---|
| original | success | - | unknown |
| corrected | planning_failed | proven_infeasible | proven_infeasible |

## Evaluation-layout ledger

Ledger `results/eval/ledger.jsonl`: 95 entries in total, 95 for frozen seeds, 0 of them before the freeze (freeze refuses to run if this is not zero). At freeze time the ledger had 0 entries and none for frozen seeds.

<details><summary>Ledger entries for frozen seeds</summary>

| seed | split | family | unix time | caller |
|---|---|---|---|---|
| 30000 | eval | mixed | 1793454644.4189684 | mirror.evaluate.paired |
| 30001 | eval | mixed | 1793454644.5379682 | mirror.evaluate.paired |
| 30002 | eval | mixed | 1793454644.7073388 | mirror.evaluate.paired |
| 30003 | eval | mixed | 1793454644.750763 | mirror.evaluate.paired |
| 30004 | eval | mixed | 1793454644.8150625 | mirror.evaluate.paired |
| 30005 | eval | mixed | 1793454644.8963113 | mirror.evaluate.paired |
| 30006 | eval | mixed | 1793454644.938386 | mirror.evaluate.paired |
| 30007 | eval | mixed | 1793454645.0095696 | mirror.evaluate.paired |
| 30008 | eval | mixed | 1793454645.099428 | mirror.evaluate.paired |
| 30009 | eval | mixed | 1793454645.3055408 | mirror.evaluate.paired |
| 30010 | eval | mixed | 1793454645.425701 | mirror.evaluate.paired |
| 30011 | eval | mixed | 1793454645.470335 | mirror.evaluate.paired |
| 30012 | eval | mixed | 1793454645.538178 | mirror.evaluate.paired |
| 30013 | eval | mixed | 1793454645.6155586 | mirror.evaluate.paired |
| 30014 | eval | mixed | 1793454646.0027804 | mirror.evaluate.paired |
| 30015 | eval | mixed | 1793454646.1056159 | mirror.evaluate.paired |
| 30016 | eval | mixed | 1793454646.15692 | mirror.evaluate.paired |
| 30017 | eval | mixed | 1793454646.2233596 | mirror.evaluate.paired |
| 30018 | eval | mixed | 1793454646.3013215 | mirror.evaluate.paired |
| 30019 | eval | mixed | 1793454646.3480604 | mirror.evaluate.paired |
| 30020 | eval | mixed | 1793454646.455925 | mirror.evaluate.paired |
| 30021 | eval | mixed | 1793454646.6539333 | mirror.evaluate.paired |
| 30022 | eval | mixed | 1793454646.7337956 | mirror.evaluate.paired |
| 30023 | eval | mixed | 1793454646.7744186 | mirror.evaluate.paired |
| 30024 | eval | mixed | 1793454646.8190708 | mirror.evaluate.paired |
| 30025 | eval | mixed | 1793454646.9132042 | mirror.evaluate.paired |
| 30026 | eval | mixed | 1793454646.9615483 | mirror.evaluate.paired |
| 30027 | eval | mixed | 1793454647.21642 | mirror.evaluate.paired |
| 30028 | eval | mixed | 1793454647.3285277 | mirror.evaluate.paired |
| 30029 | eval | mixed | 1793454647.5168438 | mirror.evaluate.paired |
| 30030 | eval | mixed | 1793454647.6192508 | mirror.evaluate.paired |
| 30031 | eval | mixed | 1793454647.7029905 | mirror.evaluate.paired |
| 30032 | eval | mixed | 1793454647.7562609 | mirror.evaluate.paired |
| 30033 | eval | mixed | 1793454647.9216614 | mirror.evaluate.paired |
| 30034 | eval | mixed | 1793454648.035803 | mirror.evaluate.paired |
| 30035 | eval | mixed | 1793454648.0971873 | mirror.evaluate.paired |
| 30036 | eval | mixed | 1793454648.1402886 | mirror.evaluate.paired |
| 30037 | eval | mixed | 1793454648.3344934 | mirror.evaluate.paired |
| 30038 | eval | mixed | 1793454648.4630692 | mirror.evaluate.paired |
| 30039 | eval | mixed | 1793454648.5267243 | mirror.evaluate.paired |
| 30040 | eval | mixed | 1793454648.6213527 | mirror.evaluate.paired |
| 30041 | eval | mixed | 1793454648.7342792 | mirror.evaluate.paired |
| 30042 | eval | mixed | 1793454648.8211768 | mirror.evaluate.paired |
| 30043 | eval | mixed | 1793454648.8468733 | mirror.evaluate.paired |
| 30044 | eval | mixed | 1793454648.932458 | mirror.evaluate.paired |
| 30045 | eval | mixed | 1793454649.0399647 | mirror.evaluate.paired |
| 30046 | eval | mixed | 1793454649.1351392 | mirror.evaluate.paired |
| 30047 | eval | mixed | 1793454649.2098494 | mirror.evaluate.paired |
| 30048 | eval | mixed | 1793454649.2618425 | mirror.evaluate.paired |
| 30049 | eval | mixed | 1793454649.3216796 | mirror.evaluate.paired |
| 30050 | eval | mixed | 1793454649.3752124 | mirror.evaluate.paired |
| 30051 | eval | mixed | 1793454649.4328403 | mirror.evaluate.paired |
| 30052 | eval | mixed | 1793454649.5335002 | mirror.evaluate.paired |
| 30053 | eval | mixed | 1793454649.6142883 | mirror.evaluate.paired |
| 30054 | eval | mixed | 1793454649.6721227 | mirror.evaluate.paired |
| 30055 | eval | mixed | 1793454649.7218628 | mirror.evaluate.paired |
| 30056 | eval | mixed | 1793454649.8046336 | mirror.evaluate.paired |
| 30057 | eval | mixed | 1793454649.8517168 | mirror.evaluate.paired |
| 30058 | eval | mixed | 1793454649.9279287 | mirror.evaluate.paired |
| 30059 | eval | mixed | 1793454650.015347 | mirror.evaluate.paired |
| 40000 | eval | shortcut | 1793455578.6667995 | mirror.evaluate.paired |
| 40001 | eval | shortcut | 1793455578.7563725 | mirror.evaluate.paired |
| 40002 | eval | shortcut | 1793455578.8143063 | mirror.evaluate.paired |
| 40003 | eval | shortcut | 1793455578.9431827 | mirror.evaluate.paired |
| 40004 | eval | shortcut | 1793455579.0272534 | mirror.evaluate.paired |
| 40005 | eval | shortcut | 1793455579.1110585 | mirror.evaluate.paired |
| 40006 | eval | shortcut | 1793455579.1455686 | mirror.evaluate.paired |
| 40007 | eval | shortcut | 1793455579.2105792 | mirror.evaluate.paired |
| 40008 | eval | shortcut | 1793455579.2460592 | mirror.evaluate.paired |
| 40009 | eval | shortcut | 1793455579.3483346 | mirror.evaluate.paired |
| 40010 | eval | shortcut | 1793455579.5059175 | mirror.evaluate.paired |
| 40011 | eval | shortcut | 1793455579.5595655 | mirror.evaluate.paired |
| 40012 | eval | shortcut | 1793455579.8090436 | mirror.evaluate.paired |
| 40013 | eval | shortcut | 1793455579.901341 | mirror.evaluate.paired |
| 40014 | eval | shortcut | 1793455579.9787161 | mirror.evaluate.paired |
| 40015 | eval | shortcut | 1793455580.0173848 | mirror.evaluate.paired |
| 40016 | eval | shortcut | 1793455580.0446916 | mirror.evaluate.paired |
| 40017 | eval | shortcut | 1793455580.10454 | mirror.evaluate.paired |
| 40018 | eval | shortcut | 1793455580.2581503 | mirror.evaluate.paired |
| 40019 | eval | shortcut | 1793455580.421215 | mirror.evaluate.paired |
| 40020 | eval | shortcut | 1793455580.5118701 | mirror.evaluate.paired |
| 40021 | eval | shortcut | 1793455580.7330718 | mirror.evaluate.paired |
| 40022 | eval | shortcut | 1793455580.823973 | mirror.evaluate.paired |
| 40023 | eval | shortcut | 1793455581.0189946 | mirror.evaluate.paired |
| 40024 | eval | shortcut | 1793455581.119326 | mirror.evaluate.paired |
| 40025 | eval | shortcut | 1793455581.2775323 | mirror.evaluate.paired |
| 40026 | eval | shortcut | 1793455581.3296416 | mirror.evaluate.paired |
| 40027 | eval | shortcut | 1793455581.4488032 | mirror.evaluate.paired |
| 40028 | eval | shortcut | 1793455581.5984423 | mirror.evaluate.paired |
| 40029 | eval | shortcut | 1793455581.7038238 | mirror.evaluate.paired |
| 30000 | blocked | mixed | 1793456077.8315077 | mirror.evaluate.paired |
| 30001 | blocked | mixed | 1793456077.873583 | mirror.evaluate.paired |
| 30002 | blocked | mixed | 1793456077.9260328 | mirror.evaluate.paired |
| 30003 | blocked | mixed | 1793456077.978595 | mirror.evaluate.paired |
| 30004 | blocked | mixed | 1793456078.0559728 | mirror.evaluate.paired |

</details>

## Rollout selection

Rollout JSON is committed for every seed where either condition carried an object covered by the corrected rule above that rule's tilt tolerance (15 degrees for the cup) or did not end in success or touched an obstacle; for every seed of the blocked stratum; and for every 5th seed of each stratum in seed order (indices 0, 5, 10, ...) regardless of outcome. All reports and metrics cover every seed.

- primary: 37 of 60 seeds (30000, 30002, 30004, 30005, 30006, 30008, 30010, 30012, 30015, 30017, 30018, 30019, 30020, 30021, 30022, 30025, 30026, 30027, 30028, 30029, 30030, 30031, 30034, 30035, 30040, 30043, 30045, 30046, 30047, 30048, 30050, 30051, 30052, 30053, 30055, 30057, 30058)
- shortcut: 29 of 30 seeds (40000, 40001, 40002, 40003, 40004, 40005, 40006, 40007, 40008, 40009, 40010, 40011, 40012, 40014, 40015, 40016, 40017, 40018, 40019, 40020, 40021, 40022, 40023, 40024, 40025, 40026, 40027, 40028, 40029)
- blocked: 5 of 5 seeds (30000-30004)

Wall time 1508 s. Finite simulated trials; not formal verification and not evidence of physical deployment.
