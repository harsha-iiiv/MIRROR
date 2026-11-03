"""Phone capture sessions in the studio, wired to mirror.capture.

Fast tests use SYNTHETIC test-pattern clips and stand-in pipeline results; the slow
test (test_capture_live.py) runs the real offline pipeline on rendered takes."""
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("cv2")

from mirror.studio import capture as CP  # noqa: E402
from mirror.studio import uploads as up  # noqa: E402
from mirror.studio.app import create_app  # noqa: E402
from mirror.studio.session_adapter import StudioService  # noqa: E402
from mirror.studio.sessions import SessionManager  # noqa: E402

pytestmark = pytest.mark.skipif(not up.tools_available(), reason="ffmpeg/ffprobe needed")

PC = {"measured_bar_mm": 50.0, "measured_square_mm": 30.0}
MUG = {"track_label": "mug", "real_identity": "red mug", "proxy": "cup", "unsupported_features": ["handle"]}


def synth(path, seconds, size="320x240"):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=10",
                    "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
                   check=True, timeout=60, stdin=subprocess.DEVNULL)
    return path.read_bytes()


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    return synth(tmp_path_factory.mktemp("synthetic_capture") / "a.mp4", 2.0)


@pytest.fixture()
def api():
    mgr = SessionManager(lambda sid, m: StudioService(backend="fixture", session_id=sid, gate=m.gate,
                                                      budget=m.budget, one_job=True, private_runtime=True))
    with TestClient(create_app(mgr)) as c:
        r = c.post("/api/session").json()
        c.headers.update({"X-Mirror-Session": r["session"], "X-Mirror-Token": r["token"]})
        yield c


def upload(c, data, view):
    r = c.post(f"/api/video/upload?view={view}", content=data)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def wait(c, job):
    t0 = time.time()
    while True:
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("done", "error", "cancelled"):
            return j
        assert time.time() - t0 < 60
        time.sleep(0.02)


def ready_capture(c, clip, mode="single_view"):
    cap = c.post("/api/capture", json={"mode": mode, "synthetic": True, "print_check": PC, "objects": [MUG]}).json()
    t = upload(c, clip, "take_top")
    c.post(f"/api/capture/{cap['id']}/clips", json={"clips": {"take_top": t}})
    r = c.post(f"/api/capture/{cap['id']}/prompts",
               json={"prompts": [{"camera": "top", "label": "mug", "frame": 0, "box": [10, 10, 40, 40]}]})
    assert r.status_code == 200, r.text
    return r.json()


def fake_build(calls):
    """Stand-in for the offline pipeline: abstains until a person confirms the mapping and the identity."""
    from mirror.capture.session import CaptureSession, ReviewItem

    def build(cap, paths):
        calls.append({"inputs": dict(cap.inputs), "paths": dict(paths)})
        review = []
        if not all(o["confirmed_by_person"] for o in cap.inputs["objects"]):
            review.append(ReviewItem(code="mapping_unconfirmed", severity="abstain", object="mug", message="Confirm the mug."))
        if not cap.inputs["identity_confirmations"]:
            review.append(ReviewItem(code="identity_uncertain", severity="abstain", object="mug", message="Same mug?"))
        review.append(ReviewItem(code="tilt_unobserved", severity="review", object="mug", message="Tilt not seen."))
        s = CaptureSession(synthetic=True, review=review)
        s.finalise_kind()
        return s
    return build


