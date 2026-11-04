"""Text and layout for twin media (PIL). Quiet: dark chrome, one accent, legible small type."""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont

INK = (236, 236, 232)
MUTED = (160, 160, 166)
BG = (20, 20, 22)
CHIP = (16, 16, 18)
ACCENT = (238, 112, 72)       # one accent: the honesty labels

FONT_DIRS = [os.environ.get("MIRROR_FILM_FONT_DIR", ""), "/usr/share/fonts/truetype/roboto/unhinted/RobotoTTF",
             "/Library/Fonts", os.path.expanduser("~/Library/Fonts")]


@lru_cache(maxsize=64)
def font(weight: str, size: int):
    name = {"bold": "Roboto-Medium.ttf", "regular": "Roboto-Regular.ttf"}[weight]
    for d in FONT_DIRS:
        p = os.path.join(d, name) if d else ""
        if p and os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)


def wrap(draw: ImageDraw.ImageDraw, text: str, f, max_w: float) -> list[str]:
    """Split ``text`` into lines no wider than ``max_w`` (words first; an over-long word is broken by characters).
    Never drops text: joining the lines with spaces gives back every word."""
    max_w = max(8.0, float(max_w))
    out, line = [], ""
    for wd in str(text).split():
        cand = (line + " " + wd).strip()
        if draw.textlength(cand, font=f) <= max_w:
            line = cand
            continue
        if line:
            out.append(line)
        while draw.textlength(wd, font=f) > max_w and len(wd) > 1:
            k = len(wd)
            while k > 1 and draw.textlength(wd[:k], font=f) > max_w:
                k -= 1
            out.append(wd[:k]); wd = wd[k:]
        line = wd
    if line or not out:
        out.append(line)
    return out


def chip_rows(draw: ImageDraw.ImageDraw, labels: Sequence[str], f, max_w: float, gap: int = 6, pad: int = 6):
    """Lay out honesty chips left to right, wrapping chips onto new rows and long labels onto several lines inside
    their chip. Returns [(x, y, w, h, lines)] relative to (0, 0) and the total height."""
    lh = f.size + 4
    x, y, row_h, out = 0, 0, 0, []
    for lb in labels:
        if not lb:
            continue
        lines = wrap(draw, lb, f, max_w - 2 * pad)
        w = int(max(draw.textlength(ln, font=f) for ln in lines)) + 2 * pad
        h = lh * len(lines) + 4
        if x > 0 and x + w > max_w:
            x, y, row_h = 0, y + row_h + gap, 0
        out.append((x, y, w, h, lines))
        x += w + gap
        row_h = max(row_h, h)
    return out, (y + row_h if out else 0)


def draw_chips(im: Image.Image, xy, labels: Sequence[str], f, max_w: float) -> int:
    """Draw the honesty chips (accent outline, accent text) at ``xy``; returns the height used."""
    d = ImageDraw.Draw(im)
    rows, total = chip_rows(d, labels, f, max_w)
    if not rows:
        return 0
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0)); od = ImageDraw.Draw(ov)
    for x, y, w, h, _ in rows:
        od.rounded_rectangle([xy[0] + x, xy[1] + y, xy[0] + x + w, xy[1] + y + h], radius=8, fill=(*CHIP, 185),
                             outline=(*ACCENT, 235), width=1)
    im.alpha_composite(ov)
    d = ImageDraw.Draw(im)
    for x, y, w, h, lines in rows:
        for k, ln in enumerate(lines):
            d.text((xy[0] + x + 6, xy[1] + y + 2 + k * (f.size + 4)), ln, font=f, fill=ACCENT)
    return total


def _chip(draw: ImageDraw.ImageDraw, xy, lines, fonts, colours, pad=7, alpha=170, radius=6, img=None, max_w=None):
    x, y = xy
    if max_w:
        nl, nf, nc = [], [], []
        for ln, f, c in zip(lines, fonts, colours):
            for part in wrap(draw, ln, f, max_w - 2 * pad):
                nl.append(part); nf.append(f); nc.append(c)
        lines, fonts, colours = nl, nf, nc
    widths, heights = [], []
    for ln, f in zip(lines, fonts):
        b = draw.textbbox((0, 0), ln, font=f)
        widths.append(b[2] - b[0]); heights.append(f.size + 3)
    w, h = max(widths) + 2 * pad, sum(heights) + 2 * pad - 3
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle([x, y, x + w, y + h], radius=radius, fill=(*CHIP, alpha))
    img.alpha_composite(overlay)
    yy = y + pad - 1
    for ln, f, c in zip(lines, fonts, colours):
        draw.text((x + pad, yy), ln, font=f, fill=c)
        yy += f.size + 3
    return w, h


CHIP_PX = 12            # honesty chips: legible on a 540 px wide phone frame and in the 1280x720 canvases
CAPTION_PX = 12


def caption_lines(draw: ImageDraw.ImageDraw, caption: Optional[str], width: int) -> list[str]:
    return wrap(draw, caption, font("regular", CAPTION_PX), width - 24) if caption else []


