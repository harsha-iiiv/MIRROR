from mirror.contracts import Goal, ObjectSelector, Provenance, Status
from mirror.plan import ground, plan
from mirror.scenes import reference_scene

from .fixtures import corrected_spec, original_spec


def test_roles_goals_and_constraints_bind_to_instances():
    g = ground(corrected_spec(), reference_scene("shortcut"))
    assert g.ok
    assert g.roles == {"container": ["cup_0"], "part": ["block_0"], "pen": ["marker_0"]}
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
