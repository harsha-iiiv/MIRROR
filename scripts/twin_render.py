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
    man = {}
    for name in ("manifest.json", "real_run.json"):
        if (d / name).exists():
            try:
                man.update({k: v for k, v in json.loads((d / name).read_text()).items() if k not in man})
            except Exception:
                pass

    def rel(v):
        if not v:
            return None
        p = Path(v)
        return p if p.is_absolute() else d / p

    session = _first(rel(man.get("session")), d / "session.json")
    video = Path(args.video) if args.video else _first(rel(man.get("take")), *sorted(d.glob("*.MOV")), *sorted(d.glob("*.mov")),
                                                      *sorted(d.glob("*.mp4")))
    tracks = _first(rel(man.get("tracks")), d / "tracks_full.json.gz", d / "tracks.json.gz")
    base = _first(rel(man.get("rollout")), d / "run" / "rollout.json", d / "run" / "rollout.json.gz", d / "rollout.json")
    placement = None
    pf = _first(rel(man.get("placement")), d / "table_to_sim.json")
    if pf:
        placement = json.loads(pf.read_text())
    rolls = [("before" if args.correction else "run", base)]
    corr = []
    c = man.get("corrections")
    if isinstance(c, dict):
        c = list(c.values())
    for item in c or []:
        p = rel(item.get("rollout")) if isinstance(item, dict) else rel(item)
        if p and p.exists():
            corr.append((item.get("id", "corrected") if isinstance(item, dict) else p.parent.name, p))
    for p in sorted((d / "corrections").glob("*/rollout.json*")) if (d / "corrections").exists() else []:
        if all(p != q for _, q in corr):
            corr.append((p.parent.name, p))
    if args.correction:
        corr = [x for x in corr if x[0] == args.correction] or corr
    correction = None
    if corr:
        rolls = [("before", base), ("after", corr[0][1])]
        for cand in (corr[0][1].parent / "correction.json", corr[0][1].parent.parent / "correction.json"):
            if cand.exists():
                correction = json.loads(cand.read_text())
                break
    return dict(session=session, video=video, tracks=tracks, rollouts=rolls, placement=placement, out=d / "twin",
                correction=correction)


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
    ap.add_argument("--label", action="append", default=[], help="extra on-screen label (e.g. 'Development clip D1, never scored')")
    ap.add_argument("--after-label", action="append", default=[], help="label shown only on the before/after film")
    ap.add_argument("--correction-json", type=Path, help="correction summary: text, diff, old_run_recheck/recheck violations")
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
                  out=cfg["out"], placement=cfg.get("placement"), speed=a.speed, max_seconds=a.max_seconds, labels=a.label,
                  after_labels=a.after_label,
                  correction=json.loads(a.correction_json.read_text()) if a.correction_json else cfg.get("correction"))
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
                shutil.copy2(p, q)
        print("copied to", dst)


if __name__ == "__main__":
    main()
