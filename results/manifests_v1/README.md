# Evidence manifests, version 1

Derived, written once by `python -m mirror.evaluate.reanalysis.manifests` (never overwritten; changes go to a new version directory). Each JSON file lists, for one stored registration or benchmark: the freeze and result commits, the frozen code hash recomputed from the freeze commit's own tree with that commit's own hashing function, model ids, prompt revision and prompt-file hashes at the freeze commit, recorded-response hashes, input or scene hashes, and outcome digests. Commit times are the committer's clock, not an external timestamp.

| registration | freeze commit (time) | frozen code sha256 | freeze tree reproduces it | run start | first model call | results commit | models (network calls) | prompt revision |
|---|---|---|---|---|---|---|---|---|
| eval | `ac021f3` (31 Oct 06:50:40 PDT) | `b498c4b41664c589` | yes | 31 Oct 06:50:43 PDT | layouts from 31 Oct 06:50:44 PDT | `e23fac1` | gpt-5.5-2026-04-23 (specs replayed from recordings) | 3 |
| eval_muse | `864060d` (31 Oct 09:36:58 PDT) | `e77c4cec9c4d414d` | yes | 31 Oct 10:15:48 PDT | layouts from 31 Oct 09:37:16 PDT | `fc3e12d` | muse-spark-1.3 (specs replayed from recordings) | 3 |
| voice | `308ab44` (31 Oct 14:53:11 PDT) | `24fef070c1fb5c3a` | yes | 31 Oct 14:54:14 PDT | 31 Oct 14:54:17 PDT | `f74602b` | muse-voice-transcribe-1.0 (288) | 3 |
| robustness | `024f1cb` (31 Oct 15:33:14 PDT) | `f466fbebcb1e7e20` | yes | 31 Oct 15:36:19 PDT | 31 Oct 15:36:25 PDT | `cb03bf8` | muse-image-1.0 (63), muse-spark-1.3 (14), sam-3.1 (575) | 3 |
| video | `8e3b369` (31 Oct 18:54:20 PDT) | `74c73336fc308449` | yes | n/a (no report) | 31 Oct 18:56:36 PDT | none (attempt failed) | muse-spark-1.3 (103), sam-3.1 (75) | 3 |
| video2 | `80750a4` (31 Oct 23:30:06 PDT) | `f174076be48d0b84` | yes | 31 Oct 23:30:38 PDT | 31 Oct 23:30:55 PDT | `bb5eb64` | muse-spark-1.3 (172), muse-wonder-1.0 (16), sam-3.1 (94) | 3 |

Paired registrations (eval, eval_muse) also list every trial's scene sha256 and two outcome digests: `outcome` (status, completion, plan status, hard and advisory violations, forbidden contacts, per object placed/released/settled) and `detail` (outcome plus beads kept and route labels). `results/reanalysis_v1/replay/` compares offline, network-blocked replays against these digests.

The correction benchmark (`corrections_benchmark.json`) was not pre-registered: its case files and results were first committed together after the runs; see `docs/protocols/chronology.md`.

Notes:

- eval: `freeze.json` was committed in `ac021f3`; `manifest.json` (same frozen fields, checked) was first committed with the results.
- eval_muse: attempt 1 was interrupted after generating 90 layouts; attempt 2 produced every result (`RUN_NOTES.md`). The ledger keeps both; all entries follow the freeze commit.
- video (attempt 1): no report; tracking ran, observations failed on a harness error (`ATTEMPT_1.md`).
- video2: `RUN_NOTES.md` gives the start as 23:41 PT; the report's `started_unix` and the first model call are 23:30:38 and 23:30:55 PDT. The freeze commit (23:30:06) precedes both.
