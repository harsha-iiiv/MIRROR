"""Calibrated real-camera workflow. No real footage exists yet: every input here
is SYNTHETIC (projected board corners, drawn board images, and simulator
segmentation masks standing in for tracked footage) and labelled as such."""
import math
from pathlib import Path

import numpy as np
import pytest

from mirror.perception.calibration import (BoardPlacement, BoardSpec, CalibrationRequired, CameraCalibration,
                                           Correspondence, KitConfirmation, RigCalibration, board_pose,
                                           calibrate_intrinsics, corners_from_outer, detect_board, project)

REPO = Path(__file__).resolve().parents[2]
BOARD = BoardSpec(cols=9, rows=6, square_m=0.025)


def _rot(rx, ry, rz):
    from mirror.perception.calibration import _rodrigues
    return _rodrigues(np.array([rx, ry, rz]))


def _synthetic_views(K, dist, n=12, seed=0, noise=0.2):
    """SYNTHETIC: board corners projected from random board poses in front of the camera,
    spread over the image as the capture instructions ask."""
    rng = np.random.default_rng(seed)
    obj = BOARD.points()
    ctr = obj.mean(0)
    views = []
    while len(views) < n:
        R = _rot(*rng.uniform(-0.6, 0.6, 3))
        z = rng.uniform(0.3, 0.6)
        t = np.array([rng.uniform(-0.45, 0.45) * z, rng.uniform(-0.25, 0.25) * z, z]) - R @ ctr
        uv = project(K, dist, R, t, obj)
        if (uv.min() < 5) or (uv[:, 0].max() > 955) or (uv[:, 1].max() > 535):
            continue
        views.append(uv + rng.normal(0, noise, uv.shape))
    return views


def test_zhang_recovers_synthetic_intrinsics():
    K = np.array([[650.0, 0, 482.0], [0, 652.0, 268.0], [0, 0, 1]])
    dist = [-0.12, 0.03]
    views = _synthetic_views(K, dist)
    intr = calibrate_intrinsics(views, BOARD, (960, 540))
    Kh = np.asarray(intr.K)
    assert abs(Kh[0, 0] - 650) / 650 < 0.01 and abs(Kh[1, 1] - 652) / 652 < 0.01
    assert abs(Kh[0, 2] - 482) < 4 and abs(Kh[1, 2] - 268) < 4
    assert abs(intr.dist[0] + 0.12) < 0.03
    assert intr.rms_px < 0.5 and intr.n_views == 12 and intr.source == "checkerboard"
    assert intr.problems() == []
    cv2 = pytest.importorskip("cv2")
    obj = [BOARD.points().astype(np.float32)] * len(views)
    _, Kc, dc, _, _ = cv2.calibrateCamera(obj, [v.astype(np.float32).reshape(-1, 1, 2) for v in views], (960, 540),
                                          None, None, flags=cv2.CALIB_FIX_K3 | cv2.CALIB_ZERO_TANGENT_DIST)
    assert np.allclose(Kh, Kc, atol=0.05) and np.allclose(intr.dist, dc.ravel()[:2], atol=1e-3)


def test_too_few_views_is_a_problem():
    K = np.array([[650.0, 0, 480.0], [0, 650.0, 270.0], [0, 0, 1]])
    intr = calibrate_intrinsics(_synthetic_views(K, [0, 0], n=4), BOARD, (960, 540))  # too few on purpose
    assert any("at least" in p for p in intr.problems())


