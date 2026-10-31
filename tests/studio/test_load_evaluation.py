"""Saved evaluation results become a replayable evidence grid without sampling any layout."""
import json

import pytest

from mirror.studio import session_adapter as sa
from mirror.studio.fixtures import FixtureSession
from mirror.studio.session_adapter import EvaluationFenceError, StudioService, check_showcase


def _rollout_files(folder, seeds):
    s = FixtureSession()
    s.record_demo(); s.interpret()
    (folder / "rollouts").mkdir(parents=True)
    trials = []
    for seed in seeds:
        e = s.run(condition="x")
        (folder / "rollouts" / f"seed{seed}_original.json").write_text(e.rollout.model_dump_json())
        rep = e.rollout.report.model_dump(mode="json") | {"scene_id": f"eval_{seed}"}
        trials.append({"seed": seed, "scene_id": f"eval_{seed}",
                       "a": {"condition": "original", "report": rep, "rollout_path": f"rollouts/seed{seed}_original.json",
                             "corrected_violations": [{"constraint_id": "c", "object_id": "cup_0", "t_start": 1, "t_end": 2, "peak": 90}]},
                       "b": {"condition": "corrected", "report": rep, "rollout_path": "rollouts/missing.json",
                             "corrected_violations": []}})
    return {"split": "eval", "manifest": {"spec_a": {"version": 1}, "spec_b": {"version": 2}}, "trials": trials,
            "metrics": {}, "paired": {}}


@pytest.fixture()
def no_sampling(monkeypatch):
    """Fail the test if anything asks the scene sampler for a layout."""
    import mirror.scenes as scenes

    def boom(*a, **k):
        raise AssertionError(f"sample_layout called with {a} {k}")
    monkeypatch.setattr(scenes, "sample_layout", boom, raising=False)


def test_load_single_report(tmp_path, no_sampling):
    (tmp_path / "report.json").write_text(json.dumps(_rollout_files(tmp_path, [30000])))
    svc = StudioService(backend="fixture")
    ev = svc.load_evaluation(tmp_path)
    assert ev["spec_a"] == 1 and ev["spec_b"] == 2 and ev["source"]["kind"] == "loaded"
    assert ev["studio"]["rollout_ids"]["30000"] == {"a": "e30000a", "b": "e30000b"}
    rows = {r["id"]: r for r in svc.snapshot()["rollouts"] if "eval" in r["tags"]}
    assert rows["e30000a"]["has_frames"] and not rows["e30000b"]["has_frames"]
    assert rows["e30000a"]["sketch"] and rows["e30000a"]["trace"]["points"]
    sketch = rows["e30000a"]["scene"]
    assert {o["kind"] for o in sketch["objects"]} == {"cup", "block", "marker"}
    assert sketch["trays"] and sketch["obstacles"]
    assert svc.rollout_payload("e30000a")["t"]


def test_load_final_report_with_strata(tmp_path, no_sampling):
    strata = {}
    for name, seeds in (("primary", [30000, 30001]), ("blocked", [30000])):
        strata[name] = _rollout_files(tmp_path / name, seeds)
    (tmp_path / "report.json").write_text(json.dumps({"manifest": {"kind": "final"}, "strata": strata,
                                                      "roles": {"primary": "primary", "blocked": "blocked"}}))
    svc = StudioService(backend="fixture")
    ev = svc.load_evaluation(tmp_path / "report.json")
    assert ev["final"] and set(ev["strata"]) == {"primary", "blocked"}
    assert ev["strata"]["primary"]["studio"]["rollout_ids"]["30001"]["a"] == "eprimary-30001a"
    ids = {r["id"] for r in svc.snapshot()["rollouts"]}
    assert {"eprimary-30000a", "eblocked-30000a"} <= ids


def test_showcase_fence():
    assert check_showcase([9000, 9099], "dev") == [9000, 9099]
    for seeds, split in (([9000], "eval"), ([10000], "dev"), ([30000], "dev"), ([], "dev"), ([9000], "blocked")):
        with pytest.raises(EvaluationFenceError):
            check_showcase(seeds, split)
    svc = StudioService(backend="fixture")
    with pytest.raises(EvaluationFenceError):
        svc.evaluate([30000], split="eval")
    assert not svc.jobs, "a refused evaluation must not start a job"
