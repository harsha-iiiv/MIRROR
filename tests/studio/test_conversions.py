"""Playback conversions from step logs and demonstration traces."""
import numpy as np

from mirror.contracts import (DemoTrace, Pose, PlanStep, RolloutReport, TraceEvent, TraceFrame,
                              VisualBody, VisualGeom, VisualScene)
from mirror.statelog import StateLog
from mirror.studio.fixtures import fixture_scene
from mirror.studio.session_adapter import (_robot_names, _transport_entry, statelog_to_rollout,
                                           trace_to_rollout)


def _visual():
    g = VisualGeom(type="box", size=(0.01, 0.01, 0.01), pos=(0, 0, 0), quat=(1, 0, 0, 0), rgba=(1, 1, 1, 1))
    names = ["gripper", "gripper_finger_l", "gripper_finger_r", "cup_0", "cup_0/bead0", "cup_0/bead1"]
    return VisualScene(bodies=[VisualBody(name="world", static=True, geoms=[g])] +
                       [VisualBody(name=n, static=False, geoms=[g]) for n in names])


def _log(n=500, dt=0.002):
    t = np.arange(n) * dt
    pos = np.zeros((n, 1, 3)); pos[:, 0, 0] = np.linspace(0, 0.3, n)
    quat = np.tile([1.0, 0, 0, 0], (n, 1, 1))
    return StateLog(dt=dt, t=t, object_ids=["cup_0"], obj_pos=pos, obj_quat=quat,
                    gripper_pos=pos[:, 0, :] + [0, 0, 0.05], gripper_quat=np.tile([1.0, 0, 0, 0], (n, 1)),
                    gripper_width=np.full(n, 0.06), held=np.zeros(n, int), phase=["transport:cup_0"] * n,
                    beads={"cup_0": np.zeros((n, 2, 3))})


def test_robot_names_from_visual():
    assert _robot_names(_visual()) == ("gripper", "gripper_finger_l", "gripper_finger_r")


def test_statelog_playback_samples_at_frame_rate_with_fingers_and_beads():
    rep = RolloutReport(rollout_id="e1", scene_id="s", spec_id="x", spec_version=1, status="success")
    r = statelog_to_rollout(_log(), rep, _visual(), frame_hz=30.0)
    assert abs(r.frame_hz - 1 / (17 * 0.002)) < 1e-6
    assert r.frames[-1].t == 499 * 0.002
    f = r.frames[0].bodies
    assert {"gripper", "gripper_finger_l", "gripper_finger_r", "cup_0", "cup_0/bead0", "cup_0/bead1"} <= set(f)
    # Fingers sit at +-width/2 along the gripper's local y (identity orientation here).
    assert abs(f["gripper_finger_l"].pos[1] - 0.03) < 1e-9 and abs(f["gripper_finger_r"].pos[1] + 0.03) < 1e-9
    assert r.frames[0].phase == "transport:cup_0"


def test_trace_playback_hides_nothing_it_cannot_know():
    sc = fixture_scene("demo")
    frames = [TraceFrame(t=i * 0.1, gripper=Pose(pos=(0, 0, 0.3)), gripper_width=0.08,
                         objects={o.id: o.pose for o in sc.objects}) for i in range(5)]
    tr = DemoTrace(demo_id="d", scene=sc, source="teleop_scripted", sample_hz=10, frames=frames,
                   events=[TraceEvent(t=0.15, type="grasp", object_id="cup_0")])
    r = trace_to_rollout(tr, _visual())
    assert "cup_0/bead0" not in r.frames[0].bodies
    assert r.frames[0].phase == "" and r.frames[2].phase == "transport:cup_0"
    assert "gripper_finger_l" in r.frames[0].bodies


def test_transport_note_parsing():
    s = PlanStep(kind="transport", object_id="cup_0", note="route=through_slot:wall_0:0:pitched+90; mode=pitched+90; max_tilt_deg=90")
    e = _transport_entry(s)
    assert e == {"object_id": "cup_0", "route": "through_slot:wall_0:0:pitched+90", "mode": "pitched+90", "note": s.note}
    assert _transport_entry(PlanStep(kind="transport", object_id="b", note="direct"))["route"] == "direct"


def test_compact_rollout_carries_omitted_bodies_forward():
    from mirror.contracts import Rollout, RolloutFrame
    from mirror.studio.session_adapter import compact_rollout, trace_summary
    rep = RolloutReport(rollout_id="e", scene_id="s", spec_id="x", spec_version=1, status="success")
    frames = [RolloutFrame(t=0.0, bodies={"cup_0": Pose(pos=(0.1, 0, 0.05)), "gripper": Pose(pos=(0, 0, 0.3))}, gripper_width=0.09),
              RolloutFrame(t=0.1, bodies={"gripper": Pose(pos=(0, 0, 0.2))}, gripper_width=0.09),
              RolloutFrame(t=0.2, bodies={"cup_0": Pose(pos=(0.2, 0, 0.05))}, gripper_width=0.09)]
    p = compact_rollout(Rollout(report=rep, frames=frames))
    assert [x[0] for x in p["bodies"]["cup_0"]] == [0.1, 0.1, 0.2]
    assert [x[2] for x in p["bodies"]["gripper"]] == [0.3, 0.2, 0.2]
    tr = trace_summary(Rollout(report=rep, frames=frames), fixture_scene("demo"))
    assert [pt[0] for pt in tr["objects"]["cup_0"]][-1] == 0.2
