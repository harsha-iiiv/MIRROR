"""Capture view for the 3D workspace: the calibrated phone as a camera in simulator coordinates.

Writes ``capture_view.json`` (format ``mirror-capture-view/1``), an undistorted frame for the
camera's image plane and a rectified photo of the table for the ground. Inputs are a capture
session (intrinsics + accepted board pose), the table->simulator placement the scene was built
with, and a source image (a clean plate or any frame of the take, as recorded: distorted).

Frames (as mirror/capture and mirror/twin):
  table : board origin, z up, metres; x_cam = R x_table + t (OpenCV camera, from the pose)
  sim   : x_sim = Rz(yaw) x_table + (ox, oy, 0)   (WorkspacePlacement)
The table plane is taken to be the board plane (z = 0); the board's own thickness on its
backing is not modelled. Everything here is approximate for a single camera, and the JSON says so.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Optional

import numpy as np

FORMAT = "mirror-capture-view/1"


def rot_z(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def camera_in_sim(R_ct: np.ndarray, t_ct: np.ndarray, origin_xy, yaw_deg: float):
    """(R_wc, C_w): camera->sim rotation (columns = OpenCV camera axes in sim) and centre."""
    Rz = rot_z(yaw_deg)
    o = np.array([origin_xy[0], origin_xy[1], 0.0])
    C_table = -R_ct.T @ t_ct
    return Rz @ R_ct.T, Rz @ C_table + o


def _camera_name(session: dict) -> str:
    return next(iter(session["intrinsics"]))


def build(session: dict, placement: dict, image: np.ndarray, out: Path, *, x_range=(-0.6, 0.6),
          y_range=(-0.5, 0.5), px_per_m: int = 900, web_width: int = 540, frame_note: str = "",
          labels: Optional[list] = None, caption: str = "") -> dict:
    import cv2
    cam = _camera_name(session)
    intr = session["intrinsics"][cam]
    pose = session["poses"][cam]
    K = np.array(intr["K"], float)
    dist = np.array(intr.get("dist") or [0, 0, 0, 0, 0], float)
    W0, H0 = (int(intr["image_size"][0]), int(intr["image_size"][1]))
    R_ct, t_ct = np.array(pose["R"], float), np.array(pose["t"], float)
    h, w = image.shape[:2]
    if (w, h) != (W0, H0):            # source at another resolution (e.g. a 540x960 plate): scale K
        sx, sy = w / W0, h / H0
        K = K.copy(); K[0, :] *= sx; K[1, :] *= sy
    out.mkdir(parents=True, exist_ok=True)

    # Image plane: undistorted frame at web resolution, same pinhole K (scaled).
    und = cv2.undistort(image, K, dist)
    s = web_width / w
    small = cv2.resize(und, (web_width, int(round(h * s))), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(out / "frame.jpg"), small, [cv2.IMWRITE_JPEG_QUALITY, 88])
    Ks = K.copy(); Ks[0, :] *= s; Ks[1, :] *= s

    # Ground: rectified table photo in sim coordinates (remap from the distorted source).
    origin = placement.get("origin_xy_m") or placement.get("origin_xy") or (0.0, 0.0)
    yaw = float(placement.get("yaw_deg", 0.0))
    Rz = rot_z(yaw)
    o = np.array([origin[0], origin[1], 0.0])
    nx, ny = int((x_range[1] - x_range[0]) * px_per_m), int((y_range[1] - y_range[0]) * px_per_m)
    xs = x_range[0] + (np.arange(nx) + 0.5) / px_per_m
    ys = y_range[1] - (np.arange(ny) + 0.5) / px_per_m          # image row 0 = +y edge
    X, Y = np.meshgrid(xs, ys)
    P_sim = np.stack([X.ravel(), Y.ravel(), np.zeros(X.size)], 1)
    P_tab = (P_sim - o) @ Rz                                      # Rz^T applied row-wise
    Pc = P_tab @ R_ct.T + t_ct
    uv, _ = cv2.projectPoints(P_tab, cv2.Rodrigues(R_ct)[0], t_ct, K, dist)
    uv = uv.reshape(ny, nx, 2).astype(np.float32)
    valid = (Pc[:, 2].reshape(ny, nx) > 0.05) & (uv[..., 0] >= 0) & (uv[..., 0] <= w - 1) & (uv[..., 1] >= 0) & (uv[..., 1] <= h - 1)
    rect = cv2.remap(image, uv[..., 0], uv[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    # crop to the visible part
    rows, cols = np.where(valid.any(1))[0], np.where(valid.any(0))[0]
    r0, r1, c0, c1 = rows.min(), rows.max() + 1, cols.min(), cols.max() + 1
    alpha = (valid[r0:r1, c0:c1] * 255).astype(np.uint8)
    alpha = cv2.GaussianBlur(alpha, (0, 0), 2.0)                  # soft edge where the photo ends
    rgba = np.dstack([rect[r0:r1, c0:c1], alpha])
    cv2.imwrite(str(out / "table.png"), rgba, [cv2.IMWRITE_PNG_COMPRESSION, 9])
    tx = (float(x_range[0] + c0 / px_per_m), float(x_range[0] + c1 / px_per_m))
    ty = (float(y_range[1] - r1 / px_per_m), float(y_range[1] - r0 / px_per_m))

    R_wc, C_w = camera_in_sim(R_ct, t_ct, origin, yaw)
    view = {
        "format": FORMAT,
        "camera": {"width": int(small.shape[1]), "height": int(small.shape[0]),
                   "fx": float(Ks[0, 0]), "fy": float(Ks[1, 1]), "cx": float(Ks[0, 2]), "cy": float(Ks[1, 2]),
                   "R_wc": R_wc.round(9).tolist(), "C_w": C_w.round(6).tolist(),
                   "convention": "OpenCV camera axes (x right, y down, z forward); R_wc columns are those axes in simulator coordinates",
                   "distortion": "frame.jpg is undistorted with the session's lens model; the pinhole above is exact for it"},
        "frame": {"url": "frame.jpg", "note": frame_note},
        "table": {"url": "table.png", "x_range": tx, "y_range": ty, "z": 0.0, "px_per_m": px_per_m,
                  "note": "rectified from the source image through the board pose; table plane taken as the board plane"},
        "placement": {"origin_xy_m": [float(origin[0]), float(origin[1])], "yaw_deg": yaw, "source": placement.get("source", "")},
        "pose": {k: pose.get(k) for k in ("rms_px", "distance_m", "view_angle_deg", "holdout_mm", "pts_s") if k in pose},
        "intrinsics_source": intr.get("source", ""),
        "labels": labels or [],
        "caption": caption,
        "approximate": True,
    }
    (out / "capture_view.json").write_text(json.dumps(view, indent=1))
    return view


def _read_image(path: Optional[str], video: Optional[str], t: Optional[float]):
    import cv2
    if path:
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            raise SystemExit(f"cannot read {path}")
        return img
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "f.png"
        subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t or 0:.3f}", "-i", video, "-frames:v", "1", str(p)],
                       check=True, timeout=120, stdin=subprocess.DEVNULL)
        return cv2.imread(str(p), cv2.IMREAD_COLOR)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--session", required=True, help="capture session json (intrinsics + poses)")
    ap.add_argument("--placement", help="json with origin_xy_m and yaw_deg (table_to_sim.json, or a file with a 'placement' key)")
    ap.add_argument("--image", help="source image (clean plate or a frame), as recorded (distorted)")
    ap.add_argument("--video", help="take video, when --image is not given")
    ap.add_argument("--t", type=float, default=None, help="time in the take for the frame (s)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", action="append", default=[])
    ap.add_argument("--caption", default="")
    a = ap.parse_args(argv)
    session = json.loads(Path(a.session).read_text())
    pl = session.get("placement") or {}
    if a.placement:
        pj = json.loads(Path(a.placement).read_text())
        pl = pj.get("placement", pj)
    img = _read_image(a.image, a.video, a.t)
    v = build(session, pl, img, Path(a.out), labels=a.label, caption=a.caption,
              frame_note=(f"image {Path(a.image).name}" if a.image else f"frame at {a.t} s of {Path(a.video).name}"))
    print(json.dumps({"out": a.out, "camera_C_w": v["camera"]["C_w"], "table": v["table"]}, indent=1))


if __name__ == "__main__":
    main()
