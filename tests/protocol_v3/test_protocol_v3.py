"""Protocol v3 harness: access separation, scoring, ledger, budget, timeouts.

Held-out bundles in these tests are synthetic fixtures built in a temporary
directory; no real held-out case exists in the repository.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

from mirror.evaluate.protocol_v3 import conditions as C
from mirror.evaluate.protocol_v3.access import (DEV_SEEDS, HOLDOUT_SEEDS, AccessError, commitment,
                                                load_dev_cases, load_holdout)
from mirror.evaluate.protocol_v3.edits import apply_edit, semantic_state, state_of
from mirror.evaluate.protocol_v3.freeze import build, freeze, verify
from mirror.evaluate.protocol_v3.metrics import load, paired, per_condition
from mirror.evaluate.protocol_v3.runner import (Budget, base_spec, end_to_end, gold_chain, run, run_case,
                                                score_turn)
from mirror.evaluate.protocol_v3.scenes import resolve
from mirror.evaluate.protocol_v3.schema import CaseFile, SceneRef, StructuredEdit
from mirror.scenes.sampling import DEFAULT_LEDGER

ROOT = Path(__file__).resolve().parents[2]


def _dev():
    return load_dev_cases()


def _subset(cf, *ids):
    return cf.model_copy(update={"cases": [c for c in cf.cases if c.id in ids]})


def test_dev_cases_are_labelled_and_only_use_development_scenes():
    cf = _dev()
    assert cf.split == "dev" and cf.synthetic_or_team_written
    assert "NOT held-out" in cf.description
    cats = {c.category for c in cf.cases}
    assert cats == {"positive", "negative", "ambiguous", "unsupported", "infeasible"}
    assert any(len(c.turns) > 1 for c in cf.cases)
    for c in cf.cases:
        if c.scene.kind == "layout":
            assert c.scene.split == "dev" and DEV_SEEDS[0] <= c.scene.seed <= DEV_SEEDS[1]
        gold_chain(c, base_spec())          # every gold edit applies


def test_dev_loader_refuses_holdout_and_evaluation_layouts(tmp_path):
    doc = json.loads((ROOT / "mirror/evaluate/protocol_v3/dev_cases.json").read_text())
    doc["cases"][1]["scene"] = {"kind": "layout", "split": "eval", "family": "mixed", "seed": 90001}
    p = tmp_path / "x.json"
    p.write_text(json.dumps(doc))
    with pytest.raises(AccessError):
        load_dev_cases(p)
    doc["split"] = "holdout"
    p.write_text(json.dumps(doc))
    with pytest.raises(AccessError):
        load_dev_cases(p)


def _synthetic_holdout(tmp_path) -> Path:
    doc = json.loads((ROOT / "mirror/evaluate/protocol_v3/dev_cases.json").read_text())
    doc.update(split="holdout", synthetic_or_team_written=True,
               description="SYNTHETIC test fixture standing in for a held-out bundle; not real cases.")
    doc["cases"] = doc["cases"][:2]
    for c in doc["cases"]:
        c["author_id"] = "SYNTH-A1"
    p = tmp_path / "bundle.json"
    p.write_text(json.dumps(doc))
    return p


def test_holdout_needs_frozen_manifest_matching_commitment_and_outside_repo(tmp_path):
    p = _synthetic_holdout(tmp_path)
    com = commitment(p)
    assert com["n_cases"] == 2 and com["per_author"] == {"SYNTH-A1": 2}
    with pytest.raises(AccessError):
        load_holdout(p, None)
    with pytest.raises(AccessError):
        load_holdout(p, {"holdout_commitment": com})                    # not frozen
    with pytest.raises(AccessError):
        load_holdout(p, {"frozen_at": "x", "holdout_commitment": {"sha256": "0" * 64}})
    cf = load_holdout(p, {"frozen_at": "x", "holdout_commitment": com})
    assert cf.split == "holdout"
    inside = ROOT / "results" / ".tmp_holdout_probe.json"
    inside.write_bytes(p.read_bytes())
    try:
        with pytest.raises(AccessError):
            load_holdout(inside, {"frozen_at": "x", "holdout_commitment": com})
    finally:
        inside.unlink()


def test_evaluation_layouts_refused_in_development_and_logged_to_protocol_ledger(tmp_path):
    before = DEFAULT_LEDGER.read_bytes() if DEFAULT_LEDGER.exists() else b""
    ref = SceneRef(kind="layout", split="eval", family="mixed", seed=99990)   # outside every reserved range
    with pytest.raises(AccessError):
        resolve(ref)
    with pytest.raises(AccessError):
        resolve(ref, allow_eval=True)
    led = tmp_path / "ledger.jsonl"
    sc = resolve(ref, allow_eval=True, ledger=led)
    assert sc.seed == 99990 and json.loads(led.read_text().splitlines()[0])["seed"] == 99990
    after = DEFAULT_LEDGER.read_bytes() if DEFAULT_LEDGER.exists() else b""
    assert before == after
    assert not any(lo <= 99990 <= hi for lo, hi in HOLDOUT_SEEDS.values())


def test_every_object_rule_equals_per_object_rules():
    b = base_spec()
    a, _ = apply_edit(b, StructuredEdit.model_validate(
        {"rules": [{"type": "upright", "object": "all", "phase": "transport", "max_tilt_deg": 15}]}), text="a")
    e, _ = apply_edit(b, StructuredEdit.model_validate({"rules": [
        {"type": "upright", "object": k, "phase": "transport", "max_tilt_deg": 15} for k in ("cup", "block", "marker")]}),
        text="b")
    assert state_of(a) != state_of(e)
    assert semantic_state(a) == semantic_state(e)


def test_score_turn_flags_false_accept_refusal_and_wrong_scope():
    b = base_spec()
    s0 = semantic_state(b)
    cup, _ = apply_edit(b, StructuredEdit.model_validate(
        {"rules": [{"type": "upright", "object": "cup", "phase": "transport"}]}), text="c")
    both, _ = apply_edit(cup, StructuredEdit.model_validate(
        {"rules": [{"type": "upright", "object": "block", "phase": "transport"}]}), text="d")
    sc = score_turn(s0, C.TurnResult("apply", both), s0, semantic_state(cup), "apply")
    assert not sc["exact"] and sc["wrong_scope_kinds"] == ["block"]
    sc = score_turn(s0, C.TurnResult("asks", b), s0, semantic_state(cup), "apply")
    assert sc["false_refusal"] and not sc["exact"]
    sc = score_turn(s0, C.TurnResult("apply", cup), s0, s0, "rejects")
    assert sc["false_accept"]
    sc = score_turn(s0, C.TurnResult("abstain", b), s0, s0, "rejects")
    assert sc["refused_any"] and not sc["exact"]


def test_unknown_is_never_success():
    assert end_to_end("compliant_completion", None, True) is None
    ex = {"gold": {"status": "success", "unverified": ["o_marker_before_cup"]}, "plan_status": "ok",
          "proven_infeasible": False}
    assert end_to_end("compliant_completion", ex, True) is None
    ex["gold"]["unverified"] = []
    assert end_to_end("compliant_completion", ex, True) is True
    assert end_to_end("proven_infeasible", {"plan_status": "no_plan", "proven_infeasible": False}, True) is False


def test_ledger_run_once_resume_and_interrupted(tmp_path):
    cf = _subset(_dev(), "d05", "d06")
    out = tmp_path / "o"
    run(cf, [C.offline_rules()], out, run_id="r1", base_dir=ROOT, execute=False)
    with pytest.raises(RuntimeError):
        run(cf, [C.offline_rules()], out, run_id="r1", base_dir=ROOT, execute=False)
    led = [json.loads(x) for x in (out / "attempts.jsonl").read_text().splitlines()]
    assert [e["event"] for e in led] == ["start", "end", "start", "end"]
    # simulate a crash: a start with no end
    with open(out / "attempts.jsonl", "a") as f:
        f.write(json.dumps({"run_id": "r2", "event": "start", "attempt": 9, "case_id": "d05",
                            "condition": "offline_rules"}) + "\n")
    run(cf, [C.offline_rules()], out, run_id="r2", base_dir=ROOT, execute=False, resume=True)
    led = [json.loads(x) for x in (out / "attempts.jsonl").read_text().splitlines() if '"r2"' in x]
    assert {"interrupted", "completed"} <= {e.get("status") for e in led if e["event"] == "end"}


def _metered(outcome="asks"):
    def turn(spec, t, scene, base_dir):
        return C.TurnResult(outcome, spec, usage=[{"model": "muse-spark-1.3", "from_cassette": False,
                                                  "prompt_tokens": 2000, "completion_tokens": 2000}])
    return C.Condition("mirror_model", turn, "fresh model call")


def test_budget_stops_and_counts_every_remaining_attempt(tmp_path):
    cf = _subset(_dev(), "d05", "d06", "d07", "d16")
    out = tmp_path / "b"
    info = run(cf, [_metered()], out, run_id="b", base_dir=ROOT, execute=False,
               budget=Budget(cap_usd=0.025, reserve_per_turn_usd=0.011))
    ends = [json.loads(x) for x in (out / "attempts.jsonl").read_text().splitlines() if '"end"' in x]
    assert len(ends) == 4
    assert [e["status"] for e in ends].count("budget_stopped") >= 2
    assert info["spent_usd"] <= 0.025


def test_timeout_is_recorded_not_dropped(tmp_path):
    def slow(spec, t, scene, base_dir):
        time.sleep(30)
    cf = _subset(_dev(), "d05")
    out = tmp_path / "t"
    run(cf, [C.Condition("offline_rules", slow, "offline interpreter")], out, run_id="t", base_dir=ROOT,
        execute=False, timeout_s=2)
    ends = [json.loads(x) for x in (out / "attempts.jsonl").read_text().splitlines() if '"end"' in x]
    assert ends[0]["status"] == "timeout"
    res, led = load(out)
    assert per_condition(res, led)["offline_rules"]["attempt_status"] == {"timeout": 1}


def test_replay_miss_is_not_measured_never_offline(tmp_path):
    from mirror.interpret import LLMClient
    client = LLMClient(mode="replay", model="muse-spark-1.3", cassette_dir=tmp_path / "empty")
    cf = _subset(_dev(), "d01")
    r = run_case(cf.cases[0], C.mirror_model(client), base_dir=ROOT, executor=None)
    assert r["turns"][0]["outcome"] == "not_measured"
    assert r["end_to_end_success"] is None and r["measured"] is False


def test_replay_hit_on_recorded_benchmark_sentence():
    from mirror.interpret import LLMClient
    cas = ROOT / "data" / "cassettes_muse"
    client = LLMClient(mode="replay", model="muse-spark-1.3", cassette_dir=cas)
    case = _subset(_dev(), "d17").cases[0]
    r = run_case(case, C.mirror_model(client), base_dir=ROOT, executor=None)
    t = r["turns"][0]
    if t["outcome"] == "not_measured":
        pytest.skip("prompt changed since the recording; replay correctly reports not measured")
    assert t["outcome"] == "apply" and t["network_calls"] == 0 and t["score"]["exact"]


def test_paired_excludes_unknown():
    rows = [{"case_id": "a", "condition": "x", "end_to_end_success": True, "attempt": 1},
            {"case_id": "a", "condition": "y", "end_to_end_success": None, "attempt": 2},
            {"case_id": "b", "condition": "x", "end_to_end_success": True, "attempt": 3},
            {"case_id": "b", "condition": "y", "end_to_end_success": False, "attempt": 4}]
    p = paired(rows, "x", "y")
    assert p["unknown_or_missing"] == 1 and p["only_a"] == 1 and p["both"] == 0


def test_freeze_manifest_verifies_and_is_never_overwritten(tmp_path):
    m = build(holdout_commitment=None, models={"mirror_model": "muse-spark-1.3"},
              budget={"cap_usd": 1.0, "reserve_per_turn_usd": 0.05})
    assert verify(m) == []
    path = tmp_path / "freeze.json"
    frozen = freeze(m, path)
    assert frozen["frozen_at"]
    with pytest.raises(FileExistsError):
        freeze(m, path)
    assert verify(dict(m, code_sha256="0" * 64))


def test_design_is_machine_readable_and_not_marked_run():
    d = json.loads((ROOT / "mirror/evaluate/protocol_v3/design.json").read_text())
    assert d["status"].startswith("draft")
    assert set(d["conditions"]) == set(C.CONDITIONS)
    assert d["needs_budget"]["max_cost_usd"] > 0


def test_doc_protocol_exists():
    txt = (ROOT / "docs/protocols/v3_authoring.md").read_text()
    assert "not yet run" in txt.lower()


@pytest.mark.slow
def test_executed_dry_run_reports_independent_rejection(tmp_path):
    from mirror.evaluate.protocol_v3.runner import old_run_checks
    cf = _subset(_dev(), "d01", "d08")
    out = tmp_path / "e"
    run(cf, [C.offline_rules(), C.manual_structured()], out, run_id="e", base_dir=ROOT)
    res, led = load(out)
    s = per_condition(res, led)
    assert s["manual_structured"]["end_to_end_success"] == 2
    old = {x["case_id"]: x for x in old_run_checks(cf)}
    assert old["d01"]["rejected"] is True
