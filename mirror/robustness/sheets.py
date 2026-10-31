"""Contact sheets: original and every counterfactual of one frame, registered
to the source, with ground-truth outlines and per-class SAM results."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy import ndimage

from .frames import GTFrame
from .geometry import check_geometry

COLORS = {"cup": (255, 0, 200), "block": (0, 200, 255), "marker": (255, 220, 0)}


def _outline(img: np.ndarray, masks: dict, width: int = 1) -> np.ndarray:
    out = img.copy()
    for k, c in COLORS.items():
        m = masks.get(k)
        if m is None or not m.any():
            continue
        b = m & ~ndimage.binary_erosion(m, iterations=width)
        out[ndimage.binary_dilation(b)] = c
    return out


def _label(img: np.ndarray, lines: list[str]) -> np.ndarray:
    from PIL import Image, ImageDraw
    im = Image.fromarray(img)
    d = ImageDraw.Draw(im)
    h = 14 * len(lines) + 8
    d.rectangle([0, 0, im.width, h], fill=(0, 0, 0))
    for i, t in enumerate(lines):
        d.text((6, 4 + 14 * i), t, fill=(255, 255, 255))
    return np.asarray(im)


def contact_sheet(frame: GTFrame, report: dict, frames_dir: Path, out_path: Path, cols: int = 3,
                  scale: float = 0.5, condition: str = "A", correction: str = "corrected") -> Path:
    from PIL import Image
    rows = [r for r in report["rows"] if r["frame_id"] == frame.frame_id
            and r.get("condition", condition) == condition and r.get("correction", correction) == correction]
    by_ver: dict[str, dict] = {}
    for r in rows:
        by_ver.setdefault(r["version"], {})[r["cls"]] = r
    edits = {e["instruction_id"]: e for e in report["edits"] if e["frame_id"] == frame.frame_id}
    tiles = []
    for ver, cls_rows in by_ver.items():
        if ver == "original":
            img = frame.rgb
            head = f"{frame.frame_id}: original"
        else:
            p = Path(frames_dir) / f"{frame.frame_id}__{ver}.webp"
            if p.exists():
                from .counterfactuals import _decode_any
                raw = _decode_any(p.read_bytes())
            else:
                from .instructions import by_id
                from .local import apply_local
                ins = by_id([ver])[0]
                if ins.kind != "local":
                    continue
                raw = apply_local(frame.rgb, ins)
            _, img = check_geometry(frame.rgb, raw, frame.masks)
            e = edits.get(ver, {})
            g = e.get("geometry", {})
            reg = g.get("registration", {})
            head = (f"{ver}: geometry {g.get('verdict', '?')}"
                    + (f", registered dx={reg.get('dx')} dy={reg.get('dy')} s={reg.get('scale')}" if reg.get("applied") else ""))
        res = ", ".join(f"{c} IoU {r['iou']:.2f}" + ("" if r["found"] else " (not found)")
                        for c, r in cls_rows.items())
        tile = _label(_outline(np.asarray(img), frame.masks), [head, res])
        tiles.append(tile)
    H, W = tiles[0].shape[:2]
    while len(tiles) % cols:
        tiles.append(np.full((H, W, 3), 255, np.uint8))
    grid = np.concatenate([np.concatenate(tiles[i:i + cols], 1) for i in range(0, len(tiles), cols)], 0)
    im = Image.fromarray(grid)
    im = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(out_path, quality=88)
    return out_path
