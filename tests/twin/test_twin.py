"""Twin: camera chain, placement fit, time map, plate and colour helpers, pipeline discovery."""
from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from mirror.twin.camera import PhoneCamera, Placement, VirtualCamera  # noqa: E402

K = [[830.8, 0.0, 262.9], [0.0, 829.9, 469.5], [0.0, 0.0, 1.0]]
DIST = [0.118, -0.139, -0.005, -0.0027, -0.836]          # D1-like strong lens (540x960 scale)


def _pose():
    # camera ~0.88 m above the table, looking down and slightly forward (D1-like)
    R = np.array([[0.2415, 0.9651, 0.1013], [0.8778, -0.1727, -0.4469], [-0.4138, 0.1969, -0.8888]])
    U, _, Vt = np.linalg.svd(R)
    return U @ Vt, np.array([0.141, 0.261, 0.859])


def test_placement_roundtrip():
    pl = Placement((0.31, 0.05), 23.0)
    X = np.array([[0.1, -0.2, 0.03], [-0.4, 0.07, 0.0]])
    assert np.allclose(pl.sim_to_table(pl.table_to_sim(X)), X)


def test_phone_camera_matches_capture_projection():
    """Projecting a table point through the capture pose equals projecting its simulator position."""
    from mirror.capture.geometry import project
    R, t = _pose()
    pl = Placement((0.31, 0.046), 15.0)
    cam = PhoneCamera.from_capture(K, DIST, (540, 960), R, t, pl)
    Xt = np.array([[-0.27, 0.07, 0.0], [-0.1, -0.24, 0.05], [0.0, 0.0, 0.0]])
    a = project(K, DIST, R, t, Xt)
    b = cam.project(pl.table_to_sim(Xt))
    assert np.abs(a - b).max() < 1e-6


def test_rescale_keeps_rays():
    R, t = _pose()
    pl = Placement((0.0, 0.0), 0.0)
    big = PhoneCamera.from_capture(np.array(K) * [[2, 1, 2], [1, 2, 2], [1, 1, 1]], DIST, (1080, 1920), R, t, pl)
    small = PhoneCamera.from_capture(np.array(K) * [[2, 1, 2], [1, 2, 2], [1, 1, 1]], DIST, (1080, 1920), R, t, pl, out_size=(540, 960))
    X = np.array([[-0.2, 0.05, 0.0]])
    # pixel-centre convention: u_small = (u_big + 0.5) / 2 - 0.5
    assert np.allclose((big.project(X) + 0.5) / 2 - 0.5, small.project(X), atol=0.05)


def test_remap_tables_point_where_the_lens_sees():
    """For pixels across the image, the MuJoCo pinhole pixel the remap reads from is the projection of
    the same ray (no distortion); invalid far-corner pixels are flagged, not wrong."""
    R, t = _pose()
    cam = PhoneCamera.from_capture(K, DIST, (540, 960), R, t, Placement((0.0, 0.0), 0.0))
    v = cam.mujoco_view()
    assert v.valid.mean() > 0.98
    f = (v.height / 2.0) / math.tan(math.radians(v.fovy) / 2)
    for (u, w) in [(270, 480), (40, 60), (500, 900), (100, 850)]:
        if not v.valid[w, u]:
            continue
        n = cv2.undistortPoints(np.array([[[u, w]]], float), np.array(K), np.array(DIST)).reshape(2)
        assert abs(v.map_x[w, u] - (v.width / 2 + f * n[0] - 0.5)) < 0.05
        assert abs(v.map_y[w, u] - (v.height / 2 + f * n[1] - 0.5)) < 0.05


def test_top_down_homography_maps_table_points():
    R, t = _pose()
    cam = PhoneCamera.from_capture(K, [0, 0, 0, 0, 0], (540, 960), R, t, Placement((0.0, 0.0), 0.0))
    vc = VirtualCamera.top_down((-0.2, 0.0), (0.6, 0.8), 600.0, up_xy=(1.0, 0.0))
    H = vc.plane_homography_from(cam, z=0.0)
    for X in ([-0.2, 0.0, 0.0], [-0.35, 0.12, 0.0], [0.0, -0.2, 0.0]):
        a = vc.project([X])[0]
        b = cam.project([X])[0]
        q = H @ np.array([a[0], a[1], 1.0])
        assert np.allclose(q[:2] / q[2], b, atol=1e-6)
    # straight down: image right is sim -y when image up is sim +x
    p0, p1 = vc.project([[-0.2, 0.0, 0.0], [-0.2, -0.1, 0.0]])
    assert p1[0] > p0[0] and abs(p1[1] - p0[1]) < 1e-6


