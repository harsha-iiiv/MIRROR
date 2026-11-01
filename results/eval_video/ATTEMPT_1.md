# Video registration, attempt 1: not completed (harness error)

Run from a clean worktree at the freeze commit 8e3b369, starting 31 October 2026 at about 18:56 PT. Every attempt is reported; this one did not produce a usable result.

## What went wrong

1. **The video-model observations never ran.** The harness passed the whole `observation` block of the design to `observe()` as keyword arguments. That block also holds descriptive notes (`event_time`, `matching`), so every call failed with `TypeError: observe() got an unexpected keyword argument 'event_time'` before any request was sent. All 24 observation attempts (12 muse-wonder-1.0, 12 muse-spark-1.3) failed this way, at no model cost. The unit test for the runner used a fake `observe` that accepted any keyword, so it did not catch the mismatch.
2. **The spec comparison therefore ran without observations.** The registered condition proposes the spec from the video-derived trace *with* the muse-spark-1.3 observations. Without them, the strict signature matched the ground-truth proposal on 4 of 12 seeds. This does not test the registered condition and is not reported as its result.
3. **The run stopped after 12 of 20 seeds** (70000 to 70011). The machine running it restarted at about 20:30 PT, and the runner cannot resume.

## What did run

- sam-3.1 tracking with muse-spark-1.3 grounding on seeds 70000 to 70011: 12 seeds, 1 to 4 re-seeds per seed, $3.50. Track files are in `attempt_1/tracks/`. No summary statistics were computed from them, and they are not used in any reported result.
- Spend: $3.75 in total (sam-3.1 $2.93, muse-spark-1.3 $0.82). Usage log: `attempt_1/meta_usage_run.jsonl`. The append-only ledger keeps every entry.

## Next

The frozen harness cannot be patched: the registration accepts amendments only before inputs exist, and the code hash is fixed. The fix (pass only the arguments `observe()` accepts, with a test that uses the real function), together with resumable and parallel seed processing, goes into a new registration on new seeds. That registration is frozen and pushed before any of its inputs are made. Thresholds are unchanged.
