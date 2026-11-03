#!/usr/bin/env python
"""Simulation sources for the demo films: every shot of robot behaviour in the
films is rendered from a rollout written by this script.

    MUJOCO_GL=osmesa python scripts/film/make_sources.py --out <dir>

What it runs (MuJoCo, offline, no model call; recorded Muse Spark responses
(data/cassettes_muse, muse-spark-1.3) are replayed to build the specification
pair):
  * the specification pair of the registrations: the proposal with the
    'cup ends upright' rule confirmed (original) and the version after the
    typed correction "This container must remain upright throughout
    transport, not just at the destination." (corrected); replay agrees with
    the offline interpreter or the script stops;
  * the reference blocked scene and development blocked layouts (seeds
    6400-6402; the slot is the only opening): original and corrected at the
    time-calibrated rotation price 0.2445 m/rad and at 0.05 m/rad;
  * the original run re-judged under the corrected specification from its
    own step log (no re-simulation);
  * development transfer layouts (mixed 6200-6202, shortcut 6300-6302): both
    specifications at 0.2445 m/rad;
  * the reference shortcut scene at 0.05 m/rad (planner set to treat tilting
    as nearly free) and at 0.2445 m/rad, for a labelled comparison.

Only development seeds and reference scenes are used; no evaluation layout
is generated. Writes rollout JSON (with playback frames) and summary.json;
refuses a non-empty output directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from mirror.evaluate import check  # noqa: E402
from mirror.evaluate.specs import evaluation_specs  # noqa: E402
from mirror.evaluate.trial import execute_trial  # noqa: E402
from mirror.interpret.diff import spec_diff  # noqa: E402
from mirror.plan import plan as make_plan  # noqa: E402
from mirror.plan.config import DEFAULT_CONFIG  # noqa: E402
from mirror.plan.outcome import planning_outcome  # noqa: E402
from mirror.scenes import reference_scene, sample_layout  # noqa: E402

CALIBRATED = 0.2445          # results/eval_objective_v1 calibration (development layouts only)
NEAR_FREE = 0.05             # planner default before v2
BLOCKED = [6400, 6401, 6402]
TRANSFER = [("mixed", 6200), ("mixed", 6201), ("mixed", 6202),
            ("shortcut", 6300), ("shortcut", 6301), ("shortcut", 6302)]


def _git(*a) -> str:
    try:
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # pragma: no cover
        return ""


def planner_at(price):
    cfg = replace(DEFAULT_CONFIG, rotation_penalty=price)
    return lambda spec, scene: make_plan(spec, scene, config=cfg)


def rule_rows(rep):
    return [dict(rule_id=r.rule_id, kind=r.kind, status=r.status.value, enforced=r.enforced,
                 verdict=r.verdict, detail=r.detail) for r in rep.rule_results if r.enforced or r.kind != "goal"]


def run(out: Path, name: str, scene, spec, price, frame_hz, save=True):
    t0 = time.time()
    ro, log, pl = execute_trial(scene, spec, condition=name, planner=planner_at(price), frame_hz=frame_hz)
    rep = ro.report
    po = planning_outcome(pl)
    rec = dict(name=name, scene_id=scene.scene_id, seed=scene.seed, spec_version=spec.version,
               rotation_price=price, frame_hz=frame_hz, status=rep.status, plan_status=pl.status,
               object_order=pl.object_order,
               routes=[r for r in rep.route_labels if not r.startswith("transit")],
               beads=[(o.object_id, o.beads_in_container, o.beads_total) for o in rep.outcomes if o.beads_total],
               max_transport_tilt=[(o.object_id, round(o.max_transport_tilt_deg, 1)) for o in rep.outcomes],
               violations=[v.model_dump() for v in rep.violations], rules=rule_rows(rep),
               planning_outcome=dict(kind=po["kind"], headline=po["headline"], detail=po["detail"],
                                     assumptions=po["assumptions"], not_covered=po["not_covered"]),
               plan_reason=pl.reason[:600], execution_time_s=rep.execution_time_s,
               wall_time_s=round(time.time() - t0, 1))
    if save and ro.frames:
        p = out / f"{name}.json"
        p.write_text(ro.model_dump_json())
        rec["rollout_file"] = p.name
        rec["rollout_sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
    print(f"{name}: {rec['status']} {rec['beads']} {po['headline']} ({rec['wall_time_s']} s)", flush=True)
    return rec, log, pl


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="", help="comma list of groups: blocked,transfer,shortcut")
    a = ap.parse_args(argv)
    out = Path(a.out)
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty; choose a new --out")
    out.mkdir(parents=True, exist_ok=True)
    groups = set(a.only.split(",")) if a.only else {"blocked", "transfer", "shortcut"}
    original, corrected, prov = evaluation_specs(source="replay", cassette_dir=ROOT / "data" / "cassettes_muse",
                                                 model="muse-spark-1.3")
    summary = dict(script="scripts/film/make_sources.py", argv=sys.argv[1:], commit=_git("rev-parse", "HEAD"),
                   dirty=bool(_git("status", "--porcelain", "--untracked-files=no")),
                   platform=f"{platform.system()} {platform.machine()}", python=platform.python_version(),
                   spec_provenance=prov, spec_diff=spec_diff(original, corrected),
                   specs=dict(original=original.model_dump(mode="json"), corrected=corrected.model_dump(mode="json")),
                   model_calls=0, seeds=dict(blocked=BLOCKED, transfer=TRANSFER), runs=[], rechecks=[])
    try:
        import mujoco
        summary["mujoco"] = mujoco.__version__
    except Exception:  # pragma: no cover
        pass
    t0 = time.time()

    def save():
        summary["wall_time_s"] = round(time.time() - t0, 1)
        (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))

    if "blocked" in groups:
        scenes = [("ref_blocked", reference_scene("blocked"))] + \
                 [(f"dev_blocked_{s}", sample_layout(s, "blocked")) for s in BLOCKED]
        for tag, sc in scenes:
            hz = 120.0 if tag == "ref_blocked" else 60.0
            r_o, log_o, pl_o = run(out, f"{tag}__original__p{CALIBRATED}", sc, original, CALIBRATED, hz)
            r_c, _, _ = run(out, f"{tag}__corrected__p{CALIBRATED}", sc, corrected, CALIBRATED, hz)
            r_o5, _, pl_o5 = run(out, f"{tag}__original__p{NEAR_FREE}", sc, original, NEAR_FREE, hz, save=False)
            r_c5, _, _ = run(out, f"{tag}__corrected__p{NEAR_FREE}", sc, corrected, NEAR_FREE, hz, save=False)
            r_o5["same_plan_as_calibrated"] = pl_o5.model_dump_json(exclude={"planning_time_s"}) == \
                pl_o.model_dump_json(exclude={"planning_time_s"})
            summary["runs"] += [r_o, r_c, r_o5, r_c5]
            if log_o is not None:
                rc = check(log_o, corrected, sc, condition="original run re-judged under the corrected spec")
                summary["rechecks"].append(dict(scene=tag, of=r_o["name"], under_spec_version=corrected.version,
                                                status=rc.status, rules=rule_rows(rc),
                                                violations=[v.model_dump() for v in rc.violations]))
                print(f"  recheck {tag}: {rc.status}", flush=True)
            save()
    if "transfer" in groups:
        for fam, s in TRANSFER:
            sc = sample_layout(s, "dev", fam)
            tag = f"dev_{fam}_{s}"
            r_c, _, _ = run(out, f"{tag}__corrected__p{CALIBRATED}", sc, corrected, CALIBRATED, 60.0)
            r_o, _, _ = run(out, f"{tag}__original__p{CALIBRATED}", sc, original, CALIBRATED, 60.0)
            summary["runs"] += [r_c, r_o]
            save()
    if "shortcut" in groups:
        sc = reference_scene("shortcut")
        for spec, nm in ((original, "original"), (corrected, "corrected")):
            for price in (NEAR_FREE, CALIBRATED):
                r, log, _ = run(out, f"ref_shortcut__{nm}__p{price}", sc, spec, price, 120.0 if price == NEAR_FREE else 60.0)
                summary["runs"].append(r)
                if nm == "original" and price == NEAR_FREE and log is not None:
                    rc = check(log, corrected, sc, condition="original run re-judged under the corrected spec")
                    summary["rechecks"].append(dict(scene="ref_shortcut", of=r["name"],
                                                    under_spec_version=corrected.version, status=rc.status,
                                                    rules=rule_rows(rc),
                                                    violations=[v.model_dump() for v in rc.violations]))
        save()
    save()
    print(f"done in {time.time() - t0:.0f} s -> {out}")


if __name__ == "__main__":
    main()
