#!/usr/bin/env python
"""Run MIRROR evaluations.

  run_eval.py dev     --seeds 0-5 --out results/dev
  run_eval.py freeze  --seeds 0-19 --manifest results/eval/freeze.json
  run_eval.py eval    --manifest results/eval/freeze.json --out results/eval
  run_eval.py blocked --out results/blocked
  run_eval.py recheck --log steplog.npz --scene scene.json --spec spec.json

Specifications default to the hand-written reference pair
(mirror.evaluate.specs); pass --spec-a/--spec-b JSON files (TaskSpec) to
evaluate specifications exported from an authoring session.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mirror.contracts import SceneState, TaskSpec  # noqa: E402


def parse_seeds(text: str) -> list[int]:
    out: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part[1:]:
            a, b = part.split("-", 1) if not part.startswith("-") else part[1:].split("-", 1)
            out += list(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def load_specs(args) -> tuple[TaskSpec, TaskSpec]:
    from mirror.evaluate.specs import reference_specs
    a, b = reference_specs()
    if args.spec_a:
        a = TaskSpec.model_validate_json(Path(args.spec_a).read_text())
    if args.spec_b:
        b = TaskSpec.model_validate_json(Path(args.spec_b).read_text())
    return a, b


def eval_seeds(seeds: list[int]) -> list[int]:
    """Eval seeds are offset by EVAL_SEED_BASE when the scene module defines it
    and the given seeds are small indices."""
    try:
        from mirror.scenes import EVAL_SEED_BASE
    except ImportError:
        return seeds
    return [s + EVAL_SEED_BASE if s < EVAL_SEED_BASE else s for s in seeds]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", choices=["dev", "freeze", "eval", "blocked", "recheck"])
    p.add_argument("--seeds", default="0-5")
    p.add_argument("--out", default=None)
    p.add_argument("--manifest", default=None)
    p.add_argument("--spec-a", default=None)
    p.add_argument("--spec-b", default=None)
    p.add_argument("--workers", type=int, default=1, help="parallel trials, capped at 2")
    p.add_argument("--max-sim-time", type=float, default=120.0)
    p.add_argument("--save-logs", action="store_true",
                   help="save every step log (npz) under results/tmp/<out name>/logs (not versioned)")
    p.add_argument("--no-blocked", action="store_true", help="skip the blocked reference case")
    p.add_argument("--n-boot", type=int, default=10_000)
    p.add_argument("--log", default=None)
    p.add_argument("--scene", default=None)
    p.add_argument("--spec", default=None)
    args = p.parse_args(argv)

    from mirror import evaluate as ev

    if args.mode == "recheck":
        from mirror.statelog import StateLog
        log = StateLog.load(args.log)
        scene = SceneState.model_validate_json(Path(args.scene).read_text())
        spec = TaskSpec.model_validate_json(Path(args.spec).read_text())
        rep = ev.check(log, spec, scene, condition=f"recheck under v{spec.version}")
        print(rep.model_dump_json(indent=1))
        return 0

    spec_a, spec_b = load_specs(args)
    if args.mode == "blocked":
        out = Path(args.out or ROOT / "results" / "blocked")
        out.mkdir(parents=True, exist_ok=True)
        res = ev.blocked_case(spec_a, spec_b, max_sim_time=args.max_sim_time)
        (out / "blocked.json").write_text(json.dumps(res, indent=1, default=str))
        for r in res:
            print(f"{r['condition']:>10}: {r['status']:<16} {r['failure_class'] or '':<34} "
                  f"independent={r['independent']['verdict']}")
        return 0

    seeds = parse_seeds(args.seeds)
    if args.mode == "freeze":
        if not args.manifest:
            p.error("--manifest is required")
        m = ev.freeze(spec_a, spec_b, eval_seeds(seeds), "eval", args.manifest,
                      config={"workers": min(args.workers, 2), "max_sim_time": args.max_sim_time})
        print(f"froze {len(m.seeds)} eval seeds, code {m.code_sha256[:12]}, "
              f"A {m.spec_a['sha256'][:12]}, B {m.spec_b['sha256'][:12]} -> {args.manifest}")
        return 0

    split = "eval" if args.mode == "eval" else "dev"
    if split == "eval":
        if not args.manifest:
            p.error("eval requires --manifest (run freeze first)")
        from mirror.evaluate.models import FreezeManifest
        seeds = FreezeManifest.model_validate_json(Path(args.manifest).read_text()).seeds
    out = Path(args.out or ROOT / "results" / split)
    rep = ev.paired_eval(spec_a, spec_b, seeds, split=split, manifest_path=args.manifest,
                         workers=args.workers,
                         log_dir=(ROOT / "results" / "tmp" / out.name / "logs") if args.save_logs else None,
                         max_sim_time=args.max_sim_time, n_boot=args.n_boot,
                         include_blocked=not args.no_blocked)
    paths = ev.write_report(rep, out)
    ca, cb = rep.config["conditions"]
    v = rep.paired["violation_free_success"]
    print(f"{split}: {len(rep.trials)} scenes | violation-free success {ca} "
          f"{rep.metrics[ca].violation_free_success}/{len(rep.trials)}, {cb} "
          f"{rep.metrics[cb].violation_free_success}/{len(rep.trials)} | McNemar p={v['mcnemar_p_exact']:.4g}")
    print(f"report: {paths['markdown']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
