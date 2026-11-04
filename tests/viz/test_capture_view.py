"""mirror.viz.capture_view: the phone as a camera in simulator coordinates."""
import json

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from mirror.viz.capture_view import build, camera_in_sim, rot_z  # noqa: E402


def _session(K, R, t, size):
    return {"intrinsics": {"top": {"K": K.tolist(), "dist": [0, 0, 0, 0, 0], "image_size": list(size), "source": "test"}},
            "poses": {"top": {"R": R.tolist(), "t": t.tolist(), "rms_px": 0.5, "distance_m": 0.8}}}


def _look_down(C_table, yaw_deg=10.0):
    # camera looking straight down: x_cam = R x_table + t
    Rcw = rot_z(yaw_deg) @ np.array([[1.0, 0, 0], [0, -1.0, 0], [0, 0, -1.0]])   # cam axes in table frame
    R = Rcw.T
    return R, -R @ np.asarray(C_table, float)


def test_camera_pose_and_rectified_table(tmp_path):
    W, H = 400, 600
    K = np.array([[500.0, 0, 210], [0, 500.0, 290], [0, 0, 1]])
    R, t = _look_down([0.10, -0.05, 0.8])
    placement = {"origin_xy_m": [0.2, -0.1], "yaw_deg": 30.0, "source": "test"}
    img = np.full((H, W, 3), 40, np.uint8)
    # A marker on the table at a known simulator point.
    P_sim = np.array([0.25, 0.02, 0.0])
    Rz, o = rot_z(30.0), np.array([0.2, -0.1, 0.0])
    P_tab = Rz.T @ (P_sim - o)
    uv = K @ (R @ P_tab + t)
    u, v = uv[:2] / uv[2]
    cv2.circle(img, (int(round(u)), int(round(v))), 6, (0, 0, 255), -1)
    view = build(_session(K, R, t, (W, H)), placement, img, tmp_path, px_per_m=500, web_width=W)
    cam = view["camera"]
    Rwc, C = np.array(cam["R_wc"]), np.array(cam["C_w"])
    assert np.allclose(Rwc @ Rwc.T, np.eye(3), atol=1e-6)
    assert np.allclose(C, Rz @ np.array([0.10, -0.05, 0.8]) + o, atol=1e-6)
    # pinhole in the JSON projects the simulator point to the marker pixel
    pc = Rwc.T @ (P_sim - C)
    assert abs(cam["fx"] * pc[0] / pc[2] + cam["cx"] - u) < 0.5 and abs(cam["fy"] * pc[1] / pc[2] + cam["cy"] - v) < 0.5
    # the rectified table shows the marker at the simulator point
    tab = cv2.imread(str(tmp_path / "table.png"), cv2.IMREAD_UNCHANGED)
    tb = view["table"]
    col = int((P_sim[0] - tb["x_range"][0]) * tb["px_per_m"])
    row = int((tb["y_range"][1] - P_sim[1]) * tb["px_per_m"])
    b, g, r, a = tab[row, col]
    assert r > 200 and g < 60 and a > 200
    assert json.loads((tmp_path / "capture_view.json").read_text())["format"] == "mirror-capture-view/1"
    assert (tmp_path / "frame.jpg").exists()


def test_camera_in_sim_matches_definition():
    R, t = _look_down([0.0, 0.0, 1.0], 0.0)
    Rwc, C = camera_in_sim(R, t, (0.5, 0.0), 90.0)
    assert np.allclose(C, [0.5, 0.0, 1.0])
    assert np.allclose(Rwc[:, 2], [0, 0, -1])     # optical axis points down
