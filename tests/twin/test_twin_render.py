"""The rendered object lands where the calibrated lens model says it should (whole camera chain)."""
from __future__ import annotations

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
pytest.importorskip("mujoco")

from mirror.contracts import Pose, RolloutFrame, VisualBody, VisualGeom, VisualScene  # noqa: E402
from mirror.twin.camera import PhoneCamera, Placement  # noqa: E402

K = np.array([[415.4, 0.0, 131.2], [0.0, 414.9, 234.5], [0.0, 0.0, 1.0]])     # 270x480
DIST = [0.118, -0.139, -0.005, -0.0027, -0.836]


def _scene():
    g = lambda **k: VisualGeom(quat=(1, 0, 0, 0), pos=(0, 0, 0), **k)
    return VisualScene(bodies=[
        VisualBody(name="world", static=True, geoms=[g(type="box", size=(0.6, 0.5, 0.02), rgba=(0.8, 0.7, 0.6, 1), name="table")
                                                     .model_copy(update={"pos": (0, 0, -0.02)})]),
        VisualBody(name="block_0", static=False, geoms=[g(type="sphere", size=(0.015,), rgba=(0.9, 0.2, 0.2, 1), name="block_0/box")]),
    ])


@pytest.mark.parametrize("target", [(-0.10, 0.05, 0.03), (0.12, -0.18, 0.05), (-0.25, -0.2, 0.015)])
def test_rendered_centroid_matches_projection(target):
    from mirror.twin.render import OBJECT, TwinRenderer
    R = np.array([[0.2415, 0.9651, 0.1013], [0.8778, -0.1727, -0.4469], [-0.4138, 0.1969, -0.8888]])
    U, _, Vt = np.linalg.svd(R)
    R = U @ Vt
    pl = Placement((0.31, 0.046), 0.0)
    cam = PhoneCamera.from_capture(K, DIST, (270, 480), R, [0.141, 0.261, 0.859], pl)
    tr = TwinRenderer(_scene(), cam, shadows=False, samples=0)
    try:
        f = RolloutFrame(t=0.0, bodies={"block_0": Pose(pos=target, quat=(1, 0, 0, 0))}, gripper_width=0.0)
        _, cat, _ = tr.render(f)
        ys, xs = np.nonzero(cat == OBJECT)
        assert len(xs) > 10
        uv = cam.project([target])[0]
        assert abs(xs.mean() - uv[0]) < 1.0 and abs(ys.mean() - uv[1]) < 1.0
        plate = np.full((480, 270, 3), 40, np.uint8)
        out, info = tr.composite(plate, f)
        assert info["fg_px"] == len(xs)
        assert out[int(round(uv[1])), int(round(uv[0])), 0] > 120          # the red sphere is drawn over the plate
        assert int(out[5, 5, 0]) == 40                                      # elsewhere the real pixels are untouched
    finally:
        tr.close()


def test_occluder_hides_far_side_only():
    from mirror.twin.render import Occluder, TwinRenderer
    R = np.array([[1.0, 0, 0], [0, -1.0, 0], [0, 0, -1.0]])                # straight down, 0.8 m up, at x = 0.1
    C = np.array([0.1, 0.0, 0.8])
    cam = PhoneCamera.from_capture(K, [0, 0, 0, 0, 0], (270, 480), R, -R @ C, Placement((0.0, 0.0), 0.0))
    tr = TwinRenderer(_scene(), cam, shadows=False, samples=0)
    try:
        plate = np.full((480, 270, 3), 40, np.uint8)
        full = np.ones((480, 270), bool)
        # a wall on the plane x = 0; the camera is on the x > 0 side
        oc = Occluder(mask=full, point=np.array([0.0, 0.0, 0.0]), normal=np.array([1.0, 0.0, 0.0]))
        near = RolloutFrame(t=0.0, bodies={"block_0": Pose(pos=(0.05, 0.0, 0.03), quat=(1, 0, 0, 0))}, gripper_width=0.0)
        far = RolloutFrame(t=0.0, bodies={"block_0": Pose(pos=(-0.05, 0.0, 0.03), quat=(1, 0, 0, 0))}, gripper_width=0.0)
        _, i_near = tr.composite(plate, near, occluders=[oc])
        _, i_far = tr.composite(plate, far, occluders=[oc])
        assert i_near["hidden_px"] == 0 and i_far["hidden_px"] == i_far["fg_px"] > 0
    finally:
        tr.close()
