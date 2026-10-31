"""Intermediate tilt modes, least-tilt selection and chosen-route flags."""
import pytest

from mirror.contracts import ObstacleSpec, Pose, SceneState, SlotSpec
from mirror.geometry import WORLD
from mirror.plan import plan
from mirror.scenes.objects import make_cup, make_tray
from mirror.scenes.reference import default_gripper_start, default_workspace

from .fixtures import base_spec, corrected_spec
from .helpers import cached_plan, chosen_routes, max_transport_tilt, replay_collisions


def tall_slot_scene() -> SceneState:
    """The shortcut layout with a taller slot (0.07 to 0.25 m): the cup fits on
    its side or pitched 45-60 deg, but not upright or at 30 deg."""
    far = WORLD.workspace_y[1] + 0.10
    yc = 0.5 * (-far + 0.14)
    wall = ObstacleSpec(id="wall_0", kind="wall", pose=Pose(pos=(0.0, yc, 0.0)), length=0.14 + far,
                        height=WORLD.wall_height, thickness=WORLD.wall_thickness,
                        slots=[SlotSpec(y_min=-0.28 - yc, y_max=-0.08 - yc, z_min=0.07, z_max=0.25)])
    return SceneState(scene_id="tall_slot", split="custom", objects=[make_cup("cup_0", -0.26, -0.18)],
                      trays=[make_tray("tray_0", 0.25, -0.18)], obstacles=[wall],
                      workspace=default_workspace(), gripper_start=default_gripper_start())


def cup_spec(max_tilt=None):
    spec = base_spec(None) if max_tilt is None else corrected_spec()
    if max_tilt is not None:
        spec.constraints[1].params["max_tilt_deg"] = float(max_tilt)
    spec.bindings = [b for b in spec.bindings if b.kind == "cup"]
    spec.goals = [g for g in spec.goals if g.id == "g_cup"]
    return spec


def test_tilt_limit_gives_least_tilted_feasible_route():
    sc = tall_slot_scene()
    r = plan(cup_spec(45), sc)
    assert r.status == "ok"
    assert chosen_routes(r)["cup_0"] == "through_slot:wall_0:0:pitched+45"
    assert 44.0 < max_transport_tilt(r, "cup_0") <= 45.0 + 1e-6
    rs = {ro.label: ro for ro in r.routes if ro.object_id == "cup_0" and "through_slot" in ro.label}
    assert "exceeds slot opening" in rs["through_slot:wall_0:0:pitched+30"].rejected_reason
    assert "c_cup_upright_transport" in rs["through_slot:wall_0:0:pitched+60"].rejected_reason
    assert replay_collisions(r, sc) == []
    # the steeper modes are feasible without the rule, and cost more rotation
    r0 = plan(cup_spec(None), sc)
    c = {ro.label: ro.cost for ro in r0.routes if ro.object_id == "cup_0" and ro.feasible}
    assert c["through_slot:wall_0:0:pitched+45"] < c["through_slot:wall_0:0:pitched+60"] \
        < c["through_slot:wall_0:0:pitched+90"]
    assert plan(cup_spec(15), sc).routes and chosen_routes(plan(cup_spec(15), sc))["cup_0"] == "around:wall_0:+y"


def test_reference_slot_only_admits_the_cup_on_its_side():
    r = cached_plan("shortcut", "orig")
    rs = {ro.label: ro for ro in r.routes if ro.object_id == "cup_0" and "through_slot" in ro.label}
    for m in ("upright", "pitched+30", "pitched+45", "pitched+60"):
        assert not rs[f"through_slot:wall_0:0:{m}"].feasible
        assert "exceeds slot opening" in rs[f"through_slot:wall_0:0:{m}"].rejected_reason
    assert rs["through_slot:wall_0:0:pitched+90"].feasible


@pytest.mark.parametrize("scene,spec", [("shortcut", "orig"), ("shortcut", "corr"), ("demo", "orig")])
def test_exactly_one_chosen_route_per_object_and_motion(scene, spec):
    r = cached_plan(scene, spec)
    notes = chosen_routes(r)
    for oid in ("cup_0", "block_0", "marker_0"):
        tr = [ro for ro in r.routes if ro.object_id == oid and ro.chosen and not ro.label.startswith("transit:")]
        tt = [ro for ro in r.routes if ro.object_id == oid and ro.chosen and ro.label.startswith("transit:")]
        assert len(tr) == 1 and len(tt) == 1
        assert tr[0].feasible and tr[0].label == notes[oid]
    moves = [s.note.split("=", 1)[1] for s in r.steps if s.kind == "move"]
    assert moves == [ro.label for ro in r.routes if ro.chosen and ro.label.startswith("transit:")]


def test_no_route_is_chosen_without_a_plan():
    r = cached_plan("blocked", "corr")
    assert r.status == "no_plan" and not any(ro.chosen for ro in r.routes)
