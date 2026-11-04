"""The robot on your table, seen from your phone.

  python scripts/twin_render.py --pipeline OUT_DIR            # files written by mirror.capture.real_run
  python scripts/twin_render.py --session S.json --video TAKE.MOV --tracks T.json.gz \
      --rollout base=rollout.json [--rollout corrected=rollout2.json] [--placement P.json] --out DIR

Display only: no model calls, no network. See mirror/twin/build.py for what each output shows.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _first(*paths):
    for p in paths:
        if p and Path(p).exists():
            return Path(p)
    return None


def from_pipeline(d: Path, args) -> dict:
    """Read mirror.capture.real_run's output directory (manifest.json; filename fallbacks)."""
    man = {}
    if (d / "manifest.json").exists():
        man = json.loads((d / "manifest.json").read_text())

    def rel(v):
        if not v:
            return None
        p = Path(v)
        return p if p.is_absolute() else d / p

    session = _first(d / "session_answered.json", rel(man.get("session")), d / "session.json")
    video = Path(args.video) if args.video else _first(rel(man.get("take")), *sorted(d.glob("*.MOV")), *sorted(d.glob("*.mov")))
    tracks = _first(rel(man.get("tracks")), d / "tracks_full.json.gz", d / "tracks.json.gz")
    ro = man.get("rollouts") or {}
    base = _first(rel(ro.get("run") if isinstance(ro, dict) else None), rel(man.get("rollout")),
                  d / "run" / "rollout.json", d / "run" / "rollout.json.gz")
    pf = _first(rel(man.get("table_to_sim")), d / "table_to_sim.json")
    placement = json.loads(pf.read_text()) if pf else None
    corr = []
    c = man.get("corrections")
    if isinstance(c, dict):
        c = [dict(v, name=k) if isinstance(v, dict) else {"name": k, "rollout": v} for k, v in c.items()]
    for item in c or []:
        item = item if isinstance(item, dict) else {"rollout": item}
        p = rel(item.get("rollout"))
        if p and p.exists():
            corr.append((item.get("name") or item.get("id") or p.parent.name, p, item))
    if (d / "corrections").exists():
        for p in sorted((d / "corrections").glob("*/rollout*.json*")):
            if all(p != q for _, q, _ in corr):
                corr.append((p.parent.name, p, {}))
    if args.correction:
        corr = [x for x in corr if x[0] == args.correction] or corr
    rolls = [("run", base)]
    correction, after_labels = None, []
    if corr:
        name, p, item = corr[0]
        rolls = [("before", base), ("after", p)]
        for cand in (p.parent / "correction.json", p.parent.parent / "correction.json"):
            if cand.exists():
                correction = json.loads(cand.read_text())
                break
        if correction is None:
            correction = {"text": item.get("text") or item.get("label") or name}
            rb = _first(p.parent / "recheck_before.json", p.parent / "recheck.json")
            if rb:
                correction["recheck"] = json.loads(rb.read_text())
            ra, r0 = _first(p.parent / "report_after.json"), _first(d / "run" / "report.json")
            if ra and r0:
                old = {x.get("rule_id") for x in json.loads(r0.read_text()).get("rule_results") or []}
                correction["diff"] = [{"target_id": x.get("rule_id")} for x in json.loads(ra.read_text()).get("rule_results") or []
                                      if x.get("rule_id") not in old]
        if item.get("rehearsal"):
            after_labels.append("Rehearsal correction, not chosen by you")
    return dict(session=session, video=video, tracks=tracks, rollouts=rolls, placement=placement, out=d / "twin",
                correction=correction, labels=[x for x in man.get("labels") or [] if isinstance(x, str)],
                after_labels=after_labels)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pipeline", type=Path)
    ap.add_argument("--session", type=Path)
    ap.add_argument("--video", type=Path)
    ap.add_argument("--tracks", type=Path)
    ap.add_argument("--rollout", action="append", default=[], help="label=path (first is the base run)")
    ap.add_argument("--placement", type=Path, help="JSON {origin_xy_m, yaw_deg} (table frame -> simulator)")
    ap.add_argument("--correction", help="pipeline mode: which correction id to show as 'after'")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--speed", type=float, help="simulated seconds per output second (default: fit to your take)")
    ap.add_argument("--max-seconds", type=float)
    ap.add_argument("--fps", type=float, default=30.0, help="output frame rate (24 renders 20 percent faster)")
    ap.add_argument("--label", action="append", default=[], help="extra on-screen label (e.g. 'Development clip D1, never scored')")
    ap.add_argument("--after-label", action="append", default=[], help="label shown only on the before/after film")
    ap.add_argument("--correction-json", type=Path, help="correction summary: text, diff, old_run_recheck/recheck violations")
    ap.add_argument("--no-render", action="store_true", help="plate, checks and twin.json only")
    ap.add_argument("--only", choices=["main", "before_after"], help="render one film (shorter lock holds)")
    ap.add_argument("--arm", action="store_true",
                    help="pipeline mode: also run the task on the arm backend (scene + spec from DIR) and render it to DIR/twin_arm")
    ap.add_argument("--upload", help="also copy outputs to this directory (e.g. /cloudspace/mirror/capture/D1/twin)")
    a = ap.parse_args(argv)

    from mirror.twin.build import TwinJob, run
    if a.pipeline:
        cfg = from_pipeline(a.pipeline, a)
        if a.out:
            cfg["out"] = a.out
    else:
        if not (a.session and a.video and a.rollout and a.out):
            ap.error("--session, --video, --rollout and --out are required without --pipeline")
        rolls = []
        for r in a.rollout:
            lbl, _, p = r.partition("=")
            rolls.append((lbl, Path(p)) if p else ("run", Path(lbl)))
        cfg = dict(session=a.session, video=a.video, tracks=a.tracks, rollouts=rolls, out=a.out,
                   placement=json.loads(a.placement.read_text()) if a.placement else None)
    missing = [k for k in ("session", "video") if not cfg.get(k)] + ([] if cfg["rollouts"] and cfg["rollouts"][0][1] else ["rollout"])
    if missing:
        raise SystemExit(f"missing inputs: {missing}")
    job = TwinJob(session=cfg["session"], video=cfg["video"], tracks=cfg.get("tracks"), rollouts=cfg["rollouts"],
                  out=cfg["out"], placement=cfg.get("placement"), speed=a.speed, max_seconds=a.max_seconds,
                  labels=list(dict.fromkeys(cfg.get("labels", []) + a.label)),
                  after_labels=list(dict.fromkeys(cfg.get("after_labels", []) + a.after_label)), render=not a.no_render, only=a.only, fps=a.fps,
                  correction=json.loads(a.correction_json.read_text()) if a.correction_json else cfg.get("correction"))
    if a.arm and a.pipeline:
        import importlib.util
        d = a.pipeline
        man = json.loads((d / "manifest.json").read_text()) if (d / "manifest.json").exists() else {}
        scene = d / (man.get("scene") or "scene/scene.json")
        spec = d / (man.get("spec") or "spec.json")
        arm_dir = d / "arm"
        spec_m = importlib.util.spec_from_file_location("twin_d1_arm", Path(__file__).with_name("twin_d1_arm.py"))
        mod = importlib.util.module_from_spec(spec_m)
        spec_m.loader.exec_module(mod)
        mod.main(["--out", str(arm_dir), "--scene", str(scene), "--spec", str(spec)])
        job.rollouts = [("arm", arm_dir / "rollout.json")]
        job.out = (a.out or d / "twin_arm") if a.out else d / "twin_arm"
        job.labels = list(dict.fromkeys(job.labels + ["Arm backend"]))
        job.only = "main"
    m = run(job)
    print(json.dumps({"out": str(job.out), "files": m["files"], "timings_s": m["timings_s"],
                      "registration": m["registration"], "time_map": m["time_map"]["anchors"]}, indent=1))
    if a.upload:
        import shutil
        dst = Path(a.upload)
        dst.mkdir(parents=True, exist_ok=True)
        for p in Path(job.out).rglob("*"):
            if p.is_file():
                q = dst / p.relative_to(job.out)
                q.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, q)   # contents only: /cloudspace (FUSE) refuses chmod/utime
        print("copied to", dst)


if __name__ == "__main__":
    main()
