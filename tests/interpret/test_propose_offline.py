from mirror.contracts import Status
from mirror.interpret import OFFLINE_LABEL, propose_spec, propose_spec_detailed, validate_spec
from tests.interpret.fixtures import make_demo


def test_offline_proposal_structure():
    spec = propose_spec(make_demo())
    assert spec.interpreter == OFFLINE_LABEL
    assert spec.version == 1 and spec.demo_id == "fixture_demo"
    assert {g.object.kind for g in spec.goals} == {"cup", "block", "marker"}
    assert all(g.status == Status.proposed for g in spec.goals)
    assert all(c.status == Status.proposed for c in spec.constraints)
    assert spec.enforced_constraints() == []
    ids = {c.id for c in spec.constraints}
    assert {"c_upright_transport_cup", "c_upright_final_cup"} <= ids
    assert {o.id for o in spec.ordering} == {"o_cup_before_block", "o_block_before_marker"}
    related = {u.related_constraint for u in spec.uncertainties}
    assert ids | {o.id for o in spec.ordering} == related
    for c in spec.constraints:
        assert c.applies_to.kind is not None  # scoped by kind, transfers across layouts
        assert c.applies_to.ids is None
        assert c.provenance.source == "demo_inference"
    assert validate_spec(spec) == []


def test_cup_with_beads_leans_required():
    spec = propose_spec(make_demo())
    u = {x.id: x for x in spec.uncertainties}
    assert u["u_upright_transport_cup"].options[0].startswith("Required")
    assert u["u_upright_transport_block"].options[0] == "Required"


def test_tilted_object_gets_no_transport_rule():
    spec = propose_spec(make_demo(tilt_kind="marker"))
    ids = {c.id for c in spec.constraints}
    assert "c_upright_transport_marker" not in ids
    assert "c_upright_final_marker" in ids


def test_object_left_out_is_bound_but_not_a_goal():
    spec = propose_spec(make_demo(leave_out=("marker_0",)))
    assert {g.object.kind for g in spec.goals} == {"cup", "block"}
    b = {b.kind: b for b in spec.bindings}
    assert b["marker"].required is False
    assert all(c.applies_to.kind != "marker" for c in spec.constraints)


def test_order_follows_demo():
    spec = propose_spec(make_demo(order=("marker_0", "cup_0", "block_0")))
    assert {o.id for o in spec.ordering} == {"o_marker_before_cup", "o_cup_before_block"}


def test_detailed_separates_observation_from_assumption():
    it = propose_spec_detailed(make_demo())
    assert it.draft.observations and it.draft.assumptions
    dismissed = {d.regularity_id for d in it.draft.dismissed}
    route_ids = {r.id for r in it.summary.regularities if r.kind == "route"}
    assert route_ids <= dismissed