def test_board_pose_on_table_and_workspace_transform():
    """SYNTHETIC: the rendered top camera looks at a board placed on the table."""
    from mirror.perception.cameras import camera_info
    cam = camera_info("top", 960, 540)
    K, R, t = np.asarray(cam.K), np.asarray(cam.R), np.asarray(cam.t)
    place = BoardPlacement(x=-0.2, y=-0.1, yaw=0.4, measured_by_user=True)
    c, s = math.cos(place.yaw), math.sin(place.yaw)
    Rwb = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])
    world = (Rwb @ BOARD.points().T).T + [place.x, place.y, 0]
    uv = project(K, [0, 0], R, t, world)
    from mirror.perception.calibration import CameraIntrinsics
    intr = CameraIntrinsics(K=K.tolist(), image_size=(960, 540), source="user_supplied")
    bp = board_pose(uv, BOARD, intr)
    assert bp.rms_px < 1e-3
    rig = RigCalibration(cameras={"top": CameraCalibration(name="top", intrinsics=intr, board_pose=bp)},
                         board=BOARD, placement=place)
    cal = rig.calibs()["top"]
    X = np.array([0.1, 0.05, 0.03])
    assert np.allclose(cal.project(X), project(K, [0, 0], R, t, X[None])[0], atol=1e-6)


def test_detects_a_drawn_board_image():
    """SYNTHETIC image: a checkerboard drawn through a homography."""
    pytest.importorskip("cv2")
    from PIL import Image, ImageDraw
    w, h = 960, 540
    s = BOARD.square_m
    outer = np.array([[260.0, 140.0], [700.0, 170.0], [690.0, 430.0], [250.0, 400.0]])
    # the board's squares extend one square beyond the inner corners
    from mirror.perception.calibration import homography
    src = np.array([[0, 0], [(BOARD.cols - 1) * s, 0], [(BOARD.cols - 1) * s, (BOARD.rows - 1) * s],
                    [0, (BOARD.rows - 1) * s]])
    H = homography(src, outer)
    im = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(im)
    for i in range(-1, BOARD.cols):
        for j in range(-1, BOARD.rows):
            if (i + j) % 2:
                continue
            q = np.array([[i * s, j * s], [(i + 1) * s, j * s], [(i + 1) * s, (j + 1) * s], [i * s, (j + 1) * s]])
            p = (H @ np.c_[q, np.ones(4)].T).T
            d.polygon([tuple(x) for x in p[:, :2] / p[:, 2:3]], fill=0)
    found = detect_board(np.asarray(im), BOARD)
    assert found is not None
    expect = corners_from_outer(outer, BOARD)
    err = np.linalg.norm(found - expect, axis=1)
    if err.mean() > 5:            # detection may start from the opposite corner
        err = np.linalg.norm(found[::-1] - expect, axis=1)
    assert err.mean() < 1.5 and err.max() < 2.5     # the drawn board is not anti-aliased


def test_rig_refuses_until_everything_is_confirmed():
    from mirror.perception.calibration import CameraIntrinsics
    intr = CameraIntrinsics(K=[[650, 0, 480], [0, 650, 270], [0, 0, 1]], image_size=(960, 540),
                            source="user_supplied")
    rig = RigCalibration(cameras={"top": CameraCalibration(name="top", intrinsics=intr)}, board=BOARD)
    probs = rig.problems()
    assert any("board pose" in p for p in probs) and any("placement" in p for p in probs)
    assert any("correspondences" in p for p in probs) and any("kit" in p for p in probs)
    rig.correspondence = [Correspondence(track="object_1", role="cup")]
    assert any("not confirmed by a person" in p for p in rig.problems())
    rig.kit = KitConfirmation(confirmed_by_user=True, measured={"cup": [0.08, 0.08, 0.15]})
    assert any("differs from the kit" in p for p in rig.problems())
    with pytest.raises(CalibrationRequired):
        rig.require_ready()


def test_camera_footage_without_calibration_is_refused():
    from mirror.perception.pipeline import trace_from_video
    with pytest.raises(CalibrationRequired):
        trace_from_video("missing_top.mp4", footage="camera")
    with pytest.raises(ValueError):
        trace_from_video("missing_top.mp4", footage="phone")


