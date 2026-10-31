# MIRROR pre-registered evaluation, registration 'muse'

> Every attempt in every stratum is reported; nothing is filtered by outcome.

## Pre-registration

- Freeze manifest written 2026-10-31T16:34:38.113695+00:00, before any of these layouts was generated.
- Code sha256 `e77c4cec9c4d414d` over 59 source files (presentation-only paths not hashed: sim/render.py, studio/**, cli.py).
- Spec A (original) `spec_demo_ref_demo_reference` v2 `7b18030c5cbbadd3`; spec B (corrected) v3 `7ee3ac0153407424`.

- Specifications: replay of recorded model responses on the pinned reference demonstration; interpreter `muse-spark-1.3`; offline interpreter agrees: True.
- Correction text: "This container must remain upright throughout transport, not just at the destination."

- Also frozen: scripts `3121ebb78f08` (3 files); data/demos `ce178018db38` (1 files); data/cassettes `cc02ce78ccfa` (238 files); data/cassettes_muse `bb07c5fd123e` (74 files).
- Runtime: python 3.11.18, numpy 2.4.6, mujoco 3.17.0, scipy 1.17.1.

| stratum | role | split | family | seeds | n |
|---|---|---|---|---|---|
| primary | primary | eval | mixed | 50000-50059 | 60 |
| shortcut | stratum | eval | shortcut | 60000-60029 | 30 |
| blocked | blocked | blocked | mixed | 50000-50004 | 5 |

## Results side by side

| Metric | primary: A / B | shortcut: A / B | blocked: A / B |
|---|---|---|---|
| Scenes | 60 / 60 | 30 / 30 | 5 / 5 |
| Executed in simulation | 60 / 60 | 30 / 30 | 5 / 0 |
| Task completion | 60 / 60 | 30 / 30 | 5 / 0 |
| Violation-free success | 33 / 60 | 3 / 30 | 0 / 0 |
| Trials violating corrected rule | 27 / 0 | 27 / 0 | 5 / 0 |
| Max corrected-object tilt (deg) | 89.7 / 0.3 | 89.7 / 0.3 | 89.7 / n/a |
| Beads lost (total) | 104 / 0 | 103 / 0 | 21 / 0 |
| Obstacle- or floor-contact trials | 0 / 0 | 0 / 0 | 0 / 0 |
| Planning failures | 0 / 0 | 0 / 0 | 0 / 5 |
| Execution failures | 0 / 0 | 0 / 0 | 0 / 0 |
| Timeouts | 0 / 0 | 0 / 0 | 0 / 0 |
| Feasible scenes wrongly rejected | 0/40 / 0/40 | 0/21 / 0/21 | 0/0 / 0/0 |

## Paired statistics per stratum

**primary** (primary): correction exercised in 27/60 scenes. Violation-free success A 0.55, B 1.00; difference +0.45 (bootstrap 95% CI [0.32, 0.57]); pairs only-A 0, only-B 27; exact McNemar p = 1.49e-08. 27 discordant pair(s) of 60. Scope: 33/33 unrelated-object tilted routes kept under the corrected spec. [Details](primary/report.md)

**shortcut** (stratum): correction exercised in 27/30 scenes. Violation-free success A 0.10, B 1.00; difference +0.90 (bootstrap 95% CI [0.77, 1.00]); pairs only-A 0, only-B 27; exact McNemar p = 1.49e-08. 27 discordant pair(s) of 30. Scope: 23/23 unrelated-object tilted routes kept under the corrected spec. [Details](shortcut/report.md)

**blocked** (blocked): correction exercised in 5/5 scenes. Violation-free success A 0.00, B 0.00; difference +0.00 (bootstrap 95% CI [0.00, 0.00]); pairs only-A 0, only-B 0; exact McNemar p = 1. 0 discordant pair(s) of 5; exact McNemar needs at least 6 discordant pairs all in one direction to reach p < 0.05, so this sample cannot establish significance either way. Scope: no unrelated object took a tilted route in the original runs; scope not exercised. [Details](blocked/report.md)

## Blocked stratum (blocked)

| seed | A status | B status | B classification | independent analysis (B) |
|---|---|---|---|---|
| 50000 | success | planning_failed | proven_infeasible | proven_infeasible |
| 50001 | success | planning_failed | proven_infeasible | proven_infeasible |
| 50002 | success | planning_failed | proven_infeasible | proven_infeasible |
| 50003 | success | planning_failed | proven_infeasible | proven_infeasible |
| 50004 | success | planning_failed | proven_infeasible | proven_infeasible |

## Blocked reference scene

| condition | status | classification | independent |
|---|---|---|---|
| original | success | - | unknown |
| corrected | planning_failed | proven_infeasible | proven_infeasible |

## Evaluation-layout ledger

Ledger `results/eval_muse/ledger.jsonl`: 185 entries in total, 185 for frozen seeds, 0 of them before the freeze (freeze refuses to run if this is not zero). At freeze time the ledger had 0 entries and none for frozen seeds.

<details><summary>Ledger entries for frozen seeds</summary>

| seed | split | family | unix time | caller |
|---|---|---|---|---|
| 50000 | eval | mixed | 1793464636.0036738 | mirror.evaluate.paired |
| 50001 | eval | mixed | 1793464636.225229 | mirror.evaluate.paired |
| 50002 | eval | mixed | 1793464636.343017 | mirror.evaluate.paired |
| 50003 | eval | mixed | 1793464636.4335237 | mirror.evaluate.paired |
| 50004 | eval | mixed | 1793464636.5142033 | mirror.evaluate.paired |
| 50005 | eval | mixed | 1793464636.621226 | mirror.evaluate.paired |
| 50006 | eval | mixed | 1793464636.8227627 | mirror.evaluate.paired |
| 50007 | eval | mixed | 1793464637.014936 | mirror.evaluate.paired |
| 50008 | eval | mixed | 1793464637.313708 | mirror.evaluate.paired |
| 50009 | eval | mixed | 1793464637.4195638 | mirror.evaluate.paired |
| 50010 | eval | mixed | 1793464637.5349953 | mirror.evaluate.paired |
| 50011 | eval | mixed | 1793464637.7636316 | mirror.evaluate.paired |
| 50012 | eval | mixed | 1793464637.8659773 | mirror.evaluate.paired |
| 50013 | eval | mixed | 1793464638.0371046 | mirror.evaluate.paired |
| 50014 | eval | mixed | 1793464638.225376 | mirror.evaluate.paired |
| 50015 | eval | mixed | 1793464638.3464758 | mirror.evaluate.paired |
| 50016 | eval | mixed | 1793464638.747127 | mirror.evaluate.paired |
| 50017 | eval | mixed | 1793464638.8330684 | mirror.evaluate.paired |
| 50018 | eval | mixed | 1793464638.934256 | mirror.evaluate.paired |
| 50019 | eval | mixed | 1793464639.0416033 | mirror.evaluate.paired |
| 50020 | eval | mixed | 1793464639.1259804 | mirror.evaluate.paired |
| 50021 | eval | mixed | 1793464639.2084806 | mirror.evaluate.paired |
| 50022 | eval | mixed | 1793464639.4405682 | mirror.evaluate.paired |
| 50023 | eval | mixed | 1793464639.7130594 | mirror.evaluate.paired |
| 50024 | eval | mixed | 1793464639.802598 | mirror.evaluate.paired |
| 50025 | eval | mixed | 1793464640.0379453 | mirror.evaluate.paired |
| 50026 | eval | mixed | 1793464640.1424434 | mirror.evaluate.paired |
| 50027 | eval | mixed | 1793464640.332519 | mirror.evaluate.paired |
| 50028 | eval | mixed | 1793464640.4227808 | mirror.evaluate.paired |
| 50029 | eval | mixed | 1793464640.5211403 | mirror.evaluate.paired |
| 50030 | eval | mixed | 1793464640.6292198 | mirror.evaluate.paired |
| 50031 | eval | mixed | 1793464640.8258035 | mirror.evaluate.paired |
| 50032 | eval | mixed | 1793464640.9263918 | mirror.evaluate.paired |
| 50033 | eval | mixed | 1793464641.5348997 | mirror.evaluate.paired |
| 50034 | eval | mixed | 1793464641.6201103 | mirror.evaluate.paired |
| 50035 | eval | mixed | 1793464641.7170672 | mirror.evaluate.paired |
| 50036 | eval | mixed | 1793464641.8166852 | mirror.evaluate.paired |
| 50037 | eval | mixed | 1793464642.0010214 | mirror.evaluate.paired |
| 50038 | eval | mixed | 1793464642.3463008 | mirror.evaluate.paired |
| 50039 | eval | mixed | 1793464642.5608904 | mirror.evaluate.paired |
| 50040 | eval | mixed | 1793464642.63689 | mirror.evaluate.paired |
| 50041 | eval | mixed | 1793464642.9527671 | mirror.evaluate.paired |
| 50042 | eval | mixed | 1793464643.2204113 | mirror.evaluate.paired |
| 50043 | eval | mixed | 1793464643.3165543 | mirror.evaluate.paired |
| 50044 | eval | mixed | 1793464643.4152927 | mirror.evaluate.paired |
| 50045 | eval | mixed | 1793464643.5141127 | mirror.evaluate.paired |
| 50046 | eval | mixed | 1793464643.6628985 | mirror.evaluate.paired |
| 50047 | eval | mixed | 1793464643.742434 | mirror.evaluate.paired |
| 50048 | eval | mixed | 1793464644.0107143 | mirror.evaluate.paired |
| 50049 | eval | mixed | 1793464644.1189532 | mirror.evaluate.paired |
| 50050 | eval | mixed | 1793464644.307403 | mirror.evaluate.paired |
| 50051 | eval | mixed | 1793464644.4219398 | mirror.evaluate.paired |
| 50052 | eval | mixed | 1793464644.5654302 | mirror.evaluate.paired |
| 50053 | eval | mixed | 1793464644.6328325 | mirror.evaluate.paired |
| 50054 | eval | mixed | 1793464644.8466234 | mirror.evaluate.paired |
| 50055 | eval | mixed | 1793464645.0030215 | mirror.evaluate.paired |
| 50056 | eval | mixed | 1793464645.1303012 | mirror.evaluate.paired |
| 50057 | eval | mixed | 1793464645.248571 | mirror.evaluate.paired |
| 50058 | eval | mixed | 1793464645.3472857 | mirror.evaluate.paired |
| 50059 | eval | mixed | 1793464645.4253685 | mirror.evaluate.paired |
| 60000 | eval | shortcut | 1793465761.1547666 | mirror.evaluate.paired |
| 60001 | eval | shortcut | 1793465761.2508593 | mirror.evaluate.paired |
| 60002 | eval | shortcut | 1793465761.338294 | mirror.evaluate.paired |
| 60003 | eval | shortcut | 1793465761.4381347 | mirror.evaluate.paired |
| 60004 | eval | shortcut | 1793465761.512951 | mirror.evaluate.paired |
| 60005 | eval | shortcut | 1793465761.6047392 | mirror.evaluate.paired |
| 60006 | eval | shortcut | 1793465761.8162389 | mirror.evaluate.paired |
| 60007 | eval | shortcut | 1793465761.9187992 | mirror.evaluate.paired |
| 60008 | eval | shortcut | 1793465762.0124683 | mirror.evaluate.paired |
| 60009 | eval | shortcut | 1793465762.0546975 | mirror.evaluate.paired |
| 60010 | eval | shortcut | 1793465762.1619682 | mirror.evaluate.paired |
| 60011 | eval | shortcut | 1793465762.2395372 | mirror.evaluate.paired |
| 60012 | eval | shortcut | 1793465762.3803375 | mirror.evaluate.paired |
| 60013 | eval | shortcut | 1793465762.4422677 | mirror.evaluate.paired |
| 60014 | eval | shortcut | 1793465762.5319948 | mirror.evaluate.paired |
| 60015 | eval | shortcut | 1793465762.5932574 | mirror.evaluate.paired |
| 60016 | eval | shortcut | 1793465762.6478577 | mirror.evaluate.paired |
| 60017 | eval | shortcut | 1793465762.836381 | mirror.evaluate.paired |
| 60018 | eval | shortcut | 1793465762.8968067 | mirror.evaluate.paired |
| 60019 | eval | shortcut | 1793465763.0238647 | mirror.evaluate.paired |
| 60020 | eval | shortcut | 1793465763.102297 | mirror.evaluate.paired |
| 60021 | eval | shortcut | 1793465763.1490283 | mirror.evaluate.paired |
| 60022 | eval | shortcut | 1793465763.2082987 | mirror.evaluate.paired |
| 60023 | eval | shortcut | 1793465763.2546258 | mirror.evaluate.paired |
| 60024 | eval | shortcut | 1793465763.3480504 | mirror.evaluate.paired |
| 60025 | eval | shortcut | 1793465763.4403958 | mirror.evaluate.paired |
| 60026 | eval | shortcut | 1793465763.5088098 | mirror.evaluate.paired |
| 60027 | eval | shortcut | 1793465763.597026 | mirror.evaluate.paired |
| 60028 | eval | shortcut | 1793465763.6992123 | mirror.evaluate.paired |
| 60029 | eval | shortcut | 1793465763.781649 | mirror.evaluate.paired |
| 50000 | eval | mixed | 1793466949.22281 | mirror.evaluate.paired |
| 50001 | eval | mixed | 1793466949.2495742 | mirror.evaluate.paired |
| 50002 | eval | mixed | 1793466949.2575696 | mirror.evaluate.paired |
| 50003 | eval | mixed | 1793466949.2661188 | mirror.evaluate.paired |
| 50004 | eval | mixed | 1793466949.2727542 | mirror.evaluate.paired |
| 50005 | eval | mixed | 1793466949.2809227 | mirror.evaluate.paired |
| 50006 | eval | mixed | 1793466949.2971811 | mirror.evaluate.paired |
| 50007 | eval | mixed | 1793466949.3124995 | mirror.evaluate.paired |
| 50008 | eval | mixed | 1793466949.3297634 | mirror.evaluate.paired |
| 50009 | eval | mixed | 1793466949.3398273 | mirror.evaluate.paired |
| 50010 | eval | mixed | 1793466949.3475845 | mirror.evaluate.paired |
| 50011 | eval | mixed | 1793466949.3624697 | mirror.evaluate.paired |
| 50012 | eval | mixed | 1793466949.370777 | mirror.evaluate.paired |
| 50013 | eval | mixed | 1793466949.3801346 | mirror.evaluate.paired |
| 50014 | eval | mixed | 1793466949.3948703 | mirror.evaluate.paired |
| 50015 | eval | mixed | 1793466949.4040177 | mirror.evaluate.paired |
| 50016 | eval | mixed | 1793466949.4373443 | mirror.evaluate.paired |
| 50017 | eval | mixed | 1793466949.4446802 | mirror.evaluate.paired |
| 50018 | eval | mixed | 1793466949.4532666 | mirror.evaluate.paired |
| 50019 | eval | mixed | 1793466949.461201 | mirror.evaluate.paired |
| 50020 | eval | mixed | 1793466949.4700096 | mirror.evaluate.paired |
| 50021 | eval | mixed | 1793466949.4772515 | mirror.evaluate.paired |
| 50022 | eval | mixed | 1793466949.4936092 | mirror.evaluate.paired |
| 50023 | eval | mixed | 1793466949.508534 | mirror.evaluate.paired |
| 50024 | eval | mixed | 1793466949.5161388 | mirror.evaluate.paired |
| 50025 | eval | mixed | 1793466949.5410264 | mirror.evaluate.paired |
| 50026 | eval | mixed | 1793466949.5500872 | mirror.evaluate.paired |
| 50027 | eval | mixed | 1793466949.5641155 | mirror.evaluate.paired |
| 50028 | eval | mixed | 1793466949.5707195 | mirror.evaluate.paired |
| 50029 | eval | mixed | 1793466949.578079 | mirror.evaluate.paired |
| 50030 | eval | mixed | 1793466949.586826 | mirror.evaluate.paired |
| 50031 | eval | mixed | 1793466949.6014216 | mirror.evaluate.paired |
| 50032 | eval | mixed | 1793466949.6101332 | mirror.evaluate.paired |
| 50033 | eval | mixed | 1793466949.657045 | mirror.evaluate.paired |
| 50034 | eval | mixed | 1793466949.6635666 | mirror.evaluate.paired |
| 50035 | eval | mixed | 1793466949.6708522 | mirror.evaluate.paired |
| 50036 | eval | mixed | 1793466949.6773908 | mirror.evaluate.paired |
| 50037 | eval | mixed | 1793466949.690197 | mirror.evaluate.paired |
| 50038 | eval | mixed | 1793466949.7203853 | mirror.evaluate.paired |
| 50039 | eval | mixed | 1793466949.7410054 | mirror.evaluate.paired |
| 50040 | eval | mixed | 1793466949.7486653 | mirror.evaluate.paired |
| 50041 | eval | mixed | 1793466949.776608 | mirror.evaluate.paired |
| 50042 | eval | mixed | 1793466949.795004 | mirror.evaluate.paired |
| 50043 | eval | mixed | 1793466949.8029306 | mirror.evaluate.paired |
| 50044 | eval | mixed | 1793466949.8106883 | mirror.evaluate.paired |
| 50045 | eval | mixed | 1793466949.8181312 | mirror.evaluate.paired |
| 50046 | eval | mixed | 1793466949.831743 | mirror.evaluate.paired |
| 50047 | eval | mixed | 1793466949.839735 | mirror.evaluate.paired |
| 50048 | eval | mixed | 1793466949.854834 | mirror.evaluate.paired |
| 50049 | eval | mixed | 1793466949.8624837 | mirror.evaluate.paired |
| 50050 | eval | mixed | 1793466949.8787932 | mirror.evaluate.paired |
| 50051 | eval | mixed | 1793466949.8863924 | mirror.evaluate.paired |
| 50052 | eval | mixed | 1793466949.8948274 | mirror.evaluate.paired |
| 50053 | eval | mixed | 1793466949.904338 | mirror.evaluate.paired |
| 50054 | eval | mixed | 1793466949.9126496 | mirror.evaluate.paired |
| 50055 | eval | mixed | 1793466949.9218001 | mirror.evaluate.paired |
| 50056 | eval | mixed | 1793466949.930417 | mirror.evaluate.paired |
| 50057 | eval | mixed | 1793466949.9383235 | mirror.evaluate.paired |
| 50058 | eval | mixed | 1793466949.9471245 | mirror.evaluate.paired |
| 50059 | eval | mixed | 1793466949.9567783 | mirror.evaluate.paired |
| 60000 | eval | shortcut | 1793467046.0359428 | mirror.evaluate.paired |
| 60001 | eval | shortcut | 1793467046.0539002 | mirror.evaluate.paired |
| 60002 | eval | shortcut | 1793467046.0608416 | mirror.evaluate.paired |
| 60003 | eval | shortcut | 1793467046.0702553 | mirror.evaluate.paired |
| 60004 | eval | shortcut | 1793467046.077318 | mirror.evaluate.paired |
| 60005 | eval | shortcut | 1793467046.085611 | mirror.evaluate.paired |
| 60006 | eval | shortcut | 1793467046.1094706 | mirror.evaluate.paired |
| 60007 | eval | shortcut | 1793467046.1169493 | mirror.evaluate.paired |
| 60008 | eval | shortcut | 1793467046.1253464 | mirror.evaluate.paired |
| 60009 | eval | shortcut | 1793467046.132457 | mirror.evaluate.paired |
| 60010 | eval | shortcut | 1793467046.1456978 | mirror.evaluate.paired |
| 60011 | eval | shortcut | 1793467046.1538215 | mirror.evaluate.paired |
| 60012 | eval | shortcut | 1793467046.1707015 | mirror.evaluate.paired |
| 60013 | eval | shortcut | 1793467046.1770444 | mirror.evaluate.paired |
| 60014 | eval | shortcut | 1793467046.1853101 | mirror.evaluate.paired |
| 60015 | eval | shortcut | 1793467046.1932385 | mirror.evaluate.paired |
| 60016 | eval | shortcut | 1793467046.2009885 | mirror.evaluate.paired |
| 60017 | eval | shortcut | 1793467046.2244375 | mirror.evaluate.paired |
| 60018 | eval | shortcut | 1793467046.2310987 | mirror.evaluate.paired |
| 60019 | eval | shortcut | 1793467046.246427 | mirror.evaluate.paired |
| 60020 | eval | shortcut | 1793467046.253529 | mirror.evaluate.paired |
| 60021 | eval | shortcut | 1793467046.2604747 | mirror.evaluate.paired |
| 60022 | eval | shortcut | 1793467046.268669 | mirror.evaluate.paired |
| 60023 | eval | shortcut | 1793467046.2766073 | mirror.evaluate.paired |
| 60024 | eval | shortcut | 1793467046.2842305 | mirror.evaluate.paired |
| 60025 | eval | shortcut | 1793467046.2930808 | mirror.evaluate.paired |
| 60026 | eval | shortcut | 1793467046.3011668 | mirror.evaluate.paired |
| 60027 | eval | shortcut | 1793467046.3090916 | mirror.evaluate.paired |
| 60028 | eval | shortcut | 1793467046.3162534 | mirror.evaluate.paired |
| 60029 | eval | shortcut | 1793467046.325335 | mirror.evaluate.paired |
| 50000 | blocked | mixed | 1793467097.820678 | mirror.evaluate.paired |
| 50001 | blocked | mixed | 1793467097.8270762 | mirror.evaluate.paired |
| 50002 | blocked | mixed | 1793467097.8358822 | mirror.evaluate.paired |
| 50003 | blocked | mixed | 1793467097.8429382 | mirror.evaluate.paired |
| 50004 | blocked | mixed | 1793467097.8480308 | mirror.evaluate.paired |

</details>

## Rollout selection

Rollout JSON is committed for every seed where either condition carried an object covered by the corrected rule above that rule's tilt tolerance (15 degrees for the cup) or did not end in success or touched an obstacle; for every seed of the blocked stratum; and for every 5th seed of each stratum in seed order (indices 0, 5, 10, ...) regardless of outcome. All reports and metrics cover every seed.

- primary: 34 of 60 seeds (50000, 50001, 50005, 50006, 50008, 50010, 50012, 50013, 50014, 50015, 50020, 50022, 50024, 50025, 50030, 50031, 50032, 50034, 50035, 50036, 50037, 50040, 50041, 50044, 50045, 50046, 50047, 50049, 50050, 50051, 50053, 50054, 50055, 50057)
- shortcut: 28 of 30 seeds (60000, 60001, 60002, 60003, 60005, 60006, 60007, 60008, 60009, 60010, 60011, 60012, 60013, 60014, 60015, 60016, 60017, 60019, 60020, 60021, 60022, 60023, 60024, 60025, 60026, 60027, 60028, 60029)
- blocked: 5 of 5 seeds (50000-50004)

Wall time 158 s. Finite simulated trials; not formal verification and not evidence of physical deployment.
