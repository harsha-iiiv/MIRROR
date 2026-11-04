"""D1 example correction for the twin's before/after view (development only).

Runs the D1 scene with the person-stated spec, then ONE of MIRROR's offered correction candidates
("keep the mug 6 cm clear of the divider"), compiled by the offline rules (no model call), re-checks
the old run and runs again. The correction was offered by MIRROR, NOT chosen by Harsha; outputs say so.

  python scripts/twin_d1_example_correction.py --out DIR
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

P2 = Path("/workspace/mirror-team/v2/phone_real/out/phase2")
TEXT = "Keep the mug at least 6 cm away from the divider while carrying it."


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--phase2", type=Path, default=P2)
    ap.add_argument("--text", default=TEXT)
    a = ap.parse_args(argv)
    from mirror.contracts import SceneState, TaskSpec
    from mirror.interpret import LLMClient
    from mirror.pipeline import Session

    a.out.mkdir(parents=True, exist_ok=True)
    scene = SceneState.model_validate_json((a.phase2 / "scene.json").read_text())
    spec = TaskSpec.model_validate_json((a.phase2 / "spec_intent.json").read_text())
    t0 = time.time()
    s = Session(scene=scene, client=LLMClient.offline_client())
    s.specs = [spec]
    before = s.run(condition="before the correction (person-stated spec)")
    new_spec, record = s.correct(a.text)
    recheck = s.recheck(before.rollout_id)
    after = s.run(condition="after the example correction")
    (a.out / "before").mkdir(exist_ok=True)
    (a.out / "after").mkdir(exist_ok=True)
    (a.out / "before" / "rollout.json").write_text(before.rollout.model_dump_json())
    (a.out / "after" / "rollout.json").write_text(after.rollout.model_dump_json())
    (a.out / "after" / "spec.json").write_text(new_spec.model_dump_json(indent=1))
    summary = {
        "what": "Example correction for the twin's before/after view. Offered by MIRROR as a candidate; NOT chosen by Harsha.",
        "text": a.text, "compiler": "offline rules (deterministic, no model call)",
        "diff": record.model_dump(mode="json").get("diff"),
        "old_run_recheck": {"status": recheck.status,
                            "violations": [v.model_dump(mode="json") for v in (recheck.violations or [])]},
        "before": {"status": before.rollout.report.status}, "after": {"status": after.rollout.report.status},
        "wall_s": round(time.time() - t0, 1)}
    (a.out / "correction.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps({k: summary[k] for k in ("before", "after", "wall_s")} | {"recheck": summary["old_run_recheck"]["status"],
                     "n_violations": len(summary["old_run_recheck"]["violations"])}, indent=1))


if __name__ == "__main__":
    main()
