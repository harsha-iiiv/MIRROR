# Protocol v3: fresh evaluation of correction authoring

**Status: not yet run.** The protocol, its machine-readable design
(`mirror/evaluate/protocol_v3/design.json`) and its harness are written and
dry-run on development cases only. Nothing here is frozen. No held-out case
exists yet, no independent author has been recruited, and no number from this
protocol is an evaluation result.

The [test matrix](v3_test_matrix.md) lists the kinds of correction both tracks
must cover, with development and independent coverage kept apart. Its separate
**language-understanding track** (fresh independent corrections on
varied bases, scored by independent adjudicators, no execution) is described in
[v3_language.md](v3_language.md).

## Why a new protocol

The two pre-registered evaluations run one fixed specification pair on new
layouts: they show that one compiled correction carries through planning and
physics, not how often new corrections are understood
([EVALUATION.md](../EVALUATION.md#scope-of-each-result)). The correction-compiler
benchmark uses single sentences in one scene, written by the same people who
wrote the compiler, and its Muse Spark rows are a post-hoc model swap on cases
already seen. Protocol v3 measures authoring itself, on cases MIRROR's
developers have not seen, and composes it with execution on the same cases.

## Question

When independent task authors correct a task specification in their own words,
across new scenes, object scopes and multi-turn changes, how often does MIRROR
(Muse Spark compiling corrections) produce the intended specification and a
compliant execution, compared with the offline rule compiler and with manual
structured-spec editing, all through an identical planner, simulator and
checker?

## Conditions

| Condition | What turns a turn into a specification change | Speech |
|---|---|---|
| `mirror_model` | `compile_correction` with `muse-spark-1.3` (fresh calls, each recorded), validation layer on, as in the product | real speakers' recordings transcribed by `muse-voice-transcribe-1.0` |
| `offline_rules` | `compile_correction` with the deterministic offline rules | typed text |
| `manual_structured` | a structured edit (rule type, object or every object, phase, limit, status; orderings; goals) entered by an independent editor who is not the case author and did not see the gold | typed text |

Identical across conditions: the base specification (the reference
demonstration after review), the scene of each case, `mirror.plan.plan`,
`mirror.sim.SimEnv`, `mirror.evaluate.check` and the gold specification.
Reteaching (a new demonstration) is not a condition here; it belongs to the
impact study (`study/`), where time to a valid specification is measured.

## Cases

* **Authors.** 5 to 8 independent authors (robotics engineers or integrators),
  none of whom wrote MIRROR's compiler, prompts or earlier benchmarks; about 15
  cases each, at least 3 multi-turn.
* **Categories.** positive (a supported change), negative (must not change what
  is enforced, for example confirming a freedom), ambiguous (the right answer is
  a question), unsupported (the right answer is a refusal), infeasible (a valid
  change that no motion can satisfy in that scene; the right result is a proven
  stop, not a violation). Target mix in the design file.
* **Coverage.** New wording (word-bigram overlap with the earlier benchmark and
  the prompts is checked before the freeze; overlapping cases are flagged and
  scored separately), every object scope, every phase, every supported rule type,
  orderings, goal changes, reversals and exceptions in later turns.
* **Speech.** Spoken turns come only from real, consenting speakers, recorded
  with the acquisition notes in the author packet. Synthetic speech is never
  mixed into these results; if any is used for testing it is reported apart.
* **Gold.** Each turn is adjudicated by two people who did not write the case.
  Disagreements are resolved before the freeze and kept with the case.
* **Manual edits.** Collected from editors before the freeze, per turn, with the
  editor's own measured time. An empty edit means the editor declined to change
  the specification.

## Development and held-out access

* **Development cases** (`mirror/evaluate/protocol_v3/dev_cases.json`) are
  written by the project team, use reserved development seeds 9600 to 9699 and
  the reference scenes, and exist to build the harness. Their manual edits are
  team fixtures equal to the gold, so the manual condition on them measures
  nothing about people.
* **Held-out cases** are sent by authors to a custodian who is not a developer
  ([author packet](v3_author_packet.md)). The custodian publishes only a
  commitment (`python -m mirror.evaluate.protocol_v3 commit BUNDLE`): sha256, a
  hash of the case ids, counts per author and per category. The freeze manifest
  records it. The loader refuses a bundle whose hash differs, a bundle inside
  the repository before the run has finished, and any bundle without a frozen
  manifest. After the run the bundle is committed under `results/protocol_v3/`.
* **Layouts** for held-out cases are assigned at freeze from reserved ranges
  never used before (evaluation mixed 90000 to 90199, shortcut 91000 to 91099,
  blocked 90000 to 90049; the largest seed in any earlier ledger is 80007).
  Their generation is logged in the protocol's own ledger, not in the earlier
  registrations' ledgers. Development runs cannot generate evaluation layouts.

## Freeze

Before the bundle is opened, `mirror/evaluate/protocol_v3/freeze.py` writes a
manifest with: the design hash, the `mirror/` source hash, the prompt revision
and template hash, the checker id, the planner configuration hash, model ids and
client modes, the spend cap and per-turn reservation, the held-out commitment
and the assigned layout seeds. `verify` recomputes every hash and the runner
refuses on any difference. A frozen manifest is never edited. A change after any
failure creates version 3.1 with a new manifest, new seeds and a new held-out
bundle; the failed version stays reported.

## Metrics

Per turn: exact (outcome and enforced state equal the gold), false accept, false
refusal, wrong-scope edit (changed an object kind the gold turn did not),
clarification asked, invalid or error, latency.

Per case: final specification exact; **end-to-end success** (the rollout of the
authored specification passes the checker under the *gold* specification; for
infeasible cases, the planner stops with a proof); violations and completion
under gold; planning time, simulated execution time, path length and routes.
Requirements the checker cannot verify make the case **unknown**, never a
success: today this applies to confirmed orderings, which the checker does not
report on (the probe looks for the ordering id in the checker's report).

Per run: cost at list prices, network calls, attempt status counts.

Old behaviour: the base specification's rollout on each scene is re-checked
under the gold specification, so the old behaviour's rejection is shown
independently of any condition.

Scoring is semantic: a rule on every object equals the same rule on each object
kind (the earlier benchmark compared rule ids strictly). A manual abstention on
an ambiguous or unsupported turn counts as a refusal of unspecified type.

## Every attempt counts

An attempt is one case under one condition. `attempts.jsonl` gets a start line
before any work and an end line with the status: completed, timeout (600 s per
attempt, each in its own process), error, budget_stopped or interrupted (a start
without an end, found on resume). A replay request without a recording is "not
measured", listed by case and never replaced by another interpreter. Nothing is
filtered by outcome.

## Analysis

* **Primary:** case-level end-to-end success, `mirror_model` vs `offline_rules`
  and `mirror_model` vs `manual_structured`, exact two-sided McNemar, unknown
  cases excluded and counted.
* **Secondary:** turn exact, false accepts and wrong-scope turns per condition
  with Wilson intervals; per category; per author; real-speaker vs typed turns
  for `mirror_model`.
* **Pre-stated checks** (in the design file): false accepts at most 5% of
  non-apply turns; wrong-scope turns at most 5% of applied turns; end-to-end
  success at least the manual condition's minus 10 points; real-speaker exact at
  least typed exact minus 10 points. These were written after the development dry
  run below, which is disclosed here.
* Perception is not part of this protocol: the demonstration trace is the
  simulator's. The video and robustness registrations report perception
  separately; composing perception with authoring needs both measured on the
  same cases, which this protocol does not claim.

## Harness

`mirror/evaluate/protocol_v3/` (new modules; the frozen evaluation runners are
not imported or changed):

```bash
# development dry run: no network, no evaluation layouts
python -m mirror.evaluate.protocol_v3 dry-run --out <dir> --mirror-mode replay   # recorded Muse Spark responses only
python -m mirror.evaluate.protocol_v3 dry-run --out <dir> --mirror-mode offline  # offline rules stand in, labelled
python -m mirror.evaluate.protocol_v3 dry-run --out <dir> --no-execute           # authoring only
python -m mirror.evaluate.protocol_v3 report <dir>
```

Outputs: `attempts.jsonl` (ledger), `results.jsonl`, `old_runs.json`,
`summary.json`, `report.md`. Tests: `tests/protocol_v3/`.

## Development dry run (not evidence)

Run on the 18 team-written development cases (20 turns) at the branch's base
commit, 2 November 2026, executing in MuJoCo, with the offline rules standing in
for the model in `mirror_model` (no Muse Spark recording exists for this
wording, and replay correctly reports those cases as not measured; one
development case reuses a recorded benchmark sentence and replays exactly). It
tests the harness, not MIRROR:

* All 54 attempts completed; nothing was dropped.
* Offline rules: 15 of 20 turns exact, 2 false refusals, 1 error, no false
  accepts, no wrong-scope edits.
* Base-specification rollouts re-checked under the gold: of 12 cases whose gold
  changes the specification, 6 reject the old behaviour, in 5 the change did not
  matter in that scene, and 1 (an ordering) is unverified.
* End-to-end: 16 of 17 known cases succeed in every condition and 1 is unknown
  (the ordering case); the failure is the clearance case below.
* Files: [`results/protocol_v3/dryrun-dev-2026-11-02/`](../../results/protocol_v3/dryrun-dev-2026-11-02/).
* Found defects, recorded rather than worked around: the offline
  compiler raises a duplicate-id error on "keep it at least 3 cm away from the
  wall"; with a confirmed 3 cm clearance rule for the cup the planner chose a
  route that the checker then flagged for that rule (development seed 9605); and
  a confirmed "marker before cup" ordering passed a cup-first rollout because the
  checker does not monitor ordering, so the harness reports such cases as
  unknown.

## Needs budget (not approved)

| Job | Calls | Max cost |
|---|---|---|
| J0, optional: record Muse Spark on the development cases | at most 22 `compile_correction` requests to `muse-spark-1.3`, at most 2 repair retries each | $1.50 |
| J1: the frozen held-out run | at most 250 turns × 3 attempts to `muse-spark-1.3`; at most 120 real-speaker clips (15 minutes of audio) to `muse-voice-transcribe-1.0` | $8.50 (expected about $3; admission reserves $0.05 per turn) |
| J2: the frozen language-understanding run ([v3_language.md](v3_language.md)) | at most 300 turns × 3 attempts to `muse-spark-1.3` | $6 (expected about $3.50) |

Prerequisites besides the budget: recruited independent authors, editors,
adjudicators and speakers with signed consent (`study/consent_form.md`), a
custodian who is not a developer, and a frozen manifest committed before the
bundle is opened. Simulation runs on CPU (about 400 executions of about 5 s).
