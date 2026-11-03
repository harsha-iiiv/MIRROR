"""Shot rendering and layout for the demo films.

Every robot image comes from a recorded rollout drawn by mirror.sim.render
(recorded poses only: nothing is interpolated or re-simulated). This module
adds a camera path (at most one eased move per shot, then a hold), text
layers and the per-frame camera log used by the motion check.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from mirror.sim.render import RolloutRenderer, _cup_beads_in, _load, _Playback, _tilt_deg

FPS = 30
MAX_FRAMES = 0     # smoke tests only: truncate every shot to this many frames
SAMPLE: list = []  # review only: render just these fractions of each shot


def sampled(i, n):
    return not SAMPLE or i in {min(n - 1, int(f * n)) for f in SAMPLE}

# ------------------------------------------------------------------ palette
BG = (244, 242, 238)          # paper
INK = (22, 22, 26)
MUTED = (96, 96, 104)
RULE = (210, 206, 198)        # hairlines
RED = (200, 36, 52)           # violated / tilted (matches the tilted trail)
GREEN = (24, 140, 72)         # satisfied / upright (matches the upright trail)
CHIP = (22, 22, 26)

FONT_DIRS = [os.environ.get("MIRROR_FILM_FONT_DIR", ""),
             "/usr/share/fonts/truetype/roboto/unhinted/RobotoTTF",
             "/Library/Fonts", os.path.expanduser("~/Library/Fonts")]
_FONT_CACHE: dict = {}


def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    key = (weight, size)
    if key not in _FONT_CACHE:
        name = {"bold": "Roboto-Bold.ttf", "medium": "Roboto-Medium.ttf", "regular": "Roboto-Regular.ttf",
                "light": "Roboto-Light.ttf", "mono": "DejaVuSansMono.ttf"}[weight]
        dirs = FONT_DIRS + (["/usr/share/fonts/truetype/dejavu"] if weight == "mono" else [])
        path = next((os.path.join(d, name) for d in dirs if d and os.path.exists(os.path.join(d, name))), None)
        _FONT_CACHE[key] = ImageFont.truetype(path, size) if path else ImageFont.load_default(size)
    return _FONT_CACHE[key]


# ------------------------------------------------------------------- camera
def ease(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return 0.5 - 0.5 * math.cos(math.pi * u)


@dataclass
class Move:
    """Camera for one shot: ``start`` (dict: lookat, distance, azimuth,
    elevation) and optionally ``end`` reached by one sine-eased move between
    output seconds ``t0`` and ``t1``; the camera holds after it lands."""
    start: dict
    end: Optional[dict] = None
    t0: float = 0.0
    t1: float = 0.0

    def at(self, t_out: float) -> dict:
        if not self.end or self.t1 <= self.t0:
            return dict(self.start)
        u = ease((t_out - self.t0) / (self.t1 - self.t0))
        out = {}
        for k in ("distance", "elevation"):
            out[k] = self.start[k] + (self.end[k] - self.start[k]) * u
        da = ((self.end["azimuth"] - self.start["azimuth"] + 180.0) % 360.0) - 180.0
        out["azimuth"] = self.start["azimuth"] + da * u
        a, b = np.asarray(self.start["lookat"], float), np.asarray(self.end["lookat"], float)
        out["lookat"] = tuple(a + (b - a) * u)
        return out


# --------------------------------------------------------------------- shots
@dataclass
class SimShot:
    source: str                          # Rollout JSON path
    t_start: float                       # seconds from the rollout's first frame
    duration: float                      # output seconds
    speed: float = 1.0                   # simulated seconds per output second
    move: Optional[Move] = None
    trail: bool = True
    trail_objects: Optional[Sequence[str]] = None
    freeze_at: Optional[float] = None    # sim time (from start) at which to hold the image
    shadows: bool = True


@dataclass
class FrameInfo:
    i: int
    n: int
    t_out: float
    t_sim: float                         # seconds from the rollout's first frame
    beads: Optional[tuple]
    tilt: Optional[float]                # cup tilt (deg)
    phase: str
    cam: dict


def resolve_lookat(look, rr: RolloutRenderer, visual, frames):
    if isinstance(look, str):
        if look == "slot":
            return tuple(rr._slot_point(visual, frames))
        if look.startswith("obj:"):          # obj:<id>@<t>  position of an object at sim time t
            oid, _, t = look[4:].partition("@")
            pb = _Playback(frames)
            f = pb.at(frames[0].t + float(t or 0))
            return tuple(f.bodies[oid].pos)
        return (0.0, 0.0, 0.04)
    return tuple(look)


def sim_frames(shot: SimShot, width: int, height: int, fps: int = FPS):
    """Yield (image, FrameInfo) for a SimShot."""
    visual, frames, _ = _load(shot.source)
    t0 = frames[0].t
    t_last = frames[-1].t - t0
    rr = RolloutRenderer(visual, frames, camera="overview", width=width, height=height, shadows=shadows_ok(shot))
    mv = shot.move
    if mv is not None:
        for c in (mv.start, mv.end):
            if c is not None:
                c["lookat"] = resolve_lookat(c["lookat"], rr, visual, frames)
    pb = _Playback(frames, shot.trail_objects)
    n = int(round(shot.duration * fps))
    if MAX_FRAMES:
        n = min(n, MAX_FRAMES)
    try:
        for i in range(n):
            t_out = i / fps
            if not sampled(i, n):
                if not SAMPLE or i > max(int(f * n) for f in SAMPLE):
                    continue
                pb.at(t0 + min(t_last, shot.t_start + t_out * shot.speed if shot.freeze_at is None
                                else min(shot.t_start + t_out * shot.speed, shot.freeze_at)))
                continue
            ts = shot.t_start + t_out * shot.speed
            if shot.freeze_at is not None:
                ts = min(ts, shot.freeze_at)
            ts = min(ts, t_last)
            f = pb.at(t0 + ts)
            cam = mv.at(t_out) if mv else None
            if cam:
                rr.cam.lookat[:] = cam["lookat"]
                rr.cam.distance = cam["distance"]
                rr.cam.azimuth = cam["azimuth"]
                rr.cam.elevation = cam["elevation"]
            img = rr.render(f, pb.trails if shot.trail else None)
            cup = f.bodies.get("cup_0")
            yield img, FrameInfo(i=i, n=n, t_out=t_out, t_sim=ts, beads=_cup_beads_in(f, visual),
                                 tilt=_tilt_deg(cup.quat) if cup is not None else None, phase=f.phase,
                                 cam=dict(lookat=list(map(float, rr.cam.lookat)), distance=float(rr.cam.distance),
                                          azimuth=float(rr.cam.azimuth), elevation=float(rr.cam.elevation)))
    finally:
        rr.close()


SHADOWS = True     # False: draw without shadow maps (some macOS OpenGL drivers darken translucent walls)


def shadows_ok(shot):
    return shot.shadows and SHADOWS


# --------------------------------------------------------------------- text
def wrap(text: str, f: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    lines = []
    for para in text.split("\n"):
        words, cur = para.split(" "), ""
        for w in words:
            trial = (cur + " " + w).strip()
            if f.getlength(trial) <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def text_block(draw: ImageDraw.ImageDraw, xy, text, f, fill=INK, max_w=10_000, line_gap=1.18, align="left",
               anchor_w=None) -> int:
    """Draw wrapped text; returns the y below the block."""
    x, y = xy
    lh = int(f.size * line_gap)
    for line in wrap(text, f, max_w):
        lx = x
        if align == "center" and anchor_w:
            lx = x + (anchor_w - f.getlength(line)) / 2
        draw.text((lx, y), line, font=f, fill=fill)
        y += lh
    return y


def chip(draw: ImageDraw.ImageDraw, xy, text, f, fg=(255, 255, 255), bg=CHIP, pad=(14, 8), alpha=235):
    x, y = xy
    w = f.getlength(text) + 2 * pad[0]
    h = f.size + 2 * pad[1] + 2
    draw.rounded_rectangle((x, y, x + w, y + h), radius=h // 2, fill=bg + (alpha,))
    draw.text((x + pad[0], y + pad[1] - 1), text, font=f, fill=fg)
    return x + w, y + h


def fade(i: int, start: int, frames: int = 6) -> float:
    return 0.0 if i < start else min(1.0, (i - start + 1) / frames)


# ------------------------------------------------------------------ writing
class Writer:
    """Pipes RGB frames to ffmpeg (H.264, yuv420p, CRF 14 intermediates)."""

    def __init__(self, path, width, height, fps=FPS, crf=14):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.w, self.h = width, height
        self.p = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
             "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(self.path)], stdin=subprocess.PIPE)
        self.n = 0

    def add(self, img: np.ndarray | Image.Image):
        a = np.asarray(img.convert("RGB") if isinstance(img, Image.Image) else img, dtype=np.uint8)
        assert a.shape == (self.h, self.w, 3), a.shape
        self.p.stdin.write(a.tobytes())
        self.n += 1

    def close(self):
        self.p.stdin.close()
        if self.p.wait(timeout=600) != 0:
            raise RuntimeError(f"ffmpeg failed for {self.path}")
        return self.path


def write_camlog(path, rows):
    Path(path).write_text("\n".join(json.dumps(r) for r in rows) + "\n")
