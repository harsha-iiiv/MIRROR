"""Video reconstruction keeps unknown values unknown (recorded SAM tracks from the
frozen video benchmark; no model calls, no rendering)."""
import json
from pathlib import Path

import numpy as np
import pytest

from mirror.interpret import summarize_demo
from mirror.observability import status_at

REPO = Path(__file__).resolve().parents[2]
SEED = 71000
ROOT = REPO / "results" / "eval_video2" / "inputs" / f"seed{SEED}" / f"demo_eval_{SEED}_reference"
TRACKS = REPO / "results" / "eval_video2" / "tracks" / f"{SEED}.json.gz"

pytestmark = pytest.mark.skipif(not TRACKS.exists(), reason="recorded tracks not present")


def _load(cams=None):
    from mirror.perception import load_demo_video
    from mirror.perception.track import load_tracks
    dv, gt = load_demo_video(ROOT / "video.json")
    tr = load_tracks(TRACKS)
    if cams:
        tr.masks = {c: v for c, v in tr.masks.items() if c in cams}
    return dv, gt, tr


def test_top_only_video_reports_tilt_unobserved():
    from mirror.perception.reconstruct import estimate, scene_from_video, trace_from_estimates
    dv, gt, tr = _load(["top"])
    est = estimate(tr, {"top": dv.cameras["top"].K}, intrinsics_source="rendered_camera_model")
    scene, _ = scene_from_video(tr, est, scene_id="t")
    trace = trace_from_estimates(tr, est, scene, demo_id="t")
    ev = trace.evidence
    assert ev.kind == "rendered_video_reconstruction"
    cup = next(o.id for o in scene.objects if o.kind == "cup")
    g = next(e.t for e in trace.events if e.type == "grasp" and e.object_id == cup)
    r = next(e.t for e in trace.events if e.type == "release" and e.object_id == cup)
    carried = [k for k, f in enumerate(trace.frames) if g <= f.t <= r]
    assert carried and all(status_at(ev.objects[cup].tilt, k) == "unobserved" for k in carried)
    assert any(x.code == "single_view" for x in ev.review)
    s = summarize_demo(trace)
    c = next(o for o in s.objects if o.kind == "cup")
    assert c.carry_max_tilt_deg is None
    assert not [x for x in s.regularities if x.kind == "carry_upright"]
    assert "tilt_unobserved" in {x["code"] for x in s.review}


def test_occluded_side_view_keeps_carry_tilt_unknown():
    """Two cameras, but the side view loses the cup for most of the carry (seed 71000):
    the summary must not state how upright it stayed."""
    from mirror.perception.reconstruct import estimate, scene_from_video, trace_from_estimates
    dv, gt, tr = _load()
    est = estimate(tr, {c: ci.K for c, ci in dv.cameras.items()}, intrinsics_source="rendered_camera_model",
                   tilt_every=1)
    scene, _ = scene_from_video(tr, est, scene_id="t")
    trace = trace_from_estimates(tr, est, scene, demo_id="t")
    s = summarize_demo(trace)
    c = next(o for o in s.objects if o.kind == "cup")
    ob = c.observability["tilt"]
    assert 0 < ob["carry_observed_frames"] < 0.8 * ob["carry_frames"]
    assert c.carry_max_tilt_deg is None
    assert not [x for x in s.regularities if x.kind == "carry_upright"]


def test_unspecified_footage_abstains():
    from mirror.perception.reconstruct import estimate, scene_from_video, trace_from_estimates
    dv, gt, tr = _load(["top"])
    est = estimate(tr, {"top": dv.cameras["top"].K}, tilt_every=10 ** 6)
    scene, _ = scene_from_video(tr, est, scene_id="t")
    trace = trace_from_estimates(tr, est, scene, demo_id="t")
    assert trace.evidence.kind == "unverified_video_reconstruction"
    assert "calibration_assumed" in {x.code for x in trace.evidence.review if x.severity == "abstain"}
    assert all(c.intrinsics_source == "assumed_rendered_model" for c in trace.evidence.calibration)


