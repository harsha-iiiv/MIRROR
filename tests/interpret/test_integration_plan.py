"""Interpretation output drives the real planner (slow)."""
import pytest

from mirror.interpret import (apply_answer, compile_correction, confirm_items, make_clarification,
                              planned_carry_tilts, propose_spec)
from tests.interpret import scenarios

plan_mod = pytest.importorskip("mirror.plan")
scenes = pytest.importorskip("mirror.scenes")

pytestmark = pytest.mark.slow
REF = scenarios.CORRECTIONS[0]


@pytest.fixture(scope="module")
def setup():
    demos = scenarios.demos("pinned")
    trace = demos[-1]
    spec = propose_spec(trace)
    spec, _ = confirm_items(spec, confirm=[g.id for g in spec.goals] + ["c_upright_final_cup"])
    return spec, scenes.reference_scene("shortcut")


def test_correction_changes_the_cup_route_only(setup):
    spec, scene = setup
    before = plan_mod.plan(spec, scene)
    assert before.status == "ok"
    tb = planned_carry_tilts(before, scene)
    assert tb["cup_0"] > 45  # the unconstrained plan takes the tilting shortcut
    new, _ = compile_correction(spec, REF, context={"scene": scene})
    after = plan_mod.plan(new, scene)
    assert after.status == "ok"
    ta = planned_carry_tilts(after, scene)
    assert ta["cup_0"] <= 15
    if tb.get("block_0", 0) > 45:  # scope: other objects may still tilt where cheaper
        assert ta["block_0"] > 45


def test_clarification_targets_the_cup(setup):
    spec, scene = setup
    cands = plan_mod.candidate_plans(spec, scene, k=4)
    clar = make_clarification(spec, scene, cands, focus_kind="cup")
    tilt = [d for d in clar.differences if d.feature == "carry_tilt"]
    assert [d.object_kind for d in tilt] == ["cup"]
    new, rec = apply_answer(spec, clar, "No, the cup must stay upright.")
    assert rec.kind == "clarification_answer"
    p = plan_mod.plan(new, scene)
    assert p.status == "ok" and planned_carry_tilts(p, scene)["cup_0"] <= 15


def test_blocked_scene_corrected_spec_has_no_plan(setup):
    spec, _ = setup
    scene = scenes.reference_scene("blocked")
    new, _ = compile_correction(spec, REF, context={"scene": scene})
    assert plan_mod.plan(spec, scene).status == "ok"
    assert plan_mod.plan(new, scene).status == "no_plan"
