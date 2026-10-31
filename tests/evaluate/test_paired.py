import json

import pytest

from mirror.evaluate import (FreezeError, blocked_case, corrected_rules, freeze, paired_eval,
                             verify_manifest, write_report)
from mirror.evaluate import paired as paired_mod
from mirror.evaluate.feasibility import classify_planning_failure, feasibility, upright_infeasibility

from .stubs import StubEnv, stub_planner
from .synth import make_scene, make_spec

SEEDS = [1, 2, 3, 4, 5, 6]


def layout(seed, split):
    s = make_scene(scene_id=f"syn_{split}_{seed}")
    s.seed = seed
    return s


@pytest.fixture(scope="module")
def dev_report():
    StubEnv.fail_seeds = set()
    return paired_eval(make_spec(), make_spec(corrected=True), SEEDS, split="dev",
                       layout_fn=layout, planner=stub_planner, env_factory=StubEnv, n_boot=2000)


def test_corrected_rules_diff():
    rules = corrected_rules(make_spec(), make_spec(corrected=True))
    assert [r.id for r in rules] == ["c_upright_transport"]
    assert corrected_rules(make_spec(), make_spec()) == []


def test_paired_dev_run_reports_every_attempt(dev_report):
    r = dev_report
    assert len(r.trials) == len(SEEDS)
    for t in r.trials:
        assert t.a.report.scene_id == t.b.report.scene_id
        assert t.a.report.status == "success" and not t.a.violation_free_success
        assert t.a.corrected_violations and t.a.beads_lost == 8
        assert t.b.violation_free_success and t.b.beads_lost == 0
    assert r.paired["correction_exercised"]["n"] == len(SEEDS)
    v = r.paired["violation_free_success"]
    assert (v["only_a"], v["only_b"]) == (0, 6)
    assert v["mcnemar_p_exact"] == pytest.approx(2 / 64)
    ma, mb = r.metrics["original"], r.metrics["corrected"]
    assert ma.completion == mb.completion == 6
    assert ma.corrected_rule_violations == 6 and mb.corrected_rule_violations == 0
    assert ma.max_tilt_deg_max > 85 and mb.max_tilt_deg_max < 1
    assert ma.interventions == mb.interventions == 0
    assert ma.obstacle_contact_trials == mb.obstacle_contact_trials == 0


def test_scope_regression_marker_still_tilts(dev_report):
    sc = dev_report.scope
    assert sc["preserved"] == len(SEEDS) and sc["removed"] == 0
    assert dev_report.scope_objects == ["block_0", "marker_0"]


def test_execution_failures_stay_visible():
    StubEnv.fail_seeds = {2, 5}
    try:
        r = paired_eval(make_spec(), make_spec(corrected=True), SEEDS, split="dev", layout_fn=layout,
                        planner=stub_planner, env_factory=StubEnv, n_boot=500)
    finally:
        StubEnv.fail_seeds = set()
    mb = r.metrics["corrected"]
    assert mb.execution_failures == 2 and mb.violation_free_success == 4
    assert {t.seed for t in r.trials if t.b.report.status == "execution_failed"} == {2, 5}


def test_eval_split_requires_manifest(tmp_path):
    with pytest.raises(FreezeError):
        paired_eval(make_spec(), make_spec(corrected=True), SEEDS, split="eval", layout_fn=layout,
                    planner=stub_planner, env_factory=StubEnv)
    with pytest.raises(FreezeError):
        paired_eval(make_spec(), make_spec(corrected=True), SEEDS, split="eval", layout_fn=layout,
                    planner=stub_planner, env_factory=StubEnv, manifest_path=tmp_path / "missing.json")


def test_eval_split_with_valid_manifest_runs_and_layouts_follow_freeze(tmp_path):
    calls = []

    def tracking_layout(seed, split):
        calls.append(seed)
        return layout(seed, split)

    a, b = make_spec(), make_spec(corrected=True)
    m = freeze(a, b, [10001, 10002], "eval", tmp_path / "freeze.json")
    assert calls == []
    with pytest.raises(FileExistsError):
        freeze(a, b, [10001, 10002], "eval", tmp_path / "freeze.json")
    r = paired_eval(a, b, [10001, 10002], split="eval", manifest_path=tmp_path / "freeze.json",
                    layout_fn=tracking_layout, planner=stub_planner, env_factory=StubEnv, n_boot=200)
    assert calls == [10001, 10002]
    assert r.manifest == m
    assert r.config["layouts_generated_unix"] >= m.created_unix


