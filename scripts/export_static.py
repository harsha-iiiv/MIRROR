#!/usr/bin/env python
"""Export a self-contained static studio (no backend) for GitHub Pages.

It drives one studio session through the reference story, or loads an
existing snapshot, then writes the web frontend plus precomputed JSON:

    site/index.html, styles.css, js/*, config.js (static mode)
    site/data/session.json         snapshot shown by the studio
    site/data/rollouts/<id>.json   playback for every rollout
    site/data/provenance.json      how and when the data was produced

Usage:
    python scripts/export_static.py --out site                # live simulation (default)
    python scripts/export_static.py --out site --evaluation results/eval/final   # show the pre-registered results
    python scripts/export_static.py --out site --backend fixture --allow-fixture   # interface preview only

The exporter never generates evaluation-split layouts. Its own paired run (when
no --evaluation is given) uses the showcase development seeds 9000-9099;
pre-registered results are loaded from the files the evaluation package wrote.

Fixture data is key-framed placeholder motion; it is refused unless
--allow-fixture is given, and the exported studio labels it on every page.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mirror.studio.session_adapter import (REFERENCE_CORRECTION, SHOWCASE_SEEDS, SHOWCASE_SPLIT,  # noqa: E402
                                           StudioService, check_showcase)

WEB = ROOT / "web"


def log(msg: str) -> None:
    print(f"[export] {msg}", flush=True)


def run_story(svc: StudioService, *, seeds: list[int], clarify: bool, blocked: bool) -> list[dict]:
    steps = []

    def optional(j, what):
        """Steps after the core story are recorded even when they fail; the site shows what exists."""
        try:
            return job(j, what)
        except RuntimeError as exc:
            log(f"WARNING: {exc}")
            return None

    def job(j, what):
        t0 = time.time()
        svc.wait(j, timeout=3600)
        steps.append({"step": what, "status": j.status, "error": j.error, "seconds": round(time.time() - t0, 1)})
        log(f"{what}: {j.status}{' ' + j.error if j.error else ''} ({time.time() - t0:.0f} s)")
        if j.status != "done":
            raise RuntimeError(f"{what} failed: {j.error}")
        return j.result

    svc.load_demo()
    log("demonstration loaded")
    job(svc.interpret(), "interpret")
    job(svc.run(condition="demonstration layout", tags=["run"]), "run on the demonstration layout")
    svc.set_scene_preset("shortcut")
    job(svc.run(condition="changed world", tags=["world"]), "run on the shortcut layout")
    res = job(svc.correct(REFERENCE_CORRECTION), "correction")
    if not res.get("changed"):
        raise RuntimeError(f"the reference correction did not change the specification: {res}")
    job(svc.run(condition="corrected", tags=["corrected"]), "corrected run on the shortcut layout")
    if clarify:
        optional(svc.clarify(), "clarifying question")
    if seeds:
        optional(svc.evaluate(seeds, split=SHOWCASE_SPLIT), f"showcase paired run on {len(seeds)} development layouts")
    if blocked:
        svc.set_scene_preset("blocked")
        optional(svc.run(condition="blocked layout", tags=["blocked"]), "blocked layout")
    svc.set_scene_preset("shortcut")
    return steps


def code_digest() -> str:
    h = hashlib.sha256()
    for p in sorted((ROOT / "mirror").rglob("*.py")):
        h.update(p.relative_to(ROOT).as_posix().encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def write_site(svc: StudioService, out: Path, steps: list[dict], extra: dict) -> None:
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(WEB, out)
    data = out / "data"
    (data / "rollouts").mkdir(parents=True)
    snap = svc.snapshot()
    snap["jobs"] = []
    snap["static"] = True
    (data / "session.json").write_text(json.dumps(snap, separators=(",", ":")))
    ids = [r["id"] for r in snap["rollouts"]] + (["demo"] if snap.get("demo") else [])
    svc.payload_digits = 3          # 1 mm and 0.001 quaternion steps: smaller files, same picture
    svc.payload_cache.clear()
    total = 0
    for rid in ids:
        payload = svc.rollout_payload(rid)
        text = json.dumps(payload, separators=(",", ":"))
        total += len(text)
        (data / "rollouts" / f"{rid}.json").write_text(text)
    prov = {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "backend": svc.mode,
        "llm_mode": os.environ.get("MIRROR_LLM_MODE", "live"),
        "llm_model": os.environ.get("MIRROR_LLM_MODEL", "muse-spark-1.3"),
        "interpreter": snap["specs"][0].get("interpreter") if snap["specs"] else None,
        "code_sha256": code_digest(),
        "steps": steps,
        "rollouts": len(ids),
        **extra,
    }
    (data / "provenance.json").write_text(json.dumps(prov, indent=1))
    cfg = {"static": True, "base": "data/"}
    if extra.get("repo_url"):
        cfg["repo"] = extra["repo_url"]
    (out / "config.js").write_text("window.MIRROR_CONFIG = " + json.dumps(cfg) + ";\n")
    (out / ".nojekyll").write_text("")
    log(f"wrote {out} ({len(ids)} rollouts, {total / 1e6:.1f} MB of playback data)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="site", help="output folder (replaced)")
    ap.add_argument("--backend", choices=["live", "fixture"], default="live")
    ap.add_argument("--allow-fixture", action="store_true", help="permit exporting key-framed fixture data")
    ap.add_argument("--seeds", type=int, default=8,
                    help="showcase paired run on this many development layouts when no --evaluation is given (0 to skip)")
    ap.add_argument("--seed-base", type=int, default=SHOWCASE_SEEDS.start,
                    help=f"first showcase seed ({SHOWCASE_SEEDS.start}-{SHOWCASE_SEEDS.stop - 1})")
    ap.add_argument("--repo-url", default="https://github.com/harsha-iiiv/MIRROR")
    ap.add_argument("--no-clarify", action="store_true")
    ap.add_argument("--no-blocked", action="store_true")
    ap.add_argument("--evaluation", default=None,
                    help="show a saved evaluation report (JSON file or its folder) instead of running one")
    args = ap.parse_args(argv)
    if args.backend == "fixture" and not args.allow_fixture:
        ap.error("fixture data is placeholder motion, not simulation; pass --allow-fixture to export it anyway")
    svc = StudioService(backend=args.backend)
    if svc.mode == "fixture" and not args.allow_fixture:
        ap.error("simulation packages unavailable; refusing to export fixture data without --allow-fixture")
    seeds = list(range(args.seed_base, args.seed_base + args.seeds))
    if seeds and not args.evaluation:
        try:
            check_showcase(seeds, SHOWCASE_SPLIT)
        except RuntimeError as exc:
            ap.error(str(exc))
    t0 = time.time()
    steps = run_story(svc, seeds=[] if args.evaluation else seeds,
                      clarify=not args.no_clarify, blocked=not args.no_blocked)
    if args.evaluation:
        svc.load_evaluation(args.evaluation)
        steps.append({"step": f"loaded evaluation report {Path(args.evaluation).name}", "status": "done",
                      "error": None, "seconds": 0})
        log(f"loaded evaluation report {args.evaluation}")
    write_site(svc, Path(args.out), steps, {"showcase_seeds": [] if args.evaluation else seeds,
                                            "evaluation": Path(args.evaluation).name if args.evaluation else None,
                                            "repo_url": args.repo_url, "wall_time_s": round(time.time() - t0, 1)})
    failed = [s["step"] for s in steps if s["status"] != "done"]
    if failed:
        log("incomplete export, failed steps: " + "; ".join(failed))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
