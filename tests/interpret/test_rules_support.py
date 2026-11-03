"""The compiler, planner and checker agree on the supported rule semantics."""
from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pytest

from mirror import rules_support as rs
from mirror.contracts import (Constraint, Goal, ObjectBinding, ObjectSelector, OrderingConstraint,
                              OrderingEvent, Phase, Provenance, Status, TaskSpec)
from mirror.interpret import specops
from mirror.interpret.drafts import ContactGroup
from mirror.interpret.offline import compile_offline
from mirror.interpret.types import CorrectionRejected, NeedsClarification

REPO = Path(__file__).resolve().parents[2]
PROV = Provenance(source="manual")


def base_spec(constraints=(), ordering=()):
    return TaskSpec(spec_id="s", bindings=[ObjectBinding(role="container", kind="cup"),
                                           ObjectBinding(role="block", kind="block"),
                                           ObjectBinding(role="marker", kind="marker")],
                    goals=[Goal(id=f"g_inside_{k}", type="inside", object=ObjectSelector(kind=k),
                                status=Status.confirmed, provenance=PROV) for k in ("cup", "block", "marker")],
                    constraints=list(constraints), ordering=list(ordering))


def params_for(ctype, variant):
    if ctype == "upright":
        return {"max_tilt_deg": 15.0}
    if ctype == "forbidden_contact":
        return {"with": [variant]}
    return {"min_m": 0.03} if variant in (None, "obstacle") else {"min_m": 0.03, "from": variant}


def all_constraint_forms():
    ctypes = get_args(Constraint.model_fields["type"].annotation)
    for ct in ctypes:
        for ph in get_args(Phase):
            variants = {"upright": [None], "forbidden_contact": list(rs.CONTACT_GROUPS),
                        "keep_clearance": ["obstacle", "other_objects", "region"]}[ct]
            for v in variants:
                yield ct, ph, v


def test_every_contract_literal_has_a_row():
    for ct, ph, v in all_constraint_forms():
        rows = rs.constraint_rows(Constraint(id="c", type=ct, applies_to=ObjectSelector(kind="cup"), phase=ph,
                                             params=params_for(ct, v), provenance=PROV))
        if ct == "keep_clearance" and v in ("other_objects", "region"):
            assert rs.lookup("constraint", ct, None, v) is not None
            continue
        assert all(r is not None for r in rows), (ct, ph, v)
    for be in get_args(OrderingEvent):
        for ae in get_args(OrderingEvent):
            assert rs.lookup("ordering", "ordering", None, f"{be}/{ae}") is not None
    for ph in get_args(Phase):
        assert rs.lookup("phase", ph) is not None
    goal_types = get_args(Goal.model_fields["type"].annotation)
    assert all(rs.lookup("goal", g) is not None for g in goal_types)


def test_contact_groups_agree_everywhere():
    assert tuple(specops.CONTACT_GROUPS) == rs.CONTACT_GROUPS
    assert set(get_args(ContactGroup)) == set(rs.CONTACT_GROUPS)


@pytest.mark.parametrize("ct,ph,v", list(all_constraint_forms()))
def test_validator_accepts_exactly_the_supported_forms(ct, ph, v):
    c = Constraint(id="c_x", type=ct, applies_to=ObjectSelector(kind="cup"), phase=ph, params=params_for(ct, v),
                   status=Status.confirmed, provenance=PROV)
    errs = specops.validate_spec(base_spec([c]))
    supported = rs.unsupported_reason(c) is None
    assert supported == (not any("unsupported rule" in e for e in errs)), errs
    # a rejected rule is only a record: always accepted
    rejected = c.model_copy(update={"status": Status.rejected})
    assert not any("unsupported rule" in e for e in specops.validate_spec(base_spec([rejected])))


@pytest.mark.parametrize("be", get_args(OrderingEvent))
@pytest.mark.parametrize("ae", get_args(OrderingEvent))
def test_validator_and_matrix_agree_on_orderings(be, ae):
    o = OrderingConstraint(id="o", before=ObjectSelector(kind="marker"), after=ObjectSelector(kind="cup"),
                           status=Status.confirmed, provenance=PROV, before_event=be, after_event=ae)
    errs = specops.validate_spec(base_spec(ordering=[o]))
    assert (rs.unsupported_reason(o) is None) == (not any("unsupported rule" in e for e in errs))


def _corpus():
    texts = [c["text"] for f in ("cases.json", "cases_holdout.json")
             for c in json.loads((REPO / "mirror/interpret/benchmark" / f).read_text())]
    from .paraphrases import DEV_PARAPHRASES, HELDOUT_PARAPHRASES
    texts += [t for t, _ in DEV_PARAPHRASES] + [t for t, _ in HELDOUT_PARAPHRASES]
    return texts


def test_compiler_never_emits_an_unsupported_rule():
    from mirror.interpret.drafts import correction_to_ops
    spec = base_spec()
    emitted = 0
    for text in _corpus():
        try:
            draft = compile_offline(spec, text)
            ops = correction_to_ops(draft, spec, text)
            new, _ = specops.apply_ops(spec, ops, text=text, kind="correction")
        except (NeedsClarification, CorrectionRejected):
            continue
        for c in new.constraints:
            if c.status != Status.rejected:
                assert rs.unsupported_reason(c, for_compiler=True) is None, (text, c)
                emitted += 1
        for o in new.ordering:
            if o.status != Status.rejected:
                assert rs.unsupported_reason(o, for_compiler=True) is None, (text, o)
                emitted += 1
    assert emitted > 30


def test_unsatisfiable_table_contact_becomes_a_question():
    with pytest.raises(NeedsClarification) as e:
        from mirror.interpret.drafts import correction_to_ops
        text = "Do not let the cup touch the table while you carry it."
        correction_to_ops(compile_offline(base_spec(), text), base_spec(), text)
    assert "table" in e.value.question.lower()


SUPPORTED = [r for r in rs.ROWS if r.category == "constraint" and r.supported]


@pytest.mark.parametrize("row", SUPPORTED, ids=[r.key for r in SUPPORTED])
def test_planner_and_checker_handle_every_supported_constraint(row):
    """The planner plans the reference layout under the rule and the checker
    returns a decided verdict for it on a dense log (no 'unknown')."""
    from mirror.plan import plan
    from mirror.scenes import reference_scene
    from tests.evaluate.synth import make_scene, pack_all
    from mirror.evaluate import check
    c = Constraint(id="c_x", type=row.op, applies_to=ObjectSelector(kind="block"), phase=row.phase,
                   params=params_for(row.op, row.variant), status=Status.confirmed, provenance=PROV)
    spec = base_spec([c])
    pr = plan(spec, reference_scene("demo"))
    assert pr.status == "ok", pr.reason
    scene = make_scene()
    rep = check(pack_all(scene).build(), spec, scene)
    r = [x for x in rep.rule_results if x.rule_id == "c_x"][0]
    assert r.verdict in ("satisfied", "violated") and r.enforced


def test_docs_table_is_in_sync():
    doc = (REPO / "docs" / "RULES.md").read_text()
    assert rs.markdown() in doc, "regenerate the table in docs/RULES.md with python -m mirror.rules_support"


def test_matrix_json_is_serialisable():
    m = rs.matrix()
    json.dumps(m)
    assert {r["key"] for r in m["rows"]} == set(rs.BY_KEY)
