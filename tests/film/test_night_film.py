"""Night film tooling: numbers come from records, outlines from recorded masks, and the motion check catches moving text."""
from __future__ import annotations
import importlib.util, json, subprocess, sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
NIGHT = ROOT / "film" / "night"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, NIGHT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_rle_outline_roundtrip():
    ea = _load("export_assets")
    from mirror.perception.masks import rle_encode
    m = np.zeros((96, 54), np.uint8)
    m[20:60, 10:40] = 1
    rle = rle_encode(m)
    assert (ea.rle_decode(rle) == m).all()
    p = np.array(ea.outline(m))
    assert p[:, 0].min() == 10 and p[:, 0].max() == 39 and p[:, 1].min() == 20 and p[:, 1].max() == 59


def _run(n, phase_at, pos):
    return {"n": n, "t": [i / 30 for i in range(n)], "phase": phase_at, "beads": [["cup_0", 8, 8]],
            "poses": {"cup_0": [[pos(i)[0], pos(i)[1], 0.05, 1, 0, 0, 0] for i in range(n)]}}


def test_film_numbers_copy_the_records():
    ea = _load("export_assets")
    rows = [{"rule_id": "c_keep_clearance_transport_cup", "verdict": "violated", "detail": "closest 0.0452 m"}]
    meas = lambda c: {"cup_0": {"min_transport_clearance_m": c, "at_t_s": 0.2, "max_transport_tilt_deg": 2.4},
                      "block_0": {"min_transport_clearance_m": 0.049}, "marker_0": {"min_transport_clearance_m": 0.0615}}
    C = {"old": {"status": "success", "measures": meas(0.0452), "rules": [], "execution_time_s": 72.5, "routes": ["a"]},
         "candidates": [{"id": "mug_clearance", "text": "Keep the mug ...", "outcome": "apply",
                         "ops": [{"op": "add_constraint", "value": {"id": "c_keep_clearance_transport_cup", "type": "keep_clearance",
                                                                     "applies_to": {"kind": "cup"}, "phase": "transport", "params": {"min_m": 0.06}}}],
                         "old_rechecked": {"status": "constraint_violated", "rules": rows},
                         "new": {"status": "success", "measures": meas(0.0633), "rules": [], "execution_time_s": 77.3, "routes": ["b"]}},
                        {"id": "marker_first", "text": "Pack ...", "outcome": "reject", "reason": "cycle"}]}
    ph = ["move:cup_0"] * 3 + ["lift:cup_0", "transport:cup_0", "transport:cup_0", "place:cup_0"] + ["settle"] * 3
    run = _run(len(ph), ph, lambda i: (i * 0.01, -i * 0.01))
    f = ea.film_numbers(C, run, run, {})
    assert f["old_clear_m"] == 0.0452 and f["new_clear_m"] == 0.0633
    assert f["old_rechecked_status"] == "constraint_violated" and f["old_detail"] == "closest 0.0452 m"
    assert f["old_path_frames"] == [3, 6] and len(f["old_path"]) == 4
    assert f["old_clear_frame"] == 6  # nearest frame to t = 0.2 s
    assert f["others"] == [{"id": "marker_first", "text": "Pack ...", "outcome": "reject", "detail": "cycle", "routes_same": None}]
    assert f["rule_id"] == "c_keep_clearance_transport_cup" and f["min_m"] == 0.06 and f["applies_obj"] == "cup_0"


def test_events_and_segments_from_both_timeline_shapes():
    ea = _load("export_assets")
    d1 = {"events": [{"object": "mug", "moved_from_s": 13.5, "moved_to_s": 13.7, "image_displacement_px_540": 29.4, "ends_inside_tray_outline_2d": False},
                     {"object": "mug", "moved_from_s": 14.57, "moved_to_s": 16.77, "image_displacement_px_540": 269.3, "ends_inside_tray_outline_2d": True},
                     {"object": "marker", "moved_from_s": 27.47, "moved_to_s": 31.27, "image_displacement_px_540": 323.6, "ends_inside_tray_outline_2d": True}]}
    rr = {"events": [{"object": "mug", "from_s": 14.57, "to_s": 16.77, "displacement_px": 269.3, "in_tray": True},
                     {"object": "marker", "from_s": 27.47, "to_s": 31.27, "displacement_px": 323.6, "in_tray": True}]}
    a, b = ea.norm_events(d1), ea.norm_events(rr)
    assert [e for e in a if e["carry"]] == b
    s = ea.segments(a, 1494, 29.999)
    assert s == ea.segments(b, 1494, 29.999)
    assert s["watch_src0"] == 12.77 and s["watch_rate"] == 1.75 and s["title_src0"] == 2.27


