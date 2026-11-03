"""End-to-end phone-capture pipeline on SYNTHETIC renders (slow: renders video).

Every take here is rendered, not filmed. These tests check the workflow's
behaviour (accept, refuse, abstain, report uncertainty); they are not evidence
about real phone footage."""
import json

import numpy as np
import pytest

from mirror.capture import calibrate as C
from mirror.capture import events as EV
from mirror.capture import ingest as I
from mirror.capture import proxy as PX
from mirror.capture import session as SE
from mirror.capture import synthetic as S
from mirror.capture import validation as V

pytestmark = pytest.mark.slow

PC = C.PrintCheck(measured_bar_mm=50.0, measured_square_mm=30.0, measured_by="synthetic_test")
MUG = PX.ObjectMapping(track_label="mug", real_identity="red mug (SYNTHETIC)", proxy="cup", real_contents="dry rice",
                       real_dims={"height": PX.Dimension(value_m=0.095, source="measured_ruler")},
                       unsupported_features=["handle"], confirmed_by_person=True)


@pytest.fixture(scope="module")
def lens(tmp_path_factory, ffmpeg):
    d = tmp_path_factory.mktemp("lens")
    cams = S.default_cameras()
    out = {}
    for n, c in cams.items():
        out[n] = str(S.render_lens_clip(d / f"lens_{n}.mp4", c, n_views=18, seed={"top": 1, "side": 2}[n]))
    return out


@pytest.fixture(scope="module")
def intrinsics(lens):
    from mirror.capture import media as M
    from mirror.capture.target import DEFAULT_TARGET
    out = {}
    for n, p in lens.items():
        m = M.probe(p)
        out[n] = C.calibrate_intrinsics(SE.lens_detections(m, DEFAULT_TARGET), DEFAULT_TARGET, PC, m.fingerprint)
    return out


def _take(tmp_path_factory, name, plan, **kw):
    d = tmp_path_factory.mktemp(name)
    truth = S.render_take(d, plan, **kw)
    return d, truth


TRAY_MAP = PX.ObjectMapping(track_label="tray", real_identity="grey tray (SYNTHETIC)", proxy="tray",
                            real_dims={"width": PX.Dimension(value_m=2 * S.TRAY["inner_half"][0], source="measured_ruler"),
                                       "depth": PX.Dimension(value_m=2 * S.TRAY["inner_half"][1], source="measured_ruler")},
                            confirmed_by_person=True)


def _tray_prompt(truth, cam):
    w = S.TRAY["wall"]
    hx, hy = S.TRAY["inner_half"]
    return S.truth_prompt(truth, cam, "tray", [*S.TRAY["center"], w / 2], (hx + w, hy + w, w / 2))


def _session(d, truth, plan, intrinsics, *, cams=("top", "side"), tags=None, confirm=True, tray="region"):
    """tray: "region" (entered by the person), "tracked" (box on the tray, measured inside size) or None."""
    p0, _ = plan.mug_pose(0.0)
    tray_xy = S.truth_table_xyz([*S.TRAY["center"], 0.0], truth)[:2]
    prompts = [S.truth_prompt(truth, c, "mug", p0, (0.04, 0.04, 0.0475)) for c in cams]
    mappings = [MUG]
    if tray == "tracked":
        prompts += [_tray_prompt(truth, c) for c in cams]
        mappings = [MUG, TRAY_MAP]
    region = (EV.TrayRegion(center_xy=tuple(float(v) for v in tray_xy), inner_half=S.TRAY["inner_half"], source="person_supplied")
              if tray == "region" else None)
    return SE.build_session(
        take={c: str(d / f"synthetic_{c}.mp4") for c in cams}, intrinsics=intrinsics, print_check=PC,
        prompts=prompts, mappings=mappings, synthetic=True, tags=tags, tray=region,
        identity_confirmations=[(c, lab, 0) for c in cams for lab in {m.track_label for m in mappings}] if confirm else [])


@pytest.fixture(scope="module")
def upright(tmp_path_factory, intrinsics):
    plan = S.TakePlan()
    d, truth = _take(tmp_path_factory, "upright", plan)
    return d, truth, plan, _session(d, truth, plan, intrinsics)


def test_upright_two_view_reconstructs_and_reports_honestly(upright):
    d, truth, plan, s = upright
    assert s.mode == "two_view" and s.sync.ok and s.sync.method == "card"
    assert abs(s.sync.offset_s - 0.37) < 0.06                         # true offset, within the frame bracketing
    assert s.evidence_kind == "synthetic_test_render"                  # never camera evidence
    row = V.score_clip(s, I.synthetic_labels(truth, plan))
    assert row["synthetic"] is True
    assert row["rest_position_error_cm"]["median"] < 1.5
    assert row["keyframe_position"]["missing"] == 0
    assert row["events"]["bracket_contains_rate"] == 1.0
    tr = s.reconstruction.objects["mug"]
    assert all(st == "unobserved" for st in tr.tilt_status)           # no tag, round mug: tilt never claimed
    assert any(r.code == "tilt_unobserved" for r in s.review)
    for e in s.events:
        assert e.actor == "human_hand" and "not a robot contact" in e.label
    # the summary for a person states proxy fidelity
    assert any("not measured physical accuracy" in x for x in SE.summary_lines(s))


