
"""first_board prefers a frame where the board is at rest (real footage: the first full view is often mid-placement)."""
import numpy as np
from mirror.capture import calibrate as C, session as SE, media as M


class _D:
    def __init__(self, i, n, shift):
        self.frame_index, self.pts_s, self.n = i, i / 30, n
        self.corner_ids = list(range(n))
        self.corners_px = [[10.0 * k + shift, 5.0 * k] for k in range(n)]


def test_first_board_prefers_steady_frame(monkeypatch):
    # frames 0-4: board sliding (24 corners, 5 px per frame); frames 5-14: at rest (24 corners)
    shifts = [5.0 * i for i in range(5)] + [25.0] * 10
    frames = [(i, i / 30, None) for i in range(15)]
    monkeypatch.setattr(M, "iter_frames", lambda meta, gray=True, **k: iter(frames))
    monkeypatch.setattr(C, "detect", lambda f, spec, frame_index, pts_s: _D(frame_index, 24, shifts[frame_index]))

    class _Meta:
        pts_s = [i / 30 for i in range(15)]
    d = SE.first_board(_Meta(), None, search_s=1.0)
    assert d.frame_index >= 6          # inside the steady run, not frame 0 (first full but moving view)


def test_first_board_falls_back_when_never_steady(monkeypatch):
    frames = [(i, i / 30, None) for i in range(6)]
    monkeypatch.setattr(M, "iter_frames", lambda meta, gray=True, **k: iter(frames))
    monkeypatch.setattr(C, "detect", lambda f, spec, frame_index, pts_s: _D(frame_index, 12 + frame_index, 9.0 * frame_index))

    class _Meta:
        pts_s = [i / 30 for i in range(6)]
    d = SE.first_board(_Meta(), None, search_s=1.0)
    assert d.frame_index == 5          # most corners
