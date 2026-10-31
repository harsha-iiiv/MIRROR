"""Deterministic local corruptions (the sensor family): no model involved."""
from __future__ import annotations

import numpy as np
from scipy import ndimage

from .instructions import EditInstruction


def apply_local(rgb: np.ndarray, ins: EditInstruction) -> np.ndarray:
    """Apply a ``kind == "local"`` instruction to an HxWx3 uint8 image."""
    if ins.kind != "local":
        raise ValueError(f"{ins.id} is not a local instruction")
    op = ins.params["op"]
    img = rgb.astype(np.float32)
    if op == "gaussian_noise":
        rng = np.random.default_rng(int(ins.params.get("seed", 0)))
        img = img + rng.normal(0.0, float(ins.params["sigma"]), img.shape)
    elif op == "gaussian_blur":
        s = float(ins.params["sigma"])
        img = ndimage.gaussian_filter(img, sigma=(s, s, 0))
    elif op == "motion_blur":
        n = int(ins.params["length"])
        img = ndimage.uniform_filter1d(img, size=n, axis=1, mode="nearest")
    else:
        raise ValueError(f"unknown local op {op!r}")
    return np.clip(np.rint(img), 0, 255).astype(np.uint8)
