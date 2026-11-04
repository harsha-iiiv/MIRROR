"""Where the simulator put things vs where the footage shows them (table frame, one camera).

``tray_from_mask`` back-projects the tray's SAM outline onto the plane of its rim (the measured
height) and fits a rotated rectangle: centre, yaw and outer size in the table frame. It is a
single-view estimate (labelled as such), useful to check, or to set, the simulated tray's pose.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np


def tray_from_mask(mask: np.ndarray, K, dist, R, t, rim_height_m: float) -> Optional[dict]:
    import cv2
    from mirror.capture.geometry import pixel_rays, ray_plane
    m = np.asarray(mask, bool).astype(np.uint8)
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cs:
        return None
    c = max(cs, key=cv2.contourArea).reshape(-1, 2).astype(float)
    C, d = pixel_rays(K, dist, R, t, c)
    P = np.array([ray_plane(C, dd, rim_height_m) for dd in d])
    P = P[np.all(np.isfinite(P), 1)][:, :2]
    if len(P) < 8:
        return None
    (cx, cy), (w, h), ang = cv2.minAreaRect(P.astype(np.float32))
    # express as: long side along the rectangle's local x; yaw in (-90, 90]
    if w < h:
        w, h = h, w
        ang += 90.0
    yaw = ((ang + 90.0) % 180.0) - 90.0
    return {"centre_xy_m": [round(float(cx), 4), round(float(cy), 4)], "yaw_deg": round(float(yaw), 2),
            "outer_long_m": round(float(w), 4), "outer_short_m": round(float(h), 4), "rim_height_m": rim_height_m,
            "method": "SAM tray outline back-projected to the rim plane, minimum-area rectangle (single view, approximate)"}


def compare_tray(est: dict, centre_xy, inner_half, yaw_deg: float) -> dict:
    """Simulated tray (table frame) vs the footage estimate."""
    long_axis_sim = 0.0 if inner_half[0] >= inner_half[1] else 90.0
    d_yaw = ((est["yaw_deg"] - (yaw_deg + long_axis_sim) + 90.0) % 180.0) - 90.0
    dc = np.asarray(est["centre_xy_m"]) - np.asarray(centre_xy)
    return {"centre_offset_m": [round(float(dc[0]), 4), round(float(dc[1]), 4)],
            "centre_offset_cm": round(float(np.linalg.norm(dc)) * 100, 1), "yaw_offset_deg": round(float(d_yaw), 1)}
