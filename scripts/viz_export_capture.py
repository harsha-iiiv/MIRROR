"""Package a real capture's runs for the 3D workspace: studio rollout payloads (columnar frames,
report, scene, spec) plus the capture view, under one directory the lab page or the studio can load.

usage: python scripts/viz_export_capture.py --out DIR --capture-view DIR/capture_view.json \
          --run before=ROLLOUT.json[.gz] [--run after=ROLLOUT.json --spec after=SPEC.json] \
          [--recheck before=CORRECTION.json] [--ghost after=before] [--scene SCENE.json] [--label TEXT ...]
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

from mirror.contracts import Rollout
from mirror.studio.session_adapter import compact_rollout


def _load(p: str):
    raw = gzip.open(p, "rt").read() if p.endswith(".gz") else Path(p).read_text()
    return json.loads(raw)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--capture-view", default="")
    ap.add_argument("--scene", default="")
    ap.add_argument("--run", action="append", default=[], help="name=rollout.json[.gz]")
    ap.add_argument("--spec", action="append", default=[], help="name=spec.json")
    ap.add_argument("--recheck", action="append", default=[], help="name=correction.json (old_run_recheck)")
    ap.add_argument("--ghost", action="append", default=[], help="name=ghostname")
    ap.add_argument("--label", action="append", default=[])
    a = ap.parse_args(argv)
    out = Path(a.out)
    (out / "rollouts").mkdir(parents=True, exist_ok=True)
    scene = json.loads(Path(a.scene).read_text()) if a.scene else None
    specs = dict(x.split("=", 1) for x in a.spec)
    rechecks = dict(x.split("=", 1) for x in a.recheck)
    ghosts = dict(x.split("=", 1) for x in a.ghost)
    index = []
    for item in a.run:
        name, path = item.split("=", 1)
        ro = Rollout.model_validate(_load(path))
        extra = {"kind": "capture", "labels": a.label}
        if scene:
            extra["scene"] = scene
        if name in specs:
            extra["spec"] = json.loads(Path(specs[name]).read_text())
        if a.capture_view:
            extra["capture_view"] = "../" + Path(a.capture_view).name if Path(a.capture_view).parent == out else a.capture_view
        if name in ghosts:
            extra["ghost_run"] = ghosts[name]
        if name in rechecks:
            corr = json.loads(Path(rechecks[name]).read_text())
            rc = corr.get("old_run_recheck") or {}
            rep = ro.report.model_dump(mode="json")
            rep.update({"rollout_id": rep["rollout_id"] + "@recheck", "status": rc.get("status", rep["status"]),
                        "violations": rc.get("violations", []),
                        "condition": f"re-check of this run under the corrected rule: {corr.get('text', '')}"})
            extra["recheck_report"] = rep
            extra["recheck_note"] = corr.get("what", "")
            if "after" in specs:
                rs = json.loads(Path(specs["after"]).read_text())
                extra["recheck_spec"] = rs
                rep.update({"spec_id": rs.get("spec_id", rep["spec_id"]), "spec_version": rs.get("version", rep["spec_version"])})
        payload = compact_rollout(ro, max_hz=30.0, extra=extra)
        (out / "rollouts" / f"{name}.json").write_text(json.dumps(payload, separators=(",", ":")))
        index.append({"name": name, "id": payload["id"], "status": ro.report.status, "frames": len(payload["t"])})
    (out / "index.json").write_text(json.dumps({"runs": index, "labels": a.label}, indent=1))
    print(json.dumps(index, indent=1))


if __name__ == "__main__":
    main()
