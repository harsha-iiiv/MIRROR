"""Observability and provenance of demonstration evidence (perception).

Most tests build small traces by hand. The reconstruction tests use recorded
SAM tracks from the frozen video benchmark (results/eval_video2/tracks): no
model calls, no rendering."""
import json
from pathlib import Path

import numpy as np
import pytest

from mirror.contracts import DemoTrace, Pose, TraceEvent, TraceFrame
from mirror.interpret import summarize_demo
from mirror.interpret.prompts import summary_payload
from mirror.observability import (ABOUT_WORDING, ReviewItem, TraceEvidence, coverage, evidence_kind,
                                  resolve_evidence, spans_from_statuses, status_at, statuses, uniform)

REPO = Path(__file__).resolve().parents[2]
REF = REPO / "data" / "demos" / "reference_demo.json"


def _tilt_quat(deg):
    a = np.radians(deg) / 2
    return (float(np.cos(a)), 0.0, float(np.sin(a)), 0.0)


def _video_trace(tilts, tilt_status, *, evidence=True, end_status=None):
    """A reference-scene trace re-labelled as video: the cup is carried between
    frames 10 and 30 with the given per-frame tilt and tilt status."""
    ref = DemoTrace.model_validate_json(REF.read_text())
    scene = ref.scene
    cup = next(o for o in scene.objects if o.kind == "cup")
    tray = scene.trays[0]
    frames, n = [], 41
    start = np.array(cup.pose.pos)
    end = np.array([tray.pose.pos[0], tray.pose.pos[1], cup.pose.pos[2] + tray.pose.pos[2]])
    for i in range(n):
        w = min(1.0, max(0.0, (i - 10) / 20))
        p = start + w * (end - start) + np.array([0, 0, 0.1 * np.sin(np.pi * w)])
        objs = {o.id: o.pose for o in scene.objects if o.id != cup.id}
        q = _tilt_quat(tilts[i]) if tilt_status[i] != "unobserved" else (1.0, 0.0, 0.0, 0.0)
        objs[cup.id] = Pose(pos=tuple(float(x) for x in p), quat=q)
        frames.append(TraceFrame(t=i * 0.2, gripper=Pose(pos=tuple(float(x) for x in p + [0, 0, 0.08])),
                                 gripper_width=0.0, objects=objs))
    events = [TraceEvent(t=2.0, type="grasp", object_id=cup.id), TraceEvent(t=6.0, type="release", object_id=cup.id)]
    ev = None
    if evidence:
        ev = resolve_evidence(ref.model_copy(update={"frames": frames}))   # simulator-shaped: all measured
        ev = TraceEvidence.model_validate(json.loads(ev.model_dump_json()))
        ev.kind = "rendered_video_reconstruction"
        ts = list(tilt_status)
        if end_status:
            ts[-1] = end_status
        ev.objects[cup.id].tilt = spans_from_statuses(ts)
        for oid, oe in ev.objects.items():
            if oid != cup.id:
                oe.tilt = uniform("unobserved", n)
    return DemoTrace(demo_id="t_video", scene=scene, source="video_grounded", sample_hz=5.0, frames=frames,
                     events=events, evidence=ev)


# ------------------------------------------------------------------ helpers


def test_spans_roundtrip_and_coverage():
    st = ["measured"] * 3 + ["unobserved"] * 2 + ["interpolated", "inferred"]
    sp = spans_from_statuses(st)
    assert [s.status for s in sp] == ["measured", "unobserved", "interpolated", "inferred"]
    assert statuses(sp, len(st)) == st
    assert status_at(sp, 4) == "unobserved" and status_at(sp, 99) == "unobserved"
    assert coverage(sp, range(7)) == pytest.approx(3 / 7)
    assert coverage(sp, []) is None
    with pytest.raises(ValueError):
        spans_from_statuses(["seen"])


def test_every_kind_has_about_wording_and_rendered_is_not_human_video():
    from typing import get_args
    from mirror.observability import EvidenceKind
    assert set(get_args(EvidenceKind)) == set(ABOUT_WORDING)
    text = ABOUT_WORDING["rendered_video_reconstruction"].lower()
    assert "simulator-rendered video reconstructed from pixels" in text and "not footage of a person" in text
    assert "human" not in text


# ------------------------------------------------------- backward compatibility


def test_old_traces_load_and_reserialise_unchanged():
    files = [REF] + sorted(REPO.glob("results/*/inputs/*/*/trace.json"))[:6]
    for p in files:
        raw = json.loads(p.read_text())
        t = DemoTrace.model_validate(raw)
        assert t.evidence is None
        assert json.loads(t.model_dump_json()) == raw            # no new key in old traces
        assert evidence_kind(t) == "simulator_ground_truth"


def test_simulator_summary_unchanged_and_still_replays():
    from mirror.interpret.auto import proposal_recorded, recorded_models
    from mirror.interpret.llm import DEFAULT_CASSETTE_DIR
    t = DemoTrace.model_validate_json(REF.read_text())
    s = summarize_demo(t)
    assert s.evidence_kind == "simulator_ground_truth" and s.review == []
    assert all(o.observability is None for o in s.objects)
    assert not any(n.startswith("Evidence:") for n in summary_payload(s)["notes"])
    # the proposal request is byte-identical to the recorded one
    models = list(recorded_models(Path(DEFAULT_CASSETTE_DIR)))
    assert models and any(proposal_recorded(t, m, Path(DEFAULT_CASSETTE_DIR)) for m in models)


