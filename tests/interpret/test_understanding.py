"""M2 understanding matrix through the structured interpretation outcome."""
from __future__ import annotations

import json

import pytest

from mirror.contracts import Status
from mirror.interpret.outcome import SUPPORTED_CHANGES, interpret

from .test_capabilities import capability_base_spec
from .understanding_matrix import INFEASIBLE, MATRIX


def _versions(turns):
    v = [capability_base_spec()]
    for t in turns or []:
        out, new = interpret(v, t)
        assert new is not None, (t, out)
        v.append(new)
    return v


@pytest.mark.parametrize("case", MATRIX, ids=[f"{c['cat']}:{c['text']}" + (":ctx" if c.get("context") else "")
                                               for c in MATRIX])
def test_understanding_matrix(case):
    v = _versions(case.get("turns"))
    out, new = interpret(v, case["text"], context=case.get("context"))
    e = case["expect"]
    assert out.outcome == e["outcome"], (out.reason_code, out.message)
    if "reason_code" in e:
        assert out.reason_code == e["reason_code"], out.message
    assert out.provenance == "offline interpreter" and out.interpreter == "offline-rules"
    if out.outcome == "refuse" and out.reason_code == "not_supported":
        assert out.supported_changes == SUPPORTED_CHANGES and "supports" in out.message
    if out.outcome == "clarify":
        assert out.message.endswith("?") and out.options
        assert new is None
    if out.outcome in ("apply", "revert"):
        assert new.version == v[-1].version + 1
        enforced = {c.id: c for c in new.enforced_constraints()}
        for rid, params in e.get("enforce", {}).items():
            assert rid in enforced, sorted(enforced)
            if params:
                assert enforced[rid].params == params
        orders = {o.id: o for o in new.ordering if o.status == Status.confirmed}
        for rid in e.get("not_enforced", []):
            assert rid not in enforced and rid not in orders
        for oid, ev in e.get("order", {}).items():
            assert (orders[oid].before_event, orders[oid].after_event) == ev
        if "alias" in e:
            assert out.alias and out.alias["resolved_to"] == e["alias"]
    json.dumps(out.model_dump(mode="json"))     # the studio/web contract is plain JSON


def test_vague_is_never_silently_upright():
    out, new = interpret([capability_base_spec()], "Be careful with the cup.")
    assert new is None and "upright" in out.message and "distance" in out.message


def test_regression_words_are_labelled():
    assert {c["text"] for c in MATRIX if c.get("regression")} == {"Put the jar in the tray.", "Keep the bowl upright."}
    from mirror.interpret.guard import ABSENT_NOUNS
    from mirror.interpret.offline import KIND_WORDS, UNKNOWN_OBJECTS
    lex = set(ABSENT_NOUNS) | set(UNKNOWN_OBJECTS) | {w for ws in KIND_WORDS.values() for w in ws}
    for w in ("thermos", "goblet", "coffee"):
        assert w not in lex   # fresh words exercise the general path, not a list entry


@pytest.mark.parametrize("case", INFEASIBLE, ids=[c["name"] for c in INFEASIBLE])
def test_infeasible_supported_combinations(case):
    from mirror.evaluate.specs import evaluation_specs
    from mirror.plan import plan
    from mirror.plan.outcome import planning_outcome
    from mirror.scenes import reference_scene
    base, _, _ = evaluation_specs(source="offline")
    out, new = interpret([base], case["text"])
    assert out.outcome == "apply", out.message
    res = planning_outcome(plan(new, reference_scene(case["scene"])))
    assert res["kind"] == case["expect"], res
    if res["kind"] == "proven_infeasible":
        assert res["headline"] == "Proven infeasible under these stated assumptions" and res["assumptions"]
        assert "not a guarantee about physical safety" in res["detail"]
    else:
        assert res["headline"] == "Planner found no compliant route" and "not a proof" in res["detail"]


# --------------------------------------------- model path (fake client) ---

def _client(api, tmp_path, mode="live"):
    from mirror.interpret import LLMClient
    return LLMClient(base_url="http://fake/v1", model="m-1", api_key="k", mode=mode,
                     cassette_dir=tmp_path, transport=api.transport() if api else None, backoff_s=0.0)


def test_model_question_and_provenance_labels(tmp_path):
    from tests.interpret.fake_api import FakeAPI
    reply = {"outcome": "clarify", "interpretation": "care requested", "question": "Which requirement matters?",
             "options": ["Upright while carried", "Distance from the wall"], "ops": []}
    api = FakeAPI([reply])
    out, new = interpret([capability_base_spec()], "Be gentle with that one.",
                         client=_client(api, tmp_path, mode="record"), prompt_revision="4")
    assert (out.outcome, out.reason_code, out.provenance, out.prompt_revision) == \
        ("clarify", "model_question", "fresh model call", "4")
    assert "13. A word that is not an object kind" in api.requests[0]["body"]["messages"][0]["content"]
    # the same request replays from the recording and is labelled as such
    out2, _ = interpret([capability_base_spec()], "Be gentle with that one.",
                        client=_client(FakeAPI([]), tmp_path, mode="replay"), prompt_revision="4")
    assert out2.provenance == "replayed cassette" and out2.outcome == "clarify"


def test_model_cannot_map_an_unknown_word_without_context(tmp_path):
    """Even if a model maps 'the coffee' to the cup, the deterministic layer asks."""
    from tests.interpret.fake_api import FakeAPI
    rule = {"type": "upright", "object_kind": "cup", "phase": "transport", "max_tilt_deg": None,
            "contact_with": None, "min_clearance_m": None}
    op = dict(action="require_rule", rule=rule, target_id=None, before_kind=None, after_kind=None, goal_kind=None,
              rationale="x", order_event=None)
    api = FakeAPI([{"outcome": "apply", "interpretation": "x", "question": None, "options": [], "ops": [op]}] * 2)
    out, _ = interpret([capability_base_spec()], "Don't tip the coffee.", client=_client(api, tmp_path),
                       prompt_revision="4")
    assert (out.outcome, out.reason_code) == ("clarify", "unknown_object")
    out, new = interpret([capability_base_spec()], "Don't tip the coffee.", client=_client(api, tmp_path / "b"),
                         context={"focus_kind": "cup"}, prompt_revision="4")
    assert out.outcome == "apply" and out.alias["resolved_to"] == "cup"


def test_docs_list_every_matrix_case():
    from pathlib import Path
    doc = (Path(__file__).resolve().parents[2] / "docs" / "RULES.md").read_text()
    for c in MATRIX + INFEASIBLE:
        assert c["text"] in doc, c["text"]
