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

```bash
python scripts/run_eval.py freeze-final --manifest results/eval/freeze.json
python scripts/run_eval.py final --manifest results/eval/freeze.json --out results/eval --workers 2
```

## Results

Results are written to `results/eval/report.md` after the frozen run.
