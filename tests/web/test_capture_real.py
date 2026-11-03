"""The phone-capture wizard end to end against the REAL studio server and the real offline phone
pipeline, driven only through the page (no API calls for any action), at 390 px (touch) and
1280 px (mouse). The clips are a SYNTHETIC rendered take from `python -m mirror.capture.ingest demo`;
nothing here is real footage, and the test checks that the result says so everywhere."""
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
    d = tmp_path_factory.mktemp("synthetic_take")
    subprocess.run([sys.executable, "-m", "mirror.capture.ingest", "demo", str(d)], check=True, timeout=900, cwd=ROOT,
                   stdin=subprocess.DEVNULL, capture_output=True, env={**os.environ, "PYTHONPATH": str(ROOT)})
    return d


@pytest.fixture()
def server(tmp_path):
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": str(ROOT), "MIRROR_STUDIO_BACKEND": "live", "MIRROR_LLM_MODE": "offline",
           "MIRROR_STUDIO_RUNTIME": str(tmp_path / "rt")}
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "mirror.studio.app:app", "--host", "127.0.0.1", "--port", str(port)],
                           cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(240):
        try:
            if httpx.get(base + "/api/health", timeout=2).is_success:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    yield base
    srv.terminate()
    srv.wait(timeout=30)


def _drag(page, frame_sel, box, touch):
    """Draw `box` ([x, y, w, h] in image pixels) on the frame: real mouse or real touch input."""
    img = page.locator(frame_sel + " img")
    page.wait_for_function("(s) => { const i = document.querySelector(s); return i && i.complete && i.naturalWidth > 0; }",
                           arg=frame_sel + " img", timeout=60_000)
    img.scroll_into_view_if_needed()
    nw = img.evaluate("(i) => i.naturalWidth")
    r = img.bounding_box()
    s = r["width"] / nw
    x0, y0 = r["x"] + box[0] * s, r["y"] + box[1] * s
    x1, y1 = r["x"] + (box[0] + box[2]) * s, r["y"] + (box[1] + box[3]) * s
    if not touch:
        page.mouse.move(x0, y0); page.mouse.down(); page.mouse.move((x0 + x1) / 2, (y0 + y1) / 2, steps=4)
        page.mouse.move(x1, y1, steps=4); page.mouse.up()
        return
    cdp = page.context.new_cdp_session(page)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x0, "y": y0}]})
    for k in range(1, 6):
        cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x0 + (x1 - x0) * k / 5, "y": y0 + (y1 - y0) * k / 5}]})
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    cdp.detach()


