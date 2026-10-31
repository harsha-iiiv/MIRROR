# Run notes: registration `muse`

* **Frozen** at 16:34:38 UTC on 31 October 2026 (`freeze.json`, committed and
  pushed in `864060d` before any layout was generated).
* **Attempt 1** started at 16:37 UTC and was interrupted when the machine running
  it restarted, after it had generated the 90 primary and shortcut layouts
  (ledger entries 1-90). It wrote no report. Its partial per-trial files were set
  aside unread and are not part of these results.
* **Attempt 2** ran the same commit (`864060d`) from a clean checkout on a
  second machine with the same runtime as the manifest (Python 3.11.18,
  NumPy 2.4.6, MuJoCo 3.17.0, SciPy 1.17.1). The runner re-checked every hash in
  the manifest before each stratum. It generated all 95 layouts again (ledger
  entries 91-185, 17:15-17:18 UTC) and produced every file in this folder.
* The ledger keeps both attempts. All 185 entries come after the freeze.
* The simulator is deterministic for a given seed, code and runtime, so a third
  run reproduces these outcomes (see docs/EVALUATION.md, How to reproduce, with
  `--registration muse`).