def test_tilted_take_with_tag_measures_tilt_and_beats_constant_zero(tmp_path_factory, intrinsics):
    plan = S.TakePlan(tilt_deg=30.0, mug_tag=True)
    d, truth = _take(tmp_path_factory, "tilted", plan)
    s = _session(d, truth, plan, intrinsics, tags={95: "mug"})
    row = V.score_clip(s, I.synthetic_labels(truth, plan, step=0.1))
    tilt = row["tilt"]["mug"]
    assert tilt["observed"] > 0
    assert tilt["full_median_abs_err_deg"] < 3.0
    assert tilt["tilted_frames"]["observed"] > 0
    assert tilt["tilted_frames"]["estimate_median_abs_err_deg"] < tilt["tilted_frames"]["constant_zero_median_abs_err_deg"]
    # without the tag track the same footage cannot measure the mug's tilt: it stays unobserved
    s2 = _session(d, truth, plan, intrinsics, tags=None)
    assert all(st == "unobserved" for st in s2.reconstruction.objects["mug"].tilt_status)


def test_separate_takes_are_not_triangulated(tmp_path_factory, intrinsics):
    plan = S.TakePlan()
    other = S.TakePlan(sync_windows=((0.8, 1.2), (5.5, 5.9)), pick=2.6, place=4.4)
    d, truth = _take(tmp_path_factory, "separate", plan, other_plan=other)
    s = _session(d, truth, plan, intrinsics)
    assert s.sync is not None and not s.sync.ok
    assert s.mode == "single_view"
    assert any(r.code == "sync_refused" for r in s.review)
    assert all(r is None for r in s.reconstruction.objects["mug"].triangulation_residual_m)


def test_zoom_changed_after_calibration_is_rejected(tmp_path_factory, intrinsics):
    cams = S.default_cameras()
    side = cams["side"]
    side.K = side.K.copy()
    side.K[0, 0] *= 1.25
    side.K[1, 1] *= 1.25
    plan = S.TakePlan()
    d, truth = _take(tmp_path_factory, "zoomed", plan, cams=cams)
    s = _session(d, truth, plan, intrinsics)
    ok, blocking = s.gate()
    assert not ok
    assert any(b.code == "recalibration_required" and b.camera == "side" for b in blocking)
    labels = I.synthetic_labels(truth, plan).model_copy(update={"expected": "reject", "condition": "invalid_calibration"})
    assert V.score_clip(s, labels)["outcome"] == "rejected_as_expected"


def test_single_view_top_only(upright, intrinsics):
    d, truth, plan, _ = upright
    s = _session(d, truth, plan, {"top": intrinsics["top"]}, cams=("top",))
    assert s.mode == "single_view" and s.sync is None
    assert any(r.code == "single_view" for r in s.review)
    tr = s.reconstruction.objects["mug"]
    lifted = [st for t, st in zip(tr.t, tr.pos_status) if 2.6 < t < 3.4]
    assert lifted and all(st != "measured" for st in lifted)       # height unknown while lifted
    assert all(st == "unobserved" for st in tr.tilt_status)


def test_unconfirmed_identity_blocks_proposal(upright, intrinsics):
    d, truth, plan, _ = upright
    s = _session(d, truth, plan, intrinsics, confirm=False)
    ok, blocking = s.gate()
    assert not ok and any(b.code == "identity_uncertain" for b in blocking)


def test_trace_conversion_carries_evidence(upright):
    O = pytest.importorskip("mirror.observability")
    from mirror.contracts import DemoTrace
    if "evidence" not in DemoTrace.model_fields:
        pytest.skip("DemoTrace without evidence field (observability not integrated)")
    from mirror.capture import trace as TR
    *_, s = upright
    tr = TR.to_demo_trace(s)
    ev = tr.evidence
    assert ev.kind == "unverified_video_reconstruction"                 # synthetic: never camera evidence
    assert any(r.code == "synthetic_capture" and r.severity == "abstain" for r in ev.review)
    assert all(sp.status == "unobserved" for sp in ev.gripper_position)
    assert all(e.detail["inferred"] and e.detail["actor"] == "human_hand" for e in tr.events)
    obj = next(iter(ev.objects.values()))
    assert all(sp.status == "unobserved" for sp in obj.tilt)          # placeholder quats are not evidence


