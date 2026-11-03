"""Correction capabilities beyond the prepared sentence (offline compiler)."""
from __future__ import annotations

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
