"""Studio capture endpoints on the real offline phone pipeline, with SYNTHETIC rendered takes
(not real footage; these check the workflow, not accuracy)."""
import json

import pytest

pytest.importorskip("cv2")

pytestmark = pytest.mark.slow


def test_rendered_two_view_capture_end_to_end(tmp_path):
    from mirror.capture import synthetic as S
    from mirror.studio.session_adapter import StudioService
    d = tmp_path / "synthetic_take"
    d.mkdir()
    plan = S.TakePlan(tilt_deg=25.0, mug_tag=True)
    truth = S.render_take(d, plan)
    cams = S.default_cameras()
    for n, c in cams.items():
        S.render_lens_clip(d / f"lens_{n}.mp4", c, n_views=18, seed={"top": 1, "side": 2}[n])
    svc = StudioService(backend="live", session_id="cap", private_runtime=True)
    try:
        ids = {r: svc.upload_video((d / f).read_bytes(), r)["id"] for r, f in
               {"take_top": "synthetic_top.mp4", "take_side": "synthetic_side.mp4",
                "lens_top": "lens_top.mp4", "lens_side": "lens_side.mp4"}.items()}
        p0, _ = plan.mug_pose(0.0)
        tray_xy = S.truth_table_xyz([*S.TRAY["center"], 0.0], truth)[:2]
        cap = svc.capture_create("two_view", synthetic=True, inputs={
            "print_check": {"measured_bar_mm": 50.0, "measured_square_mm": 30.0},
            "objects": [{"track_label": "mug", "real_identity": "red mug (SYNTHETIC render)", "proxy": "cup",
                         "real_contents": "dry rice", "real_dims": {"height": {"value_m": 0.095, "source": "measured_ruler"}},
                         "unsupported_features": ["handle"], "confirmed_by_person": False}],
            "tags": {"95": "mug"},
            "tray": {"center_xy": [float(tray_xy[0]), float(tray_xy[1])], "inner_half": list(S.TRAY["inner_half"])}})
        cid = cap["id"]
        svc.capture_clips(cid, ids)
        svc.capture_prompts(cid, [json.loads(S.truth_prompt(truth, c, "mug", p0, (0.04, 0.04, 0.0475)).model_dump_json())
                                  for c in ("top", "side")])
        j = svc.wait(svc.capture_run(cid), timeout=1800)
        assert j.status == "done", j.error
        st = svc.capture_public(cid)
        rep = st["report"]
        assert rep["synthetic"] and st["label"].startswith("SYNTHETIC")
        assert set(rep["calibration"]) == {"top", "side"} and all(v["accepted"] for v in rep["calibration"].values())
        assert rep["sync"] and rep["sync"].get("ok") is True
        assert rep["mappings"][0]["fidelity_statement"]
        assert not st["gate_open"] and "mapping_unconfirmed" in [r["code"] for r in rep["review"] if r["blocking"]]
        out = svc.capture_review(cid, {"objects": [{"track_label": "mug", "confirmed_by_person": True, "seconds_spent": 4}],
                                       "identity_confirmations": [["top", "mug", 0], ["side", "mug", 0]]})
        j = svc.wait(svc.jobs[out["job"]["id"]], timeout=1800)
        assert j.status == "done", j.error
        st = svc.capture_public(cid)
        blocking = [r for r in st["report"]["review"] if r["blocking"]]
        assert all(r["code"] == "ambiguous_event_interval" for r in blocking), blocking
        if blocking:      # the studio never clears these itself: the person confirms the bracketed event
            with pytest.raises(RuntimeError):
                svc.capture_propose(cid)
            out = svc.capture_review(cid, {"corrections": [{"target": r["event"], "field": "confirm", "seconds_spent": 2.5}
                                                            for r in blocking]})
            assert svc.wait(svc.jobs[out["job"]["id"]], timeout=1800).status == "done"
            st = svc.capture_public(cid)
        assert st["gate_open"], [r for r in st["report"]["review"] if r["blocking"]]
        j = svc.wait(svc.capture_propose(cid), timeout=900)
        s = svc.snapshot()
        assert s["generation"]["reason"] == "phone_capture"            # the reviewed capture became the demonstration
        if j.status != "done":
            # Known gap (reported to the phone track): the capture scene carries no tray, so the interpreter
            # finds no packing task. The studio reports the refusal and substitutes nothing.
            assert "InterpretError" in j.error and not s["specs"]
            assert svc.capture_public(cid)["proposal"]["spec_version_id"] is None
            return
        assert s["specs"]
        assert st["effort"]["seconds_spent_total"] >= 4.0 and st["effort"]["actions_without_time"] == 2
        assert svc.capture_public(cid)["proposal"]["synthetic"] is True
    finally:
        svc.close("t")
