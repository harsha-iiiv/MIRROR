from mirror.contracts import Goal, ObjectSelector, Provenance, Status
from mirror.plan import ground, plan
from mirror.scenes import reference_scene

from .fixtures import corrected_spec, original_spec


def test_roles_goals_and_constraints_bind_to_instances():
    g = ground(corrected_spec(), reference_scene("shortcut"))
    assert g.ok
    assert g.roles == {"container": ["cup_0"], "part": ["block_0"], "pen": ["marker_0"]}
    assert g.warnings == []
    assert g.destination == {"destination": "tray_0"}
    assert g.constraint_objects["c_cup_upright_transport"] == ["cup_0"]
    assert g.objects_to_pack == ["block_0", "cup_0", "marker_0"]


def test_role_selector_and_rejected_goal():
    spec = original_spec()
    spec.goals[0].object = ObjectSelector(role="container")
    spec.goals[1].status = Status.rejected
    g = ground(spec, reference_scene("shortcut"))
    assert g.goal_objects["g_cup"] == ["cup_0"]
    assert "g_block" not in g.goal_objects
    assert "block_0" not in g.objects_to_pack


def test_missing_object_is_invalid_spec():
    spec = original_spec()
    spec.goals.append(Goal(id="g_ids", type="inside", object=ObjectSelector(ids=["cup_9"]),
                           provenance=Provenance(source="manual")))
    r = plan(spec, reference_scene("shortcut"))
    assert r.status == "invalid_spec"
    assert "g_ids" in r.reason


def test_no_active_goal_is_invalid_spec_not_an_error():
    from mirror.contracts import Status
    for mutate in ("empty", "rejected"):
        spec = original_spec()
        if mutate == "empty":
            spec.goals = []
        else:
            for g in spec.goals:
                g.status = Status.rejected
        r = plan(spec, reference_scene("shortcut"))
        assert r.status == "invalid_spec" and "no active goal" in r.reason


def test_missing_tray_is_reported_once():
    sc = reference_scene("shortcut").model_copy(deep=True)
    sc.trays = []
    r = plan(original_spec(), sc)
    assert r.status == "invalid_spec" and r.reason == "scene has no tray"


def test_selectors_cover_every_matching_object():
    """Decision D5: a role or selector covers all matching objects; count is descriptive."""
    from mirror.contracts import ObjectSelector
    from mirror.scenes.objects import make_cup
    sc = reference_scene("demo").model_copy(deep=True)
    sc.objects.append(make_cup("cup_1", -0.30, 0.30))
    spec = original_spec()
    spec.goals[0].object = ObjectSelector(role="container")
    g = ground(spec, sc)
    assert g.roles["container"] == ["cup_0", "cup_1"]
    assert g.objects_to_pack == ["block_0", "cup_0", "cup_1", "marker_0"]
    assert any("descriptive" in w for w in g.warnings)
    r = plan(spec, sc)
    assert r.status == "ok", r.reason
    assert sorted(r.object_order) == ["block_0", "cup_0", "cup_1", "marker_0"]
    released = {s.object_id for s in r.steps if s.kind == "release"}
    assert released == {"block_0", "cup_0", "cup_1", "marker_0"}
