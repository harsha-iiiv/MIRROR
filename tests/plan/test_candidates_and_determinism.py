from mirror.contracts import ObjectSelector, OrderingConstraint, Provenance, Status
from mirror.plan import candidate_plans, plan
from mirror.scenes import reference_scene

from .fixtures import corrected_spec, original_spec, proposed_spec
from .helpers import chosen_routes, max_transport_tilt


def test_candidates_expose_the_proposed_rule():
    plans = candidate_plans(proposed_spec(), reference_scene("shortcut"), k=2)
    assert len(plans) == 2 and all(p.status == "ok" for p in plans)
    a, b = plans
    assert max_transport_tilt(a, "cup_0") > 80.0
    assert max_transport_tilt(b, "cup_0") <= 15.0
    assert chosen_routes(b)["cup_0"] == "around:wall_0:+y"
    # only the cup's behaviour differs
    ra, rb = chosen_routes(a), chosen_routes(b)
    assert {k for k in ra if ra[k] != rb[k]} == {"cup_0"}


def test_candidates_are_valid_under_the_spec():
    plans = candidate_plans(corrected_spec(), reference_scene("shortcut"), k=3)
    assert plans and all(p.status == "ok" for p in plans)
    for p in plans:
        assert max_transport_tilt(p, "cup_0") <= 15.0
    labels = [tuple(sorted(chosen_routes(p).items())) for p in plans]
    assert len(set(labels)) == len(labels)


def test_identical_inputs_give_identical_plans():
    from .helpers import cached_plan
    a = cached_plan("shortcut", "orig").model_dump()
    b = plan(original_spec(), reference_scene("shortcut")).model_dump()
    a.pop("planning_time_s"); b.pop("planning_time_s")
    assert a == b


def _ordering(status):
    return OrderingConstraint(id="o_marker_first", before=ObjectSelector(kind="marker"),
                              after=ObjectSelector(kind="cup"), status=status,
                              provenance=Provenance(source="manual"))


def test_confirmed_ordering_is_respected_and_proposed_is_not():
    sc = reference_scene("demo")
    spec = original_spec()
    spec.ordering = [_ordering(Status.confirmed)]
    r = plan(spec, sc)
    assert r.status == "ok"
    assert r.object_order.index("marker_0") < r.object_order.index("cup_0")
    spec.ordering = [_ordering(Status.proposed)]
    assert plan(spec, sc).object_order[0] == "cup_0"


def test_ordering_cycle_is_invalid():
    spec = original_spec()
    rev = OrderingConstraint(id="o_cup_first", before=ObjectSelector(kind="cup"),
                             after=ObjectSelector(kind="marker"), status=Status.confirmed,
                             provenance=Provenance(source="manual"))
    spec.ordering = [_ordering(Status.confirmed), rev]
    r = plan(spec, reference_scene("demo"))
    assert r.status == "invalid_spec" and "cycle" in r.reason


def test_prefer_selects_among_feasible_routes():
    r = plan(original_spec(), reference_scene("shortcut"), prefer="cup_0:around")
    assert chosen_routes(r)["cup_0"] == "around:wall_0:+y"
