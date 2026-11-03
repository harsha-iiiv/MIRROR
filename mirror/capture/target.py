"""Printable ChArUco calibration target with verified physical dimensions.

The target is a 5 x 7 ChArUco board (30 mm squares, 21 mm ArUco markers from
the 5x5_100 dictionary) plus a 50 mm scale bar, laid out to fit both A4 and US
Letter paper. A second page holds the synchronisation card (one large marker
that is shown to both cameras and then turned face down).

The PDF is written as plain vector rectangles in an uncompressed content
stream, so :func:`verify_pdf` can read the geometry back exactly and check it
against the spec and against OpenCV's own board layout. Printers rescale
pages ("fit to page"), so the person printing must measure the scale bar; the
measured value is recorded with the calibration (see ``calibrate.PrintCheck``).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal, Optional

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

MM_TO_PT = 72.0 / 25.4
GRID_MM = 1.5            # every board edge lies on this grid (squares 30 mm, marker cells 3 mm, marker inset 4.5 mm)
PAPER_MM = {"A4": (210.0, 297.0), "Letter": (215.9, 279.4)}


class TargetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "mirror_charuco_v1"
    version: int = 1
    kind: Literal["charuco"] = "charuco"
    dictionary: str = "DICT_5X5_100"
    squares_x: int = 5
    squares_y: int = 7
    square_mm: float = 30.0
    marker_mm: float = 21.0
    marker_ids: list[int] = Field(default_factory=lambda: list(range(17)))
    legacy_pattern: bool = False
    scale_bar_mm: float = 50.0
    sync_marker_id: int = 90
    sync_marker_mm: float = 80.0
    tag_ids: list[int] = Field(default_factory=lambda: [95, 96, 97])
    tag_mm: float = 20.0
    board_top_mm: float = 22.0         # distance from the top paper edge to the board
    papers: list[str] = Field(default_factory=lambda: ["A4", "Letter"])
    notes: str = ("Board frame: origin at the board's outer top-left corner as printed, x along the 5-square "
                  "side, y along the 7-square side, z out of the paper. Chessboard corner ids follow OpenCV.")

    @property
    def board_mm(self) -> tuple[float, float]:
        return self.squares_x * self.square_mm, self.squares_y * self.square_mm

    def board(self, square_mm: Optional[float] = None):
        """OpenCV CharucoBoard in metres. ``square_mm`` overrides the printed
        size with a measured one (marker size scales with it)."""
        import cv2
        s = (square_mm or self.square_mm) / 1000.0
        m = s * self.marker_mm / self.square_mm
        d = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, self.dictionary))
        b = cv2.aruco.CharucoBoard((self.squares_x, self.squares_y), s, m, d, np.array(self.marker_ids, dtype=np.int32))
        b.setLegacyPattern(self.legacy_pattern)
        return b

    def dictionary_obj(self):
        import cv2
        return cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, self.dictionary))


DEFAULT_TARGET = TargetSpec()


def load_spec(path) -> TargetSpec:
    return TargetSpec.model_validate_json(Path(path).read_text())


def board_bitmap(spec: TargetSpec = DEFAULT_TARGET, px_per_mm: float = 10.0) -> np.ndarray:
    """Board as a uint8 image (0 black, 255 white), no margin."""
    w, h = spec.board_mm
    size = (int(round(w * px_per_mm)), int(round(h * px_per_mm)))
    img = spec.board().generateImage(size, marginSize=0, borderBits=1)
    return img


def board_cells(spec: TargetSpec = DEFAULT_TARGET) -> np.ndarray:
    """Boolean grid (rows, cols) of GRID_MM cells, True = black. Raises if the
    board does not fall exactly on the grid."""
    ppm = 10.0
    img = board_bitmap(spec, ppm)
    k = int(round(GRID_MM * ppm))
    h, w = img.shape
    if h % k or w % k:
        raise ValueError("board size is not a multiple of the grid")
    blocks = img.reshape(h // k, k, w // k, k)
    if not (blocks.min(axis=(1, 3)) == blocks.max(axis=(1, 3))).all():
        raise ValueError("board edges do not lie on the %.1f mm grid" % GRID_MM)
    return blocks[:, 0, :, 0] == 0


def marker_cells(spec: TargetSpec, marker_id: int, size_mm: float) -> np.ndarray:
    import cv2
    bits = spec.dictionary_obj().markerSize + 2
    img = cv2.aruco.generateImageMarker(spec.dictionary_obj(), marker_id, bits * 10, borderBits=1)
    return img.reshape(bits, 10, bits, 10)[:, 0, :, 0] == 0


def _runs(cells: np.ndarray):
    """Horizontal runs of True cells -> (row, col0, length)."""
    for r, row in enumerate(cells):
        c = 0
        n = len(row)
        while c < n:
            if row[c]:
                c0 = c
                while c < n and row[c]:
                    c += 1
                yield r, c0, c - c0
            else:
                c += 1


# ---------------------------------------------------------------- PDF writer


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class _Page:
    def __init__(self, w_mm, h_mm):
        self.w, self.h = w_mm, h_mm
        self.ops: list[str] = []

    def rect(self, x_mm, y_top_mm, w_mm, h_mm, tag=""):
        """Filled black rectangle; y measured from the TOP edge of the paper."""
        x = x_mm * MM_TO_PT
        y = (self.h - y_top_mm - h_mm) * MM_TO_PT
        self.ops.append(f"{x:.4f} {y:.4f} {w_mm * MM_TO_PT:.4f} {h_mm * MM_TO_PT:.4f} re f{(' % ' + tag) if tag else ''}")

    def line(self, x0, y0, x1, y1, width_mm=0.3):
        self.ops.append(f"{width_mm * MM_TO_PT:.4f} w {x0 * MM_TO_PT:.4f} {(self.h - y0) * MM_TO_PT:.4f} m "
                        f"{x1 * MM_TO_PT:.4f} {(self.h - y1) * MM_TO_PT:.4f} l S")

    def text(self, x_mm, y_top_mm, s, size=9, bold=False):
        font = "/F2" if bold else "/F1"
        self.ops.append(f"BT {font} {size} Tf {x_mm * MM_TO_PT:.3f} {(self.h - y_top_mm) * MM_TO_PT:.3f} Td ({_esc(s)}) Tj ET")

    def stream(self) -> bytes:
        # '%' tags are PDF comments only in content streams when on their own line; keep them on separate lines
        out = []
        for op in self.ops:
            if " % " in op:
                body, tag = op.split(" % ", 1)
                out.append(body)
                out.append("%" + tag)
            else:
                out.append(op)
        return ("\n".join(out) + "\n").encode("latin-1")


def _write_pdf(pages: list[_Page], path: Path, title: str) -> None:
    objs: list[bytes] = []
    n_pages = len(pages)
    # 1 catalog, 2 pages, 3 font, 4 bold font, then (page, content) pairs
    kids = " ".join(f"{5 + 2 * i} 0 R" for i in range(n_pages))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    for i, p in enumerate(pages):
        content_ref = 6 + 2 * i
        objs.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {p.w * MM_TO_PT:.4f} {p.h * MM_TO_PT:.4f}] "
                     f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_ref} 0 R >>").encode())
        data = p.stream()
        objs.append(f"<< /Length {len(data)} >>\nstream\n".encode() + data + b"endstream")
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info << /Title ({_esc(title)}) >> >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    Path(path).write_bytes(bytes(out))


INSTRUCTIONS = [
    "1. Print at 100% / 'Actual size'. Do not use 'Fit to page'.",
    "2. Measure the 50 mm bar with a ruler. Enter what you measure; reprint if it is off by more than 1 mm.",
    "3. Measure one board square (nominal 30.0 mm) and enter it too.",
    "4. Glue the sheet flat onto stiff card. A curled or wavy board gives wrong calibration.",
    "5. Lens: wave the board in front of each phone (12+ views, tilted, filling the corners).",
    "6. Table: lay it flat where the action happens, film 3 s with both phones, then remove it.",
    "7. Repeat step 6 whenever a phone moves, zooms or changes resolution.",
]


def _scale_bar(p: _Page, x0: float, y_top: float, length: float) -> None:
    p.rect(x0, y_top, length, 2.0, tag="scale_bar")
    for k in range(0, int(length) + 1, 10):
        p.rect(x0 + k - 0.15, y_top - 3.0, 0.3, 3.0, tag="scale_tick")
    p.text(x0 + length + 3.0, y_top + 2.0, f"{length:.0f} mm: measure this bar", size=8)


def target_pages(spec: TargetSpec = DEFAULT_TARGET, paper: str = "A4") -> list[_Page]:
    W, H = PAPER_MM[paper]
    bw, bh = spec.board_mm
    x0 = (W - bw) / 2.0
    y0 = spec.board_top_mm
    p = _Page(W, H)
    p.text(x0, 11.0, f"MIRROR calibration target {spec.name} ({spec.squares_x} x {spec.squares_y} ChArUco, "
                     f"{spec.square_mm:.0f} mm squares, {spec.marker_mm:.0f} mm markers, {spec.dictionary})",
           size=8.5, bold=True)
    p.text(x0, 16.0, f"Paper: {paper}. Board {bw:.0f} x {bh:.0f} mm. Keep the white border around the board.", size=7.5)
    cells = board_cells(spec)
    for r, c0, n in _runs(cells):
        p.rect(x0 + c0 * GRID_MM, y0 + r * GRID_MM, n * GRID_MM, GRID_MM, tag="board")
    y = y0 + bh + 7.0
    _scale_bar(p, x0, y, spec.scale_bar_mm)
    y += 6.5
    for line in INSTRUCTIONS:
        p.text(x0, y, line, size=7)
        y += 3.3
    # sync card page
    q = _Page(W, H)
    m = spec.sync_marker_mm
    cells_m = marker_cells(spec, spec.sync_marker_id, m)
    cs = m / cells_m.shape[0]
    mx, my = (W - m) / 2.0, 45.0
    q.text(mx - 20, 20.0, "MIRROR sync card (marker %d)" % spec.sync_marker_id, size=11, bold=True)
    q.text(mx - 20, 27.0, "Hold it where BOTH phones see it, then turn it face down quickly.", size=8)
    q.text(mx - 20, 31.5, "Do this at the start and again at the end of every take.", size=8)
    for r, c0, n in _runs(cells_m):
        q.rect(mx + c0 * cs, my + r * cs, n * cs, cs, tag="sync_marker")
    _scale_bar(q, mx - 20, my + m + 15.0, spec.scale_bar_mm)
    # optional object tags (ids 95-97, 20 mm): tape one upright on the mug, printed 'up' towards its opening
    ty = my + m + 32.0
    q.text(mx - 20, ty, "Optional object tags (20 mm). Tape flat and upright: the arrow points to the mug's opening.", size=7.5)
    for k, tid in enumerate(spec.tag_ids):
        cells_t = marker_cells(spec, tid, spec.tag_mm)
        ct = spec.tag_mm / cells_t.shape[0]
        tx = mx - 20 + k * 40.0
        for r, c0, n in _runs(cells_t):
            q.rect(tx + c0 * ct, ty + 6.0 + r * ct, n * ct, ct, tag="object_tag")
        q.text(tx, ty + 6.0 + spec.tag_mm + 5.0, f"tag {tid}  ^ up", size=7)
    return [p, q]


def write_pdf(path, spec: TargetSpec = DEFAULT_TARGET, paper: str = "A4") -> Path:
    path = Path(path)
    _write_pdf(target_pages(spec, paper), path, f"{spec.name} ({paper})")
    return path


def _pdf_bytes(spec: TargetSpec = DEFAULT_TARGET, paper: str = "A4") -> bytes:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        return write_pdf(Path(td) / "t.pdf", spec, paper).read_bytes()


def write_png(path, spec: TargetSpec = DEFAULT_TARGET, dpi: int = 300, paper: str = "A4") -> Path:
    """Raster copy of page 1 at ``dpi`` (the DPI is stored in the file). Prefer the PDF for printing."""
    from PIL import Image
    img = rasterize_pdf_page(target_pages(spec, paper)[0], px_per_mm=dpi / 25.4)
    Image.fromarray(img).save(Path(path), dpi=(dpi, dpi))
    return Path(path)


# ----------------------------------------------------------- read back / verify

_RECT = re.compile(rb"([-\d.]+) ([-\d.]+) ([-\d.]+) ([-\d.]+) re f\n%(\w+)")


def pdf_rects(path) -> dict:
    """Rectangles per page, in mm from the top-left of the paper, grouped by tag."""
    data = Path(path).read_bytes()
    pages = []
    for m in re.finditer(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", data):
        pages.append({"w_mm": float(m.group(1)) / MM_TO_PT, "h_mm": float(m.group(2)) / MM_TO_PT, "rects": {}})
    streams = re.findall(rb"stream\n(.*?)endstream", data, re.S)
    for page, s in zip(pages, streams):
        for m in _RECT.finditer(s):
            x, y, w, h = (float(v) / MM_TO_PT for v in m.groups()[:4])
            tag = m.group(5).decode()
            page["rects"].setdefault(tag, []).append((x, page["h_mm"] - y - h, w, h))
    return {"pages": pages}


def rasterize_pdf_page(page: _Page, px_per_mm: float) -> np.ndarray:
    """Rasterise the rectangles of a page we wrote (text is not drawn)."""
    H = int(round(page.h * px_per_mm))
    Wd = int(round(page.w * px_per_mm))
    img = np.full((H, Wd), 255, np.uint8)
    for op in page.ops:
        if op.endswith(("re f",)) or " re f %" in op:
            x, y, w, h = (float(v) for v in op.split()[:4])
            x0 = x / MM_TO_PT
            y0 = page.h - (y + h) / MM_TO_PT
            r0, r1 = int(round(y0 * px_per_mm)), int(round((y0 + h / MM_TO_PT) * px_per_mm))
            c0, c1 = int(round(x0 * px_per_mm)), int(round((x0 + w / MM_TO_PT) * px_per_mm))
            img[max(r0, 0):r1, max(c0, 0):c1] = 0
    return img


def verify_pdf(path, spec: TargetSpec = DEFAULT_TARGET, paper: Optional[str] = None, tol_mm: float = 0.01) -> dict:
    """Read the PDF geometry back and check it against the spec and OpenCV's
    board layout. Returns a report; ``ok`` is False with ``problems`` listed."""
    info = pdf_rects(path)
    problems = []
    p1 = info["pages"][0]
    if paper:
        W, H = PAPER_MM[paper]
        if abs(p1["w_mm"] - W) > tol_mm or abs(p1["h_mm"] - H) > tol_mm:
            problems.append(f"page size {p1['w_mm']:.2f} x {p1['h_mm']:.2f} mm, expected {paper}")
    board = p1["rects"].get("board", [])
    if not board:
        problems.append("no board rectangles")
        return {"ok": False, "problems": problems}
    xs0 = min(r[0] for r in board)
    ys0 = min(r[1] for r in board)
    xs1 = max(r[0] + r[2] for r in board)
    ys1 = max(r[1] + r[3] for r in board)
    bw, bh = spec.board_mm
    if abs((xs1 - xs0) - bw) > tol_mm or abs((ys1 - ys0) - bh) > tol_mm:
        problems.append(f"board extent {xs1 - xs0:.3f} x {ys1 - ys0:.3f} mm, expected {bw} x {bh}")
    # rebuild the cell grid from the rectangles and compare with OpenCV's layout
    expect = board_cells(spec)
    got = np.zeros_like(expect)
    for x, y, w, h in board:
        r = int(round((y - ys0) / GRID_MM))
        c0 = int(round((x - xs0) / GRID_MM))
        n = int(round(w / GRID_MM))
        if abs(h - GRID_MM) > tol_mm or abs((y - ys0) - r * GRID_MM) > tol_mm or abs((x - xs0) - c0 * GRID_MM) > tol_mm:
            problems.append("rectangle off the grid")
            break
        got[r, c0:c0 + n] = True
    if not np.array_equal(got, expect):
        problems.append(f"{int((got != expect).sum())} grid cells differ from OpenCV's board layout")
    bar = p1["rects"].get("scale_bar", [])
    if len(bar) != 1 or abs(bar[0][2] - spec.scale_bar_mm) > tol_mm:
        problems.append("scale bar missing or not %.0f mm" % spec.scale_bar_mm)
    ticks = p1["rects"].get("scale_tick", [])
    if len(ticks) != int(spec.scale_bar_mm // 10) + 1:
        problems.append("scale bar ticks missing")
    # one square measured from the rectangles: first row starts with a black square in OpenCV's layout
    sq = None
    first_row = sorted((r for r in board if abs(r[1] - ys0) < tol_mm), key=lambda r: r[0])
    if first_row:
        sq = first_row[0][2]
    report = {
        "ok": not problems, "problems": problems, "paper_mm": [round(p1["w_mm"], 3), round(p1["h_mm"], 3)],
        "board_extent_mm": [round(xs1 - xs0, 4), round(ys1 - ys0, 4)], "board_origin_mm": [round(xs0, 4), round(ys0, 4)],
        "first_square_mm": None if sq is None else round(sq, 4),
        "scale_bar_mm": round(bar[0][2], 4) if len(bar) == 1 else None,
        "n_rects": len(board), "pages": len(info["pages"]),
    }
    return report


def build_assets(out_dir, spec: TargetSpec = DEFAULT_TARGET) -> dict:
    """Write the spec JSON, one PDF per paper size and a 300 dpi PNG, verify
    each PDF and return the verification reports."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{spec.name}.json").write_text(spec.model_dump_json(indent=2) + "\n")
    reports = {}
    for paper in spec.papers:
        pdf = write_pdf(out / f"{spec.name}_{paper}.pdf", spec, paper)
        reports[pdf.name] = verify_pdf(pdf, spec, paper)
    write_png(out / f"{spec.name}_A4_300dpi.png", spec, 300, "A4")
    return reports


if __name__ == "__main__":  # python -m mirror.capture.target <out_dir>
    import sys
    print(json.dumps(build_assets(sys.argv[1] if len(sys.argv) > 1 else "assets/calibration"), indent=2))
