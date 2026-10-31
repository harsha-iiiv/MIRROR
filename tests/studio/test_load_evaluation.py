"""A saved evaluation report becomes a replayable evidence grid."""
import json

import pytest

from mirror.studio.fixtures import FixtureSession
from mirror.studio.session_adapter import StudioService


def test_load_saved_report(tmp_path):
    scenes = pytest.importorskip("mirror.scenes")
    s = FixtureSession()
    s.record_demo(); s.interpret()
    e = s.run(condition="x")
    (tmp_path / "rollouts").mkdir()
    (tmp_path / "rollouts" / "seed10000_original.json").write_text(e.rollout.model_dump_json())
    sid = scenes.sample_layout(10000, "eval").scene_id
    rep = e.rollout.report.model_dump(mode="json") | {"scene_id": sid}
    trial = {"seed": 10000, "scene_id": sid,
             "a": {"condition": "original", "report": rep, "rollout_path": "rollouts/seed10000_original.json",
                   "corrected_violations": [{"constraint_id": "c", "object_id": "cup_0", "t_start": 1, "t_end": 2, "peak": 90}]},
             "b": {"condition": "corrected", "report": rep, "rollout_path": "rollouts/missing.json", "corrected_violations": []}}
    data = {"split": "eval", "manifest": {"spec_a": {"version": 1}, "spec_b": {"version": 2}}, "trials": [trial],
            "metrics": {}, "paired": {}}
    (tmp_path / "report.json").write_text(json.dumps(data))
    svc = StudioService(backend="fixture")
    ev = svc.load_evaluation(tmp_path)
    assert ev["spec_a"] == 1 and ev["spec_b"] == 2
    assert ev["studio"]["rollout_ids"]["10000"] == {"a": "e10000a", "b": "e10000b"}
    snap = svc.snapshot()
    rows = {r["id"]: r for r in snap["rollouts"] if "eval" in r["tags"]}
    assert rows["e10000a"]["has_frames"] and not rows["e10000b"]["has_frames"]
    assert rows["e10000a"]["trace"]["points"]
    assert svc.rollout_payload("e10000a")["t"]