def test_clip_roles_inputs_and_prompts_are_validated(api, clip):
    c = api
    cap = c.post("/api/capture", json={"mode": "two_view"}).json()
    assert cap["state"] == "awaiting_clips" and cap["label"].startswith("Phone footage")
    t, s, l = upload(c, clip, "take_top"), upload(c, clip, "take_side"), upload(c, clip, "lens_top")
    assert c.post(f"/api/capture/{cap['id']}/clips", json={"clips": {"take_side": t}}).status_code == 409   # view mismatch
    r = c.post(f"/api/capture/{cap['id']}/clips", json={"clips": {"take_top": t, "take_side": s, "lens_top": l},
                                                       "sync": {"method": "clap", "top_t": 0.2, "side_t": 0.4}}).json()
    assert set(r["clips"]) == {"take_top", "take_side", "lens_top"} and r["declared_sync"]["status"] == "declared"
    assert "path" not in str(r["clips"]) and r["state"] == "clips_ready" and r["sync"]["offset_s"] == 0.2
    assert r["hooks"] == {"calibrate": True, "track": True, "reconstruct": True} and r["session"] is None
    bad = c.post(f"/api/capture/{cap['id']}/inputs", json={"objects": [MUG, MUG]})
    assert bad.status_code == 422
    assert c.post(f"/api/capture/{cap['id']}/inputs", json={"print_check": {"measured_bar_mm": "x"}}).status_code == 422
    r = c.post(f"/api/capture/{cap['id']}/inputs", json={"print_check": PC, "objects": [MUG]}).json()
    assert r["inputs"]["print_check"]["measured_by"] == "person"
    assert c.post(f"/api/capture/{cap['id']}/prompts", json={"prompts": [
        {"camera": "front", "label": "mug", "frame": 0, "box": [1, 1, 30, 30]}]}).status_code == 422
    assert c.post(f"/api/capture/{cap['id']}/prompts", json={"prompts": [
        {"camera": "top", "label": "mug", "frame": 9999, "box": [1, 1, 30, 30]}]}).status_code == 422
    r = c.post(f"/api/capture/{cap['id']}/prompts", json={"prompts": [
        {"camera": "top", "label": "mug", "frame": 3, "box": [1, 1, 30, 30]}]}).json()
    assert r["state"] == "clips_ready" and not r["missing"]
    png = c.get(f"/api/capture/{cap['id']}/frame/take_top/3")
    assert png.status_code == 200 and png.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert c.get(f"/api/capture/{cap['id']}/frame/take_top/999").status_code == 409
    assert c.get(f"/api/capture/{cap['id']}/frame/lens_side/0").status_code == 404


def test_single_view_capture_refuses_a_side_take(api, clip):
    c = api
    cap = c.post("/api/capture", json={"mode": "single_view"}).json()
    s = upload(c, clip, "take_side")
    assert c.post(f"/api/capture/{cap['id']}/clips", json={"clips": {"take_side": s}}).status_code == 422
    assert "not observed" in cap["observability"]


def test_review_gates_the_proposal_and_records_effort(api, clip, monkeypatch):
    calls = []
    monkeypatch.setattr(CP, "build", fake_build(calls))
    c = api
    cap = ready_capture(c, clip)
    cid = cap["id"]
    assert c.post(f"/api/capture/{cid}/propose").status_code == 409                 # not run yet
    j = wait(c, c.post(f"/api/capture/{cid}/run").json())
    assert j["status"] == "done" and j["result"]["gate_open"] is False and j["result"]["blocking"] == 2
    assert j["result"]["step_provenance"]["kind"] == "offline_pipeline"
    st = c.get(f"/api/capture/{cid}").json()
    rep = st["report"]
    assert [r["code"] for r in rep["review"] if r["blocking"]] == ["mapping_unconfirmed", "identity_uncertain"]
    assert all(r["how_to_resolve"] for r in rep["review"] if r["blocking"])
    r = c.post(f"/api/capture/{cid}/propose")
    assert r.status_code == 409 and "resolve these first" in r.json()["detail"]
    assert c.post(f"/api/capture/{cid}/review", json={"corrections": [{"target": "nope", "field": "t", "after": "1.0"}]}).status_code == 422
    r = c.post(f"/api/capture/{cid}/review", json={
        "objects": [{"track_label": "mug", "confirmed_by_person": True, "seconds_spent": 6.5}],
        "identity_confirmations": [{"camera": "top", "label": "mug", "from_frame": 12, "seconds_spent": 3}]}).json()
    assert [e["kind"] for e in r["recorded"]] == ["mapping", "identity"]
    wait(c, r["job"])
    st = c.get(f"/api/capture/{cid}").json()
    assert st["gate_open"] and st["report"]["review"][0]["code"] == "tilt_unobserved" and st["state"] == "reviewed"
    assert st["session"]["evidence_kind"] == "synthetic_test_render"
    assert st["effort"] == {"actions": 2, "by_kind": {"mapping": 1, "identity": 1}, "seconds_spent_total": 9.5,
                            "actions_without_time": 0}
    assert calls[-1]["inputs"]["identity_confirmations"] == [["top", "mug", 12]]
    assert st["label"].startswith("SYNTHETIC")


