"""Perception: rendered videos and ground truth, camera geometry, calibration
and reconstruction (oracle masks; no model calls)."""
import json

import numpy as np
import pytest

from mirror.perception import (camera_info, load_demo_video, mask_iou, perception_metrics, project, render_demo_video,
                               rle_decode, rle_encode, tracks_from_ground_truth)
from mirror.perception.reconstruct import calibrate, estimate, scene_from_video, trace_from_estimates


def _gl_available():
    try:
        import mujoco
        m = mujoco.MjModel.from_xml_string("<mujoco><worldbody><geom size='.1'/></worldbody></mujoco>")
        r = mujoco.Renderer(m, 8, 8)
        r.close()
        return True
    except Exception:
        return False


needs_gl = pytest.mark.skipif(not _gl_available(), reason="no off-screen OpenGL backend")


def test_rle_roundtrip():
    rng = np.random.default_rng(0)
    for shape in [(5, 7), (54, 96)]:
        m = rng.random(shape) > 0.6
        assert np.array_equal(rle_decode(rle_encode(m)), m)
    m = np.ones((4, 4), bool)
    assert np.array_equal(rle_decode(rle_encode(m)), m)
    assert mask_iou(m, m) == 1.0


def test_camera_projection_convention():
    cam = camera_info("top", 960, 540)
    u = project(cam, [cam.lookat])[0]
    assert np.allclose(u, [480, 270], atol=1e-6)
    # +x world appears to the right in the top view
    ux = project(cam, [(0.1, 0.0, 0.0)])[0]
    assert ux[0] > 480


@pytest.fixture(scope="module")
def short_video(tmp_path_factory):
    if not _gl_available():
        pytest.skip("no off-screen OpenGL backend")
    out = tmp_path_factory.mktemp("pv")
    man, dv = render_demo_video(9001, out, max_seconds=12.0, width=480, height=270)
    return man


@needs_gl
def test_video_files_and_ground_truth(short_video):
    dv, gt = load_demo_video(short_video)
    root = short_video.parent
    assert (root / dv.videos["top"]).stat().st_size > 1000 and (root / dv.videos["side"]).stat().st_size > 1000
    assert dv.n_frames == len(gt["frames"]) == 181
    assert {"cup_0", "block_0", "marker_0", "tray_0", "wall_0", "gripper", "table"} <= set(dv.labels)
    f = gt["frames"][0]
    for c in ("top", "side"):
        cam = dv.cameras[c]
        for oid in ("cup_0", "block_0"):
            u = project(cam, [f["objects"][oid]["pos"]])[0]
            cx, cy = f["masks"][c][oid]["centroid"]
            assert abs(u[0] - cx) < 8 and abs(u[1] - cy) < 8   # projection matches segmentation
    assert any(e["type"] == "grasp" and e["object_id"] == "cup_0" for e in gt["events"])
    trace = json.loads((root / dv.trace).read_text())
    assert trace["source"] == "teleop_scripted"


@needs_gl
def test_calibration_recovers_cameras(short_video):
    dv, gt = load_demo_video(short_video)
    for c in ("top", "side"):
        m = rle_decode(gt["frames"][0]["masks"][c]["table"]["rle"])
        from scipy import ndimage
        cal = calibrate(ndimage.binary_fill_holes(m), np.array(dv.cameras[c].K))
        assert np.linalg.norm(cal.center - np.array(dv.cameras[c].position)) < 0.05


@needs_gl
def test_oracle_reconstruction_accuracy(short_video):
    dv, gt = load_demo_video(short_video)
    tr = tracks_from_ground_truth(dv, gt)
    est = estimate(tr, {c: ci.K for c, ci in dv.cameras.items()}, tilt_every=5)
    m = perception_metrics(est, gt)
    assert m["summary"]["found_rate"] > 0.95
    assert m["summary"]["centroid_err_cm"] < 2.0
    grasp = [e for e in m["events"]["matched"] if e["type"] == "grasp" and e["object"] == "cup_0"]
    assert grasp and abs(grasp[0]["err_s"]) < 1.0
    scene, checks = scene_from_video(tr, est, scene_id="t")
    assert {o.kind for o in scene.objects} == {"cup", "block", "marker"}
    assert scene.trays and scene.obstacles and scene.obstacles[0].kind == "wall"
    true = json.loads((short_video.parent / dv.scene).read_text())
    tp = np.array(true["trays"][0]["pose"]["pos"][:2])
    assert np.linalg.norm(np.array(scene.trays[0].pose.pos[:2]) - tp) < 0.03
    trace = trace_from_estimates(tr, est, scene, demo_id="t")
    assert trace.source == "video_grounded" and len(trace.frames) == dv.n_frames
