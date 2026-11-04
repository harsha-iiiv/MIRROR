"""Correction sources for the night film (D1 development clip, never scored).

Re-runs the D1 phase-2 layout and person-stated spec exactly as phase 2 did (planner default config,
gripper backend), checks it reproduces the recorded phase-2 report, then compiles each correction
candidate shown on the D1 page with the OFFLINE rule interpreter (no model call), re-checks the old
run from its step log under the new spec, and plans + executes the new spec. Every result is written;
none is filtered.

    python film/night/make_correction.py --phase2 DIR --out DIR          # D1 phase-2 artefacts
    python film/night/make_correction.py --pipeline DIR --out DIR        # a mirror.capture.real_run output dir

With --pipeline the scene, the spec and the reference report come from the run's manifest (scene/scene.json, spec.json,
run/report.json) and the sentences from page.json correction_candidates (compile_text), the manifest's corrections first.
"""
from __future__ import annotations
import argparse, json, platform, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from mirror.contracts import SceneState, TaskSpec  # noqa: E402
from mirror.interpret import LLMClient  # noqa: E402
from mirror.interpret.diff import spec_diff  # noqa: E402
from mirror.interpret.types import CorrectionRejected, InterpretError, NeedsClarification  # noqa: E402
import numpy as np  # noqa: E402
from mirror.evaluate import geom  # noqa: E402
from mirror.evaluate.checker import _transport_mask  # noqa: E402
from mirror.statelog import tilt_deg  # noqa: E402
from mirror.pipeline import Session  # noqa: E402

CANDIDATES = [  # the three candidates on the D1 page (wiki .../real-demo/correction_candidates), plus ordering
    ("mug_clearance", "Keep the mug at least 6 cm away from the divider while carrying it."),
    ("box_upright", "Keep the block upright while it is carried."),
    ("no_table_contact", "Nothing may touch the table outside the tray."),
    ("marker_first", "Pack the marker before the cup."),
]


def rows(rep):
    return [dict(rule_id=r.rule_id, kind=r.kind, type=r.type, verdict=r.verdict, enforced=r.enforced,
                 detail=r.detail, objects=list(r.objects)) for r in rep.rule_results]


def measures(scene, log):
    """Per object: minimum distance to the obstacle while carried (every physics step) and max tilt."""
    if log is None:
        return {}
    boxes = [b for o in scene.obstacles for b in geom.obstacle_boxes(o)]
    objs = geom.objects_by_id(scene)
    tl = tilt_deg(log.obj_quat)
    out = {}
    for oid in log.object_ids:
        k = log.index(oid)
        m = _transport_mask(log, k)
        idx = np.flatnonzero(m)
        d = geom.object_obstacle_distance(objs[oid], log.obj_pos[idx, k], log.obj_quat[idx, k], boxes) if idx.size and boxes else None
        j = int(np.argmin(d)) if d is not None else None
        out[oid] = dict(min_transport_clearance_m=None if d is None else round(float(d.min()), 4),
                        at_t_s=None if j is None else round(float(log.t[idx[j]]), 3),
                        max_transport_tilt_deg=round(float(tl[m, k].max()), 2) if m.any() else None,
                        transport_steps=int(m.sum()))
    return out


