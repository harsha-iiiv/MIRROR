"""Calibration on SYNTHETIC renders of the printed target (exact ground truth)."""
import numpy as np
import pytest

from mirror.capture import calibrate as C
from mirror.capture import synthetic as S
from mirror.capture.media import CaptureFingerprint
from mirror.capture.target import DEFAULT_TARGET as SPEC

W, H = 800, 450
DIST = (0.07, -0.13, 0.0008, -0.0004, 0.0)
PC = C.PrintCheck(measured_bar_mm=50.0, measured_square_mm=30.0, measured_by="synthetic_test")
BOARD = S.board_prim(SPEC, origin=(-0.075, 0.105))


def _views(cam, n, seed=0):
    rng = np.random.default_rng(seed)
    dets = []
    for k in range(n):
        az = rng.uniform(0, 2 * np.pi)
        el = rng.uniform(40, 88)
        r = rng.uniform(0.32, 0.5)
        tgt = np.array([rng.uniform(-0.05, 0.05), rng.uniform(-0.06, 0.06), 0])
        eye = tgt + r * np.array([np.cos(np.radians(el)) * np.cos(az), np.cos(np.radians(el)) * np.sin(az), np.sin(np.radians(el))])
        c = S.SynthCamera.make("x", W, H, 60, eye, tgt, roll_deg=rng.uniform(-40, 40))
        c.K, c.dist = cam.K.copy(), cam.dist.copy()
        shift = np.array([rng.uniform(-0.3, 0.3) * W, rng.uniform(-0.3, 0.3) * H])
        img = S._shifted_render(c, BOARD, shift, k)
        dets.append(C.detect(img, SPEC))
    return dets


@pytest.fixture(scope="module")
def cam():
    return S.SynthCamera.make("top", W, H, 66, (0.0, -0.3, 0.6), (0.0, 0.0, 0.0), dist=DIST)


@pytest.fixture(scope="module")
def intr(cam):
    return C.calibrate_intrinsics(_views(cam, 18), SPEC, PC, CaptureFingerprint(width=W, height=H))


def test_intrinsics_recovered_and_accepted(cam, intr):
    assert intr.accepted, [c for c in intr.checks if c.passed is False]
    assert abs(intr.Km[0, 0] / cam.K[0, 0] - 1) < 0.01
    assert abs(intr.Km[1, 1] / cam.K[1, 1] - 1) < 0.01
    assert np.hypot(*(intr.Km[:2, 2] - cam.K[:2, 2])) < 6.0
    assert intr.rms_px < 0.5
    names = {c.name for c in intr.checks}
    assert {"intr_views", "intr_rms_px", "intr_coverage", "intr_tilted_views", "intr_focal_rel_std"} <= names


def test_too_few_views_rejected(cam):
    bad = C.calibrate_intrinsics(_views(cam, 4, seed=3), SPEC, PC)
    assert not bad.accepted
    assert any(c.name == "intr_views" and c.passed is False for c in bad.checks)


def test_bad_print_scale_rejected(cam):
    pc = C.PrintCheck(measured_bar_mm=47.0, measured_square_mm=28.2, measured_by="synthetic_test")
    bad = C.calibrate_intrinsics(_views(cam, 12, seed=4), SPEC, pc)
    assert not bad.accepted
    assert any(c.name == "print_scale_bar_mm" and c.passed is False for c in bad.checks)


def test_misread_square_caught():
    pc = C.PrintCheck(measured_bar_mm=50.0, measured_square_mm=33.0, measured_by="synthetic_test")
    chk = {c.name: c for c in pc.checks(SPEC)}
    assert chk["print_scale_bar_mm"].passed and chk["print_square_consistency_rel"].passed is False


