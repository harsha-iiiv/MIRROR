import pytest

from mirror.contracts import Status
from mirror.interpret import (CorrectionRejected, NeedsClarification, compile_correction, confirm_items,
                              propose_spec)
from tests.interpret.fixtures import make_demo, make_scene

REF = "This container must remain upright throughout transport, not just at the destination."


@pytest.fixture
def spec():
    """Proposal after the user confirmed the goals and the cup's final-upright rule."""
    s = propose_spec(make_demo())
    s, _ = confirm_items(s, confirm=[g.id for g in s.goals] + ["c_upright_final_cup"])
    return s


def status(spec):
    return {c.id: c.status.value for c in spec.constraints}


def test_reference_correction(spec):
    new, rec = compile_correction(spec, REF)
    c = {c.id: c for c in new.constraints}["c_upright_transport_cup"]
    assert c.status == Status.confirmed and c.phase == "transport"
    assert c.applies_to.kind == "cup" and c.params["max_tilt_deg"] == 15.0
    assert status(new)["c_upright_final_cup"] == "confirmed"  # existing final rule kept
    # scope: no other object's rule changed
    for k in ("block", "marker"):
        assert status(new)[f"c_upright_transport_{k}"] == "proposed"
    assert new.version == spec.version + 1
    assert rec.kind == "correction" and rec.text == REF and rec.model == "offline-rules"
    u = {u.id: u for u in new.uncertainties}["u_upright_transport_cup"]
    assert u.status == "resolved"


def test_reference_correction_adds_rule_when_absent(spec):
    spec.constraints = [c for c in spec.constraints if c.id != "c_upright_transport_cup"]
    spec.uncertainties = [u for u in spec.uncertainties if u.related_constraint != "c_upright_transport_cup"]
    new, rec = compile_correction(spec, REF)
    assert rec.diff[0].op == "add_constraint"
    c = {c.id: c for c in new.constraints}["c_upright_transport_cup"]
    assert c.status == Status.confirmed and c.provenance.source == "user_correction"
    assert c.provenance.version_introduced == new.version


def test_marker_can_tilt_only_cup_matters(spec):
    new, _ = compile_correction(spec, "The marker can tilt, only the cup matters.")
    st = status(new)
    assert st["c_upright_transport_cup"] == "confirmed"
    assert st["c_upright_transport_marker"] == "rejected"
    assert st["c_upright_final_marker"] == "rejected"
    assert st["c_upright_transport_block"] == "rejected"  # "only the cup matters"
    assert st["c_upright_final_cup"] == "confirmed"


def test_negation_relaxes(spec):
    new, _ = compile_correction(spec, "The cup doesn't need to stay upright while it is carried.")
    st = status(new)
    assert st["c_upright_transport_cup"] == "rejected"
    assert st["c_upright_final_cup"] == "confirmed"


def test_must_not_tilt_is_a_requirement(spec):
    new, _ = compile_correction(spec, "The cup must not tilt while you carry it.")
    assert status(new)["c_upright_transport_cup"] == "confirmed"


def test_never_spill(spec):
    new, _ = compile_correction(spec, "Never spill the cup on the way.")
    assert status(new)["c_upright_transport_cup"] == "confirmed"


def test_only_at_destination(spec):
    new, _ = compile_correction(spec, "The cup only needs to be upright at the destination.")
    st = status(new)
    assert st["c_upright_transport_cup"] == "rejected"
    assert st["c_upright_final_cup"] == "confirmed"


def test_ambiguous_pronoun_asks(spec):
    with pytest.raises(NeedsClarification) as e:
        compile_correction(spec, "It must stay upright the whole time.")
    assert "cup" in e.value.question and "marker" in e.value.question
    assert e.value.options


def test_pronoun_resolved_by_focus(spec):
    new, _ = compile_correction(spec, "It must stay upright the whole time.", context={"focus_kind": "marker"})
    st = status(new)
    assert st["c_upright_transport_marker"] == "confirmed"
    assert st["c_upright_transport_cup"] == "proposed"


def test_pronoun_resolved_within_text(spec):
    new, _ = compile_correction(spec, "Look at the cup. It must stay upright while carried.")
    assert status(new)["c_upright_transport_cup"] == "confirmed"


def test_unknown_object_asks(spec):
    with pytest.raises(NeedsClarification) as e:
        compile_correction(spec, "The bowl must stay upright.")
    assert "bowl" in e.value.question


def test_object_not_in_scene_asks():
    s = propose_spec(make_demo())
    s.bindings = [b for b in s.bindings if b.kind != "marker"]
    s.goals = [g for g in s.goals if g.object.kind != "marker"]
    s.constraints = [c for c in s.constraints if c.applies_to.kind != "marker"]
    s.ordering = [o for o in s.ordering if "marker" not in o.id]
    s.uncertainties = [u for u in s.uncertainties if "marker" not in u.id]
    with pytest.raises(NeedsClarification) as e:
        compile_correction(s, "The marker must stay upright.", context={"scene": make_scene(with_marker=False)})
    assert "marker" in e.value.question


def test_contradiction_asks(spec):
    with pytest.raises(NeedsClarification) as e:
        compile_correction(spec, "The cup must stay upright while carried but it can tilt.")
    assert e.value.reason == "contradictory correction"


def test_unsupported_request_rejected(spec):
    with pytest.raises(CorrectionRejected):
        compile_correction(spec, "Go faster please.")


def test_explicit_tolerance(spec):
    new, rec = compile_correction(spec, "Keep the cup within 10 degrees of upright while carrying it.")
    c = {c.id: c for c in new.constraints}["c_upright_transport_cup"]
    assert c.params["max_tilt_deg"] == 10.0 and c.status == Status.confirmed
    assert [o.op for o in rec.diff][:2] == ["modify_constraint", "set_status"]


def test_universal_scope_only_when_said(spec):
    new, _ = compile_correction(spec, "Every object must stay upright while carried.")
    c = {c.id: c for c in new.constraints}
    assert c["c_upright_transport_all"].applies_to.kind is None
    assert c["c_upright_transport_all"].status == Status.confirmed


def test_order_corrections(spec):
    new, _ = compile_correction(spec, "The block must go in before the cup.")
    st = {o.id: o.status.value for o in new.ordering}
    assert st["o_block_before_cup"] == "confirmed" and st["o_cup_before_block"] == "rejected"
    new2, _ = compile_correction(spec, "The order doesn't matter.")
    assert all(o.status == Status.rejected for o in new2.ordering)
    new3, _ = compile_correction(spec, "Put the marker in first.")
    st3 = {o.id: o.status.value for o in new3.ordering}
    assert st3["o_marker_before_cup"] == "confirmed" and st3["o_marker_before_block"] == "confirmed"
    assert st3["o_block_before_marker"] == "rejected"


def test_contact_rule(spec):
    new, _ = compile_correction(spec, "The cup must not touch the wall.")
    c = {c.id: c for c in new.constraints}["c_forbidden_contact_transport_cup"]
    assert c.params["with"] == ["obstacle"] and c.status == Status.confirmed


def test_goal_removal(spec):
    new, _ = compile_correction(spec, "Leave the marker out, it doesn't need to be packed.")
    g = {g.id: g for g in new.goals}
    assert g["g_inside_marker"].status == Status.rejected
    assert g["g_inside_cup"].status == Status.confirmed


def test_restating_existing_rule_is_rejected(spec):
    new, _ = compile_correction(spec, REF)
    with pytest.raises(CorrectionRejected):
        compile_correction(new, REF)


def test_empty_text_rejected(spec):
    with pytest.raises(CorrectionRejected):
        compile_correction(spec, "   ")