def test_previous_event_timing_reproduces_the_frozen_benchmark_row():
    """event_timing="motion" with the recorded tracks gives the timing and tracking
    numbers stored by the frozen video benchmark for this seed."""
    from mirror.evaluate.bench_video import (EVENT_MATCH_S, est_events, est_frames, frame_errors, gt_events,
                                             gt_frames, match_events)
    from mirror.perception.reconstruct import estimate
    dv, gt, tr = _load()
    est = estimate(tr, {c: ci.K for c, ci in dv.cameras.items()}, tilt_every=10 ** 6, event_timing="motion")
    row = json.loads((REPO / "results" / "eval_video2" / "seeds" / f"{SEED}.json").read_text())
    timing = match_events(gt_events(gt), est_events(est), EVENT_MATCH_S)
    assert timing["tp"] == row["timing"]["tp"] and timing["n_est"] == row["timing"]["n_est"]
    assert np.allclose(sorted(timing["abs_err_s"]), sorted(row["timing"]["abs_err_s"]))
    fe = frame_errors(gt_frames(gt), est_frames(est, est.scene_checks["kinds"], gt_fps=dv.fps))
    assert fe["found_rate_all"] == pytest.approx(row["tracking"]["found_rate_all"])
    assert fe["centroid_cm_all"]["median"] == pytest.approx(row["tracking"]["centroid_cm_all"]["median"])


def test_model_outputs_read_from_saved_tracks_are_recordings():
    from mirror.perception.reconstruct import Tracks, models_from_tracks
    _, _, tr = _load(["top"])
    m = models_from_tracks(tr)
    assert {x["role"] for x in m} == {"grounding", "segmentation"}
    assert all(x["origin"] == "recorded_replay" for x in m)
    assert any(x["model"] == "sam-3.1" for x in m)
    oracle = Tracks(fps=5.0, n_frames=1, size={}, masks={}, source="ground-truth masks (oracle)")
    assert models_from_tracks(oracle)[0]["origin"] == "oracle_ground_truth"


def test_failed_calibration_abstains_instead_of_placing_objects():
    """Seed 71010: grounding boxed the wall over the whole top image; the table fit is
    off and every position was more than 5 cm wrong (found rate 0 in the frozen run).
    Reconstruction must say so rather than hand on a confident trace."""
    from mirror.perception import load_demo_video
    from mirror.perception.reconstruct import estimate
    from mirror.perception.track import load_tracks
    p = REPO / "results" / "eval_video2" / "tracks" / "71010.json.gz"
    if not p.exists():
        pytest.skip("recorded tracks not present")
    root = REPO / "results" / "eval_video2" / "inputs" / "seed71010" / "demo_eval_71010_reference"
    dv, _ = load_demo_video(root / "video.json")
    est = estimate(load_tracks(p), {c: ci.K for c, ci in dv.cameras.items()}, tilt_every=10 ** 6,
                   intrinsics_source="rendered_camera_model")
    codes = {r["code"] for r in est.review if r["severity"] == "abstain"}
    assert {"calibration_invalid", "scale_mismatch"} <= codes


def test_object_centric_events_never_claim_robot_contact():
    """SYNTHETIC stand-in for human-hand footage: recorded tracks of a rendered demo with
    the gripper ignored (actor="human_hand")."""
    from mirror.perception.reconstruct import estimate, scene_from_video, trace_from_estimates
    dv, gt, tr = _load(["top"])
    est = estimate(tr, {"top": dv.cameras["top"].K}, intrinsics_source="rendered_camera_model", actor="human_hand")
    assert np.isnan(est.gripper).all()
    scene, _ = scene_from_video(tr, est, scene_id="t")
    trace = trace_from_estimates(tr, est, scene, demo_id="t")
    assert trace.events, "carry intervals are still inferred from object motion"
    for e in trace.events:
        assert e.detail["actor"] == "human_hand" and e.detail["observability"] == "inferred"
        assert "not robot contact" in e.detail["meaning"]
    ev = trace.evidence
    assert all(s.status == "unobserved" for s in ev.gripper_position + ev.gripper_width)
    assert "no robot gripper" in ev.events_basis
    assert "object_centric_events" in {r.code for r in ev.review}
    with pytest.raises(ValueError):
        estimate(tr, {"top": dv.cameras["top"].K}, actor="robot")
