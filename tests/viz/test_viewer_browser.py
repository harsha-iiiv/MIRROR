"""The 3D workspace in a real browser (software GL): rule geometry, verdict ribbons, stepping,
and hover readouts that equal the checker's geometry on the same frame."""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from mirror.contracts import ObjectSpec, ObstacleSpec
from mirror.evaluate import geom
from tests.web.harness import StaticServer, build_site

CDN = "https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js"


def _online() -> bool:
    try:
        import httpx
        return httpx.head(CDN, timeout=5, follow_redirects=True).is_success
    except Exception:
        return False


def _payload():
    n = 60
    t = [round(0.05 * i, 3) for i in range(n)]
    phase = ["move:cup_0"] * 10 + ["grasp:cup_0"] * 5 + ["transport:cup_0"] * 35 + ["release:cup_0"] * 5 + ["retreat:cup_0"] * 5
    cup = []
    for i in range(n):
        s = min(1.0, max(0.0, (i - 15) / 35))
        x, y, z = -0.2 + 0.35 * s, -0.12, 0.05 + 0.1 * math.sin(math.pi * s)
        ang = math.radians(40) * math.sin(math.pi * s)            # tilts about x, up to 40 deg
        cup.append([round(x, 4), y, round(z, 4), math.cos(ang / 2), math.sin(ang / 2), 0.0, 0.0])
    grip = [[c[0], c[1], c[2] + 0.07, 1, 0, 0, 0] for c in cup]
    box = lambda name, half, pos, rgba: {"type": "box", "size": half, "pos": pos, "quat": [1, 0, 0, 0], "rgba": rgba, "name": name}
    visual = {"bodies": [
        {"name": "world", "static": True, "geoms": [box("table", [0.6, 0.5, 0.02], [0, 0, -0.02], [0.86, 0.85, 0.82, 1])]},
        {"name": "wall_0", "static": True, "geoms": [box("wall_0", [0.01, 0.15, 0.1], [0.0, -0.05, 0.1], [0.7, 0.68, 0.64, 1])]},
        {"name": "gripper", "static": False, "geoms": [box("palm", [0.025, 0.05, 0.02], [0, 0, 0], [0.2, 0.2, 0.2, 1])]},
        {"name": "cup_0", "static": False, "geoms": [box("cup", [0.03, 0.03, 0.05], [0, 0, 0], [0.93, 0.93, 0.9, 1])]}]}
    scene = {"scene_id": "viz_test", "objects": [{"id": "cup_0", "kind": "cup", "size": {"outer_half": [0.03, 0.03, 0.05], "wall": 0.004},
                                                   "mass": 0.05, "pose": {"pos": [-0.2, -0.12, 0.05], "quat": [1, 0, 0, 0]}}],
             "trays": [{"id": "tray_0", "pose": {"pos": [0.25, 0.1, 0.006], "quat": [1, 0, 0, 0]}, "inner_half": [0.1, 0.08], "wall_height": 0.03, "wall_thickness": 0.008}],
             "obstacles": [{"id": "wall_0", "kind": "box", "pose": {"pos": [0.0, -0.05, 0.1], "quat": [1, 0, 0, 0]}, "half": [0.01, 0.15, 0.1], "slots": []}]}
    prov = {"source": "user_correction", "evidence": "test", "version_introduced": 1}
    spec = {"spec_id": "viz_spec", "version": 2, "constraints": [
        {"id": "c_up", "type": "upright", "applies_to": {"kind": "cup"}, "phase": "transport", "params": {"max_tilt_deg": 15}, "status": "confirmed", "provenance": prov},
        {"id": "c_clear", "type": "keep_clearance", "applies_to": {"kind": "cup"}, "phase": "transport", "params": {"min_m": 0.06}, "status": "confirmed", "provenance": prov}]}
    report = {"rollout_id": "viz:r1", "scene_id": "viz_test", "spec_id": "viz_spec", "spec_version": 2, "status": "constraint_violated",
              "violations": [{"constraint_id": "c_up", "object_id": "cup_0", "t_start": 1.2, "t_end": 2.0, "peak": 40.0, "detail": "t"}],
              "advisory_violations": [], "forbidden_contacts": []}
    return {"format": "mirror-studio-rollout/1", "id": "viz:r1", "report": report, "visual": visual, "frame_hz": 20, "t": t,
            "width": [0.09] * n, "phase": phase, "bodies": {"cup_0": cup, "gripper": grip}, "scene": scene, "spec": spec}, scene


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    pytest.importorskip("playwright.sync_api")
    if not _online():
        pytest.skip("three.js CDN not reachable")
    root = tmp_path_factory.mktemp("vizsite")
    out = build_site(root / "site", None)
    payload, scene = _payload()
    (out / "data" / "pack" / "rollouts").mkdir(parents=True)
    (out / "data" / "pack" / "rollouts" / "r1.json").write_text(json.dumps(payload))
    with StaticServer(out) as srv:
        yield srv.url, payload, scene


