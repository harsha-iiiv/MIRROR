"""Slow studio checks: real simulation in the loop, and a headless browser."""
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.slow
def test_live_session_runs_and_rechecks():
    from mirror.studio.session_adapter import REFERENCE_CORRECTION, StudioService, packages_available
    ok, missing = packages_available()
    if not ok:
        pytest.skip(f"packages missing: {missing}")
    svc = StudioService(backend="live")
    svc.load_demo()
    assert svc.wait(svc.interpret()).status == "done"
    svc.set_scene_preset("shortcut")
    j = svc.wait(svc.run(condition="v1", tags=["world"]), timeout=600)
    assert j.status == "done", j.error
    j = svc.wait(svc.correct(REFERENCE_CORRECTION), timeout=120)
    assert j.status == "done" and j.result["changed"], j.result
    assert any(r["rollout_id"] == "r001" for r in svc.rechecks)
    p = svc.rollout_payload("r001")
    assert p["t"] and any("bead" in n for n in p["bodies"])
    assert svc.rollout_payload("demo")["beads_hidden"] is True


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


@pytest.mark.slow
def test_static_site_renders_without_console_errors(tmp_path):
    pw = pytest.importorskip("playwright.sync_api")
    out = tmp_path / "site"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "export_static.py"), "--backend", "fixture",
                    "--allow-fixture", "--seeds", "2", "--out", str(out)], check=True, timeout=120,
                   cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True)
    port = _free_port()
    srv = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"], cwd=out,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    errors = []
    try:
        time.sleep(1.0)
        with pw.sync_playwright() as p:
            b = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
            pg = b.new_page(viewport={"width": 1280, "height": 800})
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            pg.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle", timeout=60_000)
            pg.get_by_text("The demonstration").first.wait_for(timeout=30_000)
            for step in ["Interpret", "Run", "Change world", "Correct", "Evidence"]:
                pg.get_by_role("button", name=step).first.click()
                pg.wait_for_timeout(600)
            pg.get_by_text("paired layouts").first.wait_for(timeout=10_000)
            assert pg.locator("canvas").count() == 1
            b.close()
    finally:
        srv.terminate()
        srv.wait(timeout=10)
    assert not errors, errors
