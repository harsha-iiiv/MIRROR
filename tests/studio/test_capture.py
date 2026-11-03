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
    assert "path" not in str(r["clips"]) and r["state"] == "inputs_needed"
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
    assert r["state"] == "ready_to_run" and not r["missing"]
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
    assert st["gate_open"] and st["report"]["review"][0]["code"] == "tilt_unobserved"
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