@pytest.fixture(scope="module")
def synthetic_footage():
    """SYNTHETIC stand-in for tracked camera footage: simulator segmentation masks of
    a rendered demonstration (every 5th frame), relabelled with neutral track
    names as a tracker would leave them before the correspondence review."""
    from mirror.perception import load_demo_video, tracks_from_ground_truth
    root = REPO / "results" / "eval_video2" / "inputs" / "seed71000" / "demo_eval_71000_reference"
    if not (root / "ground_truth.json.gz").exists():
        pytest.skip("rendered input not present")
    dv, gt = load_demo_video(root / "video.json")
    gt = dict(gt, frames=gt["frames"][::5])
    for k, f in enumerate(gt["frames"]):
        f["i"] = k
    tr = tracks_from_ground_truth(dv, gt)
    tr.fps, tr.n_frames = dv.fps / 5, len(gt["frames"])
    names = {"cup_0": "object_1", "block_0": "object_2", "marker_0": "object_3", "tray_0": "object_4",
             "wall_0": "object_5"}
    tr.masks = {c: {names.get(l, l): v for l, v in labs.items() if not l.startswith("table")}
                for c, labs in tr.masks.items()}
    tr.source = "simulator segmentation masks (synthetic stand-in for tracked footage)"
    return dv, gt, tr, root


def _rig(dv):
    """SYNTHETIC calibration: projected board corners for both rendered cameras."""
    from mirror.perception.calibration import CameraIntrinsics
    place = BoardPlacement(x=-0.25, y=-0.15, yaw=0.2, measured_by_user=True)
    c, s = math.cos(place.yaw), math.sin(place.yaw)
    Rwb = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])
    world = (Rwb @ BOARD.points().T).T + [place.x, place.y, 0]
    cams = {}
    rng = np.random.default_rng(3)
    for name, ci in dv.cameras.items():
        K = np.asarray(ci.K)
        views = _synthetic_views(K, [0, 0], seed=len(cams))
        intr = calibrate_intrinsics(views, BOARD, (ci.width, ci.height))
        uv = project(K, [0, 0], np.asarray(ci.R), np.asarray(ci.t), world) + rng.normal(0, 0.2, (len(world), 2))
        cams[name] = CameraCalibration(name=name, intrinsics=intr, board_pose=board_pose(uv, BOARD, intr))
    return RigCalibration(cameras=cams, board=BOARD, placement=place, undistorted=True,
                          correspondence=[Correspondence(track="object_1", role="cup"),
                                          Correspondence(track="object_2", role="block"),
                                          Correspondence(track="object_3", role="marker"),
                                          Correspondence(track="object_4", role="tray"),
                                          Correspondence(track="object_5", role="wall")],
                          kit=KitConfirmation(confirmed_by_user=True))


def test_calibrated_footage_workflow_end_to_end(synthetic_footage):
    import copy
    from mirror.perception.pipeline import trace_from_video
    dv, gt, tr, root = synthetic_footage
    rig = _rig(dv)
    with pytest.raises(CalibrationRequired):            # identities proposed but not yet confirmed
        trace_from_video(root / "top.mp4", root / "side.mp4", fps=tr.fps, tracks=copy.deepcopy(tr),
                         footage="camera", calibration=rig)
    for c in rig.correspondence:
        c.confirmed_by_user = True
    trace, est, scene, checks, _ = trace_from_video(root / "top.mp4", root / "side.mp4", fps=tr.fps,
                                                    tracks=copy.deepcopy(tr), footage="camera", calibration=rig,
                                                    return_details=True)
    ev = trace.evidence
    assert ev.kind == "camera_video_reconstruction"
    assert {c.intrinsics_source for c in ev.calibration} == {"checkerboard"}
    assert {c.extrinsics_source for c in ev.calibration} == {"calibration_board"}
    assert all(c.table_size_source == "not_used" for c in ev.calibration)
    assert "calibration_assumed" not in {r.code for r in ev.review}
    assert sum(1 for m in ev.manual_corrections if m.what == "identity") == 5
    assert set(ev.media) == {"top", "side"}
    # positions in the workspace frame agree with the simulator (first frame)
    for oid in ("cup_0", "block_0", "marker_0"):
        true = np.asarray(gt["frames"][0]["objects"][oid]["pos"][:2])
        got = np.asarray(trace.frames[0].objects[oid].pos[:2])
        assert np.linalg.norm(got - true) < 0.02, oid