def test_table_pose_and_metric_scale(cam, intr):
    img = S.render(cam, [BOARD], 0.0)
    pose = C.table_pose(C.detect(img, SPEC), intr, SPEC, PC)
    assert pose.accepted, [c for c in pose.checks if c.passed is False]
    true_centre = -cam.R.T @ cam.t - np.array([-0.075, 0.105, 0.0])
    assert np.linalg.norm(pose.centre - true_centre) < 0.015
    assert pose.holdout_mm < 2.0
    # metric scale follows the measured square: a 3 % larger print moves the camera 3 % further away
    pc2 = C.PrintCheck(measured_bar_mm=51.5, measured_square_mm=30.9, measured_by="synthetic_test")
    pose2 = C.table_pose(C.detect(img, SPEC), intr, SPEC, pc2)
    assert abs(pose2.distance_m / pose.distance_m - 1.03) < 0.005


def test_table_frame_is_z_up(cam, intr):
    img = S.render(cam, [BOARD], 0.0)
    pose = C.table_pose(C.detect(img, SPEC), intr, SPEC, PC)
    assert pose.centre[2] > 0.4      # camera above the table


def test_board_missing_rejects_pose(cam, intr):
    img = S.render(cam, [], 0.0)
    pose = C.table_pose(C.detect(img, SPEC), intr, SPEC, PC)
    assert not pose.accepted


def test_recalibration_after_resolution_change(intr, cam):
    img = S.render(cam, [BOARD], 0.0)
    det = C.detect(img, SPEC)
    reasons, _ = C.recalibration_reasons(intr, CaptureFingerprint(width=1920, height=1080), det, SPEC, PC)
    assert any("resolution" in r for r in reasons)


def test_recalibration_after_zoom_seen_from_an_oblique_view(intr, cam):
    """Zoom changed after calibration: an oblique board view re-estimates focal length."""
    side = S.SynthCamera.make("side", W, H, 66, (0.0, -0.45, 0.32), (0.0, 0.0, 0.0), dist=DIST)
    side.K = cam.K.copy()
    zoomed = S.SynthCamera(side.name, W, H, side.K.copy(), side.dist, side.R, side.t)
    zoomed.K[0, 0] *= 1.15
    zoomed.K[1, 1] *= 1.15
    same = C.detect(S.render(side, [BOARD], 0.0), SPEC)
    zoom = C.detect(S.render(zoomed, [BOARD], 0.0), SPEC)
    fp = CaptureFingerprint(width=W, height=H)
    r_same, chk_same = C.recalibration_reasons(intr, fp, same, SPEC, PC)
    r_zoom, chk_zoom = C.recalibration_reasons(intr, fp, zoom, SPEC, PC)
    assert not r_same, r_same
    assert any("zoom" in r or "lens" in r for r in r_zoom), r_zoom


def test_face_on_view_reports_zoom_check_not_run(intr, cam):
    top = S.SynthCamera.make("top", W, H, 66, (0.0, -0.02, 0.6), (0.0, 0.0, 0.0), dist=DIST)
    top.K = cam.K.copy()
    det = C.detect(S.render(top, [BOARD], 0.0), SPEC)
    _, checks = C.recalibration_reasons(intr, CaptureFingerprint(width=W, height=H), det, SPEC, PC)
    focal = next(c for c in checks if c.name == "recal_focal_change_rel")
    assert focal.passed is None and "cannot be checked" in focal.note


def test_lens_metadata_change_requires_recalibration(intr, cam):
    a = CaptureFingerprint(width=W, height=H, model="Phone X", lens_tags={"com.apple.quicktime.camera.focal_length.35mm_equivalent": "26"})
    b = a.model_copy(update={"lens_tags": {"com.apple.quicktime.camera.focal_length.35mm_equivalent": "52"}})
    assert any("zoom" in d for d in a.differences(b))


def test_camera_shift_detected():
    rng = np.random.default_rng(0)
    import cv2
    base = cv2.GaussianBlur((rng.random((360, 640)) * 255).astype(np.uint8), (7, 7), 0)
    shifted = np.roll(base, (4, 5), axis=(0, 1))
    assert C.camera_shift_px(base, base) < 0.5
    assert abs(C.camera_shift_px(base, shifted) - np.hypot(4, 5)) < 1.0


def test_user_supplied_intrinsics_are_labelled_not_measured():
    u = C.user_supplied_intrinsics(np.eye(3), [0, 0], (W, H))
    assert u.source == "user_supplied"
    assert any(c.passed is None for c in u.checks)
