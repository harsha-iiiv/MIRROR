"""Arm backend report: reference scenes and development layouts on both backends.

Runs the reference specification pair (v1: cup upright at the end; v2: also
upright while carried) on the reference scenes and on development layouts,
with the baseline gripper backend and the arm backend, through the same
planner and checker. Every v1 rollout is re-checked under v2. Writes
``<out>/report.json`` and ``<out>/report.md``; nothing else is touched.

Usage:  python scripts/arm_report.py --out results/arm/v1 [--seeds 300-309]

Development seeds only (never evaluation seeds). Seeds 100-107 were used to
choose the arm's mount and are excluded by default.
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TUNING_SEEDS = set(range(100, 108))


def _seeds(text: str) -> list[int]:
    out: list[int] = []
    for part in text.split(","):
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def _row(backend, scene, spec, cond, ro, log, plan, wall):
    rep = ro.report
    cup = next((o for o in rep.outcomes if o.object_id.startswith("cup")), None)
    row = {
        "backend": backend, "scene_id": scene.scene_id, "seed": scene.seed, "spec_version": spec.version,
        "condition": cond, "status": rep.status, "routes": rep.route_labels,
        "violations": [{"constraint": v.constraint_id, "object": v.object_id, "peak": round(v.peak, 4)}
                       for v in rep.violations],
        "cup_max_transport_tilt_deg": None if cup is None else round(cup.max_transport_tilt_deg, 2),
        "cup_beads_kept": None if cup is None else f"{cup.beads_in_container}/{cup.beads_total}",
        "planning_time_s": round(plan.planning_time_s, 3), "episode_wall_time_s": round(wall, 3),
        "simulated_time_s": round(rep.execution_time_s, 3), "physics_steps": rep.physics_steps,
        "plan_status": plan.status,
    }
    if plan.infeasibility:
        row["infeasibility"] = {k: plan.infeasibility.get(k) for k in
                                ("proven", "kind", "failure_kind", "search_budget_exhausted", "validation_attempts")}
    if plan.status != "ok":
        row["plan_reason"] = plan.reason[:600]
    if log is not None and "backend" in log.meta:
        tr = log.meta["backend"].get("trajectory") or {}
        row["arm"] = {"peak_tcp_tracking_error_mm": log.meta["arm_joint_log"]["peak_tcp_tracking_error_mm"],
                      "tool_flipped": tr.get("tool_flipped"), "start_branch": tr.get("start_branch"),
                      "validation_time_s": tr.get("validation_time_s")}
        row["arm_variant"] = next((p for p in plan.reason.split("; ") if p.startswith("arm: executable")), "")
    return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--seeds", default="300-309")
    ap.add_argument("--backends", default="gripper,arm")
    args = ap.parse_args(argv)
    from mirror.backends import get_backend
    from mirror.evaluate.checker import check
    from mirror.evaluate.specs import reference_specs
    from mirror.evaluate.trial import execute_trial
    from mirror.scenes import reference_scene
    from mirror.scenes.sampling import sample_layout

    seeds = [s for s in _seeds(args.seeds) if s not in TUNING_SEEDS]
    v1, v2 = reference_specs()
    scenes = [reference_scene(n) for n in ("demo", "shortcut", "blocked")]
    scenes += [sample_layout(s, "dev") for s in seeds]
    rows = []
    t_all = time.perf_counter()
    for bname in args.backends.split(","):
        backend = get_backend(bname)
        for scene in scenes:
            for spec in (v1, v2):
                cond = f"v{spec.version}"
                t0 = time.perf_counter()
                ro, log, plan = execute_trial(scene, spec, condition=cond, planner=backend.plan,
                                              env_factory=backend.env, frame_hz=10.0)
                row = _row(bname, scene, spec, cond, ro, log, plan, time.perf_counter() - t0)
                if spec is v1 and log is not None:
                    rc = check(log, v2, scene)
                    row["recheck_under_v2"] = {"status": rc.status,
                                               "violations": sorted({v.constraint_id for v in rc.violations})}
                rows.append(row)
                print(f"{bname:7s} {scene.scene_id:14s} {cond}: {row['status']:20s} "
                      f"routes={','.join(r for r in row['routes'] if not r.startswith('transit'))} "
                      f"beads={row['cup_beads_kept']} wall={row['episode_wall_time_s']:.1f}s", flush=True)
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                                text=True, timeout=10).stdout.strip()
    except Exception:
        commit = ""
    import mujoco
    meta = {"generated_unix": time.time(), "commit": commit, "mujoco": mujoco.__version__,
            "python": platform.python_version(), "seeds": seeds, "split": "dev",
            "excluded_tuning_seeds": sorted(TUNING_SEEDS), "total_wall_time_s": round(time.perf_counter() - t_all, 1),
            "specs": {"v1": "cup upright at the end (reference original)",
                      "v2": "v1 + cup upright while carried (reference corrected)"},
            "note": "Simulation only (MuJoCo). No hardware. Development layouts, not an evaluation."}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps({"meta": meta, "rows": rows}, indent=1))
    (out / "report.md").write_text(render_md(meta, rows))
    print(f"wrote {out / 'report.json'}")
    return 0


def render_md(meta, rows) -> str:
    lines = ["# Arm backend vs baseline gripper (development layouts)", "",
             f"Commit {meta['commit']}, MuJoCo {meta['mujoco']}. {meta['note']}", "",
             "| backend | scene | spec | status | routes (transport) | cup beads | v1 re-checked under v2 | plan s | episode s |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        routes = ", ".join(x for x in r["routes"] if not x.startswith("transit")) or "-"
        rc = r.get("recheck_under_v2", {}).get("status", "")
        st = r["status"]
        if r.get("infeasibility"):
            inf = r["infeasibility"]
            st += " (proven)" if inf.get("proven") else f" (not proven: {inf.get('kind')})"
        lines.append(f"| {r['backend']} | {r['scene_id']} | {r['condition']} | {st} | {routes} | "
                     f"{r['cup_beads_kept'] or '-'} | {rc} | {r['planning_time_s']:.1f} | {r['episode_wall_time_s']:.1f} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
