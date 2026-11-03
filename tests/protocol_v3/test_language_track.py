"""Language-understanding track of protocol v3: varied bases, accepted
alternatives, blinded adjudication. Development cases only."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mirror.evaluate.protocol_v3 import adjudication as J
from mirror.evaluate.protocol_v3 import conditions as C
from mirror.evaluate.protocol_v3.access import DEV_LANGUAGE_CASES, load_dev_cases
from mirror.evaluate.protocol_v3.edits import apply_edit, semantic_state
from mirror.evaluate.protocol_v3.metrics import latest, load, per_condition
from mirror.evaluate.protocol_v3.runner import base_spec, run, run_case
from mirror.evaluate.protocol_v3.schema import Case, StructuredEdit

ROOT = Path(__file__).resolve().parents[2]


def _lang():
    return load_dev_cases(DEV_LANGUAGE_CASES)


def test_language_cases_vary_bases_and_cover_required_phenomena():
    cf = _lang()
    assert all(c.track == "language" and c.expect_execution == "not_executed" for c in cf.cases)
    assert len({c.base for c in cf.cases}) >= 5
    tags = {t for c in cf.cases for t in c.scope_tags}
    for need in ("negation", "multi_turn", "new_rule", "alternatives"):
        assert need in tags
    assert any(t.startswith("scope") for t in tags) and any(t.startswith("ambiguity") for t in tags)


def test_every_base_is_a_valid_distinct_specification():
    names = ["reference_reviewed", "reference_unreviewed", "cup_transport_confirmed", "block_strict",
             "ordering_confirmed", "all_transport_upright"]
    states = [json.dumps(semantic_state(base_spec(n)), sort_keys=True) for n in names]
    assert len(set(states)) == len(names)
    with pytest.raises(ValueError):
        base_spec("nope")


def test_language_case_cannot_require_execution():
    raw = json.loads(DEV_LANGUAGE_CASES.read_text())["cases"][0]
    raw["expect_execution"] = "compliant_completion"
    with pytest.raises(ValueError):
        Case.model_validate(raw)


def test_accepted_alternative_scores_as_correct_and_is_counted():
    case = next(c for c in _lang().cases if "alternatives" in c.scope_tags)
    alt = case.turns[0].gold.alternatives[0]

    def editor(spec, turn, scene, base_dir):
        new, _ = apply_edit(spec, alt, text=turn.text)
        return C.TurnResult("apply", new, interpreter="fixture")
    r = run_case(case, C.Condition("fixture", editor, "manual edit"), base_dir=ROOT, executor=None)
    sc = r["turns"][0]["score"]
    assert sc["exact"] and sc["acceptable_alternative"] and not sc["wrong_scope_kinds"]


def test_rule_baseline_and_model_may_differ(tmp_path):
    """No agreement gate: each condition is scored on its own against the gold."""
    cf = _lang()
    out = tmp_path / "l"
    run(cf, [C.offline_rules(), C.manual_structured()], out, run_id="l", base_dir=ROOT, execute=False)
    res, led = load(out)
    s = per_condition(res, led, track="language")
    assert s["manual_structured"]["final_state_exact"] == len(cf.cases)   # team fixtures equal the gold
    assert s["offline_rules"]["cases_with_result"] == len(cf.cases)
    assert "negation" in s["offline_rules"]["by_tag"]


def test_blinded_pack_hides_conditions_and_unblinds(tmp_path):
    cf = _lang()
    out = tmp_path / "p"
    run(cf, [C.offline_rules(), C.manual_structured()], out, run_id="p", base_dir=ROOT, execute=False)
    res, _ = load(out)
    info = J.make_pack(latest(res).values(), tmp_path / "pack", seed=1)
    sheets = (tmp_path / "pack" / "sheets.json").read_text()
    assert "offline" not in sheets and "manual" not in sheets and "team-fixture" not in sheets
    key = json.loads((tmp_path / "pack" / "key.json").read_text())
    assert info["outputs"] == len(key) == 2 * sum(len(c.turns) for c in cf.cases)
    judged = {k: "correct" for k in list(key)[:-1]}
    counts = J.unblind(judged, key)
    assert sum(v.get("not_judged", 0) for v in counts.values()) == 1
    with pytest.raises(ValueError):
        J.unblind({list(key)[0]: "great"}, key)


def test_gold_agreement_kappa_and_disagreements():
    e1 = {"rules": [{"type": "upright", "object": "cup", "phase": "transport", "max_tilt_deg": 15}]}
    e2 = {"rules": [{"type": "upright", "object": "cup", "phase": "transport", "max_tilt_deg": 20}]}
    a = {"x#1": {"outcome": "apply", "edit": e1}, "x#2": {"outcome": "asks", "edit": None},
         "y#1": {"outcome": "rejects", "edit": None}, "z#1": {"outcome": "apply", "edit": e1}}
    b = {"x#1": {"outcome": "apply", "edit": e1}, "x#2": {"outcome": "asks", "edit": None},
         "y#1": {"outcome": "asks", "edit": None}, "z#1": {"outcome": "apply", "edit": e2}}
    g = J.gold_agreement(a, b)
    assert g["turns"] == 4 and g["outcome_agreement"] == 0.75
    assert g["edit_agreement_when_both_apply"] == "1/2"
    assert sorted(g["to_resolve"]) == ["y#1", "z#1"]
    assert J.cohen_kappa(["a", "b", "a"], ["a", "b", "a"]) == 1.0
