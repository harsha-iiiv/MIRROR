import ast
import time
from pathlib import Path

import numpy as np
import pytest

from mirror.contracts import PlanResult, PlanStep, Pose
from mirror.scenes import reference_scene
from mirror.sim import SimEnv
from mirror.statelog import StateLog
from tests.sim.helpers import beads_in_cup, final_speed, in_tray, max_tilt
from tests.sim.plans import DOWN, demo_pack_plan, pick_steps, plan_of, slot_plan

SIM_DIR = Path(__file__).resolve().parents[2] / "mirror" / "sim"


# ------------------------------------------------- physical claims ---

def test_cup_carried_upright_around_the_wall_keeps_every_bead(demo_pack_run, demo_scene):
    env, res = demo_pack_run
    assert res.status == "completed", res.reason
    log = res.log
    assert max_tilt(log, "cup_0") < 5.0
    assert beads_in_cup(log, demo_scene) == 8
    assert in_tray(log, demo_scene, "cup_0")
    # The cup never touched the wall.
    assert not [c for c in log.contacts if c.other.startswith("obstacle:")]


def test_block_and_marker_are_grasped_placed_released_and_settle(demo_pack_run, demo_scene):
    env, res = demo_pack_run
    log = res.log
    grasped = [e["object_id"] for e in res.events if e["type"] == "grasp"]
    released = [e["object_id"] for e in res.events if e["type"] == "release"]
    assert grasped == ["cup_0", "block_0", "marker_0"] == released
    for oid in ("block_0", "marker_0"):
        k = log.index(oid)
        assert in_tray(log, demo_scene, oid)
        assert log.held[-1] == -1
        assert final_speed(log, oid) < 0.01
        assert max_tilt(log, oid) < 10.0
        # Held only while being carried.
        held_steps = np.nonzero(log.held == k)[0]
        assert len(held_steps) > 0
        assert all(log.phase[i].split(":")[-1] == oid for i in held_steps[::50])


def test_cup_pitched_through_the_slot_spills_beads(tilted_slot_run, shortcut_scene):
    env, res = tilted_slot_run
    assert res.status == "completed", res.reason
    log = res.log
    assert max_tilt(log, "cup_0") > 80.0
    remaining = beads_in_cup(log, shortcut_scene)
    assert remaining < 8, "a cup carried on its side must lose beads"
    # Spilled beads are on the table or tray, not hidden inside the cup walls.
    bz = log.beads["cup_0"][-1][:, 2]
    assert (bz < 0.03).sum() >= 8 - remaining
    # The slot admits the pitched cup without touching the wall.
    assert not [c for c in log.contacts if c.object_id == "cup_0" and c.other.startswith("obstacle:")]
    assert in_tray(log, shortcut_scene, "cup_0")


def test_cup_carried_upright_cannot_pass_the_slot(upright_slot_run):
    env, res = upright_slot_run
    assert res.status == "execution_failed"
    assert "tracking error" in res.reason and "obstacle:wall_0" in res.reason
    hits = [c for c in res.log.contacts if c.other == "obstacle:wall_0"
            and c.object_id in ("cup_0", "gripper")]
    assert hits
    # The cup is still on the start side of the wall.
    assert res.log.obj_pos[-1, res.log.index("cup_0"), 0] < 0.0


def test_three_object_rollout_is_fast(demo_pack_run):
    env, res = demo_pack_run
    assert res.status == "completed"
    assert res.wall_time < 45.0, f"three-object rollout took {res.wall_time:.1f} s"


# ------------------------------------------------- execution honesty ---

class _GuardedEnv(SimEnv):
    """Fails if anything changes object or bead state between physics steps."""

    def _object_state(self):
        idx = self.index
        qa = [a + i for a in idx.object_qposadr.values() for i in range(7)]
        qa += [a + i for lst in idx.bead_qposadr.values() for a in lst for i in range(7)]
        return self.data.qpos[qa].copy(), self.data.qvel.copy()

    def _step_physics(self):
        if getattr(self, "_last", None) is not None:
            q, v = self._object_state()
            assert np.array_equal(q, self._last[0]), "object pose written outside physics"
            assert np.array_equal(v, self._last[1]), "velocity written outside physics"
        super()._step_physics()
        self._last = self._object_state()
        self.guarded_steps = getattr(self, "guarded_steps", 0) + 1


