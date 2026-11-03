"""The capture API against the REAL studio server process and the real offline phone pipeline,
with no page: two phones, calibrate, map, draw boxes, track, review in the wizard's shape, propose.
The clips are a SYNTHETIC rendered take (python -m mirror.capture.ingest demo); nothing here is real
footage, and the proposal must say so. The page-driven flow is tests/web/test_capture_real.py."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("cv2")
httpx = pytest.importorskip("httpx")
pytestmark = pytest.mark.slow
ROOT = Path(__file__).resolve().parents[2]


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    d = tmp_path_factory.mktemp("synthetic_demo_take")
    subprocess.run([sys.executable, "-m", "mirror.capture.ingest", "demo", str(d)], check=True, timeout=900, cwd=ROOT,
                   stdin=subprocess.DEVNULL, capture_output=True, env={**os.environ, "PYTHONPATH": str(ROOT)})
    return d


@pytest.fixture()
def server(tmp_path):
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": str(ROOT), "MIRROR_STUDIO_BACKEND": "live", "MIRROR_LLM_MODE": "offline",
           "MIRROR_STUDIO_RUNTIME": str(tmp_path / "rt")}
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "mirror.studio.app:app", "--host", "127.0.0.1",
                            "--port", str(port)], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(240):
            try:
                if httpx.get(base + "/api/health", timeout=2).is_success:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        yield base
    finally:
        srv.terminate()
        srv.wait(timeout=30)


def _wait(c, job, limit=900):
    for _ in range(limit):
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("done", "error", "cancelled"):
            return j
        time.sleep(1)
    raise AssertionError(f"job {job['id']} did not finish")


def test_two_phone_capture_through_the_api(demo, server):
    cfg = json.loads((demo / "capture.json").read_text())
    s = httpx.post(server + "/api/session", timeout=10).json()
    c = httpx.Client(base_url=server, timeout=60, headers={"X-Mirror-Session": s["session"], "X-Mirror-Token": s["token"]})
    assert c.get("/calibration/mirror_charuco_v1_A4.pdf").content[:4] == b"%PDF"
    assert c.post("/api/capture", json={"mode": "two_view", "print_check": {"measured_bar_mm": 52, "measured_square_mm": 30}}
                  ).status_code == 422
    cap = c.post("/api/capture", json={"mode": "two_view", "synthetic": True,
                                       "print_check": {"measured_bar_mm": 50, "measured_square_mm": 30}}).json()
    cid = cap["id"]
    clips = {}
    for role, name in (("take_top", "synthetic_top.mp4"), ("take_side", "synthetic_side.mp4"),
                       ("lens_top", "lens_top.mp4"), ("lens_side", "lens_side.mp4")):
        r = c.post(f"/api/video/upload?view={role}", content=(demo / name).read_bytes())
        assert r.is_success, r.text
        clips[role] = r.json().get("id") or r.json().get("video_id")
    assert c.post(f"/api/capture/{cid}/clips", json={"clips": clips}).json()["state"] == "clips_ready"

    j = _wait(c, c.post(f"/api/capture/{cid}/calibrate").json())
    assert j["status"] == "done", j.get("error")
    st = c.get(f"/api/capture/{cid}").json()
    assert st["state"] == "calibrated" and st["calibration"]["ok"], st["calibration"]
    assert set(st["calibration"]["cameras"]) == {"top", "side"} and st["sync"]["status"] == "detected"
    assert c.post(f"/api/capture/{cid}/track").status_code == 409            # no objects or boxes yet

    objects = [{**o, "confirmed_by_person": False} for o in cfg["objects"]]
    objects.append({"track_label": "tray", "real_identity": "shallow tray (SYNTHETIC render)", "proxy": "tray",
                    "confirmed_by_person": False})
    assert c.post(f"/api/capture/{cid}/inputs", json={"objects": objects, "tags": cfg.get("tags"),
                                                      "tray": cfg.get("tray")}).is_success
    assert c.get(f"/api/capture/{cid}/frame/take_top/0").headers["content-type"] == "image/png"
    assert c.post(f"/api/capture/{cid}/prompts", json={"prompts": cfg["prompts"]}).is_success

    j = _wait(c, c.post(f"/api/capture/{cid}/track").json())
    assert j["status"] == "done", j.get("error")
    st = c.get(f"/api/capture/{cid}").json()
    assert st["state"] == "tracked" and st["session"]["synthetic"] is True and not st["gate_open"]
    assert "mapping_unconfirmed" in {x["code"] for x in st["report"]["review"] if x["blocking"]}
    assert c.post(f"/api/capture/{cid}/propose").status_code == 409

    # The wizard's review shape: confirm each object, then confirm what the re-run still asks.
    r = c.post(f"/api/capture/{cid}/review", json={"correspondence": [
        {"track_id": m["track_label"], "proxy": m["proxy"] if m["proxy"] in ("cup", "block", "marker") else None,
         "label": m["real_identity"], "seconds_spent": 2.0} for m in objects]}).json()
    assert _wait(c, r["job"])["status"] == "done"
    for _ in range(3):
        st = c.get(f"/api/capture/{cid}").json()
        left = [x for x in st["report"]["review"] if x["blocking"]]
        if not left:
            break
        assert not any(x["needs_new_input"] for x in left), left
        assert c.post(f"/api/capture/{cid}/propose").status_code == 409
        body = {"identity": [{"object": x["object"], "confirmed": True, "seconds_spent": 1.0}
                             for x in left if x["code"] == "identity_uncertain"],
                "events": [{"id": x["event"], "status": "confirmed", "seconds_spent": 1.0}
                           for x in left if x["code"] == "ambiguous_event_interval"]}
        r = c.post(f"/api/capture/{cid}/review", json=body)
        assert r.is_success, r.text
        assert _wait(c, r.json()["job"])["status"] == "done"
    st = c.get(f"/api/capture/{cid}").json()
    assert st["gate_open"] and st["state"] == "reviewed", [x for x in st["report"]["review"] if x["blocking"]]

    j = _wait(c, c.post(f"/api/capture/{cid}/propose").json())
    assert j["status"] == "done", j.get("error")
    assert j["result"]["synthetic_capture"] is True
    snap = c.get("/api/state").json()
    g = snap["generation"]
    assert g["reason"] == "phone_capture" and g["synthetic_capture"] and g["evidence_label"].startswith("SYNTHETIC")
    assert snap["specs"] and all(sp["synthetic_capture"] for sp in snap["specs"]) and snap["demo"]["synthetic_capture"]
    st = c.get(f"/api/capture/{cid}").json()
    assert st["state"] == "proposed" and st["effort"]["actions"] >= len(objects)
