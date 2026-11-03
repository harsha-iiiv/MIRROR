"""Printable calibration target: physical dimensions are read back from the PDF
and checked against the spec and OpenCV's board layout. Detection runs on a
SYNTHETIC rasterisation of the page."""
import json
from pathlib import Path

import numpy as np
import pytest

from mirror.capture import target as T

ASSETS = Path(__file__).resolve().parents[2] / "assets" / "calibration"


@pytest.mark.parametrize("paper", ["A4", "Letter"])
def test_written_pdf_has_exact_board_geometry(tmp_path, paper):
    pdf = T.write_pdf(tmp_path / f"t_{paper}.pdf", T.DEFAULT_TARGET, paper)
    rep = T.verify_pdf(pdf, T.DEFAULT_TARGET, paper, tol_mm=0.005)
    assert rep["ok"], rep["problems"]
    assert rep["board_extent_mm"] == [150.0, 210.0]
    assert rep["first_square_mm"] == 30.0
    assert rep["scale_bar_mm"] == 50.0
    assert rep["pages"] == 2
    # board and text fit inside a 5 mm printer margin on this paper
    w, h = T.PAPER_MM[paper]
    x0, y0 = rep["board_origin_mm"]
    assert x0 >= 5 and x0 + 150 <= w - 5 and y0 >= 5 and y0 + 210 <= h - 5


def test_committed_assets_match_the_spec_and_verify():
    spec = T.load_spec(ASSETS / "mirror_charuco_v1.json")
    assert spec == T.DEFAULT_TARGET
    for paper in spec.papers:
        rep = T.verify_pdf(ASSETS / f"mirror_charuco_v1_{paper}.pdf", spec, paper)
        assert rep["ok"], rep["problems"]
    # committed PDFs are byte-identical to what the generator writes now
    for paper in spec.papers:
        assert (ASSETS / f"mirror_charuco_v1_{paper}.pdf").read_bytes() == T._pdf_bytes(spec, paper)


def test_tampered_pdf_fails_verification(tmp_path):
    pdf = T.write_pdf(tmp_path / "t.pdf")
    data = pdf.read_bytes()
    # shrink one board rectangle by editing its width in the content stream
    i = data.index(b" re f\n%board")
    j = data.rindex(b" ", 0, i)
    k = data.rindex(b" ", 0, j)
    bad = data[:k + 1] + b"1.0000" + data[j:]
    (tmp_path / "bad.pdf").write_bytes(bad)
    assert not T.verify_pdf(tmp_path / "bad.pdf")["ok"]


def test_scale_bar_change_is_caught(tmp_path):
    spec = T.DEFAULT_TARGET.model_copy(update={"scale_bar_mm": 40.0})
    pdf = T.write_pdf(tmp_path / "t.pdf", spec)
    rep = T.verify_pdf(pdf, T.DEFAULT_TARGET)
    assert not rep["ok"] and any("scale bar" in p for p in rep["problems"])


def test_board_detected_on_synthetic_raster_of_the_pdf_page():
    """SYNTHETIC: the vector page rasterised at 4 px/mm; every inner corner found."""
    import cv2
    page = T.target_pages(T.DEFAULT_TARGET, "A4")[0]
    img = T.rasterize_pdf_page(page, px_per_mm=4.0)
    det = cv2.aruco.CharucoDetector(T.DEFAULT_TARGET.board())
    cc, ci, mc, mi = det.detectBoard(img)
    assert ci is not None and len(ci) == (5 - 1) * (7 - 1)
    assert sorted(mi.ravel().tolist()) == T.DEFAULT_TARGET.marker_ids
    # corner spacing in the raster equals 30 mm at 4 px/mm
    pts = {int(i): p for i, p in zip(ci.ravel(), cc.reshape(-1, 2))}
    assert abs(np.linalg.norm(pts[1] - pts[0]) - 120.0) < 0.5


def test_sync_card_and_tags_are_distinct_from_board_markers():
    ids = set(T.DEFAULT_TARGET.marker_ids)
    from mirror.capture.track import TAG_IDS
    assert T.DEFAULT_TARGET.sync_marker_id not in ids
    assert not (set(TAG_IDS) & ids) and T.DEFAULT_TARGET.sync_marker_id not in TAG_IDS