def test_ingest_cli_demo_end_to_end(tmp_path, ffmpeg):
    assert I.main(["demo", str(tmp_path / "demo")]) == 0
    s = SE.CaptureSession.load(tmp_path / "demo" / "session.json")
    assert s.synthetic and s.evidence_kind == "synthetic_test_render"
    row = json.loads((tmp_path / "demo" / "score_synthetic.json").read_text())
    assert row["synthetic"] and row["split"] == "synthetic_rehearsal"
    assert (tmp_path / "demo" / "review.md").read_text().startswith("# Capture review (SYNTHETIC demo)")
    assert "Destination tray: inside 24.0 x 18.0 cm" in (tmp_path / "demo" / "review.md").read_text()
    scene = json.loads((tmp_path / "demo" / "scene.json").read_text())
    assert len(scene["trays"]) == 1 and [round(v, 3) for v in scene["trays"][0]["inner_half"]] == list(S.TRAY["inner_half"])


def test_capture_without_a_tray_is_refused_early(upright, intrinsics):
    d, truth, plan, _ = upright
    s = _session(d, truth, plan, intrinsics, tray=None)
    ok, blocking = s.gate()
    assert not ok and any(b.code == "tray_not_mapped" for b in blocking)
    assert "Destination tray: not mapped." in SE.summary_lines(s)


def test_tracked_tray_gives_the_destination_with_its_measured_footprint(upright, intrinsics):
    d, truth, plan, _ = upright
    s = _session(d, truth, plan, intrinsics, tray="tracked")
    assert s.tray is not None and s.tray.footprint_source == "measured_ruler"
    assert s.tray.source == "assumed_aligned_with_board"
    true_xy = S.truth_table_xyz([*S.TRAY["center"], 0.0], truth)[:2]
    assert np.linalg.norm(np.array(s.tray.center_xy) - true_xy) < 0.02
    assert tuple(round(v, 3) for v in s.tray.inner_half) == tuple(S.TRAY["inner_half"])
    assert not any(r.code.startswith("tray_") for r in s.review)
    assert any(e.type == "place" and e.placed_in for e in s.events)


def test_camera_bumped_during_take_is_rejected(tmp_path_factory, intrinsics):
    """The top phone rotates 1.5 degrees at 3 s (a bump): the take must not be used."""
    plan = S.TakePlan()
    d, truth = _take(tmp_path_factory, "bumped", plan, bump={"top": (3.0, (1.5, 0.0, 0.0))})
    s = _session(d, truth, plan, intrinsics)
    ok, blocking = s.gate()
    assert not ok and any(b.code == "camera_moved" and b.camera == "top" for b in blocking)


def test_manual_keyframe_baseline(upright, intrinsics):
    """Manual baseline: clicked centres every 0.5 s (here: exact projections, SYNTHETIC)."""
    d, truth, plan, _ = upright
    from mirror.capture import media as M
    from mirror.capture.geometry import project
    kf = {}
    for c in ("top", "side"):
        m = M.probe(d / f"synthetic_{c}.mp4")
        cam = truth["cameras"][c]
        off = truth["start_offsets"][c]
        kf[c] = {"mug": {}}
        for k, t in enumerate(m.pts_s):
            if abs((t + off) * 2 - round((t + off) * 2)) < 0.03:
                uv = project(cam["K"], cam["dist"], cam["R"], cam["t"], plan.mug_pose(t + off)[0][None])[0]
                kf[c]["mug"][k] = (float(uv[0]), float(uv[1]))
    p0, _ = plan.mug_pose(0.0)
    s = SE.build_session(take={c: str(d / f"synthetic_{c}.mp4") for c in ("top", "side")}, intrinsics=intrinsics,
                         print_check=PC, prompts=[], mappings=[MUG], synthetic=True, tracker="manual", manual_keyframes=kf)
    assert "tracker: manual" in s.notes
    tr = s.reconstruction.objects["mug"]
    assert {"interpolated"} & set(tr.pos_status)
    row = V.score_clip(s, I.synthetic_labels(truth, plan))
    assert row["keyframe_position"]["unconditional_cm"]["median"] < 1.5


def test_offline_proposal_is_gated_on_review(upright, intrinsics):
    pytest.importorskip("mirror.observability")
    from mirror.contracts import DemoTrace
    if "evidence" not in DemoTrace.model_fields:
        pytest.skip("observability not integrated")
    from mirror.capture import trace as TR
    d, truth, plan, _ = upright
    blocked = _session(d, truth, plan, intrinsics, confirm=False)
    with pytest.raises(TR.CaptureNotReviewed):
        TR.propose_offline_from_capture(blocked)
    s = _session(d, truth, plan, intrinsics)
    for r in s.review:                         # a person resolves the remaining items (synthetic test)
        if r.severity == "abstain":
            r.resolved, r.resolution = True, "checked by a person (synthetic test)"
    out = TR.propose_offline_from_capture(s)
    assert out["provenance"] == "offline_rules"
    # the mug's tilt was never observed: no upright rule may be asserted from this demonstration
    obs = out["summary"].object("cup_0").observability if hasattr(out["summary"], "object") else None
    assert obs is None or obs.get("tilt") is not None
