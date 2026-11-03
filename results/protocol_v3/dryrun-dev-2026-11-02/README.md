# Protocol v3 development dry run, 2 November 2026 (not evidence)

Harness check on the 18 team-written development cases
(`mirror/evaluate/protocol_v3/dev_cases.json`), executed in MuJoCo, with the
offline rules standing in for the model in the `mirror_model` condition (no
Muse Spark recording exists for this wording). Manual edits are team fixtures
equal to the gold. Nothing here measures MIRROR's model, people, or held-out
cases. Command:

    python -m mirror.evaluate.protocol_v3 dry-run --out <dir> --mirror-mode offline

Files: `attempts.jsonl` (ledger), `results.jsonl`, `old_runs.json`, `summary.json`, `report.md`.
