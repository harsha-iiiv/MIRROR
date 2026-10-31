import pytest

from mirror.contracts import Status
from mirror.interpret import (InterpretError, NeedsClarification, apply_answer, confirm_items,
                              make_clarification, planned_carry_tilts, propose_spec)
from tests.interpret.fixtures import make_candidates, make_demo


@pytest.fixture
def spec():
    s = propose_spec(make_demo())
    s, _ = confirm_items(s, confirm=[g.id for g in s.goals] + ["c_upright_final_cup"])
    return s


def test_planned_tilts():
    scene, (a, b) = make_candidates()
    assert planned_carry_tilts(a, scene)["cup_0"] == pytest.approx(0.0)
    assert planned_carry_tilts(b, scene)["cup_0"] == pytest.approx(90.0, abs=0.1)


def test_clarification_contents(spec):
    scene, cands = make_candidates()
    c = make_clarification(spec, scene, cands)
    assert c.question == "Would both of these still count as doing the task correctly?"
    tilt = [d for d in c.differences if d.feature == "carry_tilt"]
    assert len(tilt) == 1 and tilt[0].object_kind == "cup" and tilt[0].values == [0.0, 90.0]
    assert any(d.feature == "route" for d in c.differences)
    assert [d.routes for d in c.candidates] == [["cup_0: around_+y"], ["cup_0: through_slot"]]
    assert c.spec_version == spec.version
    assert make_clarification(spec, scene, cands) == c  # deterministic


def test_answer_no_confirms_transport_rule(spec):
    scene, cands = make_candidates()
    c = make_clarification(spec, scene, cands)
    new, rec = apply_answer(spec, c, "No")
    st = {x.id: x.status for x in new.constraints}
    assert st["c_upright_transport_cup"] == Status.confirmed
    assert st["c_upright_final_cup"] == Status.confirmed
    assert rec.kind == "clarification_answer" and rec.text.startswith("Q: Would both")
    assert {u.id: u.status for u in new.uncertainties}["u_upright_transport_cup"] == "resolved"


def test_answer_no_with_reason(spec):
    scene, cands = make_candidates()
    c = make_clarification(spec, scene, cands)
    new, rec = apply_answer(spec, c, "No, the cup must stay upright.")
    assert {x.id: x.status for x in new.constraints}["c_upright_transport_cup"] == Status.confirmed
    assert rec.kind == "clarification_answer"
    assert {x.id: x.status for x in new.constraints}["c_upright_transport_marker"] == Status.proposed


def test_answer_yes_rejects_transport_rule(spec):
    scene, cands = make_candidates()
    c = make_clarification(spec, scene, cands)
    new, _ = apply_answer(spec, c, "Yes, both are fine.")
    assert {x.id: x.status for x in new.constraints}["c_upright_transport_cup"] == Status.rejected


def test_unclear_answer_asks_again(spec):
    scene, cands = make_candidates()
    c = make_clarification(spec, scene, cands)
    with pytest.raises(NeedsClarification):
        apply_answer(spec, c, "Hmm, maybe?")


def test_stale_clarification_rejected(spec):
    scene, cands = make_candidates()
    c = make_clarification(spec, scene, cands)
    newer, _ = confirm_items(spec, confirm=["c_upright_final_block"])
    with pytest.raises(InterpretError):
        apply_answer(newer, c, "No")


def test_identical_candidates_cannot_be_compared(spec):
    scene, (a, _) = make_candidates()
    with pytest.raises(InterpretError):
        make_clarification(spec, scene, [a, a.model_copy(deep=True)])


def test_pair_selection_prefers_single_open_difference(spec):
    """With several candidates, pick the pair that differs only in how the cup
    (open question, loose contents) is carried."""
    from mirror.contracts import Pose, PlanStep
    from tests.interpret.fixtures import quat_y
    scene, (up, tilted) = make_candidates()

    def with_block(plan, deg):
        p = plan.model_copy(deep=True)
        p.steps += [PlanStep(kind="grasp", object_id="block_0"),
                    PlanStep(kind="transport", object_id="block_0", note=f"b{deg}",
                             waypoints=[Pose(pos=(0.0, 0.3, 0.2), quat=quat_y(deg))]),
                    PlanStep(kind="release", object_id="block_0")]
        p.object_order = ["cup_0", "block_0"]
        return p

    cands = [with_block(tilted, 90), with_block(tilted, 0), with_block(up, 90)]
    c = make_clarification(spec, scene, cands)
    tilt = [d for d in c.differences if d.feature == "carry_tilt"]
    assert [d.object_kind for d in tilt] == ["cup"]
    focus = make_clarification(spec, scene, cands, focus_kind="block")
    assert [d.object_kind for d in focus.differences if d.feature == "carry_tilt"] == ["block"]
