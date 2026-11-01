"""Binary masks: run-length encoding (row-major, starting with a 0-run),
IoU and centroids."""
from __future__ import annotations

import numpy as np


def rle_encode(mask: np.ndarray) -> dict:
    m = np.asarray(mask, dtype=bool).ravel()
    if m.size == 0:
        return {"size": list(mask.shape), "counts": []}
    change = np.flatnonzero(m[1:] != m[:-1]) + 1
    bounds = np.concatenate([[0], change, [m.size]])
    runs = np.diff(bounds).tolist()
    if m[0]:
        runs = [0] + runs
    return {"size": [int(mask.shape[0]), int(mask.shape[1])], "counts": [int(r) for r in runs]}


def rle_decode(rle: dict) -> np.ndarray:
    h, w = rle["size"]
    out = np.zeros(h * w, dtype=bool)
    pos, val = 0, False
    for r in rle["counts"]:
        if val:
            out[pos:pos + r] = True
        pos += r
        val = not val
    return out.reshape(h, w)


def rle_area(rle: dict) -> int:
    return int(sum(rle["counts"][1::2]))


def mask_iou(a: np.ndarray, b: np.ndarray) -> float:
    a, b = np.asarray(a, bool), np.asarray(b, bool)
    u = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / u) if u else 1.0


def mask_centroid(mask: np.ndarray):
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return float(xs.mean()), float(ys.mean())


def mask_bbox(mask: np.ndarray):
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
