"""Reproducible demonstrations of correction types beyond the prepared sentence.

For each case: old behaviour -> explicit specification change -> independent
re-check of the old rollout under the new specification -> new execution ->
transfer to development layouts that were not used while writing the rule
language. Everything runs offline: the offline rule interpreter compiles the
sentences (labelled "offline-rules"), the planner plans, MuJoCo executes and
the independent checker judges. No model is called and no evaluation seed is
touched (development seeds are below 10000; see TRANSFER_SEEDS).

    python scripts/demo_corrections.py [--out results/dev_v2/demo_corrections] [--cases a,b] [--quick]

Outputs per case: summary.json (spec versions and diffs, rule verdicts,
statuses, routes, timings, provenance) and the showcase rollouts (old and new)
with playback frames; plus report.md and manifest.json at the top level.
Existing output folders are never overwritten: pass a new --out.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mirror.contracts import TaskSpec  # noqa: E402
from mirror.evaluate import check  # noqa: E402
from mirror.evaluate import geom  # noqa: E402
from mirror.evaluate.checker import _transport_mask  # noqa: E402
from mirror.evaluate.specs import evaluation_specs  # noqa: E402
from mirror.evaluate.trial import execute_trial  # noqa: E402
from mirror.interpret.correct import apply_correction  # noqa: E402
from mirror.interpret.diff import spec_diff  # noqa: E402
from mirror.interpret.types import CorrectionRejected, NeedsClarification  # noqa: E402
from mirror.scenes import reference_scene, sample_layout  # noqa: E402
from mirror.statelog import tilt_deg  # noqa: E402

# Development layouts chosen before any run of this script and not used while
# the rule language was written. Both families, development split only.
TRANSFER_SEEDS = [("mixed", 6200), ("mixed", 6201), ("mixed", 6202), ("shortcut", 6300), ("shortcut", 6301),
                  ("shortcut", 6302)]

CASES = {
    "object_scope": dict(
        title="Object-specific scope: only the cup must stay upright while carried",
        scene="shortcut", turns=["Only the cup has to stay upright while it is carried; the marker can tilt."],
        kind="tighten", watch=["cup_0", "marker_0"]),
    "phase_scope": dict(
        title="Phase-specific scope: the cup may tilt in transit but must stand in the tray",
        scene="shortcut", setup=["Keep the cup upright the whole time it is carried."],
        turns=["The cup may tilt while it is carried, but it has to stand upright in the tray."],
        kind="relax", watch=["cup_0"]),
    "ordering": dict(
        title="Ordering (new rule, opposite to the demonstrated order): pack the marker before the cup",
        scene="demo", turns=["Pack the marker before the cup."], kind="tighten", watch=[]),
    "ordering_start": dict(
        title="Ordering by grasp: start with the block",
        scene="demo", turns=["Start with the block."], kind="tighten", watch=[]),
    "clearance": dict(
        title="Clearance (new constraint): keep the cup at least 6 cm from the wall",
        scene="demo", turns=["Keep the cup at least 6 cm away from the wall."], kind="tighten",
        watch=["cup_0", "block_0"]),
    "multi_turn": dict(
        title="Multi-turn update and reversal: 6 cm, then 10 cm, then undo",
        scene="demo", turns=["Keep the cup at least 6 cm away from the wall.", "Actually, make it 10 cm.",
                             "Undo that."], kind="tighten", watch=["cup_0"]),
    "ambiguous": dict(
        title="Ambiguous instruction: a question, not a guess",
        scene="demo", turns=["Be careful with the cup."], kind="question", watch=[]),
    "unsupported": dict(
        title="Unsupported request: clearance from another object",
        scene="demo", turns=["Keep the cup 5 cm away from the marker."], kind="question", watch=[]),
}


def _git(*args) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # pragma: no cover
        return ""


def _scene(name):
    return reference_scene(name)


def _rule_rows(report):
    return [dict(rule_id=r.rule_id, kind=r.kind, status=r.status.value, enforced=r.enforced, verdict=r.verdict,
                 detail=r.detail) for r in report.rule_results if r.enforced or r.kind != "goal"]


def _measures(scene, log, watch):
    out = {}
    if log is None:
        return out
    boxes = [b for o in scene.obstacles for b in geom.obstacle_boxes(o)]
    objs = geom.objects_by_id(scene)
    tl = tilt_deg(log.obj_quat)
    for oid in watch:
        if oid not in log.object_ids:
            continue
        k = log.index(oid)
        m = _transport_mask(log, k)
        idx = np.flatnonzero(m)[::4]
        d = (float(geom.object_obstacle_distance(objs[oid], log.obj_pos[idx, k], log.obj_quat[idx, k], boxes).min())
             if idx.size and boxes else None)
        out[oid] = {"max_transport_tilt_deg": round(float(tl[m, k].max()), 2) if m.any() else None,
                    "min_transport_clearance_m": None if d is None else round(d, 4)}
    from mirror.evaluate.evidence import object_events
    tray = scene.trays[0] if scene.trays else None
    out["_events"] = {o: object_events(log, scene, o, tray).summary() for o in log.object_ids}
    return out


def _run(scene, spec, condition, watch):
    t0 = time.time()
    ro, log, plan = execute_trial(scene, spec, condition=condition)
    rep = ro.report
    return ro, log, dict(
        scene_id=scene.scene_id, seed=scene.seed, spec_version=spec.version, condition=condition,
        status=rep.status, plan_status=plan.status, plan_reason=plan.reason[:400],
        object_order=plan.object_order,
        routes=[r for r in rep.route_labels if not r.startswith("transit")],
        rules=_rule_rows(rep), violations=[v.model_dump() for v in rep.violations],
        beads=[(o.object_id, o.beads_in_container, o.beads_total) for o in rep.outcomes if o.beads_total],
        measures=_measures(scene, log, watch), infeasibility=plan.infeasibility,
        execution_time_s=rep.execution_time_s, wall_time_s=round(time.time() - t0, 2))


def run_case(name, cfg, base: TaskSpec, out: Path, seeds, log_fn):
    case_dir = out / name
    case_dir.mkdir(parents=True, exist_ok=False)
    rec = dict(case=name, title=cfg["title"], kind=cfg["kind"], interpreter="offline-rules (no model call)",
               showcase_scene=cfg["scene"], turns=[], spec_versions=[])
    versions = [base]
    for t in cfg.get("setup", []):
        new, r = apply_correction(versions, t)
        versions.append(new)
        rec["turns"].append(dict(text=t, role="setup", outcome="apply", version=new.version, record_kind=r.kind,
                                 ops=[o.model_dump(mode="json") for o in r.diff]))
    start = versions[-1]
    for t in cfg["turns"]:
        try:
            new, r = apply_correction(versions, t)
        except NeedsClarification as e:
            rec["turns"].append(dict(text=t, outcome="clarify", question=e.question, options=e.options,
                                     reason=e.reason))
            continue
        except CorrectionRejected as e:
            rec["turns"].append(dict(text=t, outcome="reject", reason=str(e)))
            continue
        rec["turns"].append(dict(text=t, outcome="apply", version=new.version, record_kind=r.kind,
                                 ops=[o.model_dump(mode="json") for o in r.diff],
                                 diff=spec_diff(versions[-1], new)))
        versions.append(new)
    rec["spec_versions"] = [s.model_dump(mode="json") for s in versions]
    final = versions[-1]
    if final.version == start.version:
        rec["note"] = "no specification change: nothing is executed"
        (case_dir / "summary.json").write_text(json.dumps(rec, indent=1, default=str))
        return rec
    scene = _scene(cfg["scene"])
    watch = cfg["watch"]
    log_fn(f"[{name}] old behaviour v{start.version} on {scene.scene_id}")
    old_ro, old_log, rec["old"] = _run(scene, start, f"old_v{start.version}", watch)
    (case_dir / "rollout_old.json").write_text(old_ro.model_dump_json())
    rechecks = []
    for s in versions[versions.index(start) + 1:]:
        rep = check(old_log, s, scene, condition=f"recheck old under v{s.version}") if old_log is not None else None
        rechecks.append(dict(spec_version=s.version, status=rep.status if rep else "no_log",
                             rules=_rule_rows(rep) if rep else []))
    rec["old_rechecked"] = rechecks
    log_fn(f"[{name}] new behaviour v{final.version}")
    new_ro, new_log, rec["new"] = _run(scene, final, f"new_v{final.version}", watch)
    (case_dir / "rollout_new.json").write_text(new_ro.model_dump_json())
    transfer = []
    for fam, seed in seeds:
        sc = sample_layout(seed, "dev", fam)
        log_fn(f"[{name}] transfer {sc.scene_id}")
        _, la, a = _run(sc, start, f"old_v{start.version}", watch)
        _, _, b = _run(sc, final, f"new_v{final.version}", watch)
        rc = check(la, final, sc, condition="old rollout re-checked under the new spec") if la is not None else None
        transfer.append(dict(family=fam, seed=seed, old=a, new=b,
                             old_under_new=dict(status=rc.status if rc else "no_rollout",
                                                rules=_rule_rows(rc) if rc else [])))
    rec["transfer"] = transfer
    (case_dir / "summary.json").write_text(json.dumps(rec, indent=1, default=str))
    return rec


def _line(rec):
    if "old" not in rec:
        t = rec["turns"][-1]
        return f"| {rec['case']} | {t['outcome']}: {t.get('question') or t.get('reason', '')} | | | | |"
    rc = rec["old_rechecked"][-1]
    tr_new = [x["new"]["status"] for x in rec["transfer"]]
    tr_oun = [x["old_under_new"]["status"] for x in rec["transfer"]]
    n = len(tr_new)
    return (f"| {rec['case']} | {rec['old']['status']} | {rc['status']} | {rec['new']['status']} | "
            f"{sum(s == 'constraint_violated' for s in tr_oun)}/{n} | {sum(s == 'success' for s in tr_new)}/{n} |")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "results" / "dev_v2" / "demo_corrections"))
    ap.add_argument("--cases", default=",".join(CASES))
    ap.add_argument("--quick", action="store_true", help="two transfer layouts instead of six")
    a = ap.parse_args(argv)
    out = Path(a.out)
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} exists and is not empty; choose a new --out (evidence is never overwritten)")
    out.mkdir(parents=True, exist_ok=True)
    seeds = TRANSFER_SEEDS[:2] if a.quick else TRANSFER_SEEDS
    base, _, prov = evaluation_specs(source="offline")
    t0 = time.time()
    records = []
    for name in a.cases.split(","):
        records.append(run_case(name, CASES[name], base, out, seeds, lambda m: print(m, flush=True)))
    manifest = dict(script="scripts/demo_corrections.py", argv=sys.argv[1:], commit=_git("rev-parse", "HEAD"),
                    dirty=bool(_git("status", "--porcelain")), python=platform.python_version(),
                    numpy=np.__version__, base_spec=dict(source=prov["source"], version=base.version,
                                                         sha256=hashlib.sha256(base.model_dump_json().encode()).hexdigest()),
                    transfer_seeds=seeds, split="dev", wall_time_s=round(time.time() - t0, 1),
                    model_calls=0, interpreter="offline-rules")
    try:
        import mujoco
        manifest["mujoco"] = mujoco.__version__
    except Exception:  # pragma: no cover
        pass
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    lines = ["# Correction capability demonstrations (development layouts)", "",
             "Offline interpreter (no model call), MuJoCo execution, independent checker. Development seeds only. "
             "On each unseen development layout the old specification and the new one are both executed; the "
             "old rollout is then re-checked under the new specification. Columns: old rollouts that violate the "
             "new rule; new rollouts that succeed. A relaxation is not expected to make old rollouts fail. Every "
             "rollout is reported; none is filtered. Per-layout details are in each case's summary.json.", "",
             "| Case | Old run | Old run re-checked under new spec | New run | Unseen layouts: old run violates new rule | Unseen layouts: new run succeeds |",
             "|---|---|---|---|---|---|"] + [_line(r) for r in records]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
