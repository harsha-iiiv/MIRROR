import json
import os
import shutil

import numpy as np
import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: long-running test (deselect with -m 'not slow')")


@pytest.fixture(scope="session")
def ffmpeg():
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe required")
    return True


def truth_camera_models(cams: dict, origin=(-0.075, 0.105)):
    """Exact camera models in the table frame from SYNTHETIC cameras (tests only)."""
    from mirror.capture.calibrate import TablePose, Intrinsics
    from mirror.capture.reconstruct import CameraModel
    o = np.array([origin[0], origin[1], 0.0])
    out = {}
    for n, c in cams.items():
        intr = Intrinsics(K=c.K.tolist(), dist=c.dist.tolist(), image_size=(c.width, c.height), source="synthetic_ground_truth",
                          accepted=True)
        t_tab = c.R @ o + c.t
        centre = -c.R.T @ t_tab
        pose = TablePose(R=c.R.tolist(), t=t_tab.tolist(), rms_px=0.0, n_corners=24, view_angle_deg=30.0,
                         distance_m=float(np.linalg.norm(centre)), centre_sigma_mm=0.0, accepted=True)
        out[n] = CameraModel(name=n, intrinsics=intr, pose=pose)
    return out
