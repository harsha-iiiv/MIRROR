"""The "Your table" step in a browser: real D1 capture data on a static site, driven by clicks.

Needs an exported session (MIRROR_SITE_DATA, a site's data/ folder) and a built table
(MIRROR_TABLE_DIR, default data/table/D1 from `python -m mirror.studio.real_table d1`)."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow
ROOT = Path(__file__).resolve().parents[2]
SITE_DATA = os.environ.get("MIRROR_SITE_DATA")
TABLE = Path(os.environ.get("MIRROR_TABLE_DIR") or ROOT / "data" / "table" / "D1")
pw = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    if not SITE_DATA or not (Path(SITE_DATA) / "session.json").exists():
        pytest.skip("set MIRROR_SITE_DATA to an exported site's data/ folder")
    if not (TABLE / "table.json").exists():
        pytest.skip("build a table first: python -m mirror.studio.real_table d1 --out data/table/D1")
    from tests.web.harness import STATIC_CONFIG
    from mirror.studio.real_table import write_index
    out = tmp_path_factory.mktemp("site") / "s"
    shutil.copytree(ROOT / "web", out)
    (out / "config.js").write_text(STATIC_CONFIG)
    shutil.copytree(SITE_DATA, out / "data", ignore=shutil.ignore_patterns("table"))
    shutil.copytree(TABLE, out / "data" / "table" / TABLE.name)
    write_index(out / "data" / "table")
    return out


@pytest.mark.parametrize("width", [1440, 390])
def test_table_flow(site, width):
    from tests.web.harness import StaticServer
    from tests.web.range_server import RangeHandler
    errors = []
    with StaticServer(site, handler=RangeHandler) as srv, pw.sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": width, "height": 900})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(srv.url + "?step=table", wait_until="networkidle")
        pg.wait_for_selector(".yt-hd h2")
        labels = pg.locator(".yt-label").all_inner_texts()
        assert "Calibration reuse flagged" in labels and "Real phone footage" in labels
        # Outlines are drawn over the video once a carried moment is reached.
        pg.evaluate("() => { document.querySelector('.yt-frame video').currentTime = 15.6; }")
        pg.wait_for_timeout(1200)
        painted = pg.evaluate("""() => { const c = document.querySelector('.yt-frame canvas'); const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++; return n; }""")
        assert painted > 500
        # Task step: the gate's refusal is shown, and the task is labelled as the person's.
        pg.locator(".yt-steps button", has_text="4").click()
        assert "did not infer the task" in pg.locator(".yt-gate").inner_text()
        assert "you stated" in pg.locator(".yt-spec-h").inner_text()
        # Correct -> the clearance correction -> compare table.
        pg.locator(".yt-steps button", has_text="6").click()
        pg.locator(".yt-cand", has_text="6 cm").click()
        pg.wait_for_selector(".yt-recheck")
        row = pg.locator(".yt-recheck tr.new")
        assert "broken" in row.inner_text() and "holds" in row.inner_text()
        # A correction the compiler cannot apply as written comes back as a question, with nothing run.
        pg.locator(".yt-steps button", has_text="6").click()
        pg.locator(".yt-cand", has_text="table outside").click()
        assert "asks before changing anything" in pg.locator(".yt-side .callout").first.inner_text()
        assert pg.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 1
        b.close()
    assert not errors, errors



def _free_port():
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def test_table_live_typed_correction(tmp_path):
    """On the live server a typed correction is compiled, re-checked and planned on the capture's layout.
    9 cm cannot be kept (the mug starts 6.3 cm from the divider), so MIRROR refuses with the planner's reason."""
    import subprocess, sys, time
    httpx = pytest.importorskip("httpx")
    if not (TABLE / "scene.json").exists():
        pytest.skip("build a table first: python -m mirror.studio.real_table d1 --out data/table/D1")
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": str(ROOT), "MIRROR_STUDIO_BACKEND": "live", "MIRROR_LLM_MODE": "offline",
           "MIRROR_STUDIO_RUNTIME": str(tmp_path / "rt"), "MIRROR_TABLE_DIR": str(TABLE.parent)}
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "mirror.studio.app:app", "--host", "127.0.0.1", "--port", str(port)],
                           cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(240):
            try:
                if httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1).status_code == 200:
                    break
            except Exception:
                time.sleep(0.5)
        with pw.sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(f"http://127.0.0.1:{port}/?step=table&table={TABLE.name}&ts=correct", wait_until="networkidle")
            pg.wait_for_selector(".yt-say.live input", timeout=60000)
            pg.fill(".yt-say.live input", "Keep the mug at least 9 cm away from the divider while carrying it.")
            pg.click(".yt-say.live .btn.primary")
            pg.wait_for_selector(".yt-recheck", timeout=240000)
            side = pg.locator(".yt-side").inner_text()
            assert "refuses to run it" in side and "closer than the 0.09 m" in side
            assert "not run" in side and "broken" in side
            b.close()
        assert not errors, errors
    finally:
        srv.terminate(); srv.wait(10)
