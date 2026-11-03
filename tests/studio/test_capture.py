"""Real-phone capture sessions: clip roles, declared synchronisation, review, hooks.

Videos are SYNTHETIC test patterns; the phone reconstruction module is a stub."""
import subprocess
import sys
import time
import types

import pytest
from fastapi.testclient import TestClient

from mirror.studio import uploads as up
from mirror.studio.app import create_app
from mirror.studio.session_adapter import StudioService
from mirror.studio.sessions import SessionManager

pytestmark = pytest.mark.skipif(not up.tools_available(), reason="ffmpeg/ffprobe needed")


def synth(path, seconds):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                    "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    str(path)], check=True, timeout=60, stdin=subprocess.DEVNULL)
    return path.read_bytes()


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("synthetic_capture")
    return {"a": synth(d / "a.mp4", 2.0), "b": synth(d / "b.mp4", 2.2), "long": synth(d / "c.mp4", 9.0)}


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
        if j["status"] in ("done", "error"):
            return j
        assert time.time() - t0 < 30
        time.sleep(0.02)


@pytest.fixture()
def phone(monkeypatch):
    from mirror.studio.fixtures import FixtureSession
    mod = types.ModuleType("mirror.perception.phone")
    calls = []

    def calibrate(cap):
        calls.append("calibrate")
        assert set(cap["paths"]) >= {"calibration"}
        return {"ok": mod.calib_ok, "quality": {"reprojection_px": 0.6}, "errors": [] if mod.calib_ok else ["board not found"]}

    def track(cap):
        calls.append("track")
        return {"tracks": [{"id": "1"}, {"id": "2"}], "events": [], "coverage": 0.9}

    def reconstruct(cap, review):
        calls.append("reconstruct")
        s = FixtureSession()
        return s.record_demo()
    mod.calibrate, mod.track, mod.reconstruct, mod.calib_ok, mod.calls = calibrate, track, reconstruct, True, calls
    monkeypatch.setitem(sys.modules, "mirror.perception.phone", mod)
    return mod


def test_two_view_capture_needs_three_clips_and_a_declared_sync(api, clips):
    c = api
    cap = c.post("/api/capture", json={"mode": "two_view"}).json()
    top, side, cal = upload(c, clips["a"], "top"), upload(c, clips["b"], "side"), upload(c, clips["a"], "calibration")
    r = c.post(f"/api/capture/{cap['id']}/clips", json={"clips": {"top": top, "side": side}})
    assert r.status_code == 422 and "calibration" in r.json()["detail"]
    r = c.post(f"/api/capture/{cap['id']}/clips", json={"clips": {"top": top, "side": side, "calibration": cal}})
    assert r.status_code == 422 and "synchronisation" in r.json()["detail"]
    r = c.post(f"/api/capture/{cap['id']}/clips", json={"clips": {"top": top, "side": side, "calibration": cal},
                                                       "sync": {"method": "clap", "top_t": 0.5, "side_t": 9.0}})
    assert r.status_code == 422 and "outside" in r.json()["detail"]
    r = c.post(f"/api/capture/{cap['id']}/clips", json={"clips": {"top": side, "side": top, "calibration": cal},
                                                       "sync": {"method": "clap", "top_t": 0.5, "side_t": 0.7}})
    assert r.status_code == 409                                    # roles must match the upload views
    ok = c.post(f"/api/capture/{cap['id']}/clips", json={"clips": {"top": top, "side": side, "calibration": cal},
                                                        "sync": {"method": "clap", "top_t": 0.5, "side_t": 0.7}}).json()
    assert ok["state"] == "clips_ready" and ok["sync"]["status"] == "declared" and ok["sync"]["offset_s"] == 0.2
    assert "path" not in str(ok["clips"]) and ok["clips"]["top"]["sha256"]


def test_separate_takes_are_refused(api, clips):
    c = api
    cap = c.post("/api/capture", json={"mode": "two_view"}).json()
    top, side, cal = upload(c, clips["a"], "top"), upload(c, clips["long"], "side"), upload(c, clips["a"], "calibration")
    r = c.post(f"/api/capture/{cap['id']}/clips", json={"clips": {"top": top, "side": side, "calibration": cal},
                                                       "sync": {"method": "clap", "top_t": 0.5, "side_t": 0.5}})
    assert r.status_code == 422 and "same action" in r.json()["detail"]


def test_without_the_phone_module_nothing_is_faked(api, clips, monkeypatch):
    monkeypatch.setitem(sys.modules, "mirror.perception.phone", None)
    c = api
    cap = c.post("/api/capture", json={"mode": "single_view"}).json()
    assert "not observed" in cap["observability"]
    s, cal = upload(c, clips["a"], "single"), upload(c, clips["a"], "calibration")
    c.post(f"/api/capture/{cap['id']}/clips", json={"clips": {"single": s, "calibration": cal}})
    r = c.post(f"/api/capture/{cap['id']}/calibrate")
    assert r.status_code == 409 and "not available" in r.json()["detail"]


def test_capture_to_review_to_new_generation(api, clips, phone):
    c = api
    cap = c.post("/api/capture", json={"mode": "single_view"}).json()
    s, cal = upload(c, clips["a"], "single"), upload(c, clips["a"], "calibration")
    c.post(f"/api/capture/{cap['id']}/clips", json={"clips": {"single": s, "calibration": cal}})
    assert c.post(f"/api/capture/{cap['id']}/track").status_code == 409             # calibrate first
    phone.calib_ok = False
    wait(c, c.post(f"/api/capture/{cap['id']}/calibrate").json())
    st = c.get(f"/api/capture/{cap['id']}").json()
    assert st["state"] == "clips_ready" and "board not found" in st["errors"][-1]
    phone.calib_ok = True
    wait(c, c.post(f"/api/capture/{cap['id']}/calibrate").json())
    wait(c, c.post(f"/api/capture/{cap['id']}/track").json())
    assert c.post(f"/api/capture/{cap['id']}/propose").status_code == 409           # review first
    bad = c.post(f"/api/capture/{cap['id']}/review", json={"correspondence": [{"track_id": "1", "proxy": "cup"},
                                                                             {"track_id": "2", "proxy": "cup"}]})
    assert bad.status_code == 422
    rv = c.post(f"/api/capture/{cap['id']}/review", json={
        "correspondence": [{"track_id": "1", "proxy": "cup", "label": "mug with rice", "dimensions_m": [0.08, 0.08, 0.1],
                            "unsupported_features": ["handle"], "assumed": {"contents": "rice (simulated as beads)"}},
                           {"track_id": "2", "proxy": None, "label": "phone stand"}],
        "events": [{"track_id": "1", "type": "carry_start", "t_start": 0.4, "t_end": 0.6, "status": "edited"}]}).json()
    assert rv["state"] == "reviewed" and rv["review"]["events"][0]["observed_as"].startswith("human hand")
    assert rv["review"]["correspondence"][0]["dimensions_source"] == "manual"
    before = c.get("/api/state").json()["generation"]["id"]
    j = wait(c, c.post(f"/api/capture/{cap['id']}/propose").json())
    assert j["status"] == "done", j["error"]
    s2 = c.get("/api/state").json()
    assert s2["generation"]["id"] != before and s2["generation"]["reason"] == "phone_capture"
    assert {m["role"] for m in s2["generation"]["source_media"]} == {"single", "calibration"}
    assert s2["generation"]["calibration"]["quality"]["reprojection_px"] == 0.6
    assert phone.calls == ["calibrate", "calibrate", "track", "reconstruct"]
