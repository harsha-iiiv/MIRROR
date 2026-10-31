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
