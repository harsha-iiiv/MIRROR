#!/usr/bin/env python
"""Planner-objective fairness study (objective_v1). See docs/protocols/objective_v1.md.

  repro      plan-only rotation sweep on the Muse registration's 90 layouts + discordant recount
  calibrate  route time model on development layouts only
  freeze     write results/eval_objective_v1/freeze.json (generates no layout)
  run        execute every condition on the frozen seeds (after the freeze is committed)
  report     write report.json / report.md from the run's records
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "results" / "eval_objective_v1"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("repro")
    c = sub.add_parser("calibrate")
    c.add_argument("--workers", type=int, default=1)
    fz = sub.add_parser("freeze")
    fz.add_argument("--audit-root", action="append", default=[],
                    help="extra folders searched for ledgers/outputs naming this registration's seeds")
    r = sub.add_parser("run")
    r.add_argument("--workers", type=int, default=1)
    r.add_argument("--attempt", required=True, help="attempt label, e.g. attempt_1")
    rp = sub.add_parser("report")
    rp.add_argument("--attempt", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "repro":
        out = OUT / "audit_repro"
        os.environ.setdefault("MIRROR_EVAL_LEDGER", str(out / "layout_ledger.jsonl"))
        from mirror.evaluate.objective import sweep
        print(json.dumps(sweep.run(out)["reproduced"], indent=1))
    elif args.cmd == "calibrate":
        from mirror.evaluate.objective import calibrate
        res = calibrate.run(OUT / "calibration", workers=args.workers)
        print(json.dumps(res["objective_of_record"], indent=1))
    elif args.cmd == "freeze":
        from mirror.evaluate.objective import design
        m = design.freeze(OUT / "freeze.json", extra_audit_roots=args.audit_root)
        print(json.dumps({k: m[k] for k in ("created_at", "code_sha256", "calibrated_rotation_penalty")}, indent=1))
    elif args.cmd == "run":
        from mirror.evaluate.objective import run as runner
        runner.run(OUT, attempt=args.attempt, workers=args.workers)
    elif args.cmd == "report":
        from mirror.evaluate.objective import report
        report.write(OUT, attempt=args.attempt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
