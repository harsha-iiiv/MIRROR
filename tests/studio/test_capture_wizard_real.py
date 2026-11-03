"""The web capture wizard against the REAL studio server and the real offline phone pipeline,
on a SYNTHETIC rendered take (python -m mirror.capture.ingest demo). Nothing here is real footage.

The wizard has no object-mapping form or box-drawing tool yet, so this test supplies the objects
(mug, tray) and the person-drawn boxes through the API, exactly as those tools would."""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

requests = pytest.importorskip("httpx")

pytest.importorskip("cv2")
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


def test_wizard_upload_calibrate_track_review_propose(demo, tmp_path):
    pw = pytest.importorskip("playwright.sync_api")
    cfg = json.loads((demo / "capture.json").read_text())
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": str(ROOT), "MIRROR_STUDIO_BACKEND": "live", "MIRROR_LLM_MODE": "offline",
           "MIRROR_STUDIO_RUNTIME": str(tmp_path / "rt")}
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "mirror.studio.app:app", "--host", "127.0.0.1", "--port", str(port)],
                           cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(120):
            try:
                if requests.get(base + "/api/health", timeout=2).is_success:
                    break
            except requests.HTTPError:
                time.sleep(0.5)
        assert requests.get(base + "/calibration/mirror_charuco_v1_A4.pdf", timeout=10).content[:4] == b"%PDF"
        with pw.sync_playwright() as p:
            b = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
            page = b.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/?step=capture", wait_until="load")
            page.wait_for_selector("text=Honest status", timeout=60_000)
            page.locator(".cap-step-h").nth(1).click()
            page.get_by_label("Scale bar measured (mm)").fill("50")
            page.get_by_label("One square measured (mm)").fill("30")
            page.get_by_role("tab", name="Upload & review").click()
            page.get_by_label("Phones").select_option("single_view")
            page.get_by_label("Take", exact=True).set_input_files(str(demo / "synthetic_top.mp4"))
            page.get_by_label("Lens calibration clip").set_input_files(str(demo / "lens_top.mp4"))
            page.get_by_role("button", name="Upload and check clips").click()
            page.wait_for_selector("text=clips_ready", timeout=120_000)

            origin = base
            sid = page.evaluate(f"sessionStorage.getItem('mirror-session:{origin}')")
            tok = page.evaluate(f"sessionStorage.getItem('mirror-session:{origin}:token')")
            h = {"X-Mirror-Session": sid, "X-Mirror-Token": tok}
            st = requests.get(base + "/api/state", headers=h, timeout=10).json()
            cid = st["captures"][-1]["id"]
            # Rendered clips: label the capture SYNTHETIC before anything is computed from it.
            assert requests.post(f"{base}/api/capture/{cid}/inputs", json={"synthetic": True}, headers=h, timeout=10).is_success

            page.get_by_role("button", name="Calibrate").click()
            page.wait_for_selector("text=Calibration accepted.", timeout=600_000)

            # What the missing mapping form and box tool would send (the mug and tray are not yet confirmed).
            objects = [{**o, "confirmed_by_person": False} for o in cfg["objects"]]
            objects.append({"track_label": "tray", "real_identity": "shallow tray (SYNTHETIC render)", "proxy": "tray",
                            "confirmed_by_person": False})
            r = requests.post(f"{base}/api/capture/{cid}/inputs", headers=h, timeout=10,
                              json={"objects": objects, "tags": cfg.get("tags"), "tray": cfg.get("tray")})
            assert r.is_success, r.text
            r = requests.post(f"{base}/api/capture/{cid}/prompts", headers=h, timeout=10,
                              json={"prompts": [x for x in cfg["prompts"] if x["camera"] == "top"]})
            assert r.is_success, r.text

            page.get_by_role("button", name="Track").click()
            page.wait_for_selector(".cap-review", timeout=900_000)
            review_txt = page.locator(".cap-review").text_content()
            assert "SYNTHETIC" in review_txt
            propose_btn = page.get_by_role("button", name="Propose rules from this capture")
            assert propose_btn.is_disabled()
            assert requests.post(f"{base}/api/capture/{cid}/propose", headers=h, timeout=10).status_code == 409
            cap = requests.get(f"{base}/api/capture/{cid}", headers=h, timeout=10).json()
            assert cap["session"] and not cap["gate_open"]
            blocking0 = {x["code"] for x in cap["report"]["review"] if x["blocking"]}
            assert "mapping_unconfirmed" in blocking0

            page.get_by_role("button", name="Save review").click()          # the wizard's review shape
            try:
                page.wait_for_function("() => document.body.textContent.includes('reviewed')", timeout=120_000)
            except Exception:
                print("PAGE", page.locator(".panel").text_content()[-1500:])
                raise
            for _ in range(600):
                cap = requests.get(f"{base}/api/capture/{cid}", headers=h, timeout=10).json()
                if cap["state"] == "reviewed" and not cap["stale"]:
                    break
                time.sleep(1)
            left = [x for x in cap["report"]["review"] if x["blocking"]]
            assert "mapping_unconfirmed" not in {x["code"] for x in left}
            # Whatever still blocks needs an explicit person action the wizard does not offer yet.
            assert requests.post(f"{base}/api/capture/{cid}/propose", headers=h, timeout=10).status_code == (409 if left else 200)
            if left:
                body = {"identity_confirmations": [["top", x["object"], 0] for x in left if x["code"] == "identity_uncertain"],
                        "corrections": [{"target": x["event"], "field": "confirm", "seconds_spent": 1.0}
                                        for x in left if x["code"] == "ambiguous_event_interval"]}
                assert {x["code"] for x in left} <= {"identity_uncertain", "ambiguous_event_interval"}, left
                out = requests.post(f"{base}/api/capture/{cid}/review", json=body, headers=h, timeout=10).json()
                for _ in range(900):
                    j = requests.get(f"{base}/api/jobs/{out['job']['id']}", headers=h, timeout=10).json()
                    if j["status"] in ("done", "error"):
                        break
                    time.sleep(1)
                cap = requests.get(f"{base}/api/capture/{cid}", headers=h, timeout=10).json()
                assert cap["gate_open"], [x for x in cap["report"]["review"] if x["blocking"]]
                j = requests.post(f"{base}/api/capture/{cid}/propose", headers=h, timeout=10).json()
            else:
                j = requests.post(f"{base}/api/capture/{cid}/propose", headers=h, timeout=10)
                j = j.json()
            for _ in range(600):
                jj = requests.get(f"{base}/api/jobs/{j['id']}", headers=h, timeout=10).json()
                if jj["status"] in ("done", "error"):
                    break
                time.sleep(1)
            assert jj["status"] == "done", jj.get("error")
            st = requests.get(base + "/api/state", headers=h, timeout=10).json()
            assert st["generation"]["reason"] == "phone_capture" and st["specs"]
            assert st["generation"]["calibration"]["synthetic"] is True
            cap = requests.get(f"{base}/api/capture/{cid}", headers=h, timeout=10).json()
            assert cap["state"] == "proposed" and cap["effort"]["actions"] >= 2
            print("E2E", json.dumps({"blocking_initial": sorted(blocking0), "left_after_wizard_review": [x["code"] for x in left],
                                     "effort": cap["effort"], "spec": st["specs"][-1]["version_id"],
                                     "calibration": cap["calibration"]["cameras"]}, default=str))
            assert not errors, errors
            b.close()
    finally:
        srv.terminate()
