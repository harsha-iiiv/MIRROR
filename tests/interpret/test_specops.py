import pytest

from mirror.contracts import SpecOp, Status
from mirror.interpret import InterpretError, apply_ops, confirm_items, propose_spec, validate_spec
from tests.interpret.fixtures import make_demo


@pytest.fixture
def spec():
    return propose_spec(make_demo())


def test_offline_proposal_is_valid(spec):
    assert validate_spec(spec) == []


def test_apply_ops_bumps_version_and_records_history(spec):
    ops = [SpecOp(op="set_status", target_id="c_upright_final_cup", value={"status": "confirmed"})]
    new, rec = apply_ops(spec, ops, text="confirm", kind="confirmation")
    assert new.version == spec.version + 1 and new.parent_version == spec.version
    assert new.history[-1] == rec and rec.version == new.version and rec.diff == ops
    assert spec.version == 1 and not spec.history  # input untouched
    assert {c.id for c in new.enforced_constraints()} == {"c_upright_final_cup"}


def test_apply_ops_rejects_unknown_target(spec):
    with pytest.raises(InterpretError):
        apply_ops(spec, [SpecOp(op="set_status", target_id="nope", value={"status": "confirmed"})],
                  text="x", kind="correction")


def test_duplicate_confirmed_rules_invalid(spec):
    dup = spec.constraints[0].model_copy(deep=True)
    dup.id = "c_dup"
    dup.status = Status.confirmed
    spec.constraints[0].status = Status.confirmed
    spec.constraints.append(dup)
    assert any("both confirmed" in e for e in validate_spec(spec))


def test_validate_catches_bad_params_and_selectors(spec):
    spec.constraints[0].params = {"max_tilt_deg": -3}
    spec.constraints[1].applies_to.kind = None
    spec.constraints[1].applies_to.role = "ghost"
    errs = validate_spec(spec)
    assert any("max_tilt_deg" in e for e in errs)
    assert any("ghost" in e for e in errs)


def test_ordering_cycle_detected(spec):
    new, _ = confirm_items(spec, confirm=["o_cup_before_block", "o_block_before_marker"])
    o = new.ordering[0].model_copy(deep=True)
    o.id, o.before, o.after = "o_marker_before_cup", new.ordering[1].after, new.ordering[0].before
    o.status = Status.confirmed
    new.ordering.append(o)
    assert any("cycle" in e for e in validate_spec(new))


def test_confirm_items_resolves_questions(spec):
    new, rec = confirm_items(spec, confirm=["c_upright_final_cup"], reject=["c_upright_transport_marker"])
    assert rec.kind == "confirmation"
    u = {u.id: u for u in new.uncertainties}
    assert u["u_upright_final_cup"].status == "resolved"
    assert u["u_upright_transport_marker"].status == "resolved"
    assert u["u_upright_transport_cup"].status == "open"
    with pytest.raises(InterpretError):
        confirm_items(spec, confirm=["c_upright_final_cup"], reject=["c_upright_final_cup"])
