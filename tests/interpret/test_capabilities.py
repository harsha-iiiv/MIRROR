"""Correction capabilities beyond the prepared sentence (offline compiler)."""
from __future__ import annotations

import json

import pytest

from mirror.contracts import (Constraint, Goal, ObjectBinding, ObjectSelector, OrderingConstraint,
                              Provenance, Status, TaskSpec)
from mirror.interpret.drafts import correction_to_ops
from mirror.interpret.offline import compile_offline
from mirror.interpret.specops import apply_ops
from mirror.interpret.types import CorrectionRejected, InterpretError, NeedsClarification

from .paraphrases import DEV_PARAPHRASES, HELDOUT_PARAPHRASES

P = Provenance(source="demo_inference")


def capability_base_spec() -> TaskSpec:
    kinds = ("cup", "block", "marker")
    return TaskSpec(
        spec_id="cap", version=1,
        bindings=[ObjectBinding(role={"cup": "container"}.get(k, k), kind=k) for k in kinds],
        goals=[Goal(id=f"g_inside_{k}", type="inside", object=ObjectSelector(kind=k), status=Status.confirmed,
                    provenance=P) for k in kinds],
        constraints=[
            Constraint(id="c_upright_final_cup", type="upright", applies_to=ObjectSelector(kind="cup"),
                       phase="final", params={"max_tilt_deg": 15.0}, status=Status.confirmed, provenance=P),
            Constraint(id="c_upright_transport_cup", type="upright", applies_to=ObjectSelector(kind="cup"),
                       phase="transport", params={"max_tilt_deg": 15.0}, status=Status.proposed, provenance=P)],
        ordering=[OrderingConstraint(id="o_cup_before_block", before=ObjectSelector(kind="cup"),
                                     after=ObjectSelector(kind="block"), provenance=P),
                  OrderingConstraint(id="o_block_before_marker", before=ObjectSelector(kind="block"),
                                     after=ObjectSelector(kind="marker"), provenance=P)])


def run_sentence(text, spec=None):
    """("apply", new_spec) | ("clarify", question) | ("reject", reason) | ("error", msg)."""
    spec = spec or capability_base_spec()
    try:
        draft = compile_offline(spec, text)
        ops = correction_to_ops(draft, spec, text)
        new, _ = apply_ops(spec, ops, text=text, kind="correction", model="offline-rules")
        return "apply", new
    except NeedsClarification as e:
        return "clarify", e.question
    except CorrectionRejected as e:
        return "reject", str(e)
    except InterpretError as e:
        return "error", str(e)


def score(text, expected) -> tuple[bool, str]:
    kind, out = run_sentence(text)
    if kind != expected["outcome"]:
        return False, f"{kind}: {out if kind != 'apply' else [c.id for c in out.enforced_constraints()]}"
    if kind != "apply":
        return True, ""
    enforced = {c.id: c for c in out.enforced_constraints()}
    for rid, params in expected.get("enforce", {}).items():
        if rid not in enforced:
            return False, f"{rid} not enforced; enforced={sorted(enforced)}"
        if params is not None and enforced[rid].params != params:
            return False, f"{rid} params {enforced[rid].params} != {params}"
    for rid in expected.get("not_enforced", []):
        if rid in enforced or any(o.id == rid and o.status == Status.confirmed for o in out.ordering):
            return False, f"{rid} is still enforced"
    orders = {o.id: o for o in out.ordering if o.status == Status.confirmed}
    for oid, (be, ae) in expected.get("order", {}).items():
        if oid not in orders:
            return False, f"{oid} not confirmed; confirmed={sorted(orders)}"
        if (orders[oid].before_event, orders[oid].after_event) != (be, ae):
            return False, f"{oid} events {(orders[oid].before_event, orders[oid].after_event)}"
    return True, ""


@pytest.mark.parametrize("text,expected", DEV_PARAPHRASES, ids=[t for t, _ in DEV_PARAPHRASES])
def test_dev_paraphrases(text, expected):
    ok, why = score(text, expected)
    assert ok, why


def heldout_report():
    rows = [(t, *score(t, e)) for t, e in HELDOUT_PARAPHRASES]
    return sum(ok for _, ok, _ in rows), len(rows), rows


def test_heldout_paraphrases_are_reported_not_tuned(capsys):
    passed, total, rows = heldout_report()
    with capsys.disabled():
        print(f"\nheld-out author-written paraphrases: {passed}/{total}")
        for t, ok, why in rows:
            if not ok:
                print(f"  MISS {t!r}: {why}")
    assert total == len(HELDOUT_PARAPHRASES)



# ------------------------------------------------------- multi-turn ---

def test_multi_turn_updates_and_reversals_keep_a_monotonic_history():
    from mirror.interpret.correct import apply_correction
    v = [capability_base_spec()]
    steps = ["Keep the cup at least 5 cm away from the wall.",
             "Pack the marker before the cup.",
             "Actually, make it 8 cm.",
             "Undo that.",
             "Go back to version 1."]
    for t in steps:
        new, rec = apply_correction(v, t)
        assert new.version == v[-1].version + 1 and new.parent_version == v[-1].version
        v.append(new)
    clr = lambda s: {c.id: c.params for c in s.enforced_constraints()}.get("c_keep_clearance_transport_cup")
    assert clr(v[1]) == {"min_m": 0.05} and clr(v[3]) == {"min_m": 0.08}
    assert clr(v[4]) == {"min_m": 0.05}                        # undo: back to v3's rules
    assert any(o.id == "o_marker_before_cup" and o.status == Status.confirmed for o in v[4].ordering)
    assert clr(v[5]) is None and not [o for o in v[5].ordering if o.status == Status.confirmed]
    assert [h.kind for h in v[-1].history] == ["correction", "correction", "correction", "revert", "revert"]
    assert [h.version for h in v[-1].history] == [2, 3, 4, 5, 6]