def test_proposal_starts_a_new_generation_with_capture_provenance(api, clip, monkeypatch):
    calls = []
    monkeypatch.setattr(CP, "build", fake_build(calls))
    from mirror.capture import trace as TRC
    from mirror.studio.fixtures import FixtureSession
    monkeypatch.setattr(TRC, "to_demo_trace", lambda s, demo_id=None: FixtureSession().record_demo())
    c = api
    cap = ready_capture(c, clip)
    cid = cap["id"]
    wait(c, c.post(f"/api/capture/{cid}/run").json())
    r = c.post(f"/api/capture/{cid}/review", json={
        "objects": [{"track_label": "mug", "confirmed_by_person": True}],
        "identity_confirmations": [["top", "mug", 0]]}).json()
    wait(c, r["job"])
    g0 = c.get("/api/state").json()["generation"]["id"]
    j = wait(c, c.post(f"/api/capture/{cid}/propose").json())
    assert j["status"] == "done", j["error"]
    s = c.get("/api/state").json()
    assert s["generation"]["id"] != g0 and s["generation"]["reason"] == "phone_capture"
    assert s["generation"]["calibration"]["capture_id"] == cid and s["generation"]["calibration"]["synthetic"] is True
    assert {m["role"] for m in s["generation"]["source_media"]} == {"take_top"}
    assert c.get(f"/api/capture/{cid}").json()["state"] == "proposed"
    # a new input makes the run stale: no proposal from outdated results
    c.post(f"/api/capture/{cid}/inputs", json={"tags": {"95": "mug"}})
    assert c.post(f"/api/capture/{cid}/propose").status_code == 409


def test_pipeline_failure_reconstructs_nothing(api, clip, monkeypatch):
    def broken(cap, paths):
        raise RuntimeError("board not found in the first 3 s")
    monkeypatch.setattr(CP, "build", broken)
    c = api
    cid = ready_capture(c, clip)["id"]
    j = wait(c, c.post(f"/api/capture/{cid}/run").json())
    st = c.get(f"/api/capture/{cid}").json()
    assert j["status"] == "error" and st["report"] is None and "board not found" in st["errors"][-1]
    assert c.post(f"/api/capture/{cid}/propose").status_code == 409


def test_capture_clips_get_capture_limits():
    svc = StudioService(backend="fixture", session_id="l", private_runtime=True)
    assert svc._limits_for("take_top").max_file_bytes > svc._limits_for("top").max_file_bytes
    assert svc._limits_for("lens_side").max_side == 3840 and svc._limits_for("side").max_side == 1920


@pytest.mark.parametrize("pc,ok", [({"measured_bar_mm": 50.0, "measured_square_mm": 30.0}, True),
                                    ({"measured_bar_mm": 51.0, "measured_square_mm": 30.6}, True),
                                    ({"measured_bar_mm": 52.0, "measured_square_mm": 31.2}, False),   # bar off by 2 mm
                                    ({"measured_bar_mm": 50.0, "measured_square_mm": 31.0}, False),   # square disagrees > 2%
                                    ({"measured_bar_mm": None, "measured_square_mm": 30.0}, False)])
