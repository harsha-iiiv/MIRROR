"""Execute plans in MuJoCo (slow)."""
import pytest

from mirror.scenes import reference_scene
from mirror.statelog import tilt_deg

from .helpers import cached_plan

sim = pytest.importorskip("mirror.sim")


def _run(scene_name, spec_name):
    sc = reference_scene(scene_name)
    r = cached_plan(scene_name, spec_name)
    env = sim.SimEnv(sc, seed=0)
    return sc, r, env.execute(r)


def _in_tray(sc, p):
    t = sc.trays[0]
    return abs(p[0] - t.pose.pos[0]) < t.inner_half[0] and abs(p[1] - t.pose.pos[1]) < t.inner_half[1]


@pytest.mark.slow
@pytest.mark.parametrize("scene_name,spec_name,cup_tilted", [("shortcut", "orig", True),
                                                             ("shortcut", "corr", False),
                                                             ("blocked", "orig", True)])
def test_plan_executes_without_wall_contact(scene_name, spec_name, cup_tilted):
    sc, r, res = _run(scene_name, spec_name)
    assert res.status == "completed", res.reason
    log = res.log
    bad = [c for c in log.contacts if c.other.startswith("obstacle:")]
    assert bad == []
    for i, oid in enumerate(log.object_ids):
        assert _in_tray(sc, log.obj_pos[-1, i]), oid
        assert float(tilt_deg(log.obj_quat[-1, i])) < 10.0, oid
    ci = log.index("cup_0")
    peak = float(tilt_deg(log.obj_quat[:, ci]).max())
    assert (peak > 60.0) if cup_tilted else (peak < 15.0)


@pytest.mark.slow
def test_cup_pitched_45_passes_a_taller_slot_without_wall_contact():
    from mirror.plan import plan
    from .test_modes_and_chosen import cup_spec, tall_slot_scene
    sc = tall_slot_scene()
    r = plan(cup_spec(45), sc)
    res = sim.SimEnv(sc, seed=0).execute(r)
    assert res.status == "completed", res.reason
    log = res.log
    assert [c for c in log.contacts if c.other.startswith("obstacle:")] == []
    peak = float(tilt_deg(log.obj_quat[:, log.index("cup_0")]).max())
    assert 40.0 < peak < 50.0
    assert _in_tray(sc, log.obj_pos[-1, log.index("cup_0")])
