"""Phone capture -> scene + DemoTrace with evidence -> offline proposal.

Needs the observability annotations (perception track); skipped without them.
Uses exact SYNTHETIC projections, no video."""
import numpy as np
import pytest

from mirror.capture import events as EV
from mirror.capture import proxy as PX
from mirror.capture import reconstruct as RC
from mirror.capture import session as SE

from .test_reconstruct_events import MODELS, MUG, ORIGIN, SYNC, mug_world, tracks_for


def _session(*, resolved=True):
    rec = RC.reconstruct({c: MODELS[c] for c in ("top", "side")}, tracks_for(mug_world), [MUG], sync=SYNC)
    tray = EV.TrayRegion(center_xy=tuple((np.array([0.25, 0.04, 0]) - ORIGIN)[:2]), inner_half=(0.12, 0.09), source="person_supplied")
    evs = EV.detect_events(rec.objects["mug"], tray)
    tray_map = PX.ObjectMapping(track_label="tray", real_identity="tray (synthetic)", proxy="tray", confirmed_by_person=True)
    s = SE.CaptureSession(synthetic=True, mappings=[MUG, tray_map], reconstruction=rec, events=evs, tray=tray, mode=rec.mode,
                          review=[SE.ReviewItem(code="tilt_unobserved", severity="review", object="mug", message="tilt never observed")])
    if not resolved:
        s.review.append(SE.ReviewItem(code="identity_uncertain", severity="abstain", object="mug", message="same object?"))
    s.finalise_kind()
    return s


def test_scene_uses_proxies_at_the_start_rest_positions():
    from mirror.capture import trace as TR
    s = _session()
    scene = TR.to_scene(s)
    cup = next(o for o in scene.objects if o.kind == "cup")
    start = mug_world(0.0)
    assert np.linalg.norm(np.array(cup.pose.pos[:2]) - start[:2]) < 0.01      # default placement = scene frame
    assert len(scene.trays) == 1 and "not measured physical models" in scene.notes


def test_demo_trace_marks_unobserved_values_and_human_events():
    O = pytest.importorskip("mirror.observability")
    from mirror.contracts import DemoTrace
    if "evidence" not in DemoTrace.model_fields:
        pytest.skip("DemoTrace without evidence (observability not integrated)")
    from mirror.capture import trace as TR
    tr = TR.to_demo_trace(_session())
    ev = tr.evidence
    assert ev.kind == "unverified_video_reconstruction"
    assert any(r.code == "synthetic_capture" for r in ev.review)
    assert all(sp.status == "unobserved" for sp in ev.gripper_position + ev.gripper_width)
    assert ev.calibration == [] or {c.intrinsics_source for c in ev.calibration} <= {"charuco_board", "user_supplied"}
    cup = ev.objects["cup_0"]
    assert all(sp.status == "unobserved" for sp in cup.tilt)
    assert {"grasp", "release"} == {e.type for e in tr.events}
    assert all(e.detail["inferred"] and e.detail["actor"] == "human_hand" for e in tr.events)
    # round trip
    assert DemoTrace.model_validate_json(tr.model_dump_json()).evidence.kind == ev.kind


def test_offline_proposal_refused_until_reviewed_and_never_asserts_unobserved_tilt():
    pytest.importorskip("mirror.observability")
    from mirror.contracts import DemoTrace
    if "evidence" not in DemoTrace.model_fields:
        pytest.skip("observability not integrated")
    from mirror.capture import trace as TR
    with pytest.raises(TR.CaptureNotReviewed):
        TR.propose_offline_from_capture(_session(resolved=False))
    out = TR.propose_offline_from_capture(_session())
    summary = out["summary"]
    text = " ".join(str(x) for x in getattr(summary, "regularities", []))
    assert "stayed within 0 degrees" not in text and "ended upright (0 degrees" not in text


def test_demo_trace_records_charuco_board_intrinsics():
    pytest.importorskip("mirror.observability")
    from mirror.contracts import DemoTrace
    if "evidence" not in DemoTrace.model_fields:
        pytest.skip("observability not integrated")
    from mirror.capture import trace as TR
    from mirror.capture.calibrate import Intrinsics, TablePose
    s = _session()
    s.intrinsics = {"top": Intrinsics(K=np.eye(3).tolist(), dist=[0.0] * 5, image_size=(960, 540), source="charuco_calibration",
                                      accepted=True, square_mm=30.0)}
    s.poses = {"top": MODELS["top"].pose}
    cal = TR.to_demo_trace(s).evidence.calibration
    assert [c.intrinsics_source for c in cal] == ["charuco_board"]
    assert not any("checkerboard" in a for a in cal[0].assumptions)


def test_session_json_carries_fidelity_statement_and_ignores_stored_copies():
    import json
    s = _session()
    d = json.loads(s.model_dump_json())
    m = next(x for x in d["mappings"] if x["track_label"] == "mug")
    assert m["fidelity_statement"] == MUG.fidelity_statement()
    assert "not measured physical accuracy" in m["fidelity_statement"]
    m["fidelity_statement"] = "measured to the millimetre"          # a tampered copy is not trusted
    back = SE.CaptureSession.model_validate(d)
    assert back.mappings[0].fidelity_statement() == MUG.fidelity_statement()
    assert json.loads(back.model_dump_json())["mappings"][0]["fidelity_statement"] == MUG.fidelity_statement()
