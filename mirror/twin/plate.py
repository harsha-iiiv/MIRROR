"""Real pixels for the twin: streamed video frames and an empty-table "clean plate".

The clean plate is the table before the person touched anything, with each object that later moved
replaced by the same pixels at the end of the take (when that spot is empty). The phone does not
move, so the two frames line up; a per-channel gain matches exposure between them. Start and end
frames are chosen automatically as the pair that agrees best outside the objects (no hands, no
calibration sheets), inside windows set by the pick/place events.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, Sequence

import numpy as np


class FrameStream:
    """Decode a clip once, in order, at a fixed output size (display orientation). ``at(t)`` returns
    the frame nearest to time ``t``; requested times must not decrease. Never holds more than two
    frames."""

    def __init__(self, path, size: tuple[int, int], start_s: float = 0.0, fps: Optional[float] = None,
                 pts: Optional[Sequence[float]] = None):
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise RuntimeError("ffmpeg is required")
        self.w, self.h = int(size[0]), int(size[1])
        self.n = self.w * self.h * 3
        ss = max(0.0, float(start_s) - 0.5)
        cmd = [ffmpeg, "-v", "error", "-ss", f"{ss:.3f}", "-i", str(path), "-map", "0:v:0", "-fps_mode", "passthrough",
               "-vf", f"scale={self.w}:{self.h}:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-copyts", "-"]
        # -copyts keeps absolute timestamps; we recover per-frame times from the clip's pts list
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        self.pts = list(pts) if pts is not None else None
        self.fps = fps
        self._prev = None      # (t, frame)
        self._next = None
        self._k = None
        self._ss = ss

    def _read(self):
        buf = self.proc.stdout.read(self.n)
        if len(buf) < self.n:
            return None
        return np.frombuffer(buf, np.uint8).reshape(self.h, self.w, 3)

    def _time_of(self, k: int) -> float:
        if self.pts is not None:
            # first decoded frame is the first pts >= ss (ffmpeg seeks to the frame at/after ss)
            if self._k is None:
                pass
            return self.pts[k]
        return self._ss + k / float(self.fps or 30.0)

    def at(self, t: float) -> np.ndarray:
        if self._k is None:
            if self.pts is not None:
                k0 = int(np.searchsorted(np.asarray(self.pts), self._ss - 1e-6))
            else:
                k0 = 0
            self._k = k0
            f = self._read()
            if f is None:
                raise RuntimeError("no frames")
            self._prev = (self._time_of(self._k), f)
            nf = self._read()
            self._next = None if nf is None else (self._time_of(self._k + 1), nf)
        while self._next is not None and self._next[0] <= t:
            self._k += 1
            self._prev = self._next
            nf = self._read()
            self._next = None if nf is None else (self._time_of(self._k + 1), nf)
        if self._next is not None and abs(self._next[0] - t) < abs(self._prev[0] - t):
            return self._next[1]
        return self._prev[1]

    def close(self):
        try:
            self.proc.stdout.close()
            self.proc.kill()
            self.proc.wait(timeout=30)
        except Exception:
            pass


def read_at(path, t: float, size: tuple[int, int]) -> np.ndarray:
    ffmpeg = shutil.which("ffmpeg")
    out = subprocess.run([ffmpeg, "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(path), "-frames:v", "1",
                          "-vf", f"scale={size[0]}:{size[1]}:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, timeout=120, stdin=subprocess.DEVNULL).stdout
    return np.frombuffer(out[: size[0] * size[1] * 3], np.uint8).reshape(size[1], size[0], 3).copy()


def low_res_samples(path, t0: float, t1: float, step: float, size=(135, 240)) -> list[tuple[float, np.ndarray]]:
    """Grey frames every ``step`` s between t0 and t1 (small, for frame selection)."""
    ffmpeg = shutil.which("ffmpeg")
    w, h = size
    out = subprocess.run([ffmpeg, "-v", "error", "-ss", f"{max(0.0, t0):.3f}", "-t", f"{max(step, t1 - t0):.3f}",
                          "-i", str(path), "-vf", f"fps={1.0 / step:.4f},scale={w}:{h}:flags=area",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, timeout=600, stdin=subprocess.DEVNULL).stdout
    n = len(out) // (w * h)
    return [(max(0.0, t0) + i * step, np.frombuffer(out[i * w * h:(i + 1) * w * h], np.uint8).reshape(h, w))
            for i in range(n)]


@dataclass
class Plate:
    image: np.ndarray                     # uint8 RGB, output size
    start_s: float
    end_s: float
    filled: dict = field(default_factory=dict)       # label -> pixels replaced
    gain: tuple = (1.0, 1.0, 1.0)
    agreement: float = 0.0                # median abs grey difference outside objects (0-255) of the chosen pair
    notes: list = field(default_factory=list)


def choose_pair(path, start_window, end_window, avoid_masks_small: np.ndarray, step: float = 0.1):
    """Start/end times whose frames agree best outside ``avoid_masks_small`` (bool, 240x135)."""
    S = low_res_samples(path, start_window[0], start_window[1], step)
    E = low_res_samples(path, end_window[0], end_window[1], step)
    if not S or not E:
        raise RuntimeError("no frames in the plate windows")
    keep = ~avoid_masks_small
    best = None
    for ts, fs in S:
        for te, fe in E:
            d = np.abs(fs.astype(np.int16) - fe.astype(np.int16))[keep]
            score = float(np.median(d)) + 0.2 * float(np.percentile(d, 95))
            if best is None or score < best[0]:
                best = (score, ts, te, float(np.median(d)))
    return best[1], best[2], best[3]


def clean_plate(path, start_s: float, end_s: float, masks: dict[str, np.ndarray], size: tuple[int, int],
                dilate_px: int = 6, feather_px: float = 3.0, ring_px: int = 25) -> Plate:
    """Start frame with each mask region taken from the end frame (exposure-matched, feathered)."""
    import cv2
    A = read_at(path, start_s, size).astype(np.float32)
    B = read_at(path, end_s, size).astype(np.float32)
    union = np.zeros(A.shape[:2], bool)
    for m in masks.values():
        union |= m
    k = np.ones((2 * dilate_px + 1, 2 * dilate_px + 1), np.uint8)
    fill = cv2.dilate(union.astype(np.uint8), k) > 0
    ring = (cv2.dilate(fill.astype(np.uint8), np.ones((2 * ring_px + 1,) * 2, np.uint8)) > 0) & ~fill
    gain = []
    for c in range(3):
        a, b = A[..., c][ring], B[..., c][ring]
        g = float(np.median(a) / max(1.0, np.median(b))) if ring.any() else 1.0
        gain.append(min(1.3, max(0.77, g)))
    Bg = np.clip(B * np.array(gain, np.float32), 0, 255)
    alpha = cv2.GaussianBlur(fill.astype(np.float32), (0, 0), feather_px)
    alpha = np.maximum(alpha, fill.astype(np.float32) * 0.0)
    alpha = np.clip(alpha * 1.6, 0, 1)
    out = A * (1 - alpha[..., None]) + Bg * alpha[..., None]
    filled = {lbl: int((cv2.dilate(m.astype(np.uint8), k) > 0).sum()) for lbl, m in masks.items()}
    return Plate(image=np.clip(out + 0.5, 0, 255).astype(np.uint8), start_s=start_s, end_s=end_s, filled=filled,
                 gain=tuple(round(g, 3) for g in gain))


def mean_colour(img: np.ndarray, mask: np.ndarray, erode_px: int = 3) -> Optional[tuple[float, float, float]]:
    """Robust colour of an object (median RGB 0-1 inside its eroded mask)."""
    import cv2
    m = cv2.erode(mask.astype(np.uint8), np.ones((2 * erode_px + 1,) * 2, np.uint8)) > 0
    if m.sum() < 20:
        m = mask
    if m.sum() < 5:
        return None
    px = img[m].astype(np.float32) / 255.0
    if len(px) > 4000:
        px = px[np.random.default_rng(0).choice(len(px), 4000, replace=False)]
    # the object's dominant colour: the largest of three colour clusters (not its print, label,
    # contents or shading)
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 30, 1e-3)
    k = 3 if len(px) >= 30 else 1
    _, lab, cen = cv2.kmeans(px.astype(np.float32), k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    big = int(np.argmax(np.bincount(lab.ravel(), minlength=k)))
    return tuple(float(x) for x in np.median(px[lab.ravel() == big], 0))