def summ(entry):
    rep = entry.rollout.report
    return dict(rollout_id=entry.rollout_id, status=rep.status, plan_status=rep.plan_status,
                plan_reason=(rep.plan_reason or "")[:400], routes=[r for r in rep.route_labels if not r.startswith("transit")],
                violations=[v.model_dump(mode="json") for v in rep.violations], rules=rows(rep),
                outcomes=[o.model_dump(mode="json") for o in rep.outcomes],
                execution_time_s=rep.execution_time_s, wall_time_s=rep.wall_time_s,
                measures=measures(entry.scene, entry.log))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase2", default=None)
    ap.add_argument("--pipeline", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out)
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty; evidence is never overwritten")
    candidates = list(CANDIDATES)
    if a.pipeline:
        pd = Path(a.pipeline)
        man = json.loads((pd / "manifest.json").read_text())
        scene = SceneState.model_validate_json((pd / man.get("scene", "scene/scene.json")).read_text())
        spec = TaskSpec.model_validate_json((pd / man.get("spec", "spec.json")).read_text())
        recorded = json.loads((pd / "run" / "report.json").read_text())
        page = json.loads((pd / man.get("page", "page.json")).read_text()) if (pd / man.get("page", "page.json")).exists() else {}
        seen, candidates = set(), []
        for c in man.get("corrections", []) + (page.get("correction_candidates") or []):
            t = c.get("compile_text") or c.get("text") or c.get("label")
            cid = c.get("id") or c.get("name")
            if t and t not in seen:
                seen.add(t); candidates.append((cid, t))
    elif a.phase2:
        p2 = Path(a.phase2)
        scene = SceneState.model_validate_json((p2 / "scene.json").read_text())
        spec = TaskSpec.model_validate_json((p2 / "spec_intent.json").read_text())
        recorded = json.loads((p2 / "report.json").read_text())
    else:
        raise SystemExit("give --phase2 or --pipeline")
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    s = Session(scene=scene, client=LLMClient.offline_client())
    s.specs = [spec]
    old = s.run(condition="D1 layout, person-stated spec (re-run for the film)")
    o = summ(old)
    repro = dict(status=o["status"] == recorded["status"],
                 routes=o["routes"] == [r for r in recorded["route_labels"] if not r.startswith("transit")],
                 violations=len(o["violations"]) == len(recorded["violations"]))
    (out / "rollout_old.json").write_text(old.rollout.model_dump_json())
    rec = dict(script="film/night/make_correction.py", interpreter="offline-rules (no model call)",
               planner="mirror.plan default config (as the D1 phase-2 run)", backend="gripper",
               labels=["real phone footage", "one camera", "calibration reuse flagged", "development clip D1, never scored",
                       "simulation proxies, layout centred by software"],
               old=o, reproduces_phase2=repro, reproduces_reference=repro, source=str(a.pipeline or a.phase2), candidates=[])
    base_v = spec.version
    for cid, text in candidates:
        c = dict(id=cid, text=text)
        try:
            new, r = s.preview_correction(text)
        except NeedsClarification as e:
            c.update(outcome="clarify", question=e.question, options=e.options); rec["candidates"].append(c); continue
        except (CorrectionRejected, InterpretError) as e:
            c.update(outcome="reject", reason=str(e)); rec["candidates"].append(c); continue
        c.update(outcome="apply", record_kind=r.kind, model=getattr(r, "model", None),
                 ops=[x.model_dump(mode="json") for x in r.diff], diff=spec_diff(spec, new),
                 new_spec=new.model_dump(mode="json"))
        s.specs = [spec, new]
        rc = s.recheck(old.rollout_id, new.version)
        c["old_rechecked"] = dict(status=rc.status, rules=rows(rc), violations=[v.model_dump(mode="json") for v in rc.violations])
        ne = s.run(condition=f"D1 layout, corrected: {cid}")
        c["new"] = summ(ne)
        (out / f"rollout_{cid}.json").write_text(ne.rollout.model_dump_json())
        s.specs = [spec]
        rec["candidates"].append(c)
        print(cid, c["old_rechecked"]["status"], "->", c["new"]["status"], c["new"]["routes"], flush=True)
    rec["wall_time_s"] = round(time.time() - t0, 1)
    rec["commit"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
    rec["platform"] = f"{platform.system()} {platform.machine()}"
    rec["model_calls"] = 0
    (out / "corrections.json").write_text(json.dumps(rec, indent=1, default=str))
    print(json.dumps(repro), rec["wall_time_s"], "s")


if __name__ == "__main__":
    main()
