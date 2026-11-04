"""Honesty chips, wrapping, placement label, calibration flag and correction provenance in the twin films."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageDraw

from mirror.twin import text as TX
from mirror.twin.build import calibration_flag_open, placement_labels, tray_agreement

D1_LABELS = [
    "Real phone footage", "One camera (single view, reduced)", "Calibration reuse flagged",
    "Development clip D1, never scored", "Model outputs replayed from recordings (no new model calls)",
    "Layout shifted by software (+38.7 cm, -5.9 cm) to fit the robot's reach",
    "Stated task (gate refused): not inferred from the video", "End-of-take drift check not used (board re-laid)",
]
CAPTION = ("Approximate: one camera, calibration reuse flagged. Simulated gripper and proxies, not your objects. "
           "Rehearsal correction, not chosen by you. Development clip D1, never scored, single view, reduced.")


def _draw():
    return ImageDraw.Draw(Image.new("RGB", (8, 8)))


def test_wrap_keeps_every_word_and_fits():
    d, f = _draw(), TX.font("regular", 12)
    text = "Layout shifted by software (+38.7 cm, -5.9 cm) to fit the robot's reach " + "x" * 120
    lines = TX.wrap(d, text, f, 150)
    assert len(lines) > 2
    assert all(d.textlength(ln, font=f) <= 150 for ln in lines)
    assert "".join(lines).replace(" ", "") == text.replace(" ", "")          # nothing dropped, nothing truncated


@pytest.mark.parametrize("width", [540, 780, 1264])
def test_chip_rows_hold_every_label_whole(width):
    d, f = _draw(), TX.font("regular", TX.CHIP_PX)
    rows, total = TX.chip_rows(d, D1_LABELS, f, width - 20)
    assert [" ".join(r[4]) for r in rows] == D1_LABELS                       # every chip, in order, word for word
    assert all(x + w <= width - 20 for x, y, w, h, _ in rows)               # wrapped inside the frame
    assert all("…" not in " ".join(r[4]) for r in rows)
    assert total > 0 and TX.CHIP_PX >= 12                                     # legible at film size


def test_panel_label_draws_chips_and_full_caption():
    img = np.full((960, 540, 3), 90, np.uint8)
    out = TX.panel_label(img, "The robot on your table", "seen from your phone", ["carry", "beads in cup 8/8"],
                         CAPTION, D1_LABELS)
    assert out.shape == img.shape
    accent = np.all(np.abs(out.astype(int) - np.array(TX.ACCENT)) < 40, axis=2)
    assert accent[:, :].sum() > 500                                           # chips drawn (accent outline + text)
    lines = TX.caption_lines(_draw(), CAPTION, 540)
    assert len(lines) >= 3 and " ".join(lines) == " ".join(CAPTION.split())   # more than the old two-line cut


@pytest.mark.parametrize("width", [780, 1280])
def test_canvas_header_grows_and_panels_fit(width):
    title = "MIRROR · your table, your phone, the robot"
    h0 = TX.header_height(width, title, "short caption", None)
    h1 = TX.header_height(width, title, CAPTION, D1_LABELS)
    assert h1 > h0 >= 48
    ph = 720 - h1 - 8
    pw = int(ph * 378 / 672)
    panel = np.full((ph, pw, 3), 200, np.uint8)
    out = TX.canvas([panel, panel], (width, 720), 48, title, CAPTION, labels=D1_LABELS)
    assert out.shape == (720, width, 3)
    assert (out[h1:h1 + ph, :, :] == 200).all(axis=2).sum() >= 2 * pw * ph * 0.98   # panels unscaled, below the header
    # oversize panels are scaled to fit instead of being cut
    big = np.full((700, 378, 3), 200, np.uint8)
    out2 = TX.canvas([big, big], (width, 720), 48, title, CAPTION, labels=D1_LABELS)
    assert out2.shape == (720, width, 3) and (out2[-4:, :, :] == 200).all(axis=2).sum() == 0


def test_canvas_caption_not_truncated():
    d = _draw()
    lines = TX.wrap(d, CAPTION, TX.font("regular", TX.CAPTION_PX), 780 - 20)
    assert " ".join(lines) == " ".join(CAPTION.split()) and len(lines) >= 2


# ---------------------------------------------------------------- placement label
D1_T2S = {"origin_xy_m": [0.31195, 0.045912], "yaw_deg": 0.0, "source": "auto_layout_centred",
          "fell_back": {"why": ["tray_0 not fully inside the robot's reach"], "shift_cm": [38.7, -5.9], "still": []}}


def test_placement_label_from_table_to_sim():
    assert placement_labels(D1_T2S) == ["Layout shifted by software (+38.7 cm, -5.9 cm) to fit the robot's reach"]


def test_placement_label_default_and_missing():
    assert placement_labels({"source": "default_centred", "origin_xy_m": [-0.075, 0.105]}) == []
    assert placement_labels(None, None) == []


def test_placement_label_without_fell_back_uses_origin():
    t2s = {"origin_xy_m": [0.31195, 0.045912], "source": "auto_layout_centred"}
    assert placement_labels(t2s) == ["Layout shifted by software (+38.7 cm, -5.9 cm) to fit the robot's reach"]
    # the session's placement (pydantic-like object) is the fallback
    sp = SimpleNamespace(source="auto_layout_centred", origin_xy_m=(0.31195, 0.045912), yaw_deg=0.0)
    assert placement_labels(None, sp)[0].startswith("Layout shifted by software (+38.7 cm, -5.9 cm)")


def test_placement_label_person_and_still_out_of_reach():
    lab = placement_labels({"source": "user_supplied", "origin_xy_m": [0.025, 0.105]})
    assert lab == ["Layout placed by hand (+10.0 cm, +0.0 cm) from the real table frame, not measured"]
    t2s = dict(D1_T2S, fell_back={"shift_cm": [10, 0], "still": ["cup_0 outside the robot's reach"]})
    lab = placement_labels(t2s)
    assert len(lab) == 2 and "cup_0 outside the robot's reach" in lab[1]


# ---------------------------------------------------------------- calibration flag (P2-5)
def _rev(code, resolved):
    return SimpleNamespace(code=code, resolved=resolved)


def test_calibration_flag_only_for_open_item():
    checks = {"top": {"reasons": [], "checks": [{"name": "focal", "ok": True}]}}
    assert calibration_flag_open(SimpleNamespace(review=[_rev("recalibration_required", False)], recalibration=checks))
    assert not calibration_flag_open(SimpleNamespace(review=[_rev("recalibration_required", True)], recalibration=checks))
    # a clean capture still fills s.recalibration with the checks that passed: not a flag
    assert not calibration_flag_open(SimpleNamespace(review=[_rev("mapping_unconfirmed", False)], recalibration=checks))
    assert not calibration_flag_open(SimpleNamespace(review=[], recalibration={}))


# ---------------------------------------------------------------- registration wording (P1-5)
def test_tray_agreement_by_construction():
    reg = {"tray_iou": 0.921, "object_base_offset_px": {"red_box": 11.1, "mug": 8.9, "marker": 8.6}}
    r = tray_agreement(reg, "tracked_tray_at_rest", (540, 960))
    assert r["agreement_by_construction"] is True
    assert "by construction" in r["tray_note"] and "9-11 px at 540x960" in r["tray_note"]
    r2 = tray_agreement(reg, "assumed_aligned_with_board", (540, 960))
    assert r2["agreement_by_construction"] is False and "independent" in r2["tray_note"]
