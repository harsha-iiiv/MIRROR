# Evaluation

## Question

Does a single confirmed correction ("this container must remain upright
throughout transport") change executed behaviour and improve compliance on
layouts that were never used during development, without constraining
objects it does not cover?

## Design (registered before the final run)

* **Conditions.** *Original*: the reference proposal after review (goals and
  "cup upright at the destination" confirmed). *Corrected*: the same
  specification plus the reference correction (cup upright during
  transport, 15° tolerance). Both are produced from the recorded interpreter
  responses and must agree with the offline rules on the enforced
  constraints.
* **Pairing.** Each seed defines one initial scene. Both conditions run from
  that identical state with the same planner, controller and simulator
  configuration.
* **Seeds.**
  * Primary: 60 layouts from the standard sampler (`family="mixed"`), eval
    seeds 30000–30059.
  * Stratum: 30 layouts from `family="shortcut"`, where a slot is placed near
    the straight line from the cup to the tray (scene geometry only), eval
    seeds 40000–40029.
  * Blocked: 5 layouts where the slot is the only opening, seeds 30000–30004
    of the blocked split.
  Seeds 10000–10059 and 20000–20029 were the first registration. They are
  retired because development tooling (the studio's live evaluation) sampled
  eight of them before the freeze. They are not used.
* **Freeze.** Before any evaluation layout is generated, a manifest records
  the hashes of both specifications, of the `mirror/` source tree, the
  scripts, the reference demonstration and the recorded responses, the
  library versions, and the seed lists. Every evaluation-split layout ever
  generated is appended to an evaluation ledger; the freeze refuses to
  proceed if the ledger already contains any of the frozen seeds, and the
  final report embeds the ledger.
* **No filtering.** Every attempt is reported. Nothing is excluded by
  outcome.

## Metrics (reported separately)

* Task completion (every goal object inside the tray, released, settled).
* Corrected-rule violations: maximum cup tilt during transport and time over
  tolerance.
* Beads lost from the cup.
* Forbidden contacts and obstacle contacts.
* Planning failures, execution failures and timeouts, kept distinct.
* Planning time and simulated execution time.
* Human interventions after the correction (none are possible in batch
  runs; reported as zero by construction).
* Feasible scenes wrongly rejected, where feasibility is independently known.
* **Scope:** objects the correction does not cover keep using tilted routes
  where those are cheaper.
* **Blocked:** proven infeasibility (planner certificate with
  `proven: true`) is distinguished from an ordinary planner failure.

Paired statistics: per-seed outcome pairs, an exact McNemar test on
violation-free success, Wilson and bootstrap intervals.

## How to reproduce

The published registration (`results/eval/freeze.json`) and its ledger stay
as they are. `freeze-final` is for registering a *new* experiment on untouched
seeds; run on the published seed list it correctly refuses, because those
layouts now exist in the ledger.

The evaluated code is tagged `eval-final` (commit `e23fac1`). Later commits
change only rendering, the studio and documentation, so check out that tag
first: `git checkout eval-final`. To re-run the published evaluation against
the frozen manifest, with the
same library versions recorded in the manifest, write to disposable output
and an isolated ledger:

```bash
MIRROR_EVAL_LEDGER=results/tmp/repro-ledger.jsonl \
  python scripts/run_eval.py final --manifest results/eval/freeze.json \
  --out results/tmp/reproduced --workers 2 --save-rollouts
```

The runner re-checks the specification, source, script, data and version
hashes before every stratum and refuses if anything differs. The simulator is
deterministic, so a matching environment reproduces the per-seed outcomes.

To audit without re-running: the per-seed pairs are in
`results/eval/<stratum>/report.json`, the ledger in `results/eval/ledger.jsonl`
(every entry is later than the manifest's `created_unix`), and a committed
rollout can be re-checked under any specification with
`python scripts/run_eval.py recheck --log <steplog.npz> --scene <scene.json> --spec <spec.json>`.

## Results

Frozen at commit `ac021f3` (manifest `results/eval/freeze.json`) before any
evaluation layout was generated; full report in
[`results/eval/report.md`](../results/eval/report.md).

| Metric | Primary (60, mixed) original / corrected | Stratum (30, shortcut) original / corrected | Blocked (5) original / corrected |
|---|---|---|---|
| Executed in simulation | 60 / 60 | 30 / 30 | 5 / 0 |
| Task completion | 60 / 60 | 30 / 30 | 5 / 0 |
| Success without violating the corrected rule | 27 / 60 | 4 / 30 | 0 / 0 |
| Trials violating the corrected rule | 33 / 0 | 26 / 0 | 5 / 0 |
| Maximum cup tilt during transport | 89.8° / 0.3° | 89.8° / 0.3° | 89.7° / not executed |
| Beads lost | 119 / 0 | 106 / 0 | 22 / 0 |
| Obstacle or floor contacts | 0 / 0 | 0 / 0 | 0 / 0 |
| Planning failures / execution failures / timeouts | 0 / 0 / 0 both | 0 / 0 / 0 both | corrected: 5 planning failures, all proven infeasible |
| Feasible scenes wrongly rejected | 0 of 33 / 0 of 33 | 0 of 17 / 0 of 17 | n/a |

* **Primary:** the correction mattered in 33 of 60 layouts. Violation-free
  success rose from 0.45 to 1.00 (difference +0.55, bootstrap 95% CI
  [0.42, 0.68]); all 33 discordant pairs favour the corrected specification,
  exact McNemar p = 2.3 × 10⁻¹⁰.
* **Shortcut stratum:** the correction mattered in 26 of 30 layouts;
  0.13 → 1.00 (+0.87, CI [0.73, 0.97]), McNemar p = 3.0 × 10⁻⁸.
* **Scope:** every case where the original specification carried the block or
  marker tilted was still tilted under the corrected one (32 of 32 primary,
  25 of 25 stratum). The correction did not spread to objects it does not name.
* **Blocked:** the original specification completes the task by tilting the
  cup through the slot. The corrected specification stops before moving, in
  all five layouts, with a proof that the planner's certificate and the
  evaluator's independently written bound agree on.
* **Ledger:** 95 evaluation layouts were generated, all after the freeze, all
  by the frozen runner.

What this does not show: behaviour outside this task family, with other
corrections, on physical hardware, or with Muse Spark as the interpreter (the
specifications were produced from recorded development-model responses, and
the offline rules produce the same enforced constraints).


## Second registration: specifications compiled by Muse Spark

A second evaluation was frozen (`results/eval_muse/freeze.json`, commit
`864060d`) before any of its layouts existed: primary 50000-50059 (mixed),
shortcut 60000-60029, blocked 50000-50004, none used anywhere before. Its
specifications are replayed from `muse-spark-1.3` recordings
(`data/cassettes_muse`). They differ from the first pair in prose but enforce the
same rules and goals (`results/muse_spec_comparison.json`). Analysis plan and
hypotheses were stated in the manifest and match the first evaluation:
corrected beats original on violation-free success in both strata, corrected
never loses beads, blocked 5/5 proven.

| | primary: A / B | shortcut: A / B | blocked: A / B |
|---|---|---|---|
| Violation-free success | 33 / 60 | 3 / 30 | 0 / 0 |
| Trials violating the corrected rule | 27 / 0 | 27 / 0 | 5 / 0 |
| Beads lost | 104 / 0 | 103 / 0 | 21 / 0 |
| Max cup tilt (deg) | 89.7 / 0.3 | 89.7 / 0.3 | 89.7 / n/a |
| Planning / execution failures, timeouts | 0 / 0 | 0 / 0 | 0 / 5 planning (all proven infeasible) |
| Exact McNemar | p = 1.49e-8 | p = 1.49e-8 | not applicable |

All three hypotheses hold. Its code hash leaves out presentation-only modules
(renderer, studio, CLI); see `mirror/evaluate/METHOD.md`. The first run attempt
was interrupted by a machine restart after generating 90 layouts and before
writing any result; the full run was repeated from a clean checkout of the same
commit with the same runtime. Both attempts are in its ledger. Details:
`results/eval_muse/RUN_NOTES.md`.

Reproduce: `git checkout 864060d`, then
`python scripts/run_eval.py final --registration muse --workers 2` in a scratch
clone (it writes its outputs and ledger entries into `results/eval_muse`).