def panel_label(img: np.ndarray, title: str, sub: str, status: Sequence[str], caption: Optional[str],
                labels: Optional[Sequence[str]], small: bool = False) -> np.ndarray:
    """Title chip (top left), status chip (bottom left), honesty chips (under the title, wrapped, never truncated)
    and a caption band (bottom, as many lines as it needs)."""
    im = Image.fromarray(img).convert("RGBA")
    d = ImageDraw.Draw(im)
    s = 0.8 if small else 1.0
    ft, fs = font("bold", int(17 * s)), font("regular", int(13 * s))
    _, th = _chip(d, (int(10 * s), int(10 * s)), [title, sub], [ft, fs], [INK, MUTED], img=im, max_w=im.width - int(20 * s))
    d = ImageDraw.Draw(im)
    fc = font("regular", CAPTION_PX)
    cap = caption_lines(d, caption, im.width)
    band = (16 * len(cap) + 14) if cap else 0
    if status:
        fl = font("regular", int(13 * s))
        rows = [part for ln in status for part in wrap(d, ln, fl, im.width - int(20 * s) - 14)]
        hh = (fl.size + 3) * len(rows) + 11
        _chip(d, (int(10 * s), im.height - hh - int(12 * s) - band), rows, [fl] * len(rows),
              [INK] + [MUTED] * (len(rows) - 1), img=im)
        d = ImageDraw.Draw(im)
    if labels:
        draw_chips(im, (10, int(10 * s) + th + 8), [x for x in labels if x], font("regular", CHIP_PX), im.width - 20)
        d = ImageDraw.Draw(im)
    if cap:
        ov = Image.new("RGBA", im.size, (0, 0, 0, 0)); od = ImageDraw.Draw(ov)
        od.rectangle([0, im.height - band, im.width, im.height], fill=(*BG, 215))
        im.alpha_composite(ov); d = ImageDraw.Draw(im)
        for k, ln in enumerate(cap):
            d.text((12, im.height - band + 6 + 16 * k), ln, font=fc, fill=MUTED)
    return np.asarray(im.convert("RGB"))


def topdown_panel(img: np.ndarray, size, title: str, sub: str, px_per_m: float) -> np.ndarray:
    W, H = size
    im = Image.new("RGBA", (W, H), (*BG, 255))
    src = Image.fromarray(img)
    k = min(W / src.width, H / src.height)
    nw, nh = int(src.width * k), int(src.height * k)
    src = src.resize((nw, nh), Image.LANCZOS)
    im.paste(src, ((W - nw) // 2, (H - nh) // 2))
    d = ImageDraw.Draw(im)
    _chip(d, (10, 10), [title, sub], [font("bold", 14), font("regular", 11)], [INK, MUTED], img=im)
    d = ImageDraw.Draw(im)
    L = 0.10 * px_per_m * k
    x1, y1 = W - 16, H - 22
    d.line([x1 - L, y1, x1, y1], fill=INK, width=2)
    d.line([x1 - L, y1 - 4, x1 - L, y1 + 4], fill=INK, width=2)
    d.line([x1, y1 - 4, x1, y1 + 4], fill=INK, width=2)
    f = font("regular", 11)
    d.text((x1 - L, y1 - 18), "10 cm", font=f, fill=INK)
    return np.asarray(im.convert("RGB"))


def header_height(width: int, title: str, caption: Optional[str], labels: Optional[Sequence[str]] = None,
                  gap: int = 8) -> int:
    """Height of a canvas header holding the title, the whole caption (wrapped) and every honesty chip (wrapped).
    Build panels to ``H - header_height - gap`` so nothing is cut or scaled."""
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    fc = font("regular", CAPTION_PX)
    n_cap = len(wrap(d, caption, fc, width - 2 * gap - 4)) if caption else 0
    _, ch = chip_rows(d, [x for x in labels or [] if x], font("regular", CHIP_PX), width - 2 * gap - 4)
    h = 7 + 22 + 16 * n_cap + (6 + ch if ch else 0) + 6
    return max(48, int(h))


def canvas(panels: Sequence[np.ndarray], size, head: int, title: str, caption: str, gap: int = 8,
           labels: Optional[Sequence[str]] = None) -> np.ndarray:
    """Title, caption (wrapped, never truncated) and honesty chips above the panels. ``head`` is a minimum: the
    header grows to fit its text, and panels taller than what is left are scaled down to fit."""
    W, H = size
    head = max(head, header_height(W, title, caption, labels, gap))
    im = Image.new("RGBA", (W, H), (*BG, 255))
    ps = [Image.fromarray(p) for p in panels]
    avail_h = H - head - gap
    total_w = sum(p.width for p in ps) + gap * (len(ps) + 1)
    k = min(1.0, avail_h / max(p.height for p in ps), W / total_w) if ps else 1.0
    if k < 0.999:
        ps = [p.resize((max(1, int(p.width * k)), max(1, int(p.height * k))), Image.LANCZOS) for p in ps]
    x = max(gap, (W - (sum(p.width for p in ps) + gap * (len(ps) - 1))) // 2)
    for p in ps:
        im.paste(p, (x, head))
        x += p.width + gap
    d = ImageDraw.Draw(im)
    d.text((gap + 2, 7), title, font=font("bold", 16), fill=INK)
    fc = font("regular", CAPTION_PX)
    y = 29
    for ln in (wrap(d, caption, fc, W - 2 * gap - 4) if caption else []):
        d.text((gap + 2, y), ln, font=fc, fill=MUTED)
        y += 16
    if labels:
        draw_chips(im, (gap + 2, y + 6), [x for x in labels if x], font("regular", CHIP_PX), W - 2 * gap - 4)
    return np.asarray(im.convert("RGB"))
