"""Slow studio checks: real simulation in the loop, and a headless browser."""
import json
import os
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


@pytest.mark.slow
def test_final_report_loads_without_sampling(monkeypatch):
    final = ROOT / "results" / "eval" / "report.json"
    if not final.exists():
        pytest.skip("no final evaluation report")
    import mirror.scenes as scenes

    def boom(*a, **k):
        raise AssertionError("the studio must not generate layouts for a loaded report")
    monkeypatch.setattr(scenes, "sample_layout", boom, raising=False)
    from mirror.studio.session_adapter import StudioService
    svc = StudioService(backend="fixture")
    ev = svc.load_evaluation(final.parent)
    data = json.loads(final.read_text())
    assert ev["final"] and set(ev["strata"]) == set(data["strata"])
    for name, rep in data["strata"].items():
        assert len(ev["strata"][name]["studio"]["rollout_ids"]) == len(rep["trials"])
    rows = [r for r in svc.snapshot()["rollouts"] if "eval" in r["tags"]]
    assert sum(r["has_frames"] for r in rows) >= 1
    assert all(r["scene"] is not None for r in rows if r["has_frames"])


@pytest.mark.slow
def test_live_switch_connects_and_falls_back(tmp_path):
    """A recorded site with apiBase set: switch to the live server, then lose it."""
    pw = pytest.importorskip("playwright.sync_api")
    sys.path.insert(0, str(ROOT / "scripts"))
    import export_static as ex
    out = tmp_path / "site"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "export_static.py"), "--backend", "fixture",
                    "--allow-fixture", "--seeds", "0", "--no-clarify", "--out", str(out)], check=True, timeout=120,
                   cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True)
    web_port, api_port = _free_port(), _free_port()
    ex.write_config(out, f"http://127.0.0.1:{api_port}")
    cfg = ex.parse_config_js((out / "config.js").read_text())
    cfg["wakeTimeoutMs"] = 6000
    (out / "config.js").write_text(ex.config_js(cfg))
    env = dict(os.environ, MIRROR_STUDIO_BACKEND="fixture", MIRROR_LLM_MODE="offline",
               MIRROR_CORS_ORIGINS=f"http://127.0.0.1:{web_port}")
    web = subprocess.Popen([sys.executable, "-m", "http.server", str(web_port), "--bind", "127.0.0.1"], cwd=out,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    api = subprocess.Popen([sys.executable, "-m", "uvicorn", "mirror.studio.app:app", "--host", "127.0.0.1",
                            "--port", str(api_port)], cwd=ROOT, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    errors = []
    try:
        time.sleep(3.0)
        with pw.sync_playwright() as p:
            b = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
            pg = b.new_page(viewport={"width": 1280, "height": 800})
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(f"http://127.0.0.1:{web_port}/", wait_until="networkidle", timeout=60_000)
            sw = pg.locator(".live-switch")
            assert sw.get_attribute("aria-checked") == "false"
            sw.click()
            pg.wait_for_url("**live=1**", timeout=20_000)
            pg.locator(".live-switch.live").wait_for(timeout=30_000)
            pg.get_by_text("The demonstration").first.wait_for(timeout=30_000)
            pg.locator(".story .btn.primary").click()               # interpret on the server
            pg.get_by_text("What MIRROR thinks matters").wait_for(timeout=30_000)
            api.terminate()
            api.wait(timeout=10)
            pg.reload()
            pg.get_by_text("Live server unavailable, showing recordings.").wait_for(timeout=30_000)
            assert pg.locator(".live-switch").get_attribute("aria-checked") == "false"
            pg.get_by_text("The demonstration").first.wait_for(timeout=30_000)
            b.close()
    finally:
        for proc in (web, api):
            proc.terminate()
            proc.wait(timeout=10)
    assert not errors, errors