def test_manifest_mismatches_are_refused(tmp_path, monkeypatch):
    a, b = make_spec(), make_spec(corrected=True)
    p = tmp_path / "freeze.json"
    freeze(a, b, [10001], "eval", p)
    tweaked = make_spec(corrected=True)
    tweaked.constraints[-1].params["max_tilt_deg"] = 30.0
    with pytest.raises(FreezeError, match="specification B"):
        verify_manifest(p, a, tweaked, [10001], "eval")
    with pytest.raises(FreezeError, match="seeds"):
        verify_manifest(p, a, b, [10001, 10002], "eval")
    monkeypatch.setattr(paired_mod, "code_hash", lambda root=None, exclude=(): ("0" * 64, 1))
    with pytest.raises(FreezeError, match="code"):
        verify_manifest(p, a, b, [10001], "eval")


def test_executor_described_as_deterministic(dev_report):
    from mirror.evaluate.paired import DEFAULT_EXECUTOR_LABEL, _name
    label = _name(None, DEFAULT_EXECUTOR_LABEL)
    assert "deterministic" in label and "seed=0" not in label
    assert dev_report.config["executor"] == "StubEnv"


def test_code_hash_is_stable():
    assert paired_mod.code_hash() == paired_mod.code_hash()


def test_independent_infeasibility_proof_on_full_wall():
    full = make_scene(full_wall=True)
    proof = upright_infeasibility(make_spec(corrected=True), full)
    assert proof and proof["proven"] and proof["object_id"] == "cup_0"
    assert upright_infeasibility(make_spec(), full) is None          # no upright transport rule
    assert upright_infeasibility(make_spec(corrected=True), make_scene()) is None  # route around exists
    tall = make_scene(full_wall=True, slot_height=(0.05, 0.25))
    assert upright_infeasibility(make_spec(corrected=True), tall) is None
    assert feasibility(make_spec(corrected=True), make_scene())["verdict"] == "feasible_witness"
    boxed = make_scene(boxes=[((0.2, 0.2, 0.05), (0.03, 0.03, 0.05))])
    assert feasibility(make_spec(corrected=True), boxed)["verdict"] == "unknown"


def test_planning_failure_classification():
    assert classify_planning_failure({"proven": True}, "proven_infeasible") == "proven_infeasible"
    assert classify_planning_failure(None, "feasible_witness") == "planner_failure_on_feasible_scene"
    assert classify_planning_failure({"note": "heuristic"}, "unknown") == "planner_failure_feasibility_unknown"
    assert classify_planning_failure({"proven": True}, "feasible_witness") == "planner_certificate_contradicted"
    assert classify_planning_failure(None, "proven_infeasible") == "infeasible_without_planner_certificate"


def test_blocked_case_distinguishes_proof():
    out = blocked_case(make_spec(), make_spec(corrected=True), scene=make_scene(full_wall=True),
                       planner=stub_planner, env_factory=StubEnv)
    a, b = out
    assert a["status"] == "success" and a["corrected_violations"] == 1 and a["beads_lost"] == 8
    assert b["status"] == "planning_failed" and b["failure_class"] == "proven_infeasible"
    assert b["independent"]["verdict"] == "proven_infeasible"


def test_wrongly_rejected_feasible_scene_counted():
    def refusing(spec, scene):
        from mirror.contracts import PlanResult
        return PlanResult(status="no_plan", reason="search exhausted", planner="refusing")
    r = paired_eval(make_spec(), make_spec(corrected=True), [1, 2], split="dev", layout_fn=layout,
                    planner=refusing, env_factory=StubEnv, n_boot=200)
    mb = r.metrics["corrected"]
    assert mb.planning_failures == 2 and mb.executed == 0 and mb.max_tilt_deg_max is None
    assert mb.feasible_scenes_known == 2 and mb.feasible_scenes_wrongly_rejected == 2
    assert all(t.b.failure_class == "planner_failure_on_feasible_scene" for t in r.trials)


def test_write_report(dev_report, tmp_path):
    paths = write_report(dev_report, tmp_path)
    data = json.loads(paths["json"].read_text())
    assert len(data["trials"]) == len(SEEDS)
    md = paths["markdown"].read_text()
    assert "Exact McNemar p = 0.03125" in md
    assert "Every attempt" in md and md.count("| syn_dev_") == len(SEEDS)
    for k in ("paired_tilt", "outcomes", "beads_lost", "tilt_traces"):
        assert paths[k].exists() and paths[k].stat().st_size > 1000
    assert (tmp_path / "manifest.json").exists()


def test_obstacle_contacts_reported_regardless_of_spec():
    from mirror.evaluate.paired import obstacle_contacts
    from .synth import pack_all
    log = pack_all(make_scene(), cup_route="around", cup_contact="obstacle:wall").build()
    oc = obstacle_contacts(log)
    assert len(oc) == 1 and oc[0]["body"] == "cup_0" and oc[0]["phase"].startswith("transport")
