import numpy as np
import pytest

from mirror.geometry import GRIPPER
from mirror.scenes import reference_scene
from mirror.sim import REFERENCE_DEMO_PATH, Teleop, load_demo, record_scripted_demo
from mirror.statelog import tilt_deg


def test_teleop_moves_with_bounded_speed_and_detects_grasp():
    scene = reference_scene("demo")
    tele = Teleop(scene)
    block = next(o for o in scene.objects if o.id == "block_0")
    x, y, z = block.pose.pos
    start = np.array(tele.env.gripper_pose().pos)
    tele.set_target((x, y, 0.30))
    tele.advance(0.5)
    moved = np.linalg.norm(np.array(tele.env.gripper_pose().pos) - start)
    assert moved <= 0.30 * 0.5 + 0.01
    tele.advance(2.0)
    tele.set_target((x, y, z))
    tele.advance(2.0)
    tele.command((0, 0, 0), (0, 0, 0), grip=0.0)
    tele.advance(1.0)
    assert tele.held_object == "block_0"
    tele.command((0, 0, 0.1), (0, 0, 0))
    tele.advance(1.0)
    assert tele.env.object_pose("block_0").pos[2] > 0.08
    tele.command((0, 0, 0), (0, 0, 0), grip=GRIPPER.max_opening)
    tele.advance(1.5)
    trace = tele.finish()
    assert [e.type for e in trace.events] == ["grasp", "release"]
    assert trace.events[0].object_id == "block_0"
    assert trace.source == "teleop_keyboard"
    assert len(trace.frames) > 100


def test_rotation_commands_respect_wrist_limits():
    tele = Teleop(reference_scene("demo"))
    tele.command((0, 0, 0), (1.5, 0.0, 0.0))     # beyond the 80 degree roll limit: ignored
    assert np.allclose(tele._target_quat, (1, 0, 0, 0))
    tele.command((0, 0, 0), (0.0, 1.0, 0.0))     # pitch within limits
    tele.advance(1.5)
    assert float(tilt_deg(np.array(tele.env.gripper_pose().quat))) == pytest.approx(57.3, abs=1.5)


def test_reference_demo_file():
    trace = load_demo(REFERENCE_DEMO_PATH)
    assert trace.source == "teleop_scripted"
    assert trace.scene.scene_id == "ref_demo"
    assert [(e.type, e.object_id) for e in trace.events] == [
        ("grasp", "cup_0"), ("release", "cup_0"), ("grasp", "block_0"),
        ("release", "block_0"), ("grasp", "marker_0"), ("release", "marker_0")]
    last = trace.frames[-1]
    tray = trace.scene.trays[0]
    for oid, pose in last.objects.items():
        dx, dy = pose.pos[0] - tray.pose.pos[0], pose.pos[1] - tray.pose.pos[1]
        assert abs(dx) < tray.inner_half[0] and abs(dy) < tray.inner_half[1], oid
    cup_tilt = max(float(tilt_deg(np.array(f.objects["cup_0"].quat))) for f in trace.frames)
    assert cup_tilt < 5.0
    # The cup went around the wall end, not through the wall.
    ys = [f.objects["cup_0"].pos[1] for f in trace.frames
          if abs(f.objects["cup_0"].pos[0]) < 0.02]
    wall = trace.scene.obstacles[0]
    assert ys and min(ys) > wall.pose.pos[1] + wall.length / 2


@pytest.mark.slow
def test_scripted_demo_is_reproducible():
    trace = record_scripted_demo(reference_scene("demo"))
    ref = load_demo(REFERENCE_DEMO_PATH)
    assert [(e.type, e.object_id) for e in trace.events] == [(e.type, e.object_id) for e in ref.events]
    for (a, b) in zip(trace.events, ref.events):
        assert a.t == pytest.approx(b.t, abs=0.05)


def test_keyboard_keys_drive_the_target():
    tele = Teleop(reference_scene("demo"))
    p0 = tele._target_pos.copy()
    for _ in range(5):
        tele.press("w", step=0.01)
    tele.press("r", step=0.02)
    tele.press("unknown")
    assert np.allclose(tele._target_pos - p0, (0.05, 0.0, 0.02))
    tele.press("c")
    assert tele.env._jaw_target == 0.0
