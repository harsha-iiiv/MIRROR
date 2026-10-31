"""Detour around box obstacles and the sampling-based fallback."""
import numpy as np

from mirror.contracts import SceneState
from mirror.plan import plan
from mirror.plan.collision import World, check_segment, make_envelope
from mirror.plan.planner import plan_core
from mirror.plan.routes import Query
from mirror.plan.rrt import rrt_candidate
from mirror.plan.world import object_box, scene_world
from mirror.scenes import reference_scene
from mirror.scenes.objects import make_box, make_cup, make_tray
from mirror.scenes.reference import default_gripper_start, default_workspace

from .fixtures import base_spec
from .helpers import chosen_routes, replay_collisions


def _cup_only_spec():
    spec = base_spec(None)
    spec.bindings = [b for b in spec.bindings if b.kind == "cup"]
    spec.goals = [g for g in spec.goals if g.id == "g_cup"]
    return spec


def test_tall_box_forces_a_detour():
    sc = SceneState(scene_id="box_detour", split="custom", objects=[make_cup("cup_0", -0.25, 0.0)],
                    trays=[make_tray("tray_0", 0.25, 0.0)],
                    obstacles=[make_box("box_0", 0.0, 0.0, (0.04, 0.10, 0.30))],
                    workspace=default_workspace(), gripper_start=default_gripper_start())
    r = plan(_cup_only_spec(), sc)
    assert r.status == "ok", r.reason
    assert chosen_routes(r)["cup_0"] == "detour"
    direct = next(ro for ro in r.routes if ro.label == "direct" and ro.object_id == "cup_0")
    assert not direct.feasible and "box_0" in direct.rejected_reason
    assert replay_collisions(r, sc) == []


def _cup_query():
    core = plan_core(base_spec(None), reference_scene("shortcut"))
    op = core.objects[0]
    sc = core.scene
    static = scene_world(sc)
    others = [object_box(o) for o in sc.objects if o.id != op.obj.id]
    env = make_envelope(op.gm.width, op.gm.held(op.obj.id, 0.0))
    return Query(World(static.boxes + others, 0.0), env, op.grasp_tcp, op.grasp_yaw, op.placement.tcp_pos,
                 op.placement.gripper_yaw, op.allowed, op.banned, sc, core.cfg, seed_key="test")


def test_rrt_finds_a_collision_free_deterministic_path():
    q = _cup_query()
    a, b = rrt_candidate(q), rrt_candidate(q)
    assert a.feasible, a.reason
    assert [w.pos.tolist() for w in a.waypoints] == [w.pos.tolist() for w in b.waypoints]
    assert np.allclose(a.waypoints[0].pos, q.start_pos) and np.allclose(a.waypoints[-1].pos, q.goal_pos)
    for u, v in zip(a.waypoints[:-1], a.waypoints[1:]):
        assert check_segment(q.world, q.env, u.pos, u.R, v.pos, v.R, margin=0.0, ground_margin=-0.002,
                             step=0.003) is None