def test_executor_never_writes_object_state_at_runtime(demo_scene):
    env = _GuardedEnv(demo_scene)
    env._last = None
    steps = pick_steps(demo_scene, "block_0")
    res = env.execute(plan_of(demo_scene, steps))
    assert res.status == "completed", res.reason
    assert env.guarded_steps == res.steps


def _state_writes(path: Path):
    tree = ast.parse(path.read_text())
    bad = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for node in ast.walk(fn):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            for t in targets:
                base = t.value if isinstance(t, ast.Subscript) else t
                if isinstance(base, ast.Attribute) and base.attr in {
                        "qpos", "qvel", "qacc", "xpos", "xquat", "mocap_pos", "mocap_quat", "qfrc_applied", "xfrc_applied"}:
                    bad.append((path.name, fn.name, node.lineno))
    return bad


EXECUTION_MODULES = ("env.py", "teleop.py", "demo.py", "model.py", "rotations.py")


def test_only_reset_writes_simulation_state():
    writes = []
    for name in EXECUTION_MODULES:
        writes += _state_writes(SIM_DIR / name)
    assert writes and all(fn == "reset" for _, fn, _ in writes), writes


def test_renderer_never_steps_physics():
    # render.py poses a separate display-only MjData from recorded frames.
    src = (SIM_DIR / "render.py").read_text()
    assert "mj_step" not in src and "SimEnv" not in src


# ------------------------------------------------- failure reporting ---

def test_grasping_empty_air_is_reported(demo_scene):
    env = SimEnv(demo_scene)
    steps = [PlanStep(kind="approach", object_id="cup_0", waypoints=[Pose(pos=(-0.38, -0.33, 0.1), quat=DOWN)]),
             PlanStep(kind="grasp", object_id="cup_0")]
    res = env.execute(plan_of(demo_scene, steps))
    assert res.status == "execution_failed" and "grasp of cup_0 failed" in res.reason


def test_drop_is_detected_not_repaired(demo_scene, monkeypatch):
    env = SimEnv(demo_scene)
    plan = plan_of(demo_scene, pick_steps(demo_scene, "block_0"))
    real = env._contacts
    state = {"grasped": False}

    def fake():
        held, contacts = real()
        if state["grasped"] and env.sim_time() > state["t"] + 0.5:
            return -1, contacts          # pretend the fingers lost the block
        if held >= 0 and not state["grasped"]:
            state.update(grasped=True, t=env.sim_time() + 0.3)
        return held, contacts

    monkeypatch.setattr(env, "_contacts", fake)
    res = env.execute(plan)
    assert res.status == "execution_failed" and "dropped" in res.reason


def test_timeout_and_unplannable(demo_scene):
    env = SimEnv(demo_scene)
    res = env.execute(demo_pack_plan(demo_scene), max_sim_time=2.0)
    assert res.status == "timeout"
    env.reset()
    res = env.execute(PlanResult(status="no_plan", reason="none"))
    assert res.status == "execution_failed" and "no_plan" in res.reason


# ------------------------------------------------- logging ---

def test_state_log_is_dense_and_round_trips(demo_pack_run, tmp_path):
    env, res = demo_pack_run
    log = res.log
    assert log.n == res.steps
    assert np.allclose(np.diff(log.t), log.dt)
    assert log.obj_pos.shape == (log.n, 3, 3) and log.beads["cup_0"].shape == (log.n, 8, 3)
    assert len(log.phase) == log.n
    assert {"transport:cup_0", "grasp:block_0", "release:marker_0", "settle"} <= set(log.phase)
    assert any(c.other == "gripper" for c in log.contacts)
    log.save(tmp_path / "log.npz")
    back = StateLog.load(tmp_path / "log.npz")
    assert back.n == log.n and np.array_equal(back.held, log.held)
    assert len(back.contacts) == len(log.contacts)


def test_frames_are_sampled_for_playback(demo_pack_run):
    env, res = demo_pack_run
    frames = res.frames
    assert abs(len(frames) - res.sim_time * 30.0) <= 2
    assert {"cup_0", "gripper", "gripper_finger_r", "cup_0/bead7"} <= set(frames[0].bodies)
    assert frames[-1].phase == "settle"
