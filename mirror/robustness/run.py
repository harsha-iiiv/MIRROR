"""Command line: ``python -m mirror.robustness.run``.

  rehearsal  the benchmark code path on development seeds: carry-moment frames from
             frames_for_seeds, prompt conditions A (chooser) and B (colour-free),
             corrected and uncorrected scores
  dev        render development frames, edit them, run SAM 3.1, write the report
  calibrate  run the geometry check on synthetic controls of development frames
  frames     only render and save ground-truth frames

The Meta client mode comes from the environment (MIRROR_META_MODE: live,
record, replay or offline); recorded calls replay without a key.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m mirror.robustness.run")
    ap.add_argument("mode", choices=["rehearsal", "dev", "calibrate", "frames"])
    ap.add_argument("--seeds", type=int, nargs="+", default=[3, 8])
    ap.add_argument("--frames", nargs="*", default=None,
                    help="frame ids to keep, e.g. dev3_oblique_start (default: all rendered)")
    ap.add_argument("--instructions", nargs="*", default=None, help="instruction ids (default: all)")
    ap.add_argument("--out", default=str(ROOT / "results" / "robustness_dev"))
    ap.add_argument("--usage-log", default=str(ROOT / "results" / "meta_usage.jsonl"))
    a = ap.parse_args(argv)
    from .frames import dev_frames, frames_for_seeds, load_frames
    out = Path(a.out)
    gt_dir = out / "gt"
    if a.mode == "rehearsal":
        if any(s >= 10000 for s in a.seeds):
            ap.error("the rehearsal uses development seeds only")
        frames_for_seeds(a.seeds, gt_dir, split="dev")
        frames = load_frames(gt_dir)
    elif gt_dir.exists() and any(gt_dir.glob("*.json")):
        frames = load_frames(gt_dir)
    else:
        frames = dev_frames(a.seeds, out_dir=gt_dir)
    if a.frames:
        frames = [f for f in frames if f.frame_id in set(a.frames)]
    print(f"{len(frames)} frames: {[f.frame_id for f in frames]}")
    if a.mode == "frames":
        return 0
    if a.mode == "calibrate":
        from .controls import calibrate
        res = calibrate(frames)
        (out / "calibration.json").write_text(json.dumps(res, indent=1))
        print(json.dumps(res, indent=1))
        return 0
    from .evaluate import evaluate_robustness
    from .instructions import by_id
    rep = evaluate_robustness(frames, by_id(a.instructions), out_dir=out, usage_log=a.usage_log)
    print((out / "report.md").read_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