@pytest.mark.parametrize("width,mode", [(390, "single_view"), (1280, "two_view")])
def test_capture_wizard_completes_through_the_page(demo, server, width, mode):
    pw = pytest.importorskip("playwright.sync_api")
    cfg = json.loads((demo / "capture.json").read_text())
    touch = width < 600
    with pw.sync_playwright() as p:
        b = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        ctx = b.new_context(viewport={"width": width, "height": 900}, has_touch=touch, is_mobile=touch)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(server + "/?step=capture", wait_until="load")
        page.wait_for_selector("text=Honest status", timeout=60_000)
        page.locator(".cap-step-h").nth(1).click()
        page.get_by_label("Scale bar measured (mm)").fill("50")
        page.get_by_label("One square measured (mm)").fill("30")
        page.get_by_role("tab", name="Upload & review").click()

        # 1. Clips (SYNTHETIC renders, declared as such)
        page.get_by_label("Phones").select_option(mode)
        if mode == "two_view":
            files = {"Top take": "synthetic_top.mp4", "Side take": "synthetic_side.mp4", "Top lens clip": "lens_top.mp4", "Side lens clip": "lens_side.mp4"}
        else:
            files = {"Take": "synthetic_top.mp4", "Lens calibration clip": "lens_top.mp4"}
        for label, f in files.items():
            page.get_by_label(label, exact=True).set_input_files(str(demo / f))
        page.get_by_label("These clips are SYNTHETIC test renders, not real footage").check()
        page.get_by_role("button", name="Upload and check clips").click()
        page.wait_for_selector("text=clips_ready", timeout=180_000)

        # 2. Calibrate
        page.get_by_role("button", name="Calibrate").click()
        page.wait_for_selector("text=Calibration accepted.", timeout=900_000)
        if mode == "two_view":
            page.wait_for_selector("text=Synchronised:", timeout=10_000)

        # 3. Objects (mug measured with a ruler; tray position from the board origin); left unconfirmed
        mug = next(o for o in cfg["objects"] if o["track_label"] == "mug")
        page.get_by_label("Object 1 real identity").fill(mug["real_identity"])
        page.get_by_label("Object 1 height in centimetres").fill(str(round(mug["real_dims"]["height"]["value_m"] * 100, 1)))
        page.get_by_label("Object 1 contents").fill(mug["real_contents"])
        page.get_by_label("Object 1 unsupported features").fill(", ".join(mug["unsupported_features"]))
        page.get_by_label("Object 2 real identity").fill("shallow tray (SYNTHETIC render)")
        page.get_by_text("Optional: where the tray is").click()
        tr = cfg["tray"]
        for lab, v in (("Tray centre x (cm)", tr["center_xy"][0] * 100), ("Tray centre y (cm)", tr["center_xy"][1] * 100),
                       ("Tray inside width (cm)", tr["inner_half"][0] * 200), ("Tray inside depth (cm)", tr["inner_half"][1] * 200)):
            page.get_by_label(lab).fill(f"{v:.1f}")
        page.get_by_role("button", name="Save objects").click()
        page.wait_for_selector("text=2 objects saved.", timeout=30_000)

        # 4. Boxes drawn on the served frames
        cams = ["top", "side"] if mode == "two_view" else ["top"]
        for i, cam in enumerate(cams):
            pr = next(x for x in cfg["prompts"] if x["camera"] == cam and x["label"] == "mug")
            page.get_by_label(f"{cam} frame index").fill(str(pr["frame"]))
            page.get_by_label(f"Object to box in the {cam} view").select_option("mug")
            _drag(page, f"[aria-label^='{cam} camera frame']", pr["box"], touch)
            page.wait_for_selector(".cap-box", timeout=10_000)
        assert page.locator(".cap-boxlist li").count() == len(cams)
        page.get_by_role("button", name="Save boxes").click()
        page.wait_for_selector(f"text={len(cams)} boxes saved.", timeout=30_000)

        # 5. Track
        page.get_by_role("button", name="Track").click()
        page.wait_for_selector(".cap-review", timeout=1_200_000)
        assert "SYNTHETIC test render - not real footage" in page.locator(".cap-review").text_content()
        propose = page.get_by_role("button", name="Propose rules from this capture")
        assert propose.is_disabled()
        assert "What is this object?" in page.locator(".cap-review").text_content()

        # 6. Review: confirm mappings, identities and brackets until nothing blocks (each save re-runs)
        for rnd in range(4):
            if not propose.is_disabled():
                break
            acted = 0
            for name in ("Confirm: this is the cup", "Confirm: this is the tray", "Same object, I checked the video", "Confirm the bracket"):
                btns = page.get_by_role("button", name=name)
                for k in range(btns.count()):
                    btns.nth(k).click(); acted += 1
            assert acted, page.locator(".cap-review").text_content()[-1500:]
            page.get_by_role("button", name="Save review").first.click()
            page.wait_for_function("() => !document.body.textContent.includes('working…')", timeout=1_200_000)
            page.wait_for_selector(".cap-review", timeout=60_000)
        assert not propose.is_disabled(), page.locator(".cap-review").text_content()[-1500:]
        assert "SYNTHETIC" in page.locator(".panel").text_content()

        # 7. Propose; the new demonstration and everything shown from it carries SYNTHETIC
        propose.click()
        page.wait_for_selector("text=Proposed.", timeout=900_000)
        page.wait_for_selector(".synthetic-banner", timeout=60_000)
        page.get_by_role("button", name="Review the proposed rules").click()
        page.wait_for_timeout(1000)
        assert "SYNTHETIC" in page.locator(".banner").text_content()
        assert "SYNTHETIC" in page.locator(".panel").text_content()
        assert page.evaluate("document.documentElement.scrollWidth") <= width
        assert not errors, errors

        # Read-only check of what the page did (no action goes through the API).
        sid = page.evaluate(f"sessionStorage.getItem('mirror-session:{server}')")
        tok = page.evaluate(f"sessionStorage.getItem('mirror-session:{server}:token')")
        st = httpx.get(server + "/api/state", headers={"X-Mirror-Session": sid, "X-Mirror-Token": tok}, timeout=10).json()
        assert st["generation"]["reason"] == "phone_capture" and st["specs"]
        assert st["generation"]["calibration"]["synthetic"] is True
        cap = st["captures"][-1]
        assert cap["state"] == "proposed"
        print("E2E", width, mode, json.dumps({"effort": cap.get("effort"), "spec": st["specs"][-1].get("version_id")}, default=str))
        b.close()
