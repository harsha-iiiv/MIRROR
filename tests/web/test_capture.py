"""Phone capture wizard in the browser (slow). Every capture session here is SYNTHETIC
(rendered clips processed by the offline pipeline, tests/web/fixtures); nothing is real footage."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.web.fake_server import FakeServer
from tests.web.harness import StaticServer

pytestmark = pytest.mark.slow
ROOT = Path(__file__).resolve().parents[2]
SESSION = Path(__file__).parent / "fixtures" / "capture_synthetic_session.json"


@pytest.fixture(scope="module")
def fixture_data(tmp_path_factory):
    out = tmp_path_factory.mktemp("export") / "site"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "export_static.py"), "--backend", "fixture",
                    "--allow-fixture", "--seeds", "2", "--out", str(out)], check=True, timeout=300,
                   cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True)
    return out / "data"


@pytest.fixture(scope="module")
def browser():
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        b = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        yield b
        b.close()


def _page(browser, url, width=390):
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url, wait_until="load")
    return ctx, page, errors


@pytest.fixture()
def site_with_sample(fixture_data, tmp_path):
    site = tmp_path / "site"
    shutil.copytree(fixture_data.parent, site)
    (site / "data" / "capture").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SESSION, site / "data" / "capture" / "synthetic_session.json")
    return site


@pytest.mark.parametrize("width", [390, 1280])
def test_guide_and_synthetic_sample_review(site_with_sample, browser, width):
    with StaticServer(site_with_sample) as srv:
        ctx, page, errors = _page(browser, srv.url + "?step=capture", width)
        page.wait_for_selector("text=tested on synthetic renders only", timeout=30_000)
        assert "awaiting footage" in page.locator(".cap-honesty").text_content()
        page.locator(".cap-step-h").nth(1).click()
        page.get_by_label("Scale bar measured (mm)").fill("47")
        page.get_by_label("One square measured (mm)").fill("28.2")
        page.wait_for_selector("text=Reprint at 100% before continuing.")
        page.get_by_label("Scale bar measured (mm)").fill("50.2")
        page.get_by_label("One square measured (mm)").fill("33")
        page.wait_for_selector("text=These two measurements disagree; measure again.")
        page.get_by_label("One square measured (mm)").fill("30.1")
        page.wait_for_selector("text=Print scale checked.")
        page.get_by_role("tab", name="Upload & review").click()
        page.wait_for_selector(".cap-review", timeout=10_000)
        txt = page.locator(".panel").text_content()
        assert "SYNTHETIC test render - not real footage" in txt and "Uploading needs the live studio" in txt
        assert "No model calls (person-drawn boxes + classical tracking)" in txt
        assert "Synchronised by the card" in txt and "Proxy fidelity is not measured physical accuracy." in txt
        assert "Inferred from object motion (human hand), not robot contact" in txt
        assert "Needs your review before MIRROR proposes rules" in txt and "1 item must be resolved first." in txt
        propose = page.get_by_role("button", name="Propose rules from this capture")
        assert propose.is_disabled()
        page.get_by_role("button", name="Set time").nth(1).click()     # the put-down bracket is the blocking one
        page.wait_for_selector("text=Ready: nothing blocks a proposal.")
        assert propose.is_disabled(), "nothing is proposed from a synthetic sample"
        assert page.evaluate("document.documentElement.scrollWidth") == width
        assert not errors, errors
        ctx.close()


def test_upload_without_processing_hooks_says_so(fixture_data, browser):
    with FakeServer(fixture_data) as srv:
        ctx, page, errors = _page(browser, srv.url + "?step=capture")
        page.wait_for_selector("text=Honest status", timeout=30_000)
        page.get_by_role("tab", name="Upload & review").click()
        page.wait_for_selector("text=Check the print scale first")
        upload = page.get_by_role("button", name="Upload and check clips")
        assert upload.is_disabled()
        page.get_by_role("tab", name="Guide").click()
        page.locator(".cap-step-h").nth(1).click()
        page.get_by_label("Scale bar measured (mm)").fill("50")
        page.get_by_label("One square measured (mm)").fill("30")
        page.get_by_role("tab", name="Upload & review").click()
        clip = {"name": "clip.mp4", "mimeType": "video/mp4", "buffer": b"\x00" * 64}    # placeholder bytes; the fake server does not decode
        for label in ("Top take", "Side take", "Top lens clip", "Side lens clip"):
            page.get_by_label(label, exact=True).set_input_files(clip)
        upload.click()
        page.wait_for_selector("text=clips_ready", timeout=15_000)
        page.wait_for_selector("text=Processing is not available on this server yet")
        assert page.get_by_role("button", name="Calibrate").is_disabled()
        cap = next(iter(srv.studio.captures.values()))
        assert cap["print_check"]["measured_bar_mm"] == 50 and sorted(cap["clips"]) == ["lens_side", "lens_top", "take_side", "take_top"]
        assert not errors, errors
        ctx.close()


def test_upload_review_and_propose_with_hooks(fixture_data, browser):
    with FakeServer(fixture_data) as srv:
        srv.studio.capture_hooks = True
        srv.studio.capture_session = {**json.loads(SESSION.read_text()), "synthetic": False, "evidence_kind": "unverified_camera_video"}
        ctx, page, errors = _page(browser, srv.url + "?step=capture", 1280)
        page.wait_for_selector("text=Honest status", timeout=30_000)
        page.locator(".cap-step-h").nth(1).click()
        page.get_by_label("Scale bar measured (mm)").fill("50")
        page.get_by_label("One square measured (mm)").fill("30")
        page.get_by_role("tab", name="Upload & review").click()
        page.get_by_label("Phones").select_option("single_view")
        clip = {"name": "clip.mp4", "mimeType": "video/mp4", "buffer": b"\x00" * 64}
        page.get_by_label("Take", exact=True).set_input_files(clip)
        page.get_by_label("Lens calibration clip").set_input_files(clip)
        page.get_by_role("button", name="Upload and check clips").click()
        page.wait_for_selector("text=clips_ready", timeout=15_000)
        page.get_by_role("button", name="Calibrate").click()
        page.wait_for_selector("text=Calibration accepted.", timeout=15_000)
        # objects: incomplete form is refused in place
        page.get_by_label("Object 1 real identity").fill("white mug")
        assert page.get_by_role("button", name="Save objects").is_disabled()
        page.get_by_label("Object 2 real identity").fill("tray")
        page.get_by_label("Object 1 height in centimetres").fill("9.5")
        page.get_by_role("button", name="Save objects").click()
        page.wait_for_selector("text=2 objects saved.", timeout=10_000)
        cap = next(iter(srv.studio.captures.values()))
        mug = cap["inputs"]["objects"][0]
        assert mug["real_dims"]["height"] == {"value_m": 0.095, "source": "measured_ruler"} and mug["proxy"] == "cup"
        assert cap["inputs"]["objects"][1]["proxy"] == "tray"
        assert page.get_by_role("button", name="Track").is_disabled()        # boxes still missing
        page.route("**/frame/**", lambda r: r.fulfill(path=str(Path(__file__).parent / "fixtures" / "frame_synthetic.png")))
        page.get_by_label("top frame index").fill("1")      # re-requests the (stubbed) frame
        page.wait_for_function("() => { const i = document.querySelector(\".cap-frame img\"); return i && i.naturalWidth > 0; }", timeout=10_000)
        r = page.locator(".cap-frame img").bounding_box()
        page.mouse.move(r["x"] + 20, r["y"] + 20); page.mouse.down(); page.mouse.move(r["x"] + 120, r["y"] + 90, steps=5); page.mouse.up()
        page.get_by_role("button", name="Save boxes").click()
        page.wait_for_selector("text=1 boxes saved.", timeout=10_000)
        pr = cap["inputs"]["prompts"][0]
        assert pr["camera"] == "top" and pr["label"] == "mug" and pr["frame"] == 1 and pr["box"][2] > 4 and pr["box"][3] > 4
        page.get_by_role("button", name="Track").click()
        page.wait_for_selector(".cap-review", timeout=15_000)
        assert "Phone footage - not yet verified" in page.locator(".cap-review").text_content()
        propose = page.get_by_role("button", name="Propose rules from this capture")
        assert propose.is_disabled()
        page.get_by_role("button", name="Confirm the bracket").nth(1).click()
        page.get_by_role("button", name="Save review (1)").click()
        page.wait_for_selector("text=Ready: nothing blocks a proposal.", timeout=10_000)
        rv = srv.studio.capture_reviews[-1]
        assert rv["corrections"][0]["field"] == "confirm" and rv["corrections"][0]["target"] == "mug:place:0"
        assert 0 <= rv["corrections"][0]["seconds_spent"] <= 3600
        assert not propose.is_disabled()
        propose.click()
        page.wait_for_selector("text=Proposed.", timeout=15_000)
        assert not errors, errors
        ctx.close()
