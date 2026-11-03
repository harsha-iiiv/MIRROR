# Run notes: registration objective_v1

* Frozen at 02:28:45 UTC on 3 November 2026 (`freeze.json`, committed in `8be4d2e` before any layout
  was generated; the freeze sampled 0 layouts and opened 0 network connections).
* attempt_1, the only attempt, ran commit `8be4d2e` from a clean checkout, 03:10-03:53 UTC, 2 worker
  processes, under the shared heavy-work lock. It generated all 130 layouts (ledger entries 1-130,
  caller `mirror.evaluate.objective.run`) and wrote every file in `attempt_1/` except the files named below.
* Added after the run: `attempt_1/report.json` and `report.md` (computed from `records.jsonl` only),
  `attempt_1/conclusions.md` (written after reading the report), and `attempt_1/posthoc_ci_bounds.json`,
  a post hoc plan-only check at the calibration CI bounds. That check regenerated the 120 eval layouts
  again (ledger entries 131-250, caller `__main__`) and the 90 Muse-registration layouts (appended to
  `audit_repro/layout_ledger.jsonl`, never to that registration's own ledger). It executed nothing.
* `records.jsonl` keeps one record per layout and condition (1170); rollout frames were not kept.
