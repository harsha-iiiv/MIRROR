# Evaluation

## Scope of each result

Read every number below with its scope. The table says what varied between
trials and what was fixed.

| Result | What was new in each trial | What was fixed (reused in every trial) | What it supports | What it does not support |
|---|---|---|---|---|
| First and second pre-registered evaluations (60 fresh + 30 shortcut layouts each; 5 blocked layouts reported separately) | The layout: wall, slot, tray and object positions, generated after the freeze | One demonstration, one correction sentence, one compiled specification pair (original and corrected), planner, controller, checker | One compiled correction changes executed behaviour and carries to layouts it was not compiled on, without spreading to objects it does not name, under this planner's objective | Interpretation of 90 new demonstrations; 90 independent tests of language understanding; that Muse Spark caused the improvement (the pair must equal the offline-rule pair); the same failure rate under another planner objective; other corrections; other tasks |
| Correction-compiler benchmark, development model and offline rules, first run of the frozen set (44 cases) and the held-out set (28 cases) | The sentence | Base specification, scene | Single-sentence compilation accuracy on cases intended to be unseen by that interpreter (the record does not establish the freeze order; see F-A5) | Multi-turn corrections, independent authors, new scenes, pre-registration |
| Correction-compiler benchmark, Muse Spark rows (44/44, 27/28) | Only the model (swapped after development) | The same two case sets, already seen during development; prompt revision 3, tuned on the development model | That Muse Spark handles these known cases with the current prompt | A blind or held-out accuracy for Muse Spark |
| Spoken corrections (288 clips) | Synthetic voice and noise condition | The 72 benchmark sentences | Transcription does not degrade compilation on clean synthetic speech | Real speakers |
| Perception robustness, video to specification | Rendered frames and videos of new simulated layouts | Simulator renders, known kit geometry and camera intrinsics | Measured perception limits on rendered video | Camera footage, people, other objects; tilt estimation (true tilt at most 0.3°); unconditional position accuracy |

A genuinely fresh authoring evaluation (independent authors, new wording,
multi-turn, baselines, every attempt counted) is specified in
[protocols/v3_authoring.md](protocols/v3_authoring.md). It has **not yet been
run**; no result from it exists.

## Audit findings and narrowed claims (2 November 2026)

An audit of commit `7be0be7` (items F-A1 to F-A3 and F-A5 confirmed on `1787aa6`)
found five places where the published wording claims more than the evidence
shows. No original output is changed; corrected derived summaries go to new,
versioned paths. Items marked **PENDING** wait for work in progress and have
no result yet.

**F-A1, planner objective.** The planner picks the cheapest route, with route
cost = path length + 0.05 m per radian of gripper rotation
(`PlannerConfig.rotation_penalty`). The audit's plan-only sweep over 90 frozen
layouts found tilted cup routes on 54 at 0.05 m/rad, 32 at 0.10, 2 at 0.20 and 0
at 0.40 and 1.0 (planned, not executed). In the stored reports of both
registrations, in 110 of the 113 pairs where the two specifications disagreed
on violation-free success, the corrected (upright) run finished sooner in
simulation (median 3.6 s shorter): the shortest route is not the fastest. The
original-specification failure rates (27/60, 4/30, 33/60, 3/30) therefore
belong to this objective; a planner that never tilts would spill nothing. The
corrected rule still did what it says in every trial, but the size of the
improvement is not a property of MIRROR in general.
**PENDING (track `baseline`):** a new registration that executes a
rotation-cost sweep (0.05, 0.10, 0.20, 0.40, 1.0 and a value calibrated to
measured execution time on development layouts) and a never-tilt baseline with
the same controller and physics, reporting completion, violations, beads kept,
path cost and execution time.

**F-A2, model contribution.** `mirror/evaluate/specs.py` refuses to run unless
the replayed model specification equals the offline-rule specification. This is
kept as a deterministic regression check and labelled so. It means the
layout results would be identical with the offline rules alone; they do not
show that Muse Spark caused the improvement. The Muse Spark correction-benchmark
rows are post-hoc, on cases seen in development, and are not a fresh external
holdout. A separate language-understanding evaluation is prepared, not run:
[protocols/v3_language.md](protocols/v3_language.md).

