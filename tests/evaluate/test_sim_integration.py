"""Checker on real MuJoCo rollouts of hand-written plans (no planner)."""
import pytest

pytestmark = pytest.mark.slow

sim = pytest.importorskip("mirror.sim")
plans = pytest.importorskip("tests.sim.plans")

from mirror.evaluate import check  # noqa: E402
from mirror.evaluate.feasibility import feasibility  # noqa: E402
from mirror.evaluate.specs import reference_specs  # noqa: E402
from mirror.scenes import reference_scene  # noqa: E402


def test_upright_demo_rollout_passes_both_specs():
    a, b = reference_specs()
    scene = reference_scene("demo")
    res = sim.SimEnv(scene).execute(plans.demo_pack_plan(scene))
    assert res.status == "completed", res.reason
    for spec in (a, b):
        r = check(res.log, spec, scene, execution_status=res.status)
        assert r.status == "success", r.notes
        cup = next(o for o in r.outcomes if o.object_id == "cup_0")
        assert cup.beads_in_container == cup.beads_total


def test_pitched_slot_rollout_violates_only_the_corrected_rule():
    a, b = reference_specs()
    scene = reference_scene("shortcut")
    res = sim.SimEnv(scene).execute(plans.slot_plan(scene, tilt_deg=90))
    assert res.status == "completed", res.reason
    ra = check(res.log, a, scene, execution_status=res.status)
    rb = check(res.log, b, scene, execution_status=res.status)
    assert not ra.violations
    assert [v.constraint_id for v in rb.violations] == ["c_container_upright_transport"]
    assert rb.violations[0].peak > 80
    cup = next(o for o in rb.outcomes if o.object_id == "cup_0")
    assert cup.in_target and cup.beads_in_container < cup.beads_total   # beads physically spilled


def test_reference_scene_feasibility_labels():
    a, b = reference_specs()
    assert feasibility(b, reference_scene("blocked"))["verdict"] == "proven_infeasible"
    assert feasibility(b, reference_scene("shortcut"))["verdict"] == "feasible_witness"
    assert feasibility(a, reference_scene("blocked"))["verdict"] != "proven_infeasible"
