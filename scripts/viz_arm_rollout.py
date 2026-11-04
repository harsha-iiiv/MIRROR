"""Development rollout(s) on the UR arm backend, packaged for the 3D workspace.

usage: python scripts/viz_arm_rollout.py --out DIR [--scene demo|shortcut] [--hz 30]
Reference scene, reference spec pair (v1 original, v2 corrected); simulation only, not an evaluation.
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
    ap.add_argument("--out", required=True)
    ap.add_argument("--scene", default="demo")
    ap.add_argument("--hz", type=float, default=30.0)
    a = ap.parse_args(argv)
    from mirror.backends import get_backend
    from mirror.evaluate.specs import reference_specs
    from mirror.evaluate.trial import execute_trial
    from mirror.scenes import reference_scene
    from mirror.studio.session_adapter import compact_rollout

    out = Path(a.out)
    (out / "rollouts").mkdir(parents=True, exist_ok=True)
    scene = reference_scene(a.scene)
    backend = get_backend("arm")
    v1, v2 = reference_specs()
    idx = []
    for spec in (v1, v2):
        ro, log, plan = execute_trial(scene, spec, condition=f"arm v{spec.version}", planner=backend.plan,
                                      env_factory=backend.env, frame_hz=a.hz)
        name = f"arm_{a.scene}_v{spec.version}"
        payload = compact_rollout(ro, max_hz=a.hz, extra={
            "kind": "arm", "scene": scene.model_dump(mode="json"), "spec": spec.model_dump(mode="json"),
            "labels": ["simulation (MuJoCo)", "UR10e arm backend (Menagerie model, BSD-3)", "development layout, not an evaluation"],
            "ghost_run": "arm_%s_v1" % a.scene if spec.version == 2 else None})
        (out / "rollouts" / f"{name}.json").write_text(json.dumps(payload, separators=(",", ":")))
        bodies = [b["name"] for b in (payload["visual"] or {}).get("bodies", [])]
        idx.append({"name": name, "status": ro.report.status, "frames": len(payload["t"]), "bodies": bodies})
        print(name, ro.report.status, len(payload["t"]), "frames", flush=True)
    (out / "index.json").write_text(json.dumps(idx, indent=1))


if __name__ == "__main__":
    main()
