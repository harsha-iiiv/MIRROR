"""Responsive layout of the static replay site in a real browser.

The site is a fixture export (key-framed placeholder motion, labelled fixture in the page);
these checks are about layout, not about evidence.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.web.harness import StaticServer, build_site  # noqa: E402
from tests.web.measure_layout import HEIGHT, PROBE, WIDTHS  # noqa: E402

pytestmark = pytest.mark.slow


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


def _open(browser, url, width, theme):
    ctx = browser.new_context(viewport={"width": width, "height": HEIGHT.get(width, 900)})
    ctx.add_init_script(f"localStorage.setItem('mirror-theme', '{theme}')")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url, wait_until="networkidle", timeout=60_000)
    page.wait_for_selector(".main", timeout=30_000)
    page.wait_for_timeout(800)
    return ctx, page, errors


def _assert_fits(m, label):
    assert m["documentScrollWidth"] <= m["viewport"], f"{label}: document {m['documentScrollWidth']} > {m['viewport']}"
    assert not m["controlsOutside"], f"{label}: controls outside the viewport: {m['controlsOutside']}"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_fits_every_width_with_dialogs(tmp_path, fixture_data, browser, theme):
    site = build_site(tmp_path / "site", fixture_data)
    with StaticServer(site) as srv:
        for w in WIDTHS:
            for dialog in (None, "About", "Models"):
                ctx, page, errors = _open(browser, srv.url, w, theme)
                if dialog:
                    page.locator(".top-right .iconbtn", has_text=dialog).first.click()
                    page.wait_for_selector("[role=dialog]", timeout=5000)
                    box = page.locator("[role=dialog]").first.bounding_box()
                    assert box["x"] >= 0 and box["x"] + box["width"] <= w + 1, f"{dialog} dialog leaves the viewport at {w}"
                m = page.evaluate(PROBE)
                _assert_fits(m, f"{theme} {w} {dialog or 'fresh'}")
                assert not errors, errors
                ctx.close()


def test_first_visit_tells_the_story(tmp_path, fixture_data, browser):
    site = build_site(tmp_path / "site", fixture_data)
    with StaticServer(site) as srv:
        ctx, page, errors = _open(browser, srv.url, 390, "light")
        text = page.locator(".panel").text_content()
        for phrase in ("What went wrong", "What the person changed", "checked again", "What changed in execution"):
            assert phrase in text, phrase
        # The fixture export says so; a recorded export says "Recorded session".
        assert "Fixture data" in text or "Recorded session" in text
        assert page.locator(".steps button[aria-current=step]").inner_text().strip().endswith("Overview")
        # Technical detail is behind a disclosure, closed by default.
        assert page.locator("details.ov-details").get_attribute("open") is None
        assert not errors, errors
        ctx.close()


def test_long_text_and_load_errors_stay_inside(tmp_path, fixture_data, browser):
    data = tmp_path / "data"
    shutil.copytree(fixture_data, data)
    snap = json.loads((data / "session.json").read_text())
    long = ("Keep the cup upright the whole way, especially near the wall slot and the tray edge, "
            "and do not let the block or the marker touch it while it is carried " * 4).strip()
    snap["specs"][-1]["history"][-1]["text"] = long     # synthetic: a long correction for layout only
    (data / "session.json").write_text(json.dumps(snap))
    shown = [p for p in (data / "rollouts").glob("*.json")]
    for p in shown:                                      # every rollout fails to load
        p.unlink()
    site = build_site(tmp_path / "site", data)
    with StaticServer(site) as srv:
        for w in (360, 390):
            ctx, page, errors = _open(browser, srv.url, w, "light")
            page.wait_for_selector(".v-empty.err", timeout=15_000)
            m = page.evaluate(PROBE)
            _assert_fits(m, f"long+error {w}")
            q = page.locator(".ov-quote").first
            assert float(q.evaluate("(e) => parseFloat(getComputedStyle(e).fontSize)")) >= 14
            ctx.close()