def _video(path, frames):
    import cv2
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, (1920, 1080))
    for fr in frames:
        vw.write(fr)
    vw.release()


def _text_frame(x, alpha=1.0):
    import cv2
    fr = np.full((1080, 1920, 3), 17, np.uint8)
    rng = np.random.default_rng(0)
    for k in range(40):  # distinct glyph-like blobs so features match unambiguously
        cx, cy = int(rng.integers(100, 800)), int(rng.integers(200, 800))
        cv2.putText(fr, f"{k}A{k * 7 % 13}", (cx + x, cy), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (int(230 * alpha),) * 3, 3, cv2.LINE_AA)
    return fr


@pytest.mark.parametrize("step,expect_pass", [(0, True), (2, True), (40, False)])
def test_motion_check_flags_fast_text(tmp_path, step, expect_pass):
    vid = tmp_path / "v.mp4"
    _video(vid, [_text_frame(i * step) for i in range(40)])
    shots = tmp_path / "s.json"
    shots.write_text(json.dumps([{"name": "s", "frames": 40, "text_zones": [[0, 0, 1920, 1080]]}]))
    out = tmp_path / "chk"
    r = subprocess.run([sys.executable, str(NIGHT / "check_film.py"), str(vid), "--shots", str(shots), "--out", str(out)],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    m = json.loads((out / "motion.json").read_text())
    assert m["shots"][0]["pass"] is expect_pass, m["shots"][0]
    assert (out / "contact.jpg").exists()


def test_fade_is_not_motion(tmp_path):
    vid = tmp_path / "v.mp4"
    _video(vid, [_text_frame(0, alpha=min(1.0, 0.2 + i / 20)) for i in range(40)])
    shots = tmp_path / "s.json"
    shots.write_text(json.dumps([{"name": "s", "frames": 40, "text_zones": [[0, 0, 1920, 1080]]}]))
    r = subprocess.run([sys.executable, str(NIGHT / "check_film.py"), str(vid), "--shots", str(shots), "--out", str(tmp_path / "c")],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    assert json.loads((tmp_path / "c" / "motion.json").read_text())["shots"][0]["pass"]


PHASE2 = Path("/workspace/mirror-team/v2/phone_real/out/phase2")


@pytest.mark.slow
@pytest.mark.skipif(not PHASE2.exists(), reason="D1 phase-2 outputs not on this machine")
def test_correction_sources_reproduce_phase2(tmp_path):
    r = subprocess.run([sys.executable, str(NIGHT / "make_correction.py"), "--phase2", str(PHASE2), "--out", str(tmp_path / "c")],
                       capture_output=True, text=True, timeout=900, env={**__import__("os").environ, "MIRROR_LLM_MODE": "offline"})
    assert r.returncode == 0, r.stderr[-2000:]
    C = json.loads((tmp_path / "c" / "corrections.json").read_text())
    assert all(C["reproduces_phase2"].values())
    mc = next(c for c in C["candidates"] if c["id"] == "mug_clearance")
    assert mc["old_rechecked"]["status"] == "constraint_violated" and mc["new"]["status"] == "success"
    assert mc["new"]["measures"]["cup_0"]["min_transport_clearance_m"] >= 0.06 > C["old"]["measures"]["cup_0"]["min_transport_clearance_m"]
    assert C["model_calls"] == 0


def test_gate_card_reads_both_gate_shapes():
    ea = _load("export_assets")
    base = {"film": {"rule_id": "c", "applies_obj": "cup_0", "min_m": 0.06, "rule_type": "keep_clearance", "applies_kind": "cup",
                     "phase": "transport", "unchanged": {}, "others": [], "rows": {"new": []}},
            "spec": {"spec_id": "s", "version": 1, "goals": [], "ordering": [], "constraints": []}}
    msg = "top: focal length re-estimated 7.1% different from one oblique board view (a single-view estimate cannot prove which)"
    for g in ({"passed": False, "blocking": [{"code": "recalibration_required", "message": msg}]},
              {"passed": False, "blocking": 1, "blocking_items": [{"code": "recalibration_required", "message": msg}]}):
        cp = ea.build_copy({**base, "gate": g}, {"wall": "divider"}, "x")
        assert cp["gate"]["code"] == "recalibration_required" and cp["gate"]["headline"].startswith("Not yet")
        assert cp["gate"]["detail"].startswith("focal length re-estimated 7.1%")
    cp = ea.build_copy({**base, "gate": {"passed": True, "blocking": 0, "blocking_items": []}}, {}, "x")
    assert cp["gate"]["headline"].startswith("Yes") and cp["gate"]["code"] == "gate passed"