def test_fit_placement_recovers_rigid_transform():
    from mirror.twin.build import fit_placement
    pl = Placement((0.3, -0.1), 30.0)
    A = np.array([[-0.4, -0.25, 0.06], [-0.23, -0.27, 0.05], [-0.1, -0.24, 0.0], [-0.27, 0.07, 0.0]])
    fit, rms = fit_placement(A, pl.table_to_sim(A))
    assert rms < 1e-9 and abs(fit.yaw_deg - 30.0) < 1e-6 and np.allclose(fit.origin_xy, (0.3, -0.1))


def test_placement_from_accepts_pipeline_shapes():
    from mirror.twin.build import placement_from
    assert placement_from({"origin_xy_m": [0.1, 0.2], "yaw_deg": 5}).yaw_deg == 5
    assert placement_from({"placement": {"origin_xy_m": [0.1, 0.2], "yaw_deg": 0}}).origin_xy == (0.1, 0.2)
    c, s = math.cos(0.3), math.sin(0.3)
    p = placement_from({"R": [[c, -s, 0], [s, c, 0], [0, 0, 1]], "t": [0.4, 0.5, 0.0]})
    assert abs(math.radians(p.yaw_deg) - 0.3) < 1e-9 and p.origin_xy == (0.4, 0.5)


def _ev(obj, typ, t, placed_in=None):
    return SimpleNamespace(object=obj, type=typ, t=t, t_lo=t - 0.03, t_hi=t + 0.03, placed_in=placed_in)


def _frames(phases):
    from mirror.contracts import RolloutFrame
    out, t = [], 0.0
    for ph, dur in phases:
        for _ in range(int(dur / 0.5)):
            out.append(RolloutFrame.model_construct(t=t, bodies={}, gripper_width=0.0, phase=ph))
            t += 0.5
    return out


def test_time_map_anchors_and_rates():
    from mirror.twin.build import build_time_map
    frames = _frames([("move:cup_0", 6), ("grasp:cup_0", 1), ("transport:cup_0", 10), ("release:cup_0", 2),
                      ("move:block_0", 8), ("grasp:block_0", 1), ("transport:block_0", 8), ("release:block_0", 2), ("settle", 2)])
    sess = SimpleNamespace(events=[_ev("mug", "pick", 12.4), _ev("mug", "place", 13.7, "table"),   # a nudge, not the carry
                                   _ev("mug", "pick", 14.5), _ev("mug", "place", 16.8, "tray"),
                                   _ev("red_box", "pick", 21.0), _ev("red_box", "place", 23.7, "tray")])
    tm = build_time_map(frames, sess, {"cup_0": "mug", "block_0": "red_box"}, video_dur=50.0)
    assert [round(a[1], 1) for a in tm.anchors] == [14.5, 16.8, 21.0, 23.7]
    assert 1.0 <= tm.speed <= 4.0
    for s_, T, _ in tm.anchors:
        assert abs(tm.real(s_) - T) < 1e-9
    ss = np.linspace(0, frames[-1].t, 200)
    Ts = [tm.real(x) for x in ss]
    assert all(b >= a - 1e-9 for a, b in zip(Ts, Ts[1:]))           # never runs backwards
    assert abs(tm.real_rate(1.0) - 1.0) < 1e-6                     # real time before the first anchor


