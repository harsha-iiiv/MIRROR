"""The original, straightforward contact scan, kept as the reference that the
optimised ``SimEnv._contacts`` must match exactly."""
from __future__ import annotations

import numpy as np


def reference_contacts(env):
    d = env.data
    n = d.ncon
    if n == 0:
        return -1, []
    g1 = d.contact.geom1[:n]
    g2 = d.contact.geom2[:n]
    keep = ~(env._is_bead_geom[g1] | env._is_bead_geom[g2])
    if not keep.any():
        return -1, []
    g1 = g1[keep].tolist()
    g2 = g2[keep].tolist()
    dist = d.contact.dist[:n][keep].tolist()
    oi = env._oi_list
    grip = env._grip_list
    labels = env.index.geom_label
    oids = env.index.object_ids
    fl, fr = env.index.finger_l_geom, env.index.finger_r_geom
    left, right = set(), set()
    out: dict[tuple[str, str], float] = {}
    for a, b, dd in zip(g1, g2, dist):
        depth = -dd if dd < 0.0 else 0.0
        ia, ib = oi[a], oi[b]
        if ib >= 0:
            if a == fl: left.add(ib)
            elif a == fr: right.add(ib)
        if ia >= 0:
            if b == fl: left.add(ia)
            elif b == fr: right.add(ia)
        if ia >= 0 and ib >= 0:
            pairs = ((oids[ia], oids[ib]), (oids[ib], oids[ia]))
        elif ia >= 0:
            pairs = ((oids[ia], labels[b]),)
        elif ib >= 0:
            pairs = ((oids[ib], labels[a]),)
        elif grip[a] and not grip[b]:
            pairs = (("gripper", labels[b]),)
        elif grip[b] and not grip[a]:
            pairs = (("gripper", labels[a]),)
        else:
            continue
        for pr in pairs:
            if depth >= out.get(pr, -1.0):
                out[pr] = depth
    both = left & right
    held = min(both) if both else -1
    return held, [(o, x, dep) for (o, x), dep in out.items()]