def test_follow_up_number_adjusts_the_rule_changed_last():
    from mirror.interpret.correct import apply_correction
    v = [capability_base_spec()]
    v.append(apply_correction(v, "The cup can tilt up to 25 degrees while it moves, no more.")[0])
    new, rec = apply_correction(v, "make that 35 degrees")
    assert {c.id: c.params for c in new.enforced_constraints()}["c_upright_transport_cup"] == {"max_tilt_deg": 35.0}
    assert rec.diff[0].op == "modify_constraint"


def test_follow_up_without_history_is_not_guessed():
    kind, _ = run_sentence("make that 35 degrees")
    assert kind == "reject"


def test_scoped_correction_leaves_other_objects_free():
    kind, new = run_sentence("Only the cup has to stay upright while moving; the marker can be tilted.")
    assert kind == "apply"
    enforced = {c.id for c in new.enforced_constraints()}
    assert "c_upright_transport_cup" in enforced
    assert not any(c.applies_to.kind in ("marker", "block") for c in new.enforced_constraints())


def test_new_constraint_is_not_a_confirmed_proposal():
    base = capability_base_spec()
    kind, new = run_sentence("Keep the cup at least 5 cm away from the wall.")
    c = {c.id: c for c in new.constraints}["c_keep_clearance_transport_cup"]
    assert c.id not in {x.id for x in base.constraints}
    assert c.provenance.source == "user_correction" and c.provenance.version_introduced == 2
    assert c.status == Status.confirmed


def test_ambiguous_instruction_gets_a_useful_question():
    kind, q = run_sentence("Be careful with the cup.")
    assert kind == "clarify"
    for word in ("upright", "distance", "order"):
        assert word in q


# ---------------------------------------------- prompt revision 4 ---

def _client(api, tmp_path):
    from mirror.interpret import LLMClient
    return LLMClient(base_url="http://fake/v1", model="m-1", api_key="k", mode="live",
                     cassette_dir=tmp_path, transport=api.transport(), backoff_s=0.0)


def _draft(**op):
    base = dict(action="require_order", rule=None, target_id=None, before_kind=None, after_kind=None,
                goal_kind=None, rationale="test", order_event=None)
    base.update(op)
    return {"outcome": "apply", "interpretation": "x", "question": None, "options": [], "ops": [base]}


def test_revision_4_prompt_and_schema_carry_order_events(tmp_path):
    from mirror.interpret.correct import compile_correction
    from tests.interpret.fake_api import FakeAPI
    api = FakeAPI([_draft(before_kind="block", after_kind="cup", order_event="start_before_start")])
    new, rec = compile_correction(capability_base_spec(), "Start with the block, then the cup.",
                                  client=_client(api, tmp_path), prompt_revision_="4")
    o = {o.id: o for o in new.ordering}["o_block_started_before_cup"]
    assert (o.before_event, o.after_event, o.status) == ("grasp_start", "grasp_start", Status.confirmed)
    body = api.requests[0]["body"]
    system = body["messages"][0]["content"]
    assert "start_before_start" in system and "never invent one" in system
    schema = json.dumps(body.get("response_format") or body["messages"])
    assert "order_event" in schema


def test_revision_4_unsupported_rule_from_the_model_becomes_a_question(tmp_path):
    from mirror.interpret.correct import compile_correction
    from tests.interpret.fake_api import FakeAPI
    rule = {"type": "forbidden_contact", "object_kind": "cup", "phase": "transport", "max_tilt_deg": None,
            "contact_with": ["table_outside_tray"], "min_clearance_m": None}
    api = FakeAPI([_draft(action="require_rule", rule=rule)])
    with pytest.raises(NeedsClarification):
        compile_correction(capability_base_spec(), "Never let the cup touch the table while carrying it.",
                           client=_client(api, tmp_path), prompt_revision_="4")


def test_revision_3_messages_are_unchanged_by_default(tmp_path):
    """Recorded responses were made with revision 3; the default must still send it."""
    from mirror.interpret.correct import compile_correction
    from mirror.interpret.prompts import CORRECT_SYSTEM
    from tests.interpret.fake_api import FakeAPI
    d = _draft(action="require_order", before_kind="marker", after_kind="cup")
    d["ops"][0].pop("order_event")
    api = FakeAPI([d])
    compile_correction(capability_base_spec(), "Pack the marker before the cup.", client=_client(api, tmp_path))
    body = api.requests[0]["body"]
    assert body["messages"][0]["content"] == CORRECT_SYSTEM
    assert "order_event" not in json.dumps(body)


def test_phase_clause_and_distance_clause_make_one_clearance_rule():
    """Regression (protocol v3 dry run): two require ops for one sentence gave a duplicate id."""
    kind, new = run_sentence("While carrying the cup, keep it at least 3 cm away from the wall.")
    assert kind == "apply"
    rules = [c for c in new.constraints if c.type == "keep_clearance"]
    assert [(c.id, c.params) for c in rules] == [("c_keep_clearance_transport_cup", {"min_m": 0.03})]


def test_two_different_values_for_one_rule_get_a_question():
    kind, q = run_sentence("While carrying the cup keep it 3 cm from the wall, keep the cup 5 cm from the wall.")
    assert kind == "clarify"
