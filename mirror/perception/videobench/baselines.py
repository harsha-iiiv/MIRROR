"""Baselines for the video benchmark v3.

* constant_zero_tilt: the cup is always upright (never abstains).
* pca_axis_tilt: classical; angle between image vertical and the principal axis
  of the cup's side-view mask (in-image tilt), abstains without a side mask.
* static_positions: objects never move; each keeps its first estimated position.
* colour_tracker: classical, no learned segmentation. From the same first-frame
  boxes the tracker starts from, it learns each object's mean colour and, per
  frame, takes the connected region of similar colour nearest the previous
  centroid. Only meaningful on rendered video with flat colours; labelled so.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
from scipy import ndimage


def constant_zero_tilt(_t: float) -> float:
    return 0.0


def pca_axis_tilt(side_mask: Optional[np.ndarray]) -> Optional[float]:
    if side_mask is None or side_mask.sum() < 20:
        return None
    ys, xs = np.nonzero(side_mask)
    C = np.cov(np.stack([xs, ys]))
    w, V = np.linalg.eigh(C)
    v = V[:, np.argmax(w)]
    return math.degrees(math.atan2(abs(v[0]), abs(v[1])))


def static_positions(est) -> dict:
    out = {}
    for lab, P in est.objects.items():
        ok = np.flatnonzero(est.found[lab])
        out[lab] = None if not len(ok) else P[ok[0]][:2]
    return out


def colour_tracker(video_path, boxes: dict, *, colour_tol: float = 40.0, max_jump_px: float = 60.0,
                   every: int = 1) -> dict:
    """label -> {frame index: bool mask}. ``boxes``: label -> [x0, y0, x1, y1] in frame 0."""
    import imageio.v2 as imageio
    rd = imageio.get_reader(str(video_path))
    ref, last, masks = {}, {}, {lab: {} for lab in boxes}
    try:
        for i, frame in enumerate(rd):
            if i % every:
                continue
            img = frame[..., :3].astype(float)
            if not ref:
                for lab, (x0, y0, x1, y1) in boxes.items():
                    patch = img[int(y0):int(y1), int(x0):int(x1)].reshape(-1, 3)
                    if not len(patch):
                        continue
                    centre = np.median(patch, 0)
                    ref[lab] = centre
                    last[lab] = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
            for lab, c in ref.items():
                m = np.linalg.norm(img - c, axis=2) < colour_tol
                lbl, n = ndimage.label(m)
                if n == 0:
                    continue
                cents = ndimage.center_of_mass(m, lbl, range(1, n + 1))
                sizes = ndimage.sum(m, lbl, range(1, n + 1))
                best, bd = None, None
                for k, (cy, cx) in enumerate(cents):
                    if sizes[k] < 15:
                        continue
                    d = math.hypot(cx - last[lab][0], cy - last[lab][1])
                    if d <= max_jump_px and (bd is None or d < bd):
                        best, bd = k + 1, d
                if best is None:
                    continue
                mk = lbl == best
                masks[lab][i] = mk
                ys, xs = np.nonzero(mk)
                last[lab] = np.array([xs.mean(), ys.mean()])
    finally:
        rd.close()
    return masks
