"""One command from a real_run output directory to a 3D workspace pack (capture view + runs).

usage: python scripts/viz_pack_from_pipeline.py --pipeline OUT --out DIR [--plate PLATE.png] [--frame-t SECONDS]

Reads OUT/manifest.json (session, table_to_sim, scene, run rollout, spec, corrections with their
re-check reports and labels). The image for the phone's frame and the table texture is, in order:
--plate, OUT/<twin_dir>/plate.png (the twin's clean plate), or a frame of the take at --frame-t
(default: 1 s before the first inferred event). Labels from the manifest are carried to every run.
Then open web/js/viz/lab.html?pack=<DIR relative to the page>&run=after (or run=run when no correction).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--plate", default="")
    ap.add_argument("--frame-t", type=float, default=None)
    ap.add_argument("--correction", default="", help="name of the correction to pack (default: the first)")
    a = ap.parse_args(argv)
    from mirror.viz.capture_view import _read_image, build
    import importlib.util
    spec_ = importlib.util.spec_from_file_location("viz_export_capture", ROOT / "scripts" / "viz_export_capture.py")
    exp = importlib.util.module_from_spec(spec_); spec_.loader.exec_module(exp)

    P = Path(a.pipeline)
    man = json.loads((P / "manifest.json").read_text())
    session = json.loads((P / man.get("session", "session.json")).read_text())
    t2s = json.loads((P / man.get("table_to_sim", "table_to_sim.json")).read_text())
    placement = t2s.get("placement", t2s)
    labels = list(man.get("labels", []))
    plate = a.plate or (str(P / man.get("twin_dir", "twin") / "plate.png") if (P / man.get("twin_dir", "twin") / "plate.png").exists() else "")
    t = a.frame_t
    if not plate and t is None:
        evs = [e.get("t") for e in session.get("events", []) if e.get("t") is not None]
        t = max(0.0, min(evs) - 1.0) if evs else 0.0
    img = _read_image(plate or None, None if plate else man["take"], t)
    out = Path(a.out)
    note = f"clean plate {Path(plate).name}" if plate else f"frame at {t:.2f} s of the take"
    caption = ("Approximate: one camera; " + ("calibration reuse flagged; " if any("flag" in x.lower() for x in labels) else "")
               + (t2s.get("label", "") + "; " if t2s.get("label") else "") + "proxies are simulation stand-ins. Backdrop: " + note + ".")
    build(session, placement, img, out, labels=labels, caption=caption, frame_note=note)
    args = ["--out", str(out), "--capture-view", str(out / "capture_view.json"), "--scene", str(P / man["scene"]),
            "--run", f"run={P / man['rollouts']['run']}"]
    if man.get("spec"):
        args += ["--spec", f"run={P / man['spec']}"]
    corrs = man.get("corrections") or []
    c = next((x for x in corrs if x.get("name") == a.correction), corrs[0] if corrs else None)
    if c:
        args += ["--run", f"after={P / c['rollout']}", "--spec", f"after={P / c['spec']}", "--ghost", "after=run"]
        if c.get("recheck"):
            args += ["--recheck", f"run={P / c['recheck']}"]
        labels = labels + ([f"Rehearsed correction ({c.get('compiler', '')}), not chosen by the person"] if c.get("rehearsal") else [f"Correction: {c.get('text', '')}"])
    for lab in labels:
        args += ["--label", lab]
    exp.main(args)
    print(json.dumps({"pack": str(out), "runs": ["run"] + (["after"] if c else []), "image": note}))


if __name__ == "__main__":
    main()
