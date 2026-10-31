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


def test_short_rollout_log_matches_recorded_golden():
    """Golden file recorded with the original bookkeeping (same controller and
    physics), see tests/sim/data."""
    scene = touching_scene()
    res = SimEnv(scene).execute(short_rollout_plan(scene))
    gold = StateLog.load(DATA / "golden_short_rollout.npz")
    log = res.log
    assert log.n == gold.n and log.object_ids == gold.object_ids
    assert log.phase == gold.phase
    np.testing.assert_array_equal(log.held, gold.held)
    for name in ("t", "obj_pos", "obj_quat", "gripper_pos", "gripper_quat", "gripper_width"):
        np.testing.assert_allclose(getattr(log, name), getattr(gold, name), atol=1e-7, err_msg=name)
    np.testing.assert_allclose(log.beads["cup_0"], gold.beads["cup_0"], atol=1e-7)
    key = lambda c: (c.step, c.object_id, c.other)
    assert [key(c) for c in log.contacts] == [key(c) for c in gold.contacts]
    np.testing.assert_allclose([c.depth for c in log.contacts], [c.depth for c in gold.contacts], atol=1e-7)
    meta = json.loads((DATA / "golden_short_rollout_frames.json").read_text())
    assert len(res.frames) == meta["n_frames"]
    for f, g in zip(res.frames[::10], meta["frames"]):
        g = RolloutFrame.model_validate(g)
        assert f.phase == g.phase and f.t == pytest.approx(g.t)
        assert set(f.bodies) == set(g.bodies)
        for name, pose in f.bodies.items():
            np.testing.assert_allclose(pose.pos, g.bodies[name].pos, atol=1e-7)
    # Frames are ordinary contract objects.
    RolloutFrame.model_validate(res.frames[0].model_dump())
