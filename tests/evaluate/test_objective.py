"""Registration objective_v1 harness (no simulation in the fast tests)."""
import json
from pathlib import Path

import pytest

from mirror.evaluate.objective import common, conditions, design, report, sweep

ROOT = Path(__file__).resolve().parents[2]


def test_lam_keys_use_audit_names():
    assert [common.lam_key(x) for x in common.SWEEP] == ["0.05", "0.10", "0.20", "0.40", "1.0"]
    assert common.lam_key(0.13719) == "0.1372"


def test_never_tilt_spec_is_a_planning_copy():
    a, b, _ = common.spec_pair()
    before = a.model_dump_json()
    nt = conditions.never_tilt_spec(a)
    added = {c.id for c in nt.enforced_constraints()} - {c.id for c in a.enforced_constraints()}
    assert added == {f"c_baseline_never_tilt_{k}" for k in conditions.KINDS}
    assert all(c.phase == "transport" and c.type == "upright" for c in nt.constraints if c.id in added)
    assert a.model_dump_json() == before          # original untouched
    assert conditions.spec_for("never_tilt@0.2", a, b) is a     # checked against the original
    assert conditions.spec_for("corrected@0.05", a, b) is b


def test_condition_list_covers_the_brief():
    names = conditions.condition_names(0.137)
    assert names[0] == "original@0.137"
    for x in ("0.05", "0.10", "0.20", "0.40", "1.0"):
        assert f"original@{x}" in names
    assert {"never_tilt@0.137", "corrected@0.137", "corrected@0.05"} <= set(names)


def test_never_tilt_planner_never_tilts_a_carried_object():
    from mirror.scenes import sample_layout
    a, _, _ = common.spec_pair()
    sc = sample_layout(8102, "dev", family="shortcut")
    pr = conditions.planner_for("never_tilt@0.05", 0.05)(a, sc)
    assert pr.status == "ok"
    for obj in ("cup_0", "block_0", "marker_0"):
        assert common.transport_note(pr, obj)["mode"] == "upright"
    # the same layout at 0.05 without the baseline tilts the cup through the slot
    assert common.tilted(common.transport_note(conditions.planner_for("original@0.05", 0.05)(a, sc), "cup_0"))


def test_route_segments_match_planner_cost():
    """Transport features computed from the plan's poses reproduce the
    planner's own route cost L + lambda * R for the chosen candidate."""
    from mirror.plan import plan
    from mirror.scenes import sample_layout
    a, _, _ = common.spec_pair()
    sc = sample_layout(8102, "dev", family="shortcut")
    for lam in (0.05, 1.0):
        pr = plan(a, sc, config=common.config_with(lam))
        chosen = {(r.object_id, r.label.startswith("transit")): r.cost for r in pr.routes if r.chosen}
        for s in common.route_segments(pr, sc):
            if s["kind"] == "transport":
                assert s["length_m"] + lam * s["rotation_rad"] == pytest.approx(chosen[(s["object_id"], False)], abs=2e-4)


def test_seed_ranges_do_not_overlap_reserved_ranges():
    assert design.overlaps() == []
    clash = [dict(name="x", split="eval", seeds=[50_010, 50_011])]
    assert design.overlaps(clash)


def test_ledger_hits(tmp_path):
    p = tmp_path / "a_ledger.jsonl"
    p.write_text(json.dumps({"seed": 82601, "split": "eval"}) + "\n" + json.dumps({"seed": 82601, "split": "dev"}) + "\n")
    assert len(design.ledger_hits({"eval": {82601}}, [tmp_path])) == 1
    assert design.ledger_hits({"blocked": {82601}}, [tmp_path]) == []


def test_frozen_reports_recount_reproduces_audit():
    rec = sweep.discordant_recount()
    assert (rec["discordant"], rec["corrected_faster"]) == (113, 110)
    assert rec["by_registration"]["eval"]["discordant"] == 59
    assert rec["by_registration"]["eval_muse"]["discordant"] == 54


def test_committed_repro_matches_audit():
    s = json.loads((ROOT / "results/eval_objective_v1/audit_repro/summary.json").read_text())
    assert all(s["reproduced"]["plan_only_matches_audit"].values())
    assert s["reproduced"]["recount_matches_audit"]
    assert s["scenes_matching_frozen"] == "90/90"


def _synthetic(seed, cond, vfs, done=True, t=50.0, tilt=False):
    """SYNTHETIC record for report tests only."""
    return dict(stratum="primary", seed=seed, condition=cond, plan_status="ok" if done else "no_plan",
                plan_sha256=f"{cond}{seed}" if done else None, proven_infeasible=False, cup_tilted_planned=tilt,
                path_length_m=5.0, path_rotation_rad=10.0, path_cost_calibrated=6.0, beads_retained=8 if vfs else 4,
                trial=dict(violation_free_success=vfs, corrected_violations=[] if vfs else [{"x": 1}],
                           corrected_max_tilt_deg=90.0 if tilt else 0.1, beads_total=8, beads_lost=0 if vfs else 4,
                           obstacle_contacts=[], report=dict(status="success" if done else "planning_failed",
                                                             completion=done, physics_steps=100 if done else 0,
                                                             execution_time_s=t)))


def test_report_retained_benefit_on_synthetic_records():
    man = dict(calibrated_key="0.15", calibrated_rotation_penalty=0.15,
               conditions=conditions.condition_names(0.15),
               strata=[dict(name="primary", seeds=list(range(10)))])
    recs = []
    for s in range(10):
        for c in man["conditions"]:
            if c == "original@0.05":
                v = s >= 4            # 4 tilted spills at 0.05
            elif c == "original@0.15":
                v = s >= 1            # 1 at the calibrated price
            else:
                v = True
            recs.append(_synthetic(s, c, v, t=55.0 if not v else 50.0, tilt=not v))
    res = report.analyse(man, recs)
    rb = res["strata"]["primary"]["retained_benefit"]
    assert rb["retained"] == pytest.approx(0.25)
    assert res["strata"]["primary"]["vfs"]["corrected@cal vs original@cal"]["only_b"] == 1
    md = report.markdown(man, dict(attempt="synthetic", status="synthetic"), res)
    assert "Retained benefit" in md


def test_committed_attempt_report_recomputes_from_records():
    out = ROOT / "results/eval_objective_v1"
    if not (out / "attempt_1/records.jsonl").exists():
        pytest.skip("no attempt recorded")
    manifest, recs, _ = report.load(out, "attempt_1")
    res = report.analyse(manifest, recs)
    saved = json.loads((out / "attempt_1/report.json").read_text())
    assert json.loads(json.dumps(res["strata"])) == saved["strata"]
    assert len(recs) == manifest["seed_count"] * len(manifest["conditions"])
    # the run's ledger entries come after the freeze and cover every frozen seed once
    led = [json.loads(l) for l in (out / "ledger.jsonl").read_text().splitlines()]
    run = [e for e in led if e["caller"] == "mirror.evaluate.objective.run"]
    assert len(run) == manifest["seed_count"] and min(e["unix"] for e in run) > manifest["created_unix"]