def test_evidence_absent_is_never_assumed_ground_truth_for_video():
    t = _video_trace([0.0] * 41, ["measured"] * 41, evidence=False)
    assert evidence_kind(t) == "legacy_video_unannotated"
    ev = resolve_evidence(t)
    assert all(status_at(o.tilt, 20) == "unobserved" for o in ev.objects.values())
    s = summarize_demo(t)
    assert s.evidence_kind == "legacy_video_unannotated"
    assert {r["code"] for r in s.review} == {"legacy_unannotated"}
    assert not [r for r in s.regularities if r.kind in ("carry_upright", "final_upright")]
    cup = next(o for o in s.objects if o.kind == "cup")
    assert cup.carry_max_tilt_deg is None and cup.end_tilt_deg is None


# ------------------------------------------------------------ summary rules


def test_unobserved_carry_tilt_is_not_upright_evidence():
    st = ["measured"] * 10 + ["unobserved"] * 21 + ["measured"] * 10
    s = summarize_demo(_video_trace([0.0] * 41, st))
    cup = next(o for o in s.objects if o.kind == "cup")
    assert cup.carry_max_tilt_deg is None and cup.carry_mean_tilt_deg is None
    assert cup.observability["tilt"]["carry_observed_frames"] == 0
    assert not [r for r in s.regularities if r.kind == "carry_upright"]
    assert any("not upright" in n for n in s.notes)
    payload = summary_payload(s)
    assert next(o for o in payload["objects"] if o["kind"] == "cup")["carry_max_tilt_deg"] is None


def test_partial_coverage_withholds_upright_but_keeps_measured_tilt():
    tilts = [0.0] * 41
    st = ["measured"] * 41
    for i in range(14, 30):
        st[i] = "unobserved"
    s = summarize_demo(_video_trace(tilts, st))
    assert not [r for r in s.regularities if r.kind == "carry_upright"]
    # a measured tilt is still reported even with partial coverage
    tilts2 = list(tilts)
    tilts2[12] = 40.0
    s2 = summarize_demo(_video_trace(tilts2, st))
    tilted = [r for r in s2.regularities if r.kind == "carry_tilted"]
    assert tilted and "carried frames" in tilted[0].statement


def test_well_covered_carry_states_its_basis():
    st = ["measured"] * 41
    st[20] = "interpolated"
    s = summarize_demo(_video_trace([2.0] * 41, st))
    up = [r for r in s.regularities if r.kind == "carry_upright"]
    assert up and "side-view measurement" in up[0].statement
    # objects whose tilt is never observed get no final-upright statement
    kinds_final = {r.object_kind for r in s.regularities if r.kind == "final_upright"}
    assert kinds_final == {"cup"}


def test_inferred_end_tilt_is_labelled():
    st = ["measured"] * 40 + ["inferred"]
    s = summarize_demo(_video_trace([0.0] * 41, st))
    fin = [r for r in s.regularities if r.kind == "final_upright" and r.object_kind == "cup"]
    assert fin and "inferred" in fin[0].statement and "15 degrees" in fin[0].statement


def test_unobserved_final_position_does_not_claim_tray():
    t = _video_trace([0.0] * 41, ["measured"] * 41)
    cup = next(o for o in t.scene.objects if o.kind == "cup")
    st = ["measured"] * 35 + ["unobserved"] * 6
    t.evidence.objects[cup.id].position = spans_from_statuses(st)
    s = summarize_demo(t)
    o = next(o for o in s.objects if o.kind == "cup")
    assert o.ended_in_tray is None and o.observability["position"]["end"] == "unobserved"


def test_review_items_reach_the_summary():
    t = _video_trace([0.0] * 41, ["measured"] * 41)
    t.evidence.review.append(ReviewItem(code="identity_ambiguous", severity="review", message="x"))
    s = summarize_demo(t)
    assert [r["code"] for r in s.review] == ["identity_ambiguous"]
    assert s.evidence_kind == "rendered_video_reconstruction"
    assert summary_payload(s)["notes"][0].startswith("Evidence: Simulator-rendered video reconstructed from pixels")


def test_charuco_board_is_an_accepted_intrinsics_source():
    """Phone captures calibrate with a ChArUco board and say so (added after 'checkerboard';
    earlier records still validate unchanged)."""
    import typing
    from mirror import observability as O
    assert "charuco_board" in typing.get_args(O.IntrinsicsSource)
    assert {"checkerboard", "charuco_board"} == set(O.MEASURED_INTRINSICS)
    rec = O.CalibrationRecord(camera="top", intrinsics_source="charuco_board", extrinsics_source="calibration_board",
                              valid=True)
    again = O.CalibrationRecord.model_validate_json(rec.model_dump_json())
    assert again.intrinsics_source == "charuco_board"
    old = O.CalibrationRecord(camera="top", intrinsics_source="checkerboard", extrinsics_source="calibration_board")
    assert old.intrinsics_source == "checkerboard"
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        O.CalibrationRecord(camera="top", intrinsics_source="charuco", extrinsics_source="calibration_board")
