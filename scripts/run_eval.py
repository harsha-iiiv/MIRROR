#!/usr/bin/env python
"""Run MIRROR evaluations.

Pre-registered evaluation (primary, shortcut stratum and blocked split from one
manifest, one combined report):
  run_eval.py freeze-final --manifest results/eval/freeze.json
  run_eval.py final        --manifest results/eval/freeze.json --out results/eval --save-rollouts
Rehearsal of the same pipeline on development seeds:
  run_eval.py freeze-final --rehearsal --manifest results/rehearsal/freeze.json
  run_eval.py final        --manifest results/rehearsal/freeze.json --out results/rehearsal

Other modes:
  run_eval.py dev     --seeds 0-5 [--family shortcut] --out results/dev [--save-rollouts]
  run_eval.py freeze  --seeds 0-19 --manifest results/eval1/freeze.json
  run_eval.py eval    --manifest results/eval1/freeze.json --out results/eval1
  run_eval.py blocked --out results/blocked
  run_eval.py recheck --log steplog.npz --scene scene.json --spec spec.json

Specifications default to the pre-registered pair (recorded-model replay on the
pinned reference demonstration, checked against the offline interpreter).
--reference-specs uses the hand-written pair; --spec-a/--spec-b load TaskSpec
JSON files exported from an authoring session.
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
    from mirror.evaluate.specs import evaluation_specs, reference_specs
    if getattr(args, "reference_specs", False):
        a, b = reference_specs()
    else:
        a, b, _ = evaluation_specs()
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


def _rehearsal_ledger(args) -> None:
    """Rehearsals record their layouts in a disposable ledger under
    results/tmp unless MIRROR_EVAL_LEDGER is set, so they never touch the
    evaluation ledger. A new rehearsal freeze starts that ledger afresh."""
    import os
    rehearsal = args.rehearsal
    if args.mode == "final" and Path(args.manifest).exists():
        from mirror.evaluate.models import FreezeManifest
        rehearsal = FreezeManifest.model_validate_json(Path(args.manifest).read_text()).kind == "rehearsal"
    if not rehearsal or os.environ.get("MIRROR_EVAL_LEDGER"):
        return
    led = ROOT / "results" / "tmp" / f"{Path(args.manifest).resolve().parent.name}-ledger.jsonl"
    led.parent.mkdir(parents=True, exist_ok=True)
    if args.mode == "freeze-final" and led.exists():
        led.unlink()
    os.environ["MIRROR_EVAL_LEDGER"] = str(led)


def _prereg(args, p) -> int:
    """Benchmarks registered in mirror.evaluate.prereg (design -> freeze -> final)."""
    from mirror.evaluate import designs, prereg
    name = args.registration
    if not name or name not in prereg.REGISTRY:
        p.error(f"{args.mode} needs --registration one of {', '.join(prereg.REGISTRY)}")
    pp = prereg.paths(name)
    if args.mode == "design":
        doc = prereg.write_design(name, designs.DESIGNS[name](), audit_paths=tuple(args.audit_path),
                                  replace=args.replace_design)
        print(f"design {name}: {len(prereg._tokens(doc)['seeds'])} seeds audited, {len(doc.get('inputs', []))} inputs, "
              f"audit {doc['audit']['text_files']} files clean -> {pp['design'].relative_to(ROOT)} "
              f"(sha256 {prereg.sha256_file(pp['design'])[:16]})")
        return 0
    if args.mode == "rehearse":
        design, inputs, out = designs.rehearsal_setup(name)
        kw = {}
        if name == "video":
            from mirror.evaluate.bench_video import oracle_tracker
            kw = dict(tracker=oracle_tracker, tracker_label="ground-truth masks (oracle; rehearsal only)")
        if name in ("voice", "video", "robustness"):
            import os
            from mirror.meta import MetaClient
            mc = MetaClient.from_env(cassette_dir=out / "cassettes_meta", usage_log=out / "meta_usage.jsonl")
            kw["meta_client" if name != "robustness" else "client"] = mc
            if name == "video":
                from mirror.interpret import LLMClient
                kw["spark_client"] = LLMClient.from_env(cassette_dir=out / "cassettes_llm")
        rep = prereg.rehearse(name, design, inputs, out, **kw)
        print(f"rehearsal {name}: {rep['status']}; report {(out / 'report.md').relative_to(ROOT)}")
        return 0
    if args.mode == "inputs":
        files, prov = (designs.INPUTS[name](from_dir=Path(args.from_dir)) if (name in ("video", "video2") and args.from_dir)
                       else designs.INPUTS[name]())
        doc = prereg.write_inputs(name, files, provenance=prov, audit_paths=tuple(args.audit_path),
                                  replace=args.replace_design)
        print(f"inputs {name}: {len(files)} files, ledger lines {doc['ledger_after_inputs']['lines']} -> "
              f"{pp['inputs'].relative_to(ROOT)} (sha256 {prereg.sha256_file(pp['inputs'])[:16]})")
        return 0
    if args.mode == "status":
        missing = prereg.harness_ready(name)
        print(f"{name}: design {'yes' if pp['design'].exists() else 'no'}, "
              f"inputs {'yes' if pp['inputs'].exists() else 'no'}, "
              f"freeze {'yes' if pp['freeze'].exists() else 'no'}, "
              f"ledger entries {len(prereg.Ledger(pp['ledger']).entries())}, "
              f"harness {'ready' if not missing else 'missing ' + ', '.join(missing)}")
        return 0
    if args.mode == "freeze-final":
        fz = prereg.freeze(name, audit_paths=tuple(args.audit_path), allow_missing=args.allow_missing_harness)
        print(f"froze {name}: code {fz['code_sha256'][:12]} ({fz['code_files']} files), design "
              f"{fz['design_sha256'][:12]} -> {pp['freeze'].relative_to(ROOT)}")
        return 0
    if args.mode == "final":
        rep = prereg.run(name, **({"workers": args.seed_workers} if args.seed_workers else {}))
        print(f"{name}: {rep['status']}; report {pp['report_md'].relative_to(ROOT)}")
        return 0
    p.error(f"mode {args.mode} does not apply to registration {name}")
    return 2


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", choices=["dev", "freeze", "eval", "blocked", "recheck", "design", "inputs", "freeze-final",
                                    "final", "status", "rehearse"])
    p.add_argument("--family", default="mixed", help="scene family for dev/freeze/eval: mixed | shortcut")
    p.add_argument("--save-rollouts", action="store_true",
                   help="write playback Rollout JSON (15 Hz) to <out>/rollouts/seed<k>_<condition>.json")
    p.add_argument("--rehearsal", action="store_true", help="freeze-final on development seeds")
    p.add_argument("--reference-specs", action="store_true", help="use the hand-written spec pair")
    p.add_argument("--spec-source", default="replay", choices=["replay", "offline"],
                   help="freeze-final: how the pre-registered specs are built (offline only for --rehearsal)")
    p.add_argument("--registration", default=None, choices=["muse", "voice", "video", "video2", "robustness"],
                   help="design/freeze-final/final/status: a named registration (own seeds, ledger, output)")
    p.add_argument("--from-dir", default=None,
                   help="inputs --registration video: demos rendered elsewhere (seed<k>/<demo>/video.json + versions.json)")
    p.add_argument("--replace-design", action="store_true",
                   help="design: rewrite an uncommitted design (refused once frozen or measured)")
    p.add_argument("--allow-missing-harness", action="store_true",
                   help=argparse.SUPPRESS)
    p.add_argument("--audit-path", action="append", default=[],
                   help="freeze-final --registration: extra file or directory searched for the seeds")
    p.add_argument("--seeds", default="0-5")
    p.add_argument("--out", default=None)
    p.add_argument("--manifest", default=None)
    p.add_argument("--spec-a", default=None)
    p.add_argument("--spec-b", default=None)
    p.add_argument("--workers", type=int, default=1, help="parallel trials, capped at 2")
    p.add_argument("--seed-workers", type=int, default=None,
                   help="final --registration video2: seeds processed at once (default: the design's 'workers'); "
                        "a restarted run resumes from the per-seed checkpoints")
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

    if args.registration in ("voice", "video", "video2", "robustness") or args.mode in ("design", "inputs", "status", "rehearse"):
        return _prereg(args, p)

    if args.registration:
        from mirror.evaluate.final import REGISTRATIONS
        reg = REGISTRATIONS[args.registration]
        args.manifest = args.manifest or str(ROOT / reg["manifest"])
        args.out = args.out or str(ROOT / reg["out"])
        if Path(args.manifest).resolve() != (ROOT / reg["manifest"]).resolve():
            p.error(f"registration {args.registration} uses {reg['manifest']}")
    if args.mode in ("freeze-final", "final") and args.manifest:
        _rehearsal_ledger(args)

    if args.mode == "freeze-final" and args.registration:
        from mirror.evaluate.final import freeze_registration
        if args.rehearsal or args.spec_source != "replay":
            p.error("a registration freezes replayed specifications on its own seeds")
        m = freeze_registration(args.registration, audit_paths=tuple(args.audit_path),
                                config={"workers": min(args.workers, 2), "max_sim_time": args.max_sim_time})
        for st in m.strata:
            print(f"  {st['name']:9s} {st['split']:8s} {st['family']:9s} {st['seeds'][0]}-{st['seeds'][-1]}")
        io = m.config["freeze_io"]
        print(f"froze registration {args.registration}: A {m.spec_a['sha256'][:12]}, B {m.spec_b['sha256'][:12]}, "
              f"network {io['network_connections']}, layouts {io['layouts_sampled']} -> {args.manifest}")
        return 0

    if args.mode == "freeze-final":
        from mirror.evaluate.final import freeze_final, rehearsal_strata
        if not args.manifest:
            p.error("--manifest is required")
        if args.spec_a or args.spec_b or args.reference_specs:
            p.error("freeze-final uses the pre-registered specifications only")
        m = freeze_final(args.manifest, strata=rehearsal_strata() if args.rehearsal else None,
                         kind="rehearsal" if args.rehearsal else "final", spec_source=args.spec_source,
                         config={"workers": min(args.workers, 2), "max_sim_time": args.max_sim_time})
        for st in m.strata:
            print(f"  {st['name']:<9} {st['split']:<8} {st['family']:<9} {len(st['seeds'])} seeds")
        print(f"froze {m.kind}: code {m.code_sha256[:12]}, A {m.spec_a['sha256'][:12]}, "
              f"B {m.spec_b['sha256'][:12]} -> {args.manifest}")
        return 0

    if args.mode == "final":
        from mirror.evaluate.final import run_final
        from mirror.evaluate.models import FreezeManifest
        if not args.manifest:
            p.error("final requires --manifest (run freeze-final first)")
        kind = FreezeManifest.model_validate_json(Path(args.manifest).read_text()).kind
        out = Path(args.out or ROOT / "results" / ("rehearsal" if kind == "rehearsal" else "eval"))
        if kind == "rehearsal" and out.resolve() == (ROOT / "results" / "eval").resolve():
            p.error("a rehearsal manifest cannot write into results/eval")
        f = run_final(args.manifest, workers=args.workers, save_rollouts=args.save_rollouts,
                      out_dir=out, n_boot=args.n_boot, max_sim_time=args.max_sim_time,
                      log_root=(ROOT / "results" / "tmp" / out.name / "logs") if args.save_logs else None)
        paths = ev.write_final_report(f, out)
        for name, r in f.strata.items():
            ca, cb = r.config["conditions"]
            v = r.paired["violation_free_success"]
            print(f"{name:<9} n={len(r.trials):<3} violation-free success {ca} "
                  f"{r.metrics[ca].violation_free_success}, {cb} {r.metrics[cb].violation_free_success} | "
                  f"exercised {r.paired['correction_exercised']['n']} | McNemar p={v['mcnemar_p_exact']:.4g}")
        print(f"report: {paths['markdown']}")
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
        from mirror.evaluate.final import check_not_retired
        check_not_retired([dict(split="eval", seeds=eval_seeds(seeds))])
        m = ev.freeze(spec_a, spec_b, eval_seeds(seeds), "eval", args.manifest,
                      config={"workers": min(args.workers, 2), "max_sim_time": args.max_sim_time},
                      family=args.family)
        print(f"froze {len(m.seeds)} eval seeds, code {m.code_sha256[:12]}, "
              f"A {m.spec_a['sha256'][:12]}, B {m.spec_b['sha256'][:12]} -> {args.manifest}")
        return 0

    split = "eval" if args.mode == "eval" else "dev"
    if split == "eval":
        if not args.manifest:
            p.error("eval requires --manifest (run freeze first)")
        from mirror.evaluate.models import FreezeManifest
        fm = FreezeManifest.model_validate_json(Path(args.manifest).read_text())
        seeds, args.family = fm.seeds, fm.family
    out = Path(args.out or ROOT / "results" / split)
    rep = ev.paired_eval(spec_a, spec_b, seeds, split=split, manifest_path=args.manifest,
                         workers=args.workers, family=args.family,
                         rollout_dir=(out / "rollouts") if args.save_rollouts else None,
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
    from mirror.evaluate import FamilyUnsupported, FreezeError
    from mirror.evaluate.specs import SpecAgreementError
    try:
        raise SystemExit(main())
    except (FamilyUnsupported, FreezeError, SpecAgreementError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        raise SystemExit(3)
