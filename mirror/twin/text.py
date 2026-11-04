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


def _chip(draw: ImageDraw.ImageDraw, xy, lines, fonts, colours, pad=7, alpha=170, radius=6, img=None):
    x, y = xy
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


def panel_label(img: np.ndarray, title: str, sub: str, status: Sequence[str], caption: Optional[str],
                labels: Optional[Sequence[str]], small: bool = False) -> np.ndarray:
    im = Image.fromarray(img).convert("RGBA")
    d = ImageDraw.Draw(im)
    s = 0.8 if small else 1.0
    ft, fs = font("bold", int(17 * s)), font("regular", int(13 * s))
    _chip(d, (int(10 * s), int(10 * s)), [title, sub], [ft, fs], [INK, MUTED], img=im)
    d = ImageDraw.Draw(im)
    if status:
        fl = font("regular", int(13 * s))
        hh = (fl.size + 3) * len(status) + 11
        _chip(d, (int(10 * s), im.height - hh - int(12 * s) - (52 if caption else 0)), list(status), [fl] * len(status),
              [INK] + [MUTED] * (len(status) - 1), img=im)
        d = ImageDraw.Draw(im)
    if labels:
        fl = font("regular", 11)
        x = 10
        y = int(10 * s) + 50
        for lb in labels:
            b = d.textbbox((0, 0), lb, font=fl)
            w = b[2] - b[0] + 12
            if x + w > im.width - 10:
                x = 10; y += 22
            ov = Image.new("RGBA", im.size, (0, 0, 0, 0)); od = ImageDraw.Draw(ov)
            od.rounded_rectangle([x, y, x + w, y + 18], radius=9, fill=(*CHIP, 170), outline=(*ACCENT, 230), width=1)
            im.alpha_composite(ov); d = ImageDraw.Draw(im)
            d.text((x + 6, y + 2), lb, font=fl, fill=ACCENT)
            x += w + 6
    if caption:
        fc = font("regular", 12)
        ov = Image.new("RGBA", im.size, (0, 0, 0, 0)); od = ImageDraw.Draw(ov)
        od.rectangle([0, im.height - 46, im.width, im.height], fill=(*BG, 215))
        im.alpha_composite(ov); d = ImageDraw.Draw(im)
        words, line, lines = caption.split(), "", []
        for wd in words:
            t = (line + " " + wd).strip()
            if d.textlength(t, font=fc) > im.width - 24:
                lines.append(line); line = wd
            else:
                line = t
        lines.append(line)
        for k, ln in enumerate(lines[:2]):
            d.text((12, im.height - 40 + 16 * k), ln, font=fc, fill=MUTED)
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


def canvas(panels: Sequence[np.ndarray], size, head: int, title: str, caption: str, gap: int = 8) -> np.ndarray:
    W, H = size
    im = Image.new("RGB", (W, H), BG)
    x = gap
    for p in panels:
        im.paste(Image.fromarray(p), (x, head))
        x += p.shape[1] + gap
    d = ImageDraw.Draw(im)
    d.text((gap + 2, 7), title, font=font("bold", 16), fill=INK)
    fc = font("regular", 11)
    cap = caption
    while d.textlength(cap, font=fc) > W - 2 * gap and len(cap) > 20:
        cap = cap[:-2]
    if cap != caption:
        cap = cap.rstrip() + "…"
    d.text((gap + 2, 28), cap, font=fc, fill=MUTED)
    return np.asarray(im)
