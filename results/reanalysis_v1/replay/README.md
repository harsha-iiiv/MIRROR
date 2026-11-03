# Offline replays of the paired registrations (derived)

`python -m mirror.evaluate.reanalysis.replay results/<registration> --out <file> --label <code state>`
re-runs every frozen layout of a stored paired registration with every network call refused, the
specification pair rebuilt from the registration's recorded model responses (sha256 checked against the
freeze), and layout accesses written to a scratch ledger. The registration's own files are not touched.
For the freeze-commit runs the same script was run against that commit's own code (its code hash equals the
frozen hash; runtime Python 3.11.18, NumPy 2.4.6, MuJoCo 3.17.0, SciPy 1.17.1, as frozen).

Comparison levels per trial: `scene` (layout sha256), `outcome` (status, completion, plan status, hard and
advisory violations, forbidden contacts, per object placed / released / settled), `detail` (outcome plus beads
kept and route labels). Both conditions of a seed must match.

| registration | code replayed | code = frozen hash | network calls | scenes | outcomes | details |
|---|---|---|---|---|---|---|
| eval (first, gpt-5.5 specs) | `ac021f3` (freeze) | yes | 0 | 95/95 | 95/95 | 95/95 |
| eval | `1787aa6` (+ this package) | no | 0 | 95/95 | 95/95 | 95/95 |
| eval_muse (Muse Spark specs) | `864060d` (freeze) | yes | 0 | 95/95 | 95/95 | 94/95 |
| eval_muse | `1787aa6` (+ this package) | no | 0 | 95/95 | 95/95 | 94/95 |

The one difference is the same in both eval_muse replays: shortcut seed 60002, original specification (the
cup is tilted through the slot and spills in every run): the stored report keeps 6 of 8 beads, both replays
keep 2 of 8. Status, completion and every violation agree. The replay at the frozen code is identical to the
replay at today's code, so the difference is not a code change. The stored run was made on a different machine
(`results/eval_muse/RUN_NOTES.md`, attempt 2); the most likely cause is that bead spill after a tilt is
sensitive to floating-point differences between machines. This is not proven here. The evaluation therefore reproduces at the outcome level
(every scene, status and violation-free verdict), not bit-for-bit in bead counts.

The blocked reference case gives the same statuses as stored (original succeeds, corrected is refused by the
planner as infeasible) in all four replays.
