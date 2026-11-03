"""Challenge mode in a real browser, against a local fake of the challenge API.

The fake (tests/web/fake_server.py) serves synthetic fixture data and runs no simulation
or model; these tests check the frontend flow, identities and labels, not evidence.
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
from tests.web.fake_server import FakeServer  # noqa: E402
from tests.web.harness import StaticServer, build_site  # noqa: E402
from tests.web.measure_layout import PROBE  # noqa: E402

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


def _page(browser, url, width=390):
    ctx = browser.new_context(viewport={"width": width, "height": 844})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url, wait_until="load", timeout=60_000)
    return ctx, page, errors


def _build(page):
    page.wait_for_selector("text=Build it and run the current rules", timeout=30_000)
    page.get_by_role("button", name="Build it and run the current rules").click()


def test_challenge_flow_and_share_link(fixture_data, browser):
    with FakeServer(fixture_data) as srv:
        ctx, page, errors = _page(browser, srv.url + "?step=challenge")
        _build(page)
        page.wait_for_selector("text=Current rules on this layout", timeout=15_000)
        # An ambiguous correction asks instead of changing anything.
        page.fill("#ch-corr", "Be careful with it")
        page.get_by_role("button", name="Interpret my correction").click()
        page.wait_for_selector("text=Which measurable requirement matters?", timeout=15_000)
        assert page.get_by_role("button", name="Accept and re-check").count() == 0
        page.get_by_role("button", name="Keep the cup upright while carrying it.").click()
        page.get_by_role("button", name="Interpret my correction").click()
        page.wait_for_selector("text=Accept and re-check", timeout=15_000)
        scope = page.locator(".ch-scope").text_content()
        assert "cup_0" in scope and "while carried" in scope
        # Per-step provenance: which interpreter, measured latency and why it fell back.
        assert "Offline-rule interpreter (fallback)" in scope and "offline-rules" in scope and "model unavailable" in scope
        page.get_by_role("button", name="Accept and re-check").click()
        page.wait_for_selector("text=Rule violated", timeout=15_000)
        assert "Checked now from the recorded step log" in page.locator(".panel").text_content()
        page.get_by_role("button", name="Plan and run in MuJoCo").click()
        page.wait_for_selector("text=Publish and get a link", timeout=15_000)
        assert page.locator(".v-title").text_content().startswith("Your layout · after the correction")
        assert page.evaluate("!!window.__mirrorViewer.payload")
        page.get_by_role("button", name="Publish and get a link").click()
        page.wait_for_selector(".ch-share input", timeout=15_000)
        url = page.locator(".ch-share input").input_value()
        assert "?challenge=" in url and "token" not in url and "session" not in url and "live" not in url
        m = page.evaluate(PROBE)
        assert m["documentScrollWidth"] <= 390 and not m["controlsOutside"], m
        assert not errors, errors
        ctx.close()
        # A fresh visitor opens the exact record.
        ctx, page, errors = _page(browser, url)
        page.wait_for_selector("text=Shared test", timeout=30_000)
        page.get_by_role("button", name="Next: Before").click()
        page.get_by_role("button", name="Next: Correction").click()
        assert "Keep the cup upright while carrying it." in page.locator(".panel").text_content()
        rid = url.split("challenge=")[1]
        page.locator("details.ov-details summary").click()
        assert rid in page.locator(".panel").text_content()
        assert not errors, errors
        ctx.close()


def test_fenced_seed_and_unsupported_correction(fixture_data, browser):
    with FakeServer(fixture_data) as srv:
        ctx, page, errors = _page(browser, srv.url + "?step=challenge")
        page.wait_for_selector("input.num-in", timeout=30_000)
        page.fill("input.num-in", "30005")                 # outside the development range: refused in the form
        page.wait_for_selector("text=Development seeds for this family are 0 to 8999", timeout=5_000)
        assert page.get_by_role("button", name="Build it and run the current rules").is_disabled()
        page.fill("input.num-in", "1234")                  # inside the range but fenced: refused by the server
        _build(page)
        page.wait_for_selector("text=frozen evaluation set", timeout=15_000)
        page.fill("input.num-in", "4242")
        _build(page)
        page.wait_for_selector("text=Current rules on this layout", timeout=15_000)
        page.fill("#ch-corr", "Make the cup fly over the wall")
        page.get_by_role("button", name="Interpret my correction").click()
        page.wait_for_selector("text=MIRROR cannot do that", timeout=15_000)
        assert page.get_by_role("button", name="Accept and re-check").count() == 0
        # Only the server's published rule language is advertised.
        page.locator("details.ch-rules summary").click()
        rules = page.locator("details.ch-rules").text_content()
        assert "upright" in rules and "ordering" not in rules
        assert not errors, errors
        ctx.close()


def test_late_payload_from_an_old_generation_is_not_shown(fixture_data, browser):
    with FakeServer(fixture_data) as srv:
        srv.studio.stale_challenge_payloads = True
        ctx, page, errors = _page(browser, srv.url + "?step=challenge")
        _build(page)
        page.wait_for_selector("text=Current rules on this layout", timeout=15_000)
        page.wait_for_timeout(1500)
        assert page.evaluate("window.__mirrorViewer.payload === null || !window.__mirrorViewer.payload.generation_id || window.__mirrorViewer.payload.generation_id === 'g18f3aa010001'")
        assert page.locator(".v-empty.err").count() == 0
        assert not errors, errors
        ctx.close()


def test_stale_generation_is_refused_and_page_catches_up(fixture_data, browser):
    with FakeServer(fixture_data) as srv:
        ctx, page, errors = _page(browser, srv.url + "?step=challenge")
        page.wait_for_selector("text=Build it and run the current rules", timeout=30_000)
        srv.studio.generation = "g18f3aa020002"        # the demonstration changed elsewhere
        _build(page)
        page.wait_for_selector("text=The demonstration changed", timeout=15_000)
        _build(page)                                     # the page refreshed its generation; this one goes through
        page.wait_for_selector("text=Current rules on this layout", timeout=15_000)
        assert not errors, errors
        ctx.close()


def _with_tour(fixture_data: Path, dest: Path) -> Path:
    """Copy the fixture data and add a synthetic recorded tour (layout test data, not a recording)."""
    shutil.copytree(fixture_data, dest)
    snap = json.loads((dest / "session.json").read_text())
    world = next(r for r in snap["rollouts"] if "world" in r["tags"])
    fixed = next(r for r in snap["rollouts"] if "corrected" in r["tags"])
    (dest / "tour" / "rollouts").mkdir(parents=True)
    for r in (world, fixed):
        shutil.copy(dest / "rollouts" / f"{r['id']}.json", dest / "tour" / "rollouts" / f"{r['id']}.json")
    rec = {"format": "mirror-challenge-record/1", "record_id": "tour-synthetic", "created_at": 0,
           "scene": {"family": "shortcut", "split": "dev", "seed": 4242, "edits": [], "scene_id": world["scene_id"], "content_sha256": "0" * 64},
           "generation": {"id": "g0", "demo": {"demo_id": "demo", "content_sha256": "1" * 64, "source": "teleop_scripted"}},
           "spec_versions": {"base": {"version_id": "g0.v1", "content_sha256": "2" * 64}, "revised": {"version_id": "g0.v2", "content_sha256": "3" * 64}},
           "correction": {"text": "Keep the cup upright while carrying it.", "input": "typed", "ops": [],
                          "scope": {"objects": ["cup_0"], "phases": ["transport"]}, "provenance": "offline_interpreter"},
           "rechecks": [{"rollout_id": world["id"], "spec_version_id": "g0.v2", "status": "constraint_violated", "violations": []}],
           "rollouts": {"before": {"rollout_id": world["id"], "content_sha256": "4" * 64, "status": "success", "spec_version_id": "g0.v1"},
                        "after": {"rollout_id": fixed["id"], "content_sha256": "5" * 64, "status": "success", "spec_version_id": "g0.v2"}},
           "provenance": {"interpreter": "offline-rules", "recorded": True, "simulation": "fixture", "server_version": "test"}}
    (dest / "tour" / "record.json").write_text(json.dumps(rec))
    return dest


def test_recorded_tour_without_a_server(tmp_path, fixture_data, browser):
    data = _with_tour(fixture_data, tmp_path / "data")
    site = build_site(tmp_path / "site", data)
    with StaticServer(site) as srv:
        ctx, page, errors = _page(browser, srv.url)
        api_calls = []
        page.on("request", lambda r: api_calls.append(r.url) if "/api/" in r.url else None)
        page.locator(".steps .challenge").click()
        page.wait_for_selector("text=A test of MIRROR, step by step", timeout=15_000)
        assert "Produced offline" in page.locator(".panel").text_content()
        for nxt in ("Before", "Correction", "Re-check", "After"):
            page.get_by_role("button", name=f"Next: {nxt}").click()
        page.wait_for_timeout(1500)
        assert page.locator(".v-title").text_content().startswith("Recorded tour · after")
        assert page.evaluate("!!window.__mirrorViewer.payload")
        assert not api_calls, api_calls
        assert not errors, errors
        ctx.close()


def test_static_site_without_a_tour_says_so(tmp_path, fixture_data, browser):
    site = build_site(tmp_path / "site", fixture_data)
    with StaticServer(site) as srv:
        ctx, page, errors = _page(browser, srv.url + "?step=challenge")
        page.wait_for_selector("text=Testing runs on the live simulation", timeout=30_000)
        assert not errors, errors
        ctx.close()


def test_absent_object_asks_which_object(fixture_data, browser):
    with FakeServer(fixture_data) as srv:
        ctx, page, errors = _page(browser, srv.url + "?step=challenge")
        _build(page)
        page.wait_for_selector("text=Current rules on this layout", timeout=15_000)
        page.fill("#ch-corr", "Don't tip the jar")
        page.get_by_role("button", name="Interpret my correction").click()
        page.wait_for_selector("text=That object is not in this layout", timeout=15_000)
        assert page.get_by_role("button", name="Accept and re-check").count() == 0
        page.get_by_role("button", name="the cup (cup_0)").click()
        page.wait_for_selector("text=Accept and re-check", timeout=15_000)
        assert "“jar” → cup_0 (chosen by you)" in page.locator(".ch-scope").text_content()
        assert not errors, errors
        ctx.close()


@pytest.mark.parametrize("seed,headline", [(4242, "Proven infeasible under these stated assumptions"),
                                           (4243, "Planner found no compliant route")])
def test_blocked_wording_needs_a_valid_certificate(fixture_data, browser, seed, headline):
    with FakeServer(fixture_data) as srv:
        ctx, page, errors = _page(browser, srv.url + "?step=challenge")
        page.wait_for_selector("select", timeout=30_000)
        page.select_option(".ch-form select", "blocked")
        page.fill("input.num-in", str(seed))
        _build(page)
        page.wait_for_selector(".plan-outcome", timeout=15_000)
        txt = page.locator(".plan-outcome").first.text_content()
        assert headline in txt
        if seed % 2:   # a server claim of "proven" without a valid certificate is not repeated
            assert "Proven infeasible" not in txt and "not a proof" in txt
        else:
            assert "not a guarantee about physical safety" in txt
        assert not errors, errors
        ctx.close()


def test_study_consent_and_comprehension_event(fixture_data, browser):
    with FakeServer(fixture_data) as srv:
        srv.studio.study_enabled = True
        ctx, page, errors = _page(browser, srv.url + "?step=challenge&study=P01&task=C1")
        page.wait_for_selector("text=Before you start", timeout=30_000)
        assert page.get_by_role("button", name="Build it and run the current rules").count() == 0   # nothing before consent
        page.get_by_label("Record this trial anonymously").check()
        page.get_by_role("button", name="Continue", exact=True).click()
        page.wait_for_selector("text=being recorded anonymously, without correction text", timeout=10_000)
        _build(page)
        page.wait_for_selector("text=Current rules on this layout", timeout=15_000)
        page.fill("#ch-corr", "keep it upright while carried")
        page.get_by_role("button", name="Interpret my correction").click()
        page.get_by_role("button", name="Accept and re-check").click()
        page.get_by_role("button", name="Plan and run in MuJoCo").click()
        page.get_by_role("button", name="Recorded", exact=True).click()
        page.wait_for_timeout(500)
        ev = srv.studio.study_events
        assert ev[0]["path"] == "study/consent" and ev[0]["consent"] == {"recorded": True, "text": False, "audio": False}
        assert ev[0]["participant"] == "P01" and ev[0]["task"] == "C1"
        assert ev[-1]["event"] == "comprehension" and ev[-1]["answer"] == "recorded" and ev[-1]["truth"] == "fixture"
        assert not errors, errors
        ctx.close()


def test_study_without_server_support_records_nothing(fixture_data, browser):
    with FakeServer(fixture_data) as srv:
        ctx, page, errors = _page(browser, srv.url + "?step=challenge&study=P02")
        page.wait_for_selector("text=Before you start", timeout=30_000)
        page.get_by_label("Record this trial anonymously").check()
        page.get_by_role("button", name="Continue", exact=True).click()
        page.wait_for_selector("text=Nothing from this session is recorded", timeout=10_000)
        assert srv.studio.study_events == []
        ctx.close()


def test_manual_condition_edits_rules_directly(fixture_data, browser):
    with FakeServer(fixture_data) as srv:
        ctx, page, errors = _page(browser, srv.url + "?step=challenge&study=P03&condition=manual")
        page.wait_for_selector("text=Before you start", timeout=30_000)
        page.get_by_role("button", name="Continue without recording").click()
        _build(page)
        page.wait_for_selector("text=Change one rule directly", timeout=15_000)
        assert page.locator("#ch-corr").count() == 0
        page.locator(".manual input.num-in").fill("10")      # a tighter limit than any fixture rule
        page.get_by_role("button", name="Propose this change").click()
        page.wait_for_selector("text=Accept and re-check", timeout=15_000)
        assert "Manual edit (no model)" in page.locator(".ch-scope").text_content()
        assert not errors, errors
        ctx.close()