def _open(p, url, q):
    b = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    page = b.new_page(viewport={"width": 1000, "height": 700})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url + "js/viz/lab.html?" + q, wait_until="load")
    page.wait_for_function("window.__lab && (window.__lab.ready || window.__lab.error)", timeout=120_000)
    page.evaluate("() => { window.__lab.v.frozen = true; }")
    return b, page, errors


def test_workspace_rules_ribbons_and_readouts(site):
    from playwright.sync_api import sync_playwright
    url, payload, scene = site
    with sync_playwright() as p:
        b, page, errors = _open(p, url, "pack=../../data/pack/&run=r1&ghost=&still")
        st = page.evaluate("() => window.__lab.v.getState()")
        assert st["verdictMode"] == "verdict" and "cup_0" in st["ribbons"]
        assert st["rules"]["volumes"] == 1 and st["rules"]["markers"] == 1 and st["rules"]["cones"] == 1
        assert st["spec"] == "viz_spec@v2"
        # frame-accurate stepping and event seeking
        f = page.evaluate("() => { const v = window.__lab.v; v.setTime(0); v.stepFrame(3); return [v.frameIndex(), v.t]; }")
        assert f == [3, payload["t"][3]]
        g = page.evaluate("() => { const v = window.__lab.v; v.setTime(0); v.seekEvent(1); return v.t; }")
        assert g == pytest.approx(payload["t"][10])
        nxt = page.evaluate("() => { const v = window.__lab.v; v.seekEvent(1); return v.t; }")
        assert nxt == pytest.approx(1.2)      # violation start
        # readouts on an exact frame equal the checker's geometry
        k = 32
        m = page.evaluate(f"() => {{ const v = window.__lab.v; v.setTime({payload['t'][k]}); return v.measure('cup_0'); }}")
        s = payload["bodies"]["cup_0"][k]
        obj = ObjectSpec.model_validate({**scene["objects"][0]})
        boxes = [bx for o in scene["obstacles"] for bx in geom.obstacle_boxes(ObstacleSpec.model_validate(o))]
        d = float(geom.object_obstacle_distance(obj, np.array([s[:3]]), np.array([s[3:]]), boxes)[0])
        assert m["clear"]["d"] == pytest.approx(d, abs=1e-5)
        assert m["tilt"] == pytest.approx(geom.tilt_of(s[3:]), abs=1e-3)
        assert m["tiltLimit"] == 15 and m["tiltOver"] == (geom.tilt_of(s[3:]) > 15)
        assert m["clearLimit"] == pytest.approx(0.06) and m["clearOver"] == (d < 0.06)
        panel = page.inner_text(".viz-rules")
        assert "c_clear" in panel and f"{100 * d:.2f} cm" in panel and "constraint_violated" in panel
        # camera presets glide and settle
        page.evaluate("() => { const v = window.__lab.v; v.setView('top'); for (let i = 0; i < 40; i++) v._advance(0.05); }")
        st = page.evaluate("() => window.__lab.v.getState()")
        assert st["view"] == "top" and st["glide"] is False
        # keyboard: rules toggle and frame step on the focused viewer
        page.focus("#viewer")
        page.keyboard.press("r")
        assert page.evaluate("() => window.__lab.v.getOptions().rules") is False
        page.keyboard.press(".")
        assert errors == []
        b.close()


def test_without_webgl_falls_back_to_2d(site):
    from playwright.sync_api import sync_playwright
    url, _, _ = site
    with sync_playwright() as p:
        b, page, errors = _open(p, url, "pack=../../data/pack/&run=r1&ghost=&still&force2d")
        assert page.evaluate("() => window.__lab.v.fallback") == "2d"
        assert page.evaluate("() => window.__lab.error || null") is None
        assert page.evaluate("() => { const v = window.__lab.v; v.setTime(1.5); return v.frameIndex(); }") == 30
        assert errors == []
        b.close()
