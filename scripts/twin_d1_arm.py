"""D1 with the articulated arm backend (UR10e from the ceiling mount), for the twin view (development only).

Same scene, same person-stated spec, same planner and checker as the gripper run; only the robot
differs. Writes DIR/rollout.json and DIR/arm.json (status, backend info, wall time).

  python scripts/twin_d1_arm.py --out DIR [--spec spec.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
P2 = Path("/workspace/mirror-team/v2/phone_real/out/phase2")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--scene", type=Path, default=P2 / "scene.json")
    ap.add_argument("--spec", type=Path, default=P2 / "spec_intent.json")
    a = ap.parse_args(argv)
    from mirror.backends import get_backend
    from mirror.contracts import SceneState, TaskSpec
    from mirror.evaluate.trial import execute_trial
    scene = SceneState.model_validate_json(a.scene.read_text())
    spec = TaskSpec.model_validate_json(a.spec.read_text())
    arm = get_backend("arm")
    t0 = time.time()
    rollout, _, plan = execute_trial(scene, spec, condition="phone D1 layout, arm backend", planner=arm.plan, env_factory=arm.env)
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "rollout.json").write_text(rollout.model_dump_json())
    rep = rollout.report
    info = {"status": rep.status, "plan_status": rep.plan_status, "plan_reason": rep.plan_reason, "wall_s": round(time.time() - t0, 1),
            "notes": rep.notes[:10], "backend": "arm (UR10e, ceiling mount, collision shapes drawn)",
            "what": "Development only: D1 scene and Harsha's stated spec on the arm backend, for the twin view."}
    (a.out / "arm.json").write_text(json.dumps(info, indent=1, default=str))
    print(json.dumps(info, indent=1, default=str))


if __name__ == "__main__":
    main()