**F-A3, video benchmark.** In all 20 registered clips the cup's true tilt stays
at or below 0.3° (0.217° to 0.297°, from the stored traces), so a constant-zero
estimate beats the reported 2.0° median error: the tilt check cannot show
tilt estimation. `bench_video.frame_errors` computes position error only for
estimates within 5 cm of the truth, so 0.72 cm is conditional on detection, not
overall localisation accuracy. **PENDING (track `reanalysis`):** unconditional
error distributions, constant-zero and simple classical baselines, and missing
detections, outliers and identity errors reported separately, from the
existing outputs. **PENDING (track `perception`):** a new video benchmark design
with substantial, varied tilt and the same baselines, awaiting budget.

**F-A4, robustness denominators.** The near-zero median IoU change includes
objects no phrase was chosen for (IoU 0 before and after the edit).
**PENDING (track `reanalysis`):** prompted and evaluated object counts,
detection-conditional and unconditional scores, missing detections and
geometry-changing edits, next to the failed found-rate checks and the cup's
segmentation failures.

**F-A5, correction-benchmark chronology.** The held-out case file says
`frozen_at` 2026-10-31T04:45:00-07:00, but its results file says `generated_at`
2026-10-31T11:27:50Z (04:27:50 PDT), 17 minutes earlier. The frozen set's
`frozen_at` (04:25 PDT) is also later than its results (11:22:31Z, 04:22:31
PDT). A contemporaneous project log reports the frozen set at 04:21:15 PDT and
the held-out set at 04:27:37 PDT, which bound when those events were reported,
not when they happened. This is not by itself evidence of leakage, but the
record does not establish that either set was fixed before its first run, so
pre-registration of the correction benchmark is not claimed.
**PENDING (track `reanalysis`):** a timeline from file, cassette, ledger,
usage-log and commit times, with linked code, model and prompt versions and
immutable manifests.

**Positive evidence.** An isolated, network-blocked replay at the audited commit
reproduced all 95 Muse-registration scene hashes, statuses and violation-free
outcomes. One bead-loss count differed (6 vs 2). This is reproduction at the
outcome level, not bit-exact determinism. The README results table is
recomputed from the stored reports by a test (`tests/release`).

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


## Correction-compiler benchmark (not pre-registered as an evaluation)

Single sentences compiled against one base specification in the shortcut scene;
exact match of outcome and resulting enforced state. Full table and per-case
results: [`results/corrections_benchmark_summary.md`](../results/corrections_benchmark_summary.md).

| Rows | Exact | How to read it |
|---|---|---|
| Frozen set (44), first run, prompt revision 1: offline rules / development model (`gpt-5.5`) | 38/44 / 42/44 | Intended blind; the file's `frozen_at` is later than the results (F-A5) |
| Held-out set (28), written after the offline fixes, prompt revision 1: offline rules / development model | 21/28 / 27/28 | Intended blind; the record does not establish that the set was frozen before this run (F-A5) |
| Later prompt revisions and offline fixes | see summary | Post-change measurements on cases already seen |
| Muse Spark (`muse-spark-1.3`), frozen / held-out | 44/44 / 27/28 | **Post-hoc model swap.** Both sets had been seen in development and prompt revision 3 was tuned on the development model. Not blind, not held-out for Muse Spark, not a fresh external holdout |

The benchmark has no multi-turn cases, no independent authors and one scene. Its
"held-out" set was written by the same team that wrote the frozen set. The v3
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

\* Includes objects for which no phrase was chosen (0 before and after). Prompted
and evaluated counts are **PENDING** (F-A4).

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
| Median cup tilt error (in-image vs true) | ≤ 5° | 2.0° | met, but uninformative: true tilt ≤ 0.3°, a constant zero scores better (F-A3) |
| Same specification as from the true trace | ≥ 18 of 20 | 8 of 16 | **not met** |

Two of six checks met. Same structure ignoring numeric limits (reported only):
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
lost the cup entirely. The report does not diagnose why. Seed 71002: found
0.701, 1 of 6 events. Twelve of the 16 seeds had a found rate of at least 0.93.

**Spend:** $10.44 (Muse Spark $2.71, SAM 3.1 $3.88, Muse Wonder $3.86), under
the $14 cap; every call is in the ledger.

**What this means.** On these rendered videos, where perception finds an
object its position is close (0.72 cm, counted only within 5 cm), and the tilt
number says nothing about tilt estimation because the cup stayed within 0.3° of
upright (F-A3). Perception is not reliable enough
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
