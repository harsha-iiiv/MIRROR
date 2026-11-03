"""Planner + simulator + checker agree on ordering (simulation in the loop)."""
import pytest

from mirror.contracts import ObjectSelector, OrderingConstraint, Provenance, Status
from mirror.evaluate import check
from mirror.evaluate.specs import reference_specs
from mirror.evaluate.trial import execute_trial
from mirror.scenes import reference_scene


@pytest.mark.slow
def test_confirmed_order_is_planned_executed_and_verified():
    scene = reference_scene("demo")
    orig, _ = reference_specs()
    old_ro, old_log, old_plan = execute_trial(scene, orig, condition="original")
    assert old_plan.object_order[0] == "cup_0" and old_ro.report.status == "success"
    new = orig.model_copy(deep=True)
    new.version, new.parent_version = 2, 1
    new.ordering = [OrderingConstraint(id="o_marker_before_cup", before=ObjectSelector(kind="marker"),
                                       after=ObjectSelector(kind="cup"), status=Status.confirmed,
                                       provenance=Provenance(source="user_correction", version_introduced=2))]
    # the old behaviour is rejected by the checker under the new rule
    assert check(old_log, new, scene).status == "constraint_violated"
    ro, log, plan = execute_trial(scene, new, condition="ordered")
    assert plan.object_order.index("marker_0") < plan.object_order.index("cup_0")
    assert ro.report.status == "success", ro.report.notes
    r = [x for x in ro.report.rule_results if x.rule_id == "o_marker_before_cup"][0]
    assert r.verdict == "satisfied" and r.enforced
