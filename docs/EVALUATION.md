# Evaluation

## Scope of each result

Read every number below with its scope. The table says what varied between
trials and what was fixed.

| Result | What was new in each trial | What was fixed (reused in every trial) | What it supports | What it does not support |
|---|---|---|---|---|
| First and second pre-registered evaluations (60 fresh + 30 shortcut layouts each; 5 blocked layouts reported separately) | The layout: wall, slot, tray and object positions, generated after the freeze | One demonstration, one correction sentence, one compiled specification pair (original and corrected), planner, controller, checker | One compiled correction changes executed behaviour and carries to layouts it was not compiled on, without spreading to objects it does not name, under the 0.05 m/rad planner objective | Interpretation of 90 new demonstrations; 90 independent tests of language understanding; that Muse Spark caused the improvement (the pair must equal the offline-rule pair); any gain under a time-calibrated objective (objective_v1: 0.000); other corrections; other tasks |
| Correction-compiler benchmark, development model and offline rules, first run of the first set (44 cases) and the held-out set (28 cases) | The sentence | Base specification, scene | Single-sentence compilation accuracy (held-out set: by recorded response times the first model run began after the case file's final save); not pre-registered, see F-A5 | Multi-turn corrections, independent authors, new scenes, pre-registration |
| Correction-compiler benchmark, Muse Spark rows (44/44, 27/28) | Only the model (swapped after development) | The same two case sets, already seen during development; prompt revision 3, tuned on the development model | That Muse Spark handles these known cases with the current prompt | A blind or held-out accuracy for Muse Spark |
| Spoken corrections (288 clips) | Synthetic voice and noise condition | The 72 benchmark sentences | Transcription does not degrade compilation on clean synthetic speech | Real speakers |
| Perception robustness, video to specification | Rendered frames and videos of new simulated layouts | Simulator renders, known kit geometry and camera intrinsics | Measured perception limits on rendered video | Camera or phone footage, people, other objects; tilt estimation (true tilt at most 0.29°); position accuracy beyond the 5 cm gate |
| Planner objective (objective_v1: 120 new + 10 blocked layouts) | The layout, and the rotation price | The same specification pair, controller, checker | How much of the correction's effect depends on the rotation price; never-tilt and corrected trade-offs | Other tasks, real timing, pricing of spill risk |

A genuinely fresh authoring evaluation (independent authors, new wording,
multi-turn, baselines, every attempt counted) is specified in
[protocols/v3_authoring.md](protocols/v3_authoring.md). It has **not yet been
run**; no result from it exists.

## Audit findings and narrowed claims (2 November 2026)

An audit of commit `7be0be7` found five places where the published wording
claimed more than the evidence shows. All five have now been investigated from
stored evidence or new registrations. No original output was changed; the
corrected derived summaries are in new, versioned paths, cited below.

**F-A1, planner objective.** Resolved by registration objective_v1, frozen before
any of its layouts existed (see [Planner objective and baselines](#planner-objective-and-baselines-objective_v1)).
The earlier registrations planned with a rotation price of 0.05 m/rad. Calibrated
to execution time on development layouts, the price is 0.2445 m/rad. At that
price the original specification never tilts the cup on 120 new layouts, its
plans equal the corrected specification's, and the retained benefit is 0.000. The
earlier gains (27/60 → 60/60 and 33/60 → 60/60 primary; 4/30 and 3/30 → 30/30
shortcut) measure the correction against the 0.05 price, which is also slower;
they replicate on the new layouts at 0.05 (p = 9.3e-10 and 1.8e-12). The rule's
remaining measurable effect: on 10 blocked layouts it turns 10/10 spilling runs
(38 beads) into proven refusals. The plan-only audit sweep on the Muse
registration's 90 layouts (tilted cup routes 54/32/2/0/0 at 0.05/0.10/0.20/0.40/1.0)
and the 110-of-113 faster corrected runs were reproduced exactly
(`results/eval_objective_v1/audit_repro/summary.json`).

**F-A2, model contribution.** `mirror/evaluate/specs.py` refuses to run unless
the replayed model specification equals the offline-rule specification. This is
kept as a deterministic regression check and labelled so. It means the layout
results would be identical with the offline rules alone; they do not show that
Muse Spark caused the improvement. The Muse Spark correction-benchmark rows are a
post-hoc model swap on cases seen in development, not a fresh external holdout.
A separate language-understanding evaluation is prepared, not run:
[protocols/v3_language.md](protocols/v3_language.md).

**F-A3, video benchmark.** Rebuilt from the stored masks
([results/reanalysis_v1/video/report.md](../results/reanalysis_v1/video/report.md);
every rebuilt seed row equals the stored row): position error 0.72 cm was computed
only over estimates within 5 cm of the truth, and tilt error only on the 610 of
6,660 side-visible frames with a tilt estimate. Without the gate, of 20,960 visible
object-frames 88.8% are within 5 cm, 9.6% further off, 1.3% placed on another
object (identity errors) and 0.3% without an estimate; the median of all estimates
is 0.99 cm, the 90th percentile 6.4 cm, and while an object is carried 2.5 cm and
26 cm. Seed 71010 failed as a whole: the camera calibration from the tracked table
was wrong and no estimate is within 5 cm. A colour-threshold baseline has a lower
gated median (0.54 cm, against 0.77 cm for SAM 3.1 on the same frames) with only
60% of its estimates within 5 cm, which is why the gated median must not be read
as overall accuracy. True cup tilt never exceeds 0.29° in any registered clip, so a
constant-zero estimate (0.01°) beats the reported 2.0°: the benchmark cannot
measure tilt. The out-of-plane tilt component is unobservable from the side view.
A new video benchmark with substantial, varied tilt is designed, not run
([VIDEO_BENCHMARK_V3.md](VIDEO_BENCHMARK_V3.md)).

**F-A4, robustness denominators.** The registered median IoU change included 90
of 218 geometry-kept rows (condition A) for objects that were never prompted (0
before and after). On prompted objects only the median change is −0.039 (mean
−0.19, 10th percentile −0.98). Counting a detection only at IoU ≥ 0.5, 97 of 218
rows are found in A (0.445) and 81 of 218 in B (0.372). The carried cup was never
segmented (IoU ≥ 0.5) on any original or edited frame. Both found-rate checks
(≥ 0.9) were not met ([results/reanalysis_v1/robustness/report.json](../results/reanalysis_v1/robustness/report.json)).

**F-A5, correction-benchmark chronology.** The 28 held-out correction cases were
written after the offline rules were fixed on the first set. The case file's hash
matches every held-out result, and by the recorded response times the first model
run on them began after the file's final save. The case files and results were
committed together after the runs (06:32 PDT, 31 October), and the `frozen_at`
fields are hand-entered and inaccurate
([protocols/chronology.md](protocols/chronology.md)). The correction benchmark is
therefore **not pre-registered**, and `frozen_at` is not cited as evidence.

**Positive evidence.** Network-blocked replays reproduce both paired
registrations at the outcome level (see [Reproducibility](#reproducibility)), and
every registration has an immutable manifest in
[results/manifests_v1](../results/manifests_v1/README.md). The README results
table is recomputed from the stored reports by a test (`tests/release`).

## Planner objective and baselines (objective_v1)

Protocol: [protocols/objective_v1.md](protocols/objective_v1.md). Report:
[results/eval_objective_v1/attempt_1/report.md](../results/eval_objective_v1/attempt_1/report.md).
Frozen before any of its layouts was generated; one attempt, finished.

* **Conditions, all executed with the same controller and physics:** original
  specification at rotation prices 0.2445 (calibrated), 0.05, 0.10, 0.20, 0.40 and
  1.0 m/rad; never-tilt baseline at 0.2445; corrected specification at 0.2445 and
  0.05. Strata: 80 primary, 40 shortcut and 10 blocked new layouts.
* **Calibration** (development layouts only): transport time
  T = 2.30 + 6.76 L + 1.65 R (R² 0.76, leave-one-out RMSE 1.33 s), price 0.2445
  m/rad (bootstrap 95% CI 0.225 to 0.269). On 60 development layouts the fastest
  cup route was never a tilted one.

| Rotation price | Cup tilted / violation-free, primary (80) | Cup tilted / violation-free, shortcut (40) |
|---|---|---|
| 0.05 | 31 / 49 | 40 / 0 |
| 0.10 | 19 / 61 | 27 / 13 |
| 0.20 | 2 / 78 | 5 / 35 |
| 0.2445, 0.40, 1.0 | 0 / 80 | 0 / 40 |

* **Retained benefit at the calibrated price: 0.000** in both strata; original and
  corrected plans are identical on all 120 layouts.
* **At 0.05 the earlier effect replicates:** 49/80 → 80/80 (p = 9.3e-10) and
  0/40 → 40/40 (p = 1.8e-12).
* **Time:** the calibrated objective is also faster: original −3.2 s (primary) and
  −6.5 s (shortcut) against 0.05; corrected −1.7 s and −3.0 s against corrected at
  0.05. Every tilted run lost beads (2 to 6 of 8).
* **Never-tilt:** equal to corrected on 119/120 layouts (one run 2.0 s slower: the
  marker was forced around the wall).
* **Blocked layouts:** the original specification completes 10/10 at every price and
  spills 38 beads; never-tilt and both corrected conditions refuse all 10 with a
  proven infeasibility record.
* **Post hoc:** at the calibration CI bounds the original specification tilts the
  cup on 3/120 and 0/120 layouts.
* **Limits:** one controller and simulated timing; the price values time, not spill
  risk; one specification pair; 10 blocked layouts.

## Reproducibility

Network-blocked replays ([results/reanalysis_v1/replay](../results/reanalysis_v1/replay/README.md)):
the first registration (development-model specifications) matches in all 95
layouts, outcomes and bead counts, at its freeze commit and at today's code. The
Muse registration matches in all 95 layouts and outcomes, with one bead count
different (shortcut seed 60002, original specification: 6 stored, 2 replayed),
identically at its freeze commit and at today's code; the stored run was made on
a different machine. A development replay on Apple arm64
([replay_cross_platform](../results/reanalysis_v1/replay_cross_platform/README.md))
agrees on all 95 outcomes; 0 of 95 layout hashes are byte-identical (the layouts
agree to about 1e-16) and bead counts in spilling runs differ by up to 2.
Reproduction is at the outcome level, not bit-exact. This replaces the statement in
`results/eval_muse/RUN_NOTES.md` that the simulator is deterministic (that file is
kept unchanged as part of the record). macOS on Apple silicon is a supported
platform ([INSTALL.md](INSTALL.md)); compare continuous quantities only within one
platform.

## Question (first and second registrations)

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
  constraints. That agreement is a deterministic regression check on the
  recordings; it also means these results do not show what the model
  contributes (see F-A2 below).
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
hashes before every stratum and refuses if anything differs. A matching
environment reproduces the per-seed outcomes (status, completion, violations);
bead counts in spilling runs and layout hashes can differ across machines and
architectures, so reproduction is at the outcome level, not bit-exact (see
[Reproducibility](#reproducibility)).

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
corrections, or on physical hardware. It also does not measure how often a
correction is understood: the demonstration was interpreted once and the
correction compiled once, before the freeze, and that one pair was run on every
layout. These specifications were produced from
recorded development-model responses (the offline rules produce the same
enforced constraints); the second registration below repeats the evaluation
with specifications compiled by Muse Spark.


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


## Correction-compiler benchmark (not pre-registered)

Single sentences compiled against one base specification in the shortcut scene;
exact match of outcome and resulting enforced state. Full table and per-case
results: [`results/corrections_benchmark_summary.md`](../results/corrections_benchmark_summary.md).

| Rows | Exact | How to read it |
|---|---|---|
| First set (44), first run, prompt revision 1: offline rules / development model (`gpt-5.5`) | 38/44 / 42/44 | First run on these cases; the case-file hash equals the committed file. Not pre-registered: cases and results were committed together after the runs ([chronology](protocols/chronology.md)) |
| Held-out set (28), written after the offline fixes, prompt revision 1: offline rules / development model | 21/28 / 27/28 | By recorded response times the first model run began after the case file's final save, and the hash matches every result. Written by the same team; not pre-registered ([chronology](protocols/chronology.md)) |
| Later prompt revisions and offline fixes | see summary | Post-change measurements on cases already seen |
| Muse Spark (`muse-spark-1.3`), frozen / held-out | 44/44 / 27/28 | **Post-hoc model swap.** Both sets had been seen in development and prompt revision 3 was tuned on the development model. Not blind, not held-out for Muse Spark, not a fresh external holdout |

The benchmark has no multi-turn cases, no independent authors and one scene. Its
"held-out" set was written by the same team that wrote the first set. The
`frozen_at` fields in both case files are hand-entered and inaccurate and are not
evidence of when the cases were fixed. The summary file's row labels "blind" and
"before any run" (`results/corrections_benchmark_summary.md`, kept unchanged)
predate this analysis; read them with the chronology above. The v3
protocol addresses these gaps; it has not been run.


## Third registration: spoken corrections

**Question.** Does a correction spoken aloud, transcribed by
`muse-voice-transcribe-1.0` and compiled by `muse-spark-1.3`, give the same
result as the same sentence typed?

**Design** (`results/eval_voice/design.json`, written 31 October 2026 20:53 UTC,
committed in `7c2cb2f` before any transcription).

* Inputs: the 72 sentences of the correction benchmark (44 frozen + 28
  held-out cases), each spoken by two voices, clean and with pink noise at
  15 dB SNR: 288 WAV files, listed with sha256 in `inputs.json`.
* Speech was synthesised by a non-Meta engine (ElevenLabs `eleven_v4_turbo`,
  two premade voices). It is clean synthetic speech, not human recordings.
* Each file is transcribed once; the transcript goes through the same compiler,
  recordings and scorer as the typed sentence (exact operation match).
* Pre-stated checks: corpus word error rate (WER) at most 5% clean and 10% noisy;
  exact match from audio within 5 points (clean) and 10 points (noisy) of the
  text baseline.

**Amendments.** None. Frozen in `308ab44` (code, inputs and design hashes in
`freeze.json`).

**Results** (`results/eval_voice/report.md`, commit `f74602b`).

| Condition | Recordings | Corpus WER | Right from audio | Right from text | Text right, audio wrong | Audio right, text wrong | Exact McNemar |
|---|---|---|---|---|---|---|---|
| Clean | 144 | 3.0% | 141 | 142 | 2 | 1 | p = 1 |
| Pink noise 15 dB | 144 | 2.2% | 140 | 142 | 2 | 0 | p = 0.5 |

| Check | Result |
|---|---|
| Corpus WER clean ≤ 5% | met (3.0%) |
| Audio exact ≥ text exact − 5 points (clean) | met (−0.7 points) |
| Corpus WER noisy ≤ 10% | met (2.2%) |
| Audio exact ≥ text exact − 10 points (noisy) | met (−1.4 points) |

No transcription failed. Spend: $1.70 at list prices (288 transcriptions
$0.05; correction compiles $1.65).

**What the failures mean.** All four "right as text, wrong from audio" cases
had a word error rate of 0. The words were right; only punctuation differed: a
doubled full stop (two sentences, one voice, clean) and a comma heard as a full
stop ("…while you carry it. The cup too.", both voices, noisy). Those
differences alone changed what Muse Spark compiled, so the compiler is
sensitive to punctuation. The one text failure (hs04, "All three objects have to stay
upright at all times", wrong as text; from audio it was right in one of four recordings) is the same
miss as in the Muse Spark correction benchmark: one rule per object instead of
one rule for all objects, which the strict scorer counts as wrong
(`results/corrections_benchmark_holdout_muse.md`). Synthetic voices are an easier case
than real speakers; results on human speech may differ.


## Fourth registration: perception robustness

**Question.** Does SAM 3.1's segmentation of the cup, block and marker hold when
`muse-image-1.0` changes how a frame looks (lighting, background, a distractor
object, the cup's material), and under camera noise and blur?

**Design** (`results/eval_robustness/design.json`, written 31 October 2026
22:29 UTC, before any model call on its frames).

* Inputs: one carry-moment frame (the cup in the gripper) from each of 8 new
  evaluation seeds (80000-80007), top or oblique camera, with the simulator's
  true visible-pixel masks.
* 11 frozen instructions per frame: 8 edits by `muse-image-1.0` (64 edits) and
  3 local, deterministic ones (noise, Gaussian blur, motion blur).
* Two prompt conditions: **A**, phrases chosen once per original frame by
  `muse-spark-1.3` and accepted only if SAM finds exactly one object; **B**,
  fixed colour-free phrases ("open box", "small cube", "cylinder").
* The image model shifts and rescales the whole frame slightly; a whole-frame
  fit undoes this before scoring (primary, "corrected"); uncorrected scores
  are reported too. A geometry check decides per object whether an edit kept it
  in place; objects that moved, resized or vanished are reported separately,
  never dropped.
* Pre-stated checks, on objects whose geometry was kept, per condition: median
  IoU change ≥ −0.05, found rate ≥ 0.9, median centroid error ≤ 4 px.
* Disclosed in the design: the thresholds predate a development run on 3
  development frames; the frames, prompt conditions and correction rule were
  chosen after seeing that run.

**Amendments.** None. Frozen in `024f1cb`.

**Results** (`results/eval_robustness/report.md`, commit `cb03bf8`).

| Check (all edits, geometry kept, corrected) | Condition A | Condition B |
|---|---|---|
| Median IoU change ≥ −0.05 | met (−0.000)\* | met (0.000)\* |
| Found rate ≥ 0.9 | **not met** (0.47) | **not met** (0.61) |
| Median centroid error ≤ 4 px | met (0.67 px) | met (1.50 px) |

\* The registered median is taken over every geometry-kept object row, including
objects that were never prompted (no phrase chosen; IoU 0 before and after) and
objects missed both before and after: in condition A, 90 of 218 rows were never
prompted. On prompted objects only, the median IoU change is −0.039 (mean −0.19;
10th percentile −0.98); on objects found both before and after, −0.032. Counting a
detection only when SAM's top mask overlaps the object with IoU ≥ 0.5, 97 of 218
kept rows are detected in A (0.445) and 81 of 218 in B (0.372); on unedited frames
13 of 24 (A) and 11 of 24 (B). The registered "found" counts any returned mask: in
B, 50 kept rows are "found" with IoU 0. The carried cup was never segmented (IoU ≥
0.5) on any original or edited frame in either condition. Source:
[results/reanalysis_v1/robustness/report.json](../results/reanalysis_v1/robustness/report.json).
Do not read the met IoU check as evidence that the edits leave segmentation unchanged.

* Uncorrected (A): median IoU change −0.001, centroid 1.93 px; the background
  edits lose 0.17 IoU uncorrected vs 0.04 corrected, so most of the apparent
  damage from those edits is the editor's whole-frame drift.
* The image model changed the scene itself in 42 of 261 scored objects (16%;
  all 42 from image-model edits, 22% of their 189 objects): 33 moved, 3
  missing, 6 in reframed frames that could not be brought into register. One
  edit was refused by the content policy.
* Spend: $2.22 (63 image-edit requests $0.63, 575 SAM requests $1.44, 14 phrase
  choices $0.15).

**What the failure means.** The found rate was already low on the unedited
frames (0.58 in A, 0.67 in B), so the edits are not what SAM misses. In these
carry frames the cup sits inside the gripper. In condition A the phrase
chooser accepted no phrase for the cup in 7 of 8 frames (and none at all in one
frame), so the cup counts as not found. In condition B "open box" returned a
mask in about half the cup cases, but every such mask missed the cup (mean IoU
0.000). Block segmentation was strong (found 0.87 in A, 1.00 in B); the marker
was found in 39-49% of cases. The video pipeline therefore does not rely on
noun phrases alone: Muse Spark locates each object and SAM tracks it from that
box. In condition B the median centroid error on material edits was 151 px
(18 kept objects), consistent with a fixed phrase matching another object.


## Fifth registration: video to specification (two attempts)

**Question.** Can MIRROR rebuild the demonstration from rendered video (SAM 3.1
tracking, two-view reconstruction) well enough that Muse Spark proposes the
same specification as from the simulator's own trace? And does Muse Wonder
describe the video better than Muse Spark?

**Design** (`results/eval_video/design.json`, written 31 October 2026 20:56 UTC,
committed in `7c2cb2f`; repeated unchanged in attempt 2).

* Inputs: 20 new evaluation seeds, the scripted demonstration in each layout,
  rendered from a top and a side camera (960x540, 15 fps, no overlays) with
  the simulator's per-frame ground truth.
* Tracking: Muse Spark locates each object on the first frame; SAM 3.1 tracks it
  from that box on 5 fps copies, with re-seeding of lost tracks; the trace is
  rebuilt on the table plane from both views.
* Pre-stated thresholds: object found rate ≥ 0.95; median table-plane position
  error ≤ 1.5 cm; grasp/release detected within 1 s ≥ 0.9; median timing error
  ≤ 0.3 s; median cup tilt error ≤ 5°; specification from video with the same
  signature as the one from the true trace on at least 18 of 20 seeds.
* The tracking thresholds use event times from tracked object motion, never
  from the video models. The video models have no pass/fail threshold. Decision
  rule: use Muse Wonder for observations only if its mean event F1 is at least
  0.05 above Muse Spark's and it gets the relevant objects right at least as
  often; otherwise keep Muse Spark.
* A $14 spend cap on all model calls.

**Amendments** (attempt 1, both before any input existed): amendment 1 (21:42
UTC) scoped the thresholds as above, written after development-only timing
numbers from one reference demonstration (video models reported grasps about 2 s
early; normal-speed video was their best input). Amendment 2 (1 November 01:06
UTC) fixed the render settings, the tracking procedure, one Wonder and one Spark
observation per seed, the $14 cap and a reported-only structure comparison.

### Attempt 1 (registration `video`, seeds 70000-70019): no usable result

Frozen in `8e3b369`; reported in full in
[`results/eval_video/ATTEMPT_1.md`](../results/eval_video/ATTEMPT_1.md)
(commit `69c533c`).

* **The video-model observations never ran.** The runner passed descriptive
  design fields to `observe()`, so all 24 calls failed before any request was
  sent. Its unit test used a fake `observe` that accepted any argument.
* **The specification check therefore ran without observations,** which is not
  the registered condition (4 of 12 equal). It is not reported as a result.
* **The run stopped after 12 of 20 seeds** when the machine restarted; the
  runner could not resume.
* Spend $3.75. A frozen registration cannot be patched, so the experiment was
  repeated as a new registration on new seeds.

### Attempt 2 (registration `video2`, seeds 71000-71019)

Same design, thresholds and decision rule. Three harness changes: only the
arguments `observe()` declares are passed (tested against the real function);
finished seeds are checkpointed so a restarted run resumes; seeds run 8 at a
time, each reserving 1.5 times its projected cost against the cap. Renders on 8
NVIDIA L4 GPUs with EGL. Design written 1 November 06:04 UTC, frozen and pushed
in `80750a4` before any model call on its inputs; no amendments. Results in
[`results/eval_video2/report.md`](../results/eval_video2/report.md) and
[`RUN_NOTES.md`](../results/eval_video2/RUN_NOTES.md) (commit `bb5eb64`).

**Stopped early: 16 of 20 seeds.** With 8 seeds in flight, $8.50 spent and $6.80
reserved, no further seed could start under the $14 cap, so seeds 71016-71019
did not run. Actual spend for the 16 seeds was $10.44; the reservation was more
conservative than needed, but the cap worked as written. A second invocation was
refused by the run-once guard, which was left in place. The four missing seeds
cannot change any verdict: even if all four were perfect, the found rate would be
at most 0.91, detection within 1 s at most 0.71 and equal specifications at most
12 of 20.

| Check (16 seeds) | Threshold | Result | |
|---|---|---|---|
| Objects found (within 5 cm, visible frames) | ≥ 0.95 | 0.886 | **not met** |
| Median position error (estimates within 5 cm only) | ≤ 1.5 cm | 0.72 cm | met, conditional on detection (F-A3) |
| Grasp/release detected within 1 s | ≥ 0.9 | 0.635 | **not met** |
| Median timing error | ≤ 0.3 s | 0.65 s | **not met** |
| Median cup tilt error (in-image vs true) | ≤ 5° | 2.0° | met, but cannot measure tilt: true tilt ≤ 0.29°, a constant-zero estimate scores 0.01° (F-A3) |
| Same specification as from the true trace | ≥ 18 of 20 | 8 of 16 | **not met** |

Two of six checks met, and both met checks are conditional (F-A3): unconditionally,
88.8% of visible object-frames are within 5 cm, the median of all estimates is
0.99 cm and the 90th percentile 6.4 cm (26 cm while an object is carried); a
colour-threshold baseline has a lower gated median (0.54 cm) with only 60% within
5 cm ([re-analysis](../results/reanalysis_v1/video/report.md)).

**Tilt summaries fed to the model (found after the run).** Before the perception
fix of 2 November, video-derived summaries reported unobserved tilt as measured
0°. All 20 recorded Muse Spark proposal requests for video-derived demonstrations
were told, for example, that the block "stayed within 0 degrees of upright for the
whole time it was carried", although block and marker tilt are never measured from
video; on seed 71000 the cup's tilt was measured on 18 of 69 carried frames yet
summarised as staying within 8° the whole time. The "same specification 8 of 16"
result therefore partly rests on these statements. The saved results are unchanged
and remain the registered result; unobserved tilt is now marked unknown, never
upright, and the post-hoc effect of the fix is in
[results/perception_v2](../results/perception_v2/).

Same structure ignoring numeric limits (reported only):
8 of 16. The one numeric difference: seed 71007, cup upright during transport
with a 20° limit from video vs 15° from the true trace.

**Muse Wonder vs Muse Spark** (same 16 clips, one run each, no errors):

| | Muse Wonder | Muse Spark |
|---|---|---|
| Mean event F1 (0.5 s tolerance) | 0.062 | 0.115 |
| Events matched within 2 s | 24 / 96 | 59 / 96 |
| Mean timing error of those | 0.99 s | 1.00 s |
| Relevant objects right | 14 / 16 | 16 / 16 |
| Care cue for the cup (expected on 16) | 5 | 5 |
| Median latency | 79 s | 42 s |
| Cost | $3.86 | $0.76 |

Decision rule: Wonder is not 0.05 F1 above Spark and gets the objects right less
often, so **Muse Spark is kept** as the video observer.

**Per seed.** Seed 71010: tracking produced no position within 5 cm of the
truth for any object (found 0.000, 0 of 6 events); the specification from video
lost the cup entirely. The re-analysis traced this to a failed camera calibration
from the tracked table (top-view table fit 10.03 px against a 10 px threshold, with
the wall box covering the image), so no estimate is within 5 cm. Seed 71002: found
0.701, 1 of 6 events. Twelve of the 16 seeds had a found rate of at least 0.93.

**Spend:** $10.44 (Muse Spark $2.71, SAM 3.1 $3.88, Muse Wonder $3.86), under
the $14 cap; every call is in the ledger.

**What this means.** On these rendered videos, where perception finds an
object its position is close (0.72 cm, counted only within 5 cm; 88.8% of
visible object-frames are within 5 cm), and the tilt number says nothing about
tilt estimation because the cup stayed within 0.29° of upright (F-A3). None of
this is evidence of phone or camera-video performance: no real footage has been
processed ([REAL_FOOTAGE.md](REAL_FOOTAGE.md)). Perception is not reliable enough
about whether an object is present and when it is grasped or released
(found 0.886, 0.635 of events within 1 s, median timing error 0.65 s), and on one seed
it failed outright. The specification proposed from video matched the one from
the simulator's trace on half the seeds (8 of 16). Video-derived traces cannot
yet replace the simulator trace that the main evaluations use. Both video models
report events far less precisely than tracking (event F1 0.06 and 0.12 at
0.5 s), so they are not a substitute for tracked event times either. Muse
Wonder did not earn its higher cost and latency here.


## Evidence provenance

Different published artifacts draw on different registrations. None is relabelled
as another.

| Artifact | Source of the rollouts and numbers shown | Specification pair | Seeds |
|---|---|---|---|
| Studio site, evidence step | Second registration, loaded unchanged from `results/eval_muse` | Muse Spark (`muse-spark-1.3`), replayed | primary 50000–50059, shortcut 60000–60029, blocked 50000–50004 |
| Studio site, live runs (with `?live=1`) | Development showcase layouts, run on request | whatever the visitor's session holds | dev 9000–9099 only; never evaluation seeds |
| Demo film, side-by-side clip (seed 30002) and the nine-layout grid | First registration: these trials were re-run for rendering at 30 Hz with the frozen code and specifications; status, routes, beads in cup, maximum tilt and placement match the saved rollouts in `results/eval/primary` for every one | development model (`gpt-5.5`), replayed | 30002; 30000, 30002, 30004, 30005, 30006, 30008, 30010, 30012, 30015 |
| Demo film, evidence cards | Numbers from `results/eval/report.json`, `results/eval_muse/report.json`, `results/eval_voice`, `results/eval_robustness`, `results/eval_video2` | as in each report | as in each report |
| Demo film, Watch and Interpret steps | Recorded studio session: rendered simulator video of the reference demonstration, with recorded SAM 3.1 and Muse Spark outputs (no call while filming) | recorded Muse Spark proposal | reference demonstration only |
| Demo film, spill, correction and corrected run | Reference scenes `ref_shortcut` and `ref_blocked`, not evaluation layouts. The spoken correction is a synthetic voice sample (not a person, not in any benchmark), transcribed by a recorded Muse Voice Transcribe call | the first registration's frozen specifications (development model, replayed) in the rendered clips; recorded Muse Spark responses in the studio shots | none |
| README and this page | All registrations, each named in its own section | as named | as named |

Both specification pairs enforce the same rules (`results/muse_spec_comparison.json`),
so film clips from the first registration show the same rules the site
evaluates, but they are rollouts of a different registration with different
seeds. A film or page that shows a seed number should name its registration.
