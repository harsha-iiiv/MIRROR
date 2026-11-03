"""Races between the page's own requests and a visitor's clicks (slow, fake server; no simulation)."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.web.fake_server import FakeServer

pytestmark = pytest.mark.slow
ROOT = Path(__file__).resolve().parents[2]


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


def test_story_action_waits_for_the_live_demonstration_load(fixture_data, browser):
    """A fresh live session loads the reference demonstration itself; a quick click on the story's
    next action must not race that load (it used to be refused as stale, 409)."""
    with FakeServer(fixture_data) as srv:
        srv.studio.has_demo = False
        srv.studio.interpreted = False
        srv.studio.demo_load_delay = 2.0
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script("localStorage.setItem('mirror-story', 'on')")
        page.goto(srv.url + "?step=watch", wait_until="load")
        btn = page.locator(".story-act .btn.primary")
        btn.wait_for(timeout=30_000)
        # A fast visitor clicks whatever the story offers while the page's own demo load is still running.
        clicked_early = False
        t0 = time.time()
        while not srv.studio.has_demo and time.time() - t0 < 10:
            if btn.is_enabled():
                btn.click(timeout=1_000)
                clicked_early = True
                break
            page.wait_for_timeout(50)
        assert not clicked_early, f"the story action was clickable during the demonstration load: {btn.text_content()}"
        assert srv.studio.has_demo, "the page did not load the demonstration"
        page.wait_for_function("() => { const b = document.querySelector('.story-act .btn.primary'); return b && !b.disabled && /Interpret the demonstration/.test(b.textContent); }", timeout=15_000)
        btn.click()
        for _ in range(100):
            if srv.studio.interpret_calls:
                break
            page.wait_for_timeout(100)
        assert srv.studio.interpret_calls == 1 and srv.studio.refused == [], (srv.studio.interpret_calls, srv.studio.refused)
        assert not errors, errors
        ctx.close()


def test_stale_refusal_message_stays_visible(fixture_data, browser):
    """The 409 explanation must survive the state refresh that the refusal itself triggers."""
    with FakeServer(fixture_data) as srv:
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.goto(srv.url + "?step=challenge", wait_until="load")
        page.wait_for_selector("text=Build it and run the current rules", timeout=30_000)
        srv.studio.generation = "g18f3aa020002"
        page.get_by_role("button", name="Build it and run the current rules").click()
        for _ in range(100):
            if srv.studio.refused:
                break
            page.wait_for_timeout(50)
        assert srv.studio.refused, "the click was not refused as stale"
        page.wait_for_timeout(1500)                      # the refresh after the refusal has long finished
        assert page.locator("text=The demonstration changed").count() == 1
        ctx.close()
