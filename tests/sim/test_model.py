import math

import mujoco
import numpy as np
import pytest

from mirror.geometry import GRIPPER, WORLD
from mirror.scenes import reference_scene, sample_layout
from mirror.sim import SimEnv, build_model
from mirror.sim.model import wall_segments
from mirror.sim.rotations import wrist_quat


@pytest.mark.parametrize("name", ["demo", "shortcut", "blocked"])
def test_reference_scenes_compile_with_labels(name):
    scene = reference_scene(name)
    model, idx = build_model(scene)
    labels = set(idx.geom_label)
    assert {"table", "gripper", "bead", "tray:tray_0", "obstacle:wall_0"} <= labels
    assert {"cup_0", "block_0", "marker_0"} <= labels
    assert len(idx.bead_bodies["cup_0"]) == 8
    assert model.nu == 8


def test_sampled_layouts_compile():
    for seed in range(4):
        build_model(sample_layout(seed, "dev"))


def test_slot_is_an_opening_and_the_rest_of_the_wall_is_solid():
    wall = reference_scene("shortcut").obstacles[0]
    slot = wall.slots[0]
    segs = wall_segments(wall)

    def covered(y, z):
        return any(abs(y - c[1]) <= h[1] + 1e-9 and abs(z - c[2]) <= h[2] + 1e-9 for c, h in segs)

    ymid, zmid = 0.5 * (slot.y_min + slot.y_max), 0.5 * (slot.z_min + slot.z_max)
    assert not covered(ymid, zmid)
    assert covered(ymid, slot.z_min - 0.01) and covered(ymid, slot.z_max + 0.01)
    assert covered(slot.y_min - 0.01, zmid) and covered(slot.y_max + 0.01, zmid)
    assert slot.z_max - slot.z_min == pytest.approx(WORLD.slot_z[1] - WORLD.slot_z[0])


def test_joint_limits_match_workspace():
    scene = reference_scene("demo")
    model, idx = build_model(scene)
    for j, rng in zip(("gripper_x", "gripper_y", "gripper_z"),
                      (WORLD.workspace_x, WORLD.workspace_y, WORLD.workspace_z)):
        jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, j)
        assert tuple(model.jnt_range[jid]) == pytest.approx(rng)
    jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "gripper_finger_l")
    assert model.jnt_range[jid][1] * 2 == pytest.approx(GRIPPER.max_opening)


def test_wrist_joint_chain_matches_orientation_mapping():
    model, idx = build_model(reference_scene("demo"))
    data = mujoco.MjData(model)
    rng = np.random.default_rng(1)
    for _ in range(20):
        y, r, p = rng.uniform(-2, 2), rng.uniform(-1.2, 1.2), rng.uniform(-2.2, 2.2)
        for a, v in zip(idx.robot_qposadr[3:], (y, r, p)):
            data.qpos[a] = v
        mujoco.mj_kinematics(model, data)
        q = data.xquat[idx.gripper_body]
        assert abs(float(np.dot(q, wrist_quat(y, r, p)))) == pytest.approx(1.0, abs=1e-9)


def test_reset_restores_scene_poses_and_settles():
    scene = reference_scene("demo")
    env = SimEnv(scene)
    for o in scene.objects:
        assert np.allclose(env.object_pose(o.id).pos, o.pose.pos, atol=2e-3)
    assert np.allclose(env.gripper_pose().pos, scene.gripper_start.pos, atol=2e-3)
    assert env.jaw_width() == pytest.approx(GRIPPER.max_opening, abs=1e-3)


def test_visual_scene_covers_every_body():
    env = SimEnv(reference_scene("shortcut"))
    vs = env.visual_scene()
    names = {b.name for b in vs.bodies}
    assert {"world", "tray_0", "wall_0", "gripper", "gripper_finger_l", "cup_0", "block_0",
            "marker_0", "cup_0/bead0"} <= names
    static = {b.name for b in vs.bodies if b.static}
    assert {"world", "tray_0", "wall_0"} <= static and "cup_0" not in static
    wall = next(b for b in vs.bodies if b.name == "wall_0")
    # Static geoms are given in world coordinates.
    assert all(abs(g.pos[0]) < 0.02 for g in wall.geoms)