def test_print_check_uses_the_pipeline_rule(api, pc, ok):
    r = api.post("/api/capture", json={"mode": "single_view", "print_check": pc})
    assert (r.status_code == 200) is ok, r.text
    if not ok:
        assert r.status_code == 422 and "print" in r.json()["detail"]


def test_wizard_review_shape_is_translated_and_identity_still_needs_a_person(api, clip, monkeypatch):
    calls = []
    monkeypatch.setattr(CP, "build", fake_build(calls))
    c = api
    cid = ready_capture(c, clip)["id"]
    wait(c, c.post(f"/api/capture/{cid}/track").json())
    r = c.post(f"/api/capture/{cid}/review", json={"correspondence": [{"track_id": "mug", "proxy": "cup", "label": "red mug"}],
                                                  "events": []}).json()
    assert [e["kind"] for e in r["recorded"]] == ["mapping"]
    wait(c, r["job"])
    st = c.get(f"/api/capture/{cid}").json()
    assert [x["code"] for x in st["report"]["review"] if x["blocking"]] == ["identity_uncertain"]   # never assumed
    assert c.post(f"/api/capture/{cid}/propose").status_code == 409
    bad = c.post(f"/api/capture/{cid}/review", json={"events": [{"track_id": "mug", "type": "grasp", "status": "confirmed"}]})
    assert bad.status_code == 422 and "no pick event" in bad.json()["detail"]


def test_calibrate_step_reports_quality_and_sync(api, clip, monkeypatch):
    out = {"intrinsics": {"top": {"accepted": True, "rms_px": 0.4, "n_views": 18, "checks": []}},
           "poses": {"top": {"accepted": False, "rms_px": 3.1, "checks": [{"name": "pose_rms_px", "passed": False}]}},
           "recalibration": {}, "sync": None,
           "review": [{"code": "pose_invalid", "severity": "abstain", "camera": "top", "message": "top pose rejected"}]}
    monkeypatch.setattr(CP, "calibrate", lambda cap, paths: out)
    c = api
    cap = c.post("/api/capture", json={"mode": "single_view", "print_check": PC}).json()
    assert c.post(f"/api/capture/{cap['id']}/calibrate").status_code == 409                # no clips yet
    c.post(f"/api/capture/{cap['id']}/clips", json={"clips": {"single": upload(c, clip, "single")}})
    j = wait(c, c.post(f"/api/capture/{cap['id']}/calibrate").json())
    assert j["status"] == "done" and j["result"]["calibration"]["ok"] is False
    st = c.get(f"/api/capture/{cap['id']}").json()
    assert st["state"] == "calibrated" and st["calibration"]["errors"] == ["top pose rejected"]
    assert st["calibration"]["cameras"]["top"] == {"lens_accepted": True, "pose_accepted": False, "rms_px": 0.4,
                                                   "n_views": 18, "pose_rms_px": 3.1, "ok": False}
    r = c.post(f"/api/capture/{cap['id']}/track")
    assert r.status_code == 409 and "objects" in r.json()["detail"]                       # no boxes, no tracking