def test_time_map_drops_out_of_order_anchors():
    from mirror.twin.build import build_time_map
    frames = _frames([("grasp:cup_0", 1), ("release:cup_0", 1), ("grasp:block_0", 1), ("release:block_0", 1)])
    sess = SimpleNamespace(events=[_ev("red_box", "pick", 5.0), _ev("red_box", "place", 6.0, "tray"),
                                   _ev("mug", "pick", 8.0), _ev("mug", "place", 9.0, "tray")])
    tm = build_time_map(frames, sess, {"cup_0": "mug", "block_0": "red_box"}, video_dur=20.0, speed=2.0)
    # robot did cup then block; you did box then mug: only a consistent prefix is kept
    Ts = [a[1] for a in tm.anchors]
    assert Ts == sorted(Ts) and len(tm.anchors) == 2


def test_mean_colour_takes_dominant_colour():
    from mirror.twin.plate import mean_colour
    img = np.zeros((60, 60, 3), np.uint8)
    img[:, :] = (200, 60, 40)            # red box
    img[20:30, 20:40] = (250, 250, 250)  # white label
    m = np.ones((60, 60), bool)
    c = mean_colour(img, m)
    assert abs(c[0] - 200 / 255) < 0.02 and abs(c[1] - 60 / 255) < 0.02


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg")
def test_clean_plate_and_stream(tmp_path):
    from mirror.twin.build import Mp4
    from mirror.twin.plate import FrameStream, choose_pair, clean_plate
    W, H = 64, 112
    bg = np.full((H, W, 3), 90, np.uint8)
    bg[:, : W // 2] = 60
    path = tmp_path / "take.mp4"
    w = Mp4(path, (W, H), fps=10, crf=1)
    for k in range(40):                  # 4 s: object at (20,30) for 1.5 s, a "hand" at 1.6-2.4 s, object gone after
        f = bg.copy()
        if k < 15:
            f[25:40, 15:30] = (220, 40, 30)
        if 16 <= k <= 24:
            f[60:100, 30:60] = (180, 140, 120)
        f[2:6, 2 + k: 6 + k] = 255 if k > 30 else f[2:6, 2 + k: 6 + k]   # late flicker away from the object
        w.write(f)
    w.close()
    fs = FrameStream(path, (W, H), fps=10)
    assert int(fs.at(0.0)[30, 20, 0]) > 180 and int(fs.at(2.0)[80, 40, 0]) > 150 and int(fs.at(3.0)[30, 20, 0]) < 120
    fs.close()
    mask = np.zeros((H, W), bool)
    mask[25:40, 15:30] = True
    small = cv2.resize(mask.astype(np.uint8), (135, 240), interpolation=cv2.INTER_NEAREST) > 0
    ts, te, agree = choose_pair(path, (0.0, 1.4), (2.6, 3.9), small, step=0.1)
    assert ts < 1.5 and te > 2.5
    plate = clean_plate(path, ts, te, {"box": mask}, (W, H), dilate_px=2)
    assert abs(int(plate.image[32, 22, 0]) - 60) < 12          # the object is gone: table shows through
    assert abs(int(plate.image[80, 40, 0]) - 90) < 12          # no hand in the plate


def test_pipeline_discovery(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("twin_render", Path(__file__).resolve().parents[2] / "scripts" / "twin_render.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    d = tmp_path / "out"
    (d / "run").mkdir(parents=True)
    (d / "corrections" / "mug_clearance").mkdir(parents=True)
    for p in (d / "session.json", d / "run" / "rollout.json", d / "tracks_full.json.gz",
              d / "corrections" / "mug_clearance" / "rollout.json", d / "take.MOV"):
        p.write_text("{}")
    (d / "table_to_sim.json").write_text(json.dumps({"origin_xy_m": [0.1, 0.0], "yaw_deg": 0.0}))
    (d / "corrections" / "mug_clearance" / "correction.json").write_text(json.dumps({"text": "keep 6 cm"}))
    (d / "manifest.json").write_text(json.dumps({"take": "take.MOV"}))
    args = SimpleNamespace(video=None, correction=None)
    cfg = mod.from_pipeline(d, args)
    assert cfg["session"] == d / "session.json" and cfg["video"] == d / "take.MOV"
    assert [r[0] for r in cfg["rollouts"]] == ["before", "after"]
    assert cfg["placement"]["origin_xy_m"] == [0.1, 0.0] and cfg["correction"]["text"] == "keep 6 cm"
    assert cfg["out"] == d / "twin"
