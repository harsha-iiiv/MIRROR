"""Protocol v3 command line.

  python -m mirror.evaluate.protocol_v3 dry-run --out DIR [--conditions ...] [--mirror-mode replay|offline]
                                               [--no-execute] [--timeout S]
  python -m mirror.evaluate.protocol_v3 report DIR
  python -m mirror.evaluate.protocol_v3 commit HOLDOUT.json     # custodian: hashes and counts only
  python -m mirror.evaluate.protocol_v3 verify MANIFEST.json

Dry runs read only the development cases and never generate evaluation
layouts. ``--mirror-mode offline`` substitutes the offline rules for the model
in the mirror_model condition and labels it so; it is a harness check, not a
measurement of the model. There is deliberately no held-out run command until
a manifest is frozen; see docs/protocols/v3_authoring.md.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import conditions as C
from .access import DEV_CASES, commitment, load_dev_cases
from .report import write


def _mirror_condition(mode: str):
    if mode == "offline":
        cond = C.offline_rules()
        return C.Condition("mirror_model", cond.run_turn, "offline interpreter (stand-in for the model; dry run only)")
    from mirror.interpret import LLMClient
    from mirror.interpret.llm import REPO_ROOT as LLM_ROOT
    if mode != "replay":
        raise SystemExit("dry runs use replay or offline only; live and record belong to an approved, frozen run")
    client = LLMClient(mode="replay", model="muse-spark-1.3",
                       cassette_dir=LLM_ROOT / "data" / "cassettes_muse")

    def transcriber(path):
        from mirror.interpret.voice import transcribe
        from mirror.meta import MetaClient
        return transcribe(path, MetaClient(mode="replay"))
    return C.mirror_model(client, transcriber=transcriber)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m mirror.evaluate.protocol_v3", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dry-run")
    d.add_argument("--out", required=True)
    d.add_argument("--cases", default=str(DEV_CASES))
    d.add_argument("--conditions", default="mirror_model,offline_rules,manual_structured")
    d.add_argument("--mirror-mode", default="replay", choices=["replay", "offline"])
    d.add_argument("--no-execute", action="store_true")
    d.add_argument("--timeout", type=float, default=None)
    d.add_argument("--only", default="", help="comma-separated case ids")
    r = sub.add_parser("report")
    r.add_argument("out")
    c = sub.add_parser("commit")
    c.add_argument("bundle")
    v = sub.add_parser("verify")
    v.add_argument("manifest")
    a = ap.parse_args(argv)

    if a.cmd == "dry-run":
        from .runner import run
        cf = load_dev_cases(Path(a.cases))
        if a.only:
            keep = set(a.only.split(","))
            cf = cf.model_copy(update={"cases": [x for x in cf.cases if x.id in keep]})
        conds = []
        for name in a.conditions.split(","):
            conds.append({"mirror_model": lambda: _mirror_condition(a.mirror_mode),
                          "offline_rules": C.offline_rules, "manual_structured": C.manual_structured}[name]())
        out = Path(a.out)
        run_id = f"dev-{time.strftime('%Y%m%dT%H%M%S')}"
        info = run(cf, conds, out, run_id=run_id, base_dir=Path(a.cases).resolve().parent,
                   execute=not a.no_execute, timeout_s=a.timeout)
        if not a.no_execute:
            from .runner import old_run_checks
            (out / "old_runs.json").write_text(json.dumps(old_run_checks(cf), indent=2) + "\n")
        rp = write(out, title="Protocol v3 dry run on development cases (not evidence)",
                   label=("Development cases written by the project team; manual edits are team fixtures; "
                          f"mirror_model mode: {a.mirror_mode}. Run {run_id}. Not an evaluation result."),
                   n_cases=len(cf.cases))
        print(json.dumps({**info, "report": str(rp)}, indent=2))
        return 0
    if a.cmd == "report":
        out = Path(a.out)
        print(write(out, title="Protocol v3 run", label=f"Rebuilt from {out}", n_cases=None))
        return 0
    if a.cmd == "commit":
        print(json.dumps(commitment(Path(a.bundle)), indent=2))
        return 0
    if a.cmd == "verify":
        from .freeze import verify
        errs = verify(json.loads(Path(a.manifest).read_text()))
        print("\n".join(errs) or "manifest matches the current tree")
        return 1 if errs else 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