def test_calibration_targets_are_served(api):
    r = api.get("/calibration/mirror_charuco_v1_A4.pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    assert api.get("/calibration/..%2Fsecrets.pdf").status_code == 404
    assert api.get("/calibration/README.md").status_code == 404


def test_export_ships_targets_and_only_a_synthetic_sample(tmp_path):
    import json
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import export_static as ex
    names = ex.copy_calibration(tmp_path)
    assert "mirror_charuco_v1_A4.pdf" in names and (tmp_path / "calibration" / "mirror_charuco_v1_Letter.pdf").exists()
    good = tmp_path / "s.json"
    good.write_text(json.dumps({"synthetic": True, "evidence_kind": "synthetic_test_render", "notes": []}))
    ex.copy_capture_sample(tmp_path, good)
    out = json.loads((tmp_path / "data" / "capture" / "synthetic_session.json").read_text())
    assert out["synthetic"] is True and "SYNTHETIC" in out["notes"][-1]
    bad = tmp_path / "b.json"
    bad.write_text(json.dumps({"synthetic": False, "evidence_kind": "camera_video_reconstruction"}))
    with pytest.raises(SystemExit):
        ex.copy_capture_sample(tmp_path, bad)


def test_synthetic_label_is_one_way(api):
    cap = api.post("/api/capture", json={"mode": "single_view"}).json()
    r = api.post(f"/api/capture/{cap['id']}/inputs", json={"synthetic": True}).json()
    assert r["synthetic"] and r["label"].startswith("SYNTHETIC")
    assert api.post(f"/api/capture/{cap['id']}/inputs", json={"synthetic": False}).status_code == 422



def _proposed_capture(c, clip, monkeypatch, synthetic=True):
    calls = []
    monkeypatch.setattr(CP, "build", fake_build(calls))
    from mirror.capture import trace as TRC
    from mirror.studio.fixtures import FixtureSession
    monkeypatch.setattr(TRC, "to_demo_trace", lambda s, demo_id=None: FixtureSession().record_demo())
    cap = c.post("/api/capture", json={"mode": "single_view", "synthetic": synthetic, "print_check": PC,
                                        "objects": [MUG]}).json()
    t = upload(c, clip, "take_top")
    c.post(f"/api/capture/{cap['id']}/clips", json={"clips": {"take_top": t}})
    c.post(f"/api/capture/{cap['id']}/prompts", json={"prompts": [{"camera": "top", "label": "mug", "frame": 0, "box": [10, 10, 40, 40]}]})
    wait(c, c.post(f"/api/capture/{cap['id']}/run").json())
    r = c.post(f"/api/capture/{cap['id']}/review", json={"objects": [{"track_label": "mug", "confirmed_by_person": True}],
                                                        "identity_confirmations": [{"object": "mug", "seconds_spent": 2}]}).json()
    wait(c, r["job"])
    j = wait(c, c.post(f"/api/capture/{cap['id']}/propose").json())
    assert j["status"] == "done", j["error"]
    return cap["id"], j


def test_synthetic_capture_provenance_reaches_every_view(api, clip, monkeypatch, tmp_path):
    c = api
    monkeypatch.setenv("MIRROR_TRIAL_STORE", str(tmp_path / "trials"))
    cid, j = _proposed_capture(c, clip, monkeypatch, synthetic=True)
    assert j["result"]["synthetic_capture"] is True and j["result"]["input_evidence"] == "synthetic_capture"
    st = c.get("/api/state").json()
    g = st["generation"]
    assert g["synthetic_capture"] and g["synthetic"] and g["evidence_label"].startswith("SYNTHETIC")
    assert st["demo"]["synthetic_capture"] and all(s["synthetic_capture"] for s in st["specs"])
    assert st["models"][-1]["synthetic_capture"] is True
    run = wait(c, c.post("/api/run", json={}).json())
    assert run["result"]["synthetic_capture"] is True
    st = c.get("/api/state").json()
    ro = st["rollouts"][-1]
    assert ro["synthetic_capture"] and ro["input_evidence"] == "synthetic_capture"
    assert c.get(f"/api/rollouts/{ro['id']}").json()["synthetic_capture"] is True
    assert c.get("/api/rollouts/demo").json()["synthetic_capture"] is True
    # a re-interpretation (new generation) keeps the label; it can never be dropped
    wait(c, c.post("/api/interpret").json())
    st = c.get("/api/state").json()
    assert st["generation"]["synthetic_capture"] and st["generation"]["reason"] == "reinterpreted"
    # the trial export carries it
    c.post("/api/study/start", json={"participant": "DEV", "consent": {"recorded": True}})
    ch = wait(c, c.post("/api/challenge", json={"family": "reference", "preset": "shortcut"}).json())["result"]["challenge_id"]
    wait(c, c.post(f"/api/challenge/{ch}/correction", json={"text": "keep the cup upright"}).json())
    from mirror.studio.trials import load_trials
    rec = load_trials(tmp_path / "trials", "dev_smoke")[-1]
    assert rec["lineage"]["synthetic_capture"] is True and rec["lineage"]["input_evidence"] == "synthetic_capture"
    assert c.get("/api/state").json()["challenges"][-1]["synthetic_capture"] is True


def test_real_phone_capture_is_labelled_as_such(api, clip, monkeypatch):
    c = api
    cid, j = _proposed_capture(c, clip, monkeypatch, synthetic=False)
    g = c.get("/api/state").json()["generation"]
    assert g["input_evidence"] == "phone_capture" and g["synthetic_capture"] is False and g["evidence_label"] is None


def test_lineage_keeps_the_synthetic_label_across_save_and_load(tmp_path):
    from mirror.studio.fixtures import FixtureSession
    s = FixtureSession()
    s.set_demo(s.record_demo(), reason="phone_capture", input_evidence="synthetic_capture", synthetic=True)
    s.interpret()
    s.save_state(tmp_path)
    s2 = FixtureSession.load_state(tmp_path)
    assert s2.generation.synthetic and s2.generation.evidence()["synthetic_capture"]


def test_capture_options_and_review_shorthands(api, clip, monkeypatch):
    c = api
    o = c.get("/api/capture/options").json()
    assert o["available"] and o["roles"]["two_view"] == ["take_top", "take_side", "lens_top", "lens_side"]
    assert o["proxies"]["cup"]["proxy_dims_m"] and o["print_rule"]["bar_tolerance_mm"] == 1.0
    calls = []
    monkeypatch.setattr(CP, "build", fake_build(calls))
    cid = ready_capture(c, clip)["id"]
    wait(c, c.post(f"/api/capture/{cid}/run").json())
    r = c.post(f"/api/capture/{cid}/review", json={"identity": [{"object": "mug", "confirmed": True, "seconds_spent": 3}]}).json()
    assert r["recorded"][0]["kind"] == "identity" and r["recorded"][0]["target"] == "top:mug@0"
    assert c.post(f"/api/capture/{cid}/review", json={"events": [{"id": "nope", "status": "confirmed"}]}).status_code == 422



def test_tray_review_items_carry_the_right_action(api, clip, monkeypatch):
    from mirror.capture import events as EV
    from mirror.capture.session import CaptureSession, tray_review

    def build(cap, paths):
        region = EV.TrayRegion(center_xy=(0.1, 0.1), inner_half=(0.5, 0.1), footprint_source="assumed_proxy")
        s = CaptureSession(synthetic=True, review=tray_review(None, []) + tray_review(region, []))
        s.finalise_kind()
        return s
    monkeypatch.setattr(CP, "build", build)
    c = api
    cid = ready_capture(c, clip)["id"]
    wait(c, c.post(f"/api/capture/{cid}/track").json())
    items = {x["code"]: x for x in c.get(f"/api/capture/{cid}").json()["report"]["review"]}
    assert set(items) == {"tray_not_mapped", "tray_size_unsupported", "tray_footprint_assumed"}
    for code in ("tray_not_mapped", "tray_size_unsupported"):
        assert items[code]["blocking"] and not items[code]["needs_new_input"] and items[code]["how_to_resolve"]
    assert items["tray_not_mapped"]["action"]["via"] == ["inputs", "prompts"]
    assert items["tray_size_unsupported"]["action"]["via"] == ["inputs"]
    fa = items["tray_footprint_assumed"]
    assert not fa["blocking"] and fa["action"]["optional"] and "ruler" in fa["how_to_resolve"]
    assert c.post(f"/api/capture/{cid}/propose").status_code == 409
    o = c.get("/api/capture/options").json()
    assert "tray_not_mapped" in o["review"]["how_to_resolve"] and "tray_not_mapped" in o["review"]["actions"]
