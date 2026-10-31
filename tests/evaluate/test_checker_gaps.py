import numpy as np

from mirror.evaluate import check

from .synth import make_scene, make_spec, pack_all


def test_tilt_during_held_label_flicker_is_still_a_transport_violation():
    scene = make_scene()
    log = pack_all(scene, cup_route="slot", spill=False).build()
    k = log.index("cup_0")
    held = np.asarray(log.held).copy()
    steps = np.flatnonzero(held == k)
    tilted = steps[np.flatnonzero(log.obj_quat[steps, k, 2] > 0.6)]
    mid = tilted[len(tilted) // 2]
    held[mid - 50: mid + 50] = -1          # 0.2 s gap in the finger-contact label
    log.held = held
    r = check(log, make_spec(corrected=True), scene)
    v = [x for x in r.violations if x.constraint_id == "c_upright_transport"]
    assert len(v) == 1                     # one continuous interval, the gap is bridged


def test_lifted_carry_without_held_label_is_transport():
    """The cup is tilted while off its support but the finger label is missing
    (e.g. carried pinned against a wall): still a transport violation."""
    from mirror.statelog import ContactRecord
    scene = make_scene()
    log = pack_all(scene, cup_route="slot", spill=False).build()
    k = log.index("cup_0")
    held = np.asarray(log.held).copy()
    steps = np.flatnonzero(held == k)
    tilted = steps[np.flatnonzero(log.obj_quat[steps, k, 2] > 0.6)]
    held[tilted] = -1                     # label missing for the whole tilted stretch (> gap fill)
    log.held = held
    log.contacts = log.contacts + [ContactRecord(step=0, object_id="cup_0", other="table")]
    r = check(log, make_spec(corrected=True), scene)
    assert any(v.constraint_id == "c_upright_transport" for v in r.violations)
    assert any("lifted off its support" in n for n in r.notes)


def test_every_object_matching_a_goal_is_required():
    """D5: a selector covers every matching object, whatever ObjectBinding.count says."""
    from mirror.contracts import ObjectSpec, Pose
    from mirror.geometry import CUP
    scene = make_scene()
    scene.objects.append(ObjectSpec(id="cup_1", kind="cup", size={"outer_half": CUP.outer_half,
                                    "wall": CUP.wall}, mass=CUP.mass, pose=Pose(pos=(-0.3, 0.3, 0.05)), beads=0))
    log = pack_all(scene).build()          # packs cup_0, block_0, marker_0; cup_1 stays put
    r = check(log, make_spec(), scene)
    assert r.status == "task_incomplete"
    assert any("cup_1 incomplete" in n for n in r.notes)
