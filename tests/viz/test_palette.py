"""The 3D workspace, the 2D fallback and the map use the same object colours as the capture pages."""
from __future__ import annotations

import re
from pathlib import Path

from mirror.studio.real_table import LABEL_COLOURS

ROOT = Path(__file__).resolve().parents[2]


def _palette() -> dict[str, str]:
    src = (ROOT / "web/js/viz/palette.js").read_text()
    body = src[src.index("OBJECT_COLORS = {"):src.index("};")]
    return {k: v.lower() for k, v in re.findall(r"(\w+): '(#[0-9a-fA-F]{6})'", body)}


def test_viewer_palette_matches_capture_pages():
    pal = _palette()
    roles = {"mug": "cup", "red_box": "block", "marker": "marker", "tray": "tray", "divider": "obstacle"}
    for label, role in roles.items():
        assert pal[role] == LABEL_COLOURS[label].lower(), (label, role)


def test_palette_used_by_every_object_drawing():
    for rel in ("web/js/viz/look.js", "web/js/viz/fallback2d.js", "web/js/minimap.js", "web/js/viewer.js"):
        assert "palette.js" in (ROOT / rel).read_text(), rel
