"""Fixes from review round 1: role-based specs, selectors cover every matching
object, and the offline rules' worst held-out failure."""
import pytest

from mirror.contracts import Status
from mirror.interpret import LLMClient, NeedsClarification, compile_correction, propose_spec, summarize_demo
from mirror.interpret.benchmark import load_base_spec
from mirror.interpret.drafts import check_proposal
from mirror.interpret.offline import propose_offline
from tests.interpret.fixtures import make_demo


def _role_spec():
    from mirror.evaluate.specs import reference_specs
    return reference_specs()[0]


def test_role_based_goals_give_real_options():
    with pytest.raises(NeedsClarification) as e:
        compile_correction(_role_spec(), "It must stay upright.", LLMClient(mode="offline"))
    assert "the cup" in e.value.question and e.value.options


def test_role_based_rules_are_found_by_kind():
    spec = _role_spec()  # has c_container_upright_final on role "container" (the cup)
    new, rec = compile_correction(spec, "The cup must be within 10 degrees of upright once placed.")
    final = {c.id: c for c in new.constraints}["c_container_upright_final"]
    assert final.params["max_tilt_deg"] == 10.0
    assert not any(c.id == "c_upright_final_cup" for c in new.constraints)  # no duplicate rule


def test_goal_requires_every_object_of_the_kind():
    tr = make_demo()
    s = summarize_demo(tr)
    d = propose_offline(s, tr.scene)
    # pretend the scene had a second cup that was left behind
    extra = s.objects[0].model_copy(update={"object_id": "cup_1", "ended_in_tray": None, "moved": False})
    s.objects.append(extra)
    assert any("not every cup" in e for e in check_proposal(d, s))


def test_forget_about_relaxes():
    new, _ = compile_correction(load_base_spec(), "Forget about keeping the marker upright.")
    st = {c.id: c.status for c in new.constraints}
    assert st["c_upright_transport_marker"] == Status.rejected
    assert all(c.applies_to.kind != "marker" for c in new.enforced_constraints())


def test_bead_lean_is_labelled_as_scene_knowledge():
    spec = propose_spec(make_demo())
    q = {u.related_constraint: u.question for u in spec.uncertainties}["c_upright_transport_cup"]
    assert "scene description" in q and "beads" in q
    assert "scene description" not in {u.related_constraint: u.question
                                        for u in spec.uncertainties}["c_upright_transport_block"]
