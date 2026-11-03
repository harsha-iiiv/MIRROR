# Correction demonstrations at two planner prices (development layouts)

**Not a frozen evaluation.** Development seeds only, written and run by the
project team to show what each kind of correction does under the planner of
record (rotation price 0.2445 m/rad, calibrated to execution time in
[objective_v1](../../eval_objective_v1/attempt_1/report.md)) and under the
earlier price (0.05 m/rad).

* Source commit `09bb0a4b4623b3b7f0c60c63ec4a129d4f25066a` (clean tree), Linux
  x86_64, Python 3.11.18, NumPy 2.4.6, MuJoCo 3.17.0.
* Offline interpreter (no model call), MuJoCo execution, independent checker.
* Layouts: transfer on development seeds 6200-6202 (mixed) and 6300-6302
  (shortcut), 6 per case; blocked case on the reference blocked scene and
  development seeds 6400-6402. Each layout runs the old and the new
  specification, and the old run is re-checked under the new one.
* Command (per price):

```bash
python scripts/demo_corrections.py --out <dir> \
  --cases object_scope,phase_scope,ordering,ordering_start,clearance,multi_turn,blocked_refusal,ambiguous,unsupported \
  --rotation-penalty 0.2445    # or 0.05
```

Files kept here: each price's `report.md` and `manifest.json` and each case's
`summary.json` (every layout's statuses, rule verdicts, routes, object order
and measures). The per-step rollout files the script also writes (about 54 MB
per price) are not kept; re-run the command to regenerate them.

| Case (0.2445 m/rad) | Old run violates the new rule | New run succeeds |
|---|---|---|
| ordering: "Pack the marker before the cup." | 6/6 | 6/6 (packing order changed 6/6) |
| ordering_start: "Start with the block." | 6/6 | 6/6 (packing order changed 6/6) |
| clearance: "Keep the cup at least 6 cm away from the wall." | 6/6 (old 3.0 to 4.1 cm) | 6/6 (new 8.0 to 9.1 cm) |
| multi_turn: 6 cm, then 10 cm, then undo | 6/6 | 6/6 |
| blocked_refusal: "Keep the cup upright the whole time it is carried." | 3/3 (old tilts about 90° through the slot) | 0/3; 3/3 refused with a proof |
| object_scope: only the cup upright while carried | 0/6 (5/6 at 0.05) | 6/6 |
| phase_scope: cup may tilt in transit, must stand in the tray | 0/6 (0/6 at 0.05) | 6/6 |

"Be careful with the cup." and "Keep the cup 5 cm away from the marker." get a
clarifying question at both prices.
