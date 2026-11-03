# Correction-benchmark chronology

Status: derived analysis of stored evidence, 2 November 2026. No original file was edited. Machine-readable
facts: `results/reanalysis_v1/chronology/report.json`, rebuilt with
`python -m mirror.evaluate.reanalysis.chronology`.

## Question

`mirror/interpret/benchmark/cases_holdout.json` says `frozen_at: 2026-10-31T04:45:00-07:00`.
`results/corrections_benchmark_holdout.json` says `generated_at: 2026-10-31T11:27:50Z`, which is
04:27:50 PDT, 17 minutes earlier. Read literally, the held-out results predate the freeze. What happened, and
what may be claimed?

## Evidence

All times PDT (UTC-7) on 31 October 2026.

| Source | What it records | Value |
|---|---|---|
| `cases.json` `frozen_at` | typed into the file | 04:25:00 |
| `cases_holdout.json` `frozen_at` | typed into the file | 04:45:00 |
| `generated_at` in each result | `time.gmtime()` in `scripts/bench_corrections.py`, set when the run **starts**, after the case file is read and hashed; UTC, with a `Z` suffix | frozen set, blind run: 04:22:31; frozen set after rule fixes: 04:27:47; held-out, first run: 04:27:50 |
| `recorded_at` in each recorded model response (`data/cassettes/corrections_benchmark/prompt_v1/`) | `time.gmtime()` when the response arrives | frozen set: 04:22:35 to 04:25:49 (44 responses); held-out: 04:27:54 to 04:29:48 (28 responses) |
| `cases_sha256` in each result | sha256 of the case-file bytes at run start | frozen set `4eaa2282…` and held-out `37d903dd…`, both equal to the committed files |
| gold answers copied into each result | case gold at run start | identical to the committed case files for every result |
| re-score of every stored result with the committed scorer | stored outputs, committed gold | every stored summary field reproduced (later scorer only adds the prompt-overlap split fields) |
| first commit containing the case files and their results | `git log --diff-filter=A` | all in `9637c2e`, committed 06:32:34 |
| last-modified time of the case files in the original working copy (outside the repository, observed 2 November) | file-system metadata | `cases.json` 04:21:15.8; `cases_holdout.json` 04:27:37.0 |
| contemporaneous project log (outside the repository; times when results were reported, so upper bounds) | 44 cases frozen (sha `4eaa2282…`) | 04:21:15 |
| | blind 38/44 run and 28 held-out cases frozen (sha `37d903dd…`) | 04:27:37 |
| | held-out results reported | 04:39:33 |

## Most likely explanation

1. `generated_at` is UTC and marks the start of a run, not its end. Read that way it is consistent with every
   recorded model response, which all come after it (by 3 to 5 seconds for the first response of each run).
2. Both `frozen_at` values were typed by hand and are wrong. The frozen set's `04:25:00` is 2.5 minutes after
   its first run started; the held-out set's `04:45:00` is 17 minutes after its first run started, 15 minutes
   after its last recorded response and 6 minutes after its results were reported. Neither is a freeze time.
3. The held-out file's last modification (04:27:37.0) coincides with the log entry reporting it frozen with
   the same hash (04:27:37). The first held-out run started 13 seconds later (04:27:50) and its first model
   response arrived 17 seconds later (04:27:54). The hash at run start equals the committed hash, so the file
   was not changed after the run started.

So the inconsistency comes from metadata (a hand-written timestamp and a UTC start time read as local or as
an end time), not from results that preceded their cases. That is the most likely explanation; it is not
proof.

## What can and cannot be claimed

Can be claimed:
- The held-out case file used by every held-out run is byte-identical to the committed file (sha256
  `37d903dd…`), and its gold answers are the ones in the results.
- By the recorded model-response times, the first recorded model run on the held-out cases began after the
  held-out file reached its final content, according to that file's modification time and a contemporaneous
  log entry, by 13 to 17 seconds.
- The held-out cases were written after the offline rules were fixed on the first set (the rule-fix run on the
  first set started at 04:27:47, three seconds before the held-out run).

Cannot be claimed:
- **Pre-registration.** Nothing in the repository orders the freeze before the runs: the case files and all
  their results were first committed together, two hours later (`9637c2e`, 06:32:34). The two
  timestamps that place the freeze before the run (file modification time, project log) are outside the
  repository and cannot be checked by a reader. Unlike the layout, voice, robustness and video registrations,
  whose freeze files were committed before their runs (see `results/manifests_v1/`), the correction
  benchmark was not pre-registered in that sense.
- **"Frozen at 04:45" or "frozen at 04:25".** Do not cite either `frozen_at`; both are inaccurate.
- **"Before any run."** The offline-rule interpreter leaves no timestamped trace (no recorded responses), so
  nothing excludes offline-rule runs on drafts of the held-out cases while they were being written. Only the
  recorded model run can be placed in time.
- **An independent or external held-out set.** The held-out cases were written by the same team, minutes
  after seeing the first set's results, and later prompt revisions (2 and 3) and the Muse Spark swap were made
  after the held-out results had been seen (already labelled post-hoc in the summary).

## Proposed wording

> Held-out correction cases (28) were written after the offline rules were fixed on the first set. The case
> file's hash matches every held-out result, and by the recorded response times the first model run on them
> began after the file's final save. The case files and results were committed together after the runs, so
> this benchmark is not pre-registered, and the `frozen_at` fields in the case files are hand-entered and
> inaccurate (see docs/protocols/chronology.md).

## Preserved originals

No original was edited: case files, result files, recorded responses and their `frozen_at` / `generated_at`
fields stay as they are. This document and `results/reanalysis_v1/chronology/report.json` are the corrected,
derived account.
