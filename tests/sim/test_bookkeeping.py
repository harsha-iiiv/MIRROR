"""The per-step bookkeeping (contact scan, state log, playback frames) must
record exactly what the original straightforward implementation recorded."""
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from mirror.contracts import RolloutFrame
from mirror.scenes import reference_scene
from mirror.sim import SimEnv
from mirror.statelog import StateLog
from tests.sim.plans import short_rollout_plan, slot_plan, touching_scene
from tests.sim.reference_contacts import reference_contacts

DATA = Path(__file__).resolve().parent / "data"


class _CheckedEnv(SimEnv):
    """Runs the reference contact scan next to the optimised one at every step."""

    def _contacts(self):
        fast = super()._contacts()
        ref = reference_contacts(self)
        assert fast == ref
        self.checked = getattr(self, "checked", 0) + 1
        return fast


@pytest.mark.parametrize("which", ["touching", "upright_slot"])
def test_contact_scan_matches_reference_every_step(which):
    if which == "touching":
        scene = touching_scene()
        plan = short_rollout_plan(scene)
    else:
        scene = reference_scene("shortcut")
        plan = slot_plan(scene, tilt_deg=0, place=False)
    env = _CheckedEnv(scene)
    res = env.execute(plan)
    assert env.checked == res.steps
    kinds = {(c.object_id, c.other) for c in res.log.contacts}
    if which == "touching":
        assert ("cup_0", "block_0") in kinds and ("block_0", "cup_0") in kinds
    else:
        assert any(o == "gripper" and x.startswith("obstacle:") for o, x in kinds)


GOLDEN_PLATFORM = json.loads((DATA / "golden_short_rollout_platform.json").read_text())
POSE_FIELDS = ("obj_pos", "obj_quat", "gripper_pos", "gripper_quat", "gripper_width")
OTHER_PLATFORM_POS_BOUND_M = 1e-3   # sanity bound only; arm64 vs x86_64 measured 8.4e-5 m


def on_golden_platform() -> bool:
    import platform
    return platform.system() == GOLDEN_PLATFORM["system"] and platform.machine() == GOLDEN_PLATFORM["machine"]


def _beads_in_cup(log, scene):
    from mirror.evaluate.geom import beads_inside_cup
    cup = next(o for o in scene.objects if o.id == "cup_0")
    i = log.object_ids.index("cup_0")
    steps = list(range(0, log.n, 100)) + [log.n - 1]
    return [int(beads_inside_cup(cup, log.obj_pos[k, i], log.obj_quat[k, i], log.beads["cup_0"][k]).sum())
            for k in steps]


def test_short_rollout_log_matches_recorded_golden(record_property):
    """Golden file recorded with the original bookkeeping (same controller and
    physics), see tests/sim/data. Floating-point physics differs between CPU
    architectures (arm64 vs x86_64), so bit-level equality is checked only on the
    platform the golden was recorded on; every platform must reproduce the
    outcome: status, steps, phases, holding, events, contact pairs and beads."""
    scene = touching_scene()
    res = SimEnv(scene).execute(short_rollout_plan(scene))
    gold = StateLog.load(DATA / "golden_short_rollout.npz")
    meta = json.loads((DATA / "golden_short_rollout_frames.json").read_text())
    log = res.log
    # outcome level, every platform
    assert res.status == meta["status"] and res.steps == meta["steps"]
    assert [(e["type"], e["object_id"]) for e in res.events] == [(e["type"], e["object_id"]) for e in meta["events"]]
    for e, g in zip(res.events, meta["events"]):
        assert e["t"] == pytest.approx(g["t"], abs=1e-9)          # step-quantised times
    assert log.n == gold.n and log.object_ids == gold.object_ids
    assert log.phase == gold.phase
    np.testing.assert_array_equal(log.held, gold.held)
    assert {(c.object_id, c.other) for c in log.contacts} == {(c.object_id, c.other) for c in gold.contacts}
    assert _beads_in_cup(log, scene) == _beads_in_cup(gold, scene)
    dev = {name: float(np.max(np.abs(np.asarray(getattr(log, name)) - np.asarray(getattr(gold, name)))))
           for name in POSE_FIELDS}
    dev["beads"] = float(np.max(np.abs(log.beads["cup_0"] - gold.beads["cup_0"])))
    record_property("max_deviation_from_golden", json.dumps(dev))
    print("max deviation from golden:", dev)
    RolloutFrame.model_validate(res.frames[0].model_dump())   # frames are ordinary contract objects
    if not on_golden_platform():
        assert dev["obj_pos"] < OTHER_PLATFORM_POS_BOUND_M and dev["gripper_pos"] < OTHER_PLATFORM_POS_BOUND_M, dev
        return
    # bit level, on the platform the golden was recorded on
    for name in ("t",) + POSE_FIELDS:
        np.testing.assert_allclose(getattr(log, name), getattr(gold, name), atol=1e-7, err_msg=name)
    np.testing.assert_allclose(log.beads["cup_0"], gold.beads["cup_0"], atol=1e-7)
    key = lambda c: (c.step, c.object_id, c.other)
    assert [key(c) for c in log.contacts] == [key(c) for c in gold.contacts]
    np.testing.assert_allclose([c.depth for c in log.contacts], [c.depth for c in gold.contacts], atol=1e-7)
    assert len(res.frames) == meta["n_frames"]
    for f, g in zip(res.frames[::10], meta["frames"]):
        g = RolloutFrame.model_validate(g)
        assert f.phase == g.phase and f.t == pytest.approx(g.t)
        assert set(f.bodies) == set(g.bodies)
        for name, pose in f.bodies.items():
            np.testing.assert_allclose(pose.pos, g.bodies[name].pos, atol=1e-7)


def test_outcome_level_comparison_runs_off_the_golden_platform(monkeypatch, record_property):
    import tests.sim.test_bookkeeping as me
    monkeypatch.setattr(me, "on_golden_platform", lambda: False)
    me.test_short_rollout_log_matches_recorded_golden(record_property)
