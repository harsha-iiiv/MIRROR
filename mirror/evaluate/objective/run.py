"""Execute registration objective_v1 (after its freeze is committed).

Run from a clean checkout of the freeze commit:
    python scripts/run_objective_eval.py run --attempt attempt_1 --workers 2
Layouts are generated in this process (each access is appended to the
registration's own ledger); every condition then runs on the identical scene.
Per-layout records are appended to <attempt>/records.jsonl as they finish, so
an interrupted attempt keeps what it produced; a new attempt starts afresh in
a new folder and every attempt is reported.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import socket
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from .common import CUP, plan_sha, route_segments, scene_sha, spec_pair, transport_note
from .conditions import parse, planner_for, spec_for

_SPECS = None


def _specs():
    global _SPECS
    if _SPECS is None:
        from mirror.evaluate.paired import corrected_rules
        a, b, _ = spec_pair()
        _SPECS = (a, b, corrected_rules(a, b))
    return _SPECS


def _lam_value(name: str, manifest: dict) -> float:
    kind, lam = parse(name)
    key = name.split("@")[1]
    return float(manifest["calibrated_rotation_penalty"]) if key == manifest["calibrated_key"] else lam


def run_layout(args) -> list[dict]:
    """All conditions on one scene. Returns one record per condition."""
    stratum, seed, scene, manifest = args
    from mirror.evaluate.paired import _record
    from mirror.evaluate.trial import execute_trial
    a, b, rules = _specs()
    lam_cal = float(manifest["calibrated_rotation_penalty"])
    out = []
    for name in manifest["conditions"]:
        lam = _lam_value(name, manifest)
        spec = spec_for(name, a, b)
        t0 = time.perf_counter()
        rollout, log, plan = execute_trial(scene, spec, condition=name, planner=planner_for(name, lam),
                                           max_sim_time=120.0, extra_rules=rules, frame_hz=1.0)
        interventions = int(log.meta.get("interventions", 0)) if log is not None else 0
        rec = _record(name, rollout, log, plan, spec, scene, rules, interventions)
        rec.tilt_trace = {}
        segs = route_segments(plan, scene) if plan.status == "ok" else []
        L = sum(s["length_m"] for s in segs)
        R = sum(s["rotation_rad"] for s in segs)
        note = transport_note(plan, CUP)
        out.append(dict(
            stratum=stratum, seed=seed, scene_id=scene.scene_id, scene_sha256=scene_sha(scene),
            condition=name, kind=parse(name)[0], rotation_penalty=lam,
            plan_status=plan.status, plan_sha256=plan_sha(plan) if plan.status == "ok" else None,
            proven_infeasible=bool((plan.infeasibility or {}).get("proven")) if plan.status != "ok" else False,
            cup_route=note["route"], cup_mode=note["mode"],
            cup_tilted_planned=bool(note["mode"]) and note["mode"] != "upright",
            routes={s["object_id"] + ":" + s["kind"]: s["label"] for s in segs},
            path_length_m=round(L, 5), path_rotation_rad=round(R, 5),
            path_cost_calibrated=round(L + lam_cal * R, 5), path_cost_own=round(L + lam * R, 5),
            beads_retained=rec.beads_total - rec.beads_lost,
            wall_s=round(time.perf_counter() - t0, 2),
            trial=json.loads(rec.model_dump_json())))
    return out


def _git(root: Path, *args) -> str:
    import subprocess
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=30).stdout.strip()


def run(out: Path, *, attempt: str, workers: int = 1) -> dict:
    from mirror.scenes import sample_layout
    from .design import LEDGER, REPO_ROOT, verify
    out = Path(out)
    manifest = json.loads((out / "freeze.json").read_text())
    probs = verify(manifest)
    if _git(REPO_ROOT, "status", "--porcelain"):
        probs.append("the checkout is not clean")
    if not _git(REPO_ROOT, "log", "-1", "--format=%H", "--", "results/eval_objective_v1/freeze.json"):
        probs.append("the freeze manifest is not committed")
    if probs:
        raise RuntimeError("refusing to run: " + "; ".join(probs))
    adir = out / attempt
    if adir.exists():
        raise FileExistsError(f"{adir} exists; every attempt gets a new folder")
    adir.mkdir(parents=True)
    os.environ["MIRROR_EVAL_LEDGER"] = str(LEDGER)
    meta = dict(registration=manifest["registration"], attempt=attempt, git_head=_git(REPO_ROOT, "rev-parse", "HEAD"),
                started_at=_dt.datetime.now(_dt.timezone.utc).isoformat(), workers=workers,
                freeze_created_at=manifest["created_at"], status="running")
    (adir / "attempt.json").write_text(json.dumps(meta, indent=1))
    t0 = time.time()
    recf = adir / "records.jsonl"
    done = 0
    for st in manifest["strata"]:
        scenes = {s: sample_layout(s, st["split"], family=st["family"]) for s in st["seeds"]}
        jobs = [(st["name"], s, scenes[s], manifest) for s in st["seeds"]]
        if workers > 1:
            with ProcessPoolExecutor(max_workers=workers) as ex:
                futs = [ex.submit(run_layout, j) for j in jobs]
                for f in as_completed(futs):
                    with recf.open("a") as fh:
                        for r in f.result():
                            fh.write(json.dumps(r) + "\n")
                    done += 1
                    print(f"{st['name']} {done} layouts done ({time.time() - t0:.0f} s)", flush=True)
        else:
            for j in jobs:
                with recf.open("a") as fh:
                    for r in run_layout(j):
                        fh.write(json.dumps(r) + "\n")
                done += 1
                print(f"{st['name']} {done} layouts done ({time.time() - t0:.0f} s)", flush=True)
    meta.update(status="finished", finished_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
                wall_s=round(time.time() - t0, 1), layouts=done)
    (adir / "attempt.json").write_text(json.dumps(meta, indent=1))
    return meta
