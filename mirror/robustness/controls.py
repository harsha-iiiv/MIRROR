"""Synthetic controls for the geometry check (used by the tests and the report).

Positive controls change appearance only; negative controls move, rescale or
remove one object by compositing its own pixels. All are deterministic.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage


def _bg_fill(rgb: np.ndarray, hole: np.ndarray, keep: np.ndarray, iters: int = 300) -> np.ndarray:
    """Smoothly inpaint ``hole`` from its surroundings (harmonic diffusion), the
    way an editing model would paint an object out, without a seam."""
    hole = ndimage.binary_dilation(hole, iterations=1)
    ys, xs = np.nonzero(hole)
    if len(ys) == 0:
        return rgb.copy()
    pad = 6
    y0, y1 = max(0, ys.min() - pad), min(rgb.shape[0], ys.max() + pad + 1)
    x0, x1 = max(0, xs.min() - pad), min(rgb.shape[1], xs.max() + pad + 1)
    win = rgb[y0:y1, x0:x1].astype(np.float32)
    h = hole[y0:y1, x0:x1]
    known = ~h
    fill = win.copy()
    fill[h] = win[known].mean(axis=0)
    k = np.array([[0, .25, 0], [.25, 0, .25], [0, .25, 0]], np.float32)
    for _ in range(iters):
        for c in range(3):
            sm = ndimage.convolve(fill[..., c], k, mode="nearest")
            fill[..., c][h] = sm[h]
    out = rgb.copy()
    out[y0:y1, x0:x1][h] = np.clip(np.rint(fill[h]), 0, 255).astype(np.uint8)
    return out


def photometric(rgb, gain=(1.0, 1.0, 1.0), bias=0.0, gamma=1.0):
    x = (rgb.astype(np.float32) / 255.0) ** gamma
    x = x * np.array(gain, np.float32) + bias / 255.0
    return np.clip(np.rint(x * 255.0), 0, 255).astype(np.uint8)


def recolor_background(rgb, masks, color=(70, 45, 30), seed=0):
    """Replace every pixel outside the object and gripper masks with a textured colour."""
    fg = np.zeros(rgb.shape[:2], bool)
    for m in masks.values():
        fg |= m
    rng = np.random.default_rng(seed)
    tex = ndimage.gaussian_filter(rng.normal(0, 1, rgb.shape[:2]), (1.5, 12)) * 18
    bg = np.clip(np.array(color, np.float32)[None, None] + tex[..., None], 0, 255).astype(np.uint8)
    out = rgb.copy()
    out[~fg] = bg[~fg]
    return out


def move_object(rgb, masks, cls, dx, dy):
    m = masks[cls]
    others = np.zeros_like(m)
    for k, v in masks.items():
        if k != cls:
            others |= v
    out = _bg_fill(rgb, m & ~others, others | m)
    moved = np.roll(np.roll(m, dy, 0), dx, 1)
    src = np.roll(np.roll(rgb, dy, 0), dx, 1)
    out[moved] = src[moved]
    return out


def scale_object(rgb, masks, cls, s):
    m = masks[cls]
    ys, xs = np.nonzero(m)
    cy, cx = ys.mean(), xs.mean()
    others = np.zeros_like(m)
    for k, v in masks.items():
        if k != cls:
            others |= v
    out = _bg_fill(rgb, m & ~others, others | m)
    mat = np.array([[1 / s, 0], [0, 1 / s]])
    off = np.array([cy, cx]) - mat @ np.array([cy, cx])
    ms = ndimage.affine_transform(m.astype(float), mat, offset=off, order=0) > 0.5
    src = np.stack([ndimage.affine_transform(rgb[..., c].astype(float), mat, offset=off, order=1)
                    for c in range(3)], -1).astype(np.uint8)
    out[ms] = src[ms]
    return out


def remove_object(rgb, masks, cls):
    m = masks[cls]
    others = np.zeros_like(m)
    for k, v in masks.items():
        if k != cls:
            others |= v
    return _bg_fill(rgb, m & ~others, others | m)


# ------------------------------------------------------------ calibration

def positive_controls(frame):
    from .instructions import by_id
    from .local import apply_local
    return {
        "dim_x0.35": photometric(frame.rgb, (0.35, 0.35, 0.35)),
        "warm_tint": photometric(frame.rgb, (1.15, 0.9, 0.6), 10),
        "gamma_1.8": photometric(frame.rgb, gamma=1.8),
        "background_recolor": recolor_background(frame.rgb, frame.masks),
        **{i.id: apply_local(frame.rgb, i) for i in by_id(["noise_gauss", "blur_gauss", "blur_motion"])},
    }


NEGATIVE_CONTROLS = {
    "shift_4px": lambda f, c: move_object(f.rgb, f.masks, c, 4, 0),
    "shift_8px": lambda f, c: move_object(f.rgb, f.masks, c, 0, 8),
    "scale_1.12": lambda f, c: scale_object(f.rgb, f.masks, c, 1.12),
    "removed": lambda f, c: remove_object(f.rgb, f.masks, c),
}


def calibrate(frames, classes=("cup", "block", "marker")) -> dict:
    """Run the geometry check on every control and summarise detection rates.

    Positive controls should be ``preserved`` (no object flagged moved,
    resized or missing). Negative controls should flag the altered object."""
    from .geometry import check_geometry
    pos: dict[str, list[bool]] = {}
    neg: dict[str, dict] = {}
    collateral = 0
    untested = 0
    for f in frames:
        for name, img in positive_controls(f).items():
            rep, _ = check_geometry(f.rgb, img, f.masks, classes)
            pos.setdefault(name, []).append(rep.verdict == "preserved")
        for name, fn in NEGATIVE_CONTROLS.items():
            for c in classes:
                rep, _ = check_geometry(f.rgb, fn(f, c), f.masks, classes)
                o = rep.objects[c]
                d = neg.setdefault(name, {"flagged": 0, "missed": 0, "untested": 0, "verdicts": {}})
                d["verdicts"][o.verdict] = d["verdicts"].get(o.verdict, 0) + 1
                if o.verdict == "untested":
                    d["untested"] += 1
                    untested += 1
                elif o.verdict in ("moved", "resized", "missing"):
                    d["flagged"] += 1
                else:
                    d["missed"] += 1
                collateral += sum(1 for k, oo in rep.objects.items()
                                  if k != c and oo.verdict in ("moved", "resized", "missing"))
    return {
        "frames": [f.frame_id for f in frames],
        "positive_pass_rate": {k: round(sum(v) / len(v), 3) for k, v in pos.items()},
        "positive_n": {k: len(v) for k, v in pos.items()},
        "negative": {k: {**v, "detection_rate": round(v["flagged"] / max(1, v["flagged"] + v["missed"]), 3)}
                     for k, v in neg.items()},
        "collateral_flags": collateral,
    }
