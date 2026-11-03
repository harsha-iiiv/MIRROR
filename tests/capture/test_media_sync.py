"""Original timestamps and two-camera synchronisation (SYNTHETIC clips and events)."""
import numpy as np
import pytest

from mirror.capture import media as M
from mirror.capture import synthetic as S
from mirror.capture import sync as Y
from mirror.capture.target import DEFAULT_TARGET as SPEC


def test_variable_frame_rate_timestamps_preserved(tmp_path, ffmpeg):
    frames = [np.full((48, 64, 3), (10 * k) % 255, np.uint8) for k in range(24)]
    path = S.write_video(frames, tmp_path / "vfr.mp4", fps=24.0, jitter=0.008, start=3.25)
    m = M.probe(path)
    want = S.frame_times(24, 24.0, jitter=0.008, start=3.25)
    assert m.n_frames == 24
    assert np.max(np.abs(np.array(m.pts_s) - want)) < 2e-4
    assert m.pts_s[0] == pytest.approx(3.25, abs=1e-4)     # original clip time, not re-based to zero
    assert m.variable_frame_rate
    got = [(i, int(f[0, 0, 0])) for i, _, f in M.iter_frames(m)]
    assert [i for i, _ in got] == list(range(24))
    assert all(abs(v - (10 * i) % 255) <= 3 for i, v in got)
    assert m.frame_at(want[7] + 0.001) == 7


def test_undecodable_file_is_refused(tmp_path, ffmpeg):
    p = tmp_path / "not_video.mp4"
    p.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"garbage" * 100)
    with pytest.raises(M.ClipError):
        M.probe(p)


def _card_clip(tmp_path, windows, *, fps=15.0, start=0.0, n=30, name="c.mp4"):
    cam = S.SynthCamera.make("top", 640, 360, 60, (0.0, -0.25, 0.5), (0.0, 0.0, 0.0))
    card = S.sync_card_prim(SPEC, (0.0, 0.0, 0.12), (0.0, -0.4, 1.0),
                            visible=lambda t: any(a <= t < b for a, b in windows))
    times = S.frame_times(n, fps)
    bg = S.table_background(cam)
    frames = [S.render(cam, [card], t, background=bg, seed=k) for k, t in enumerate(times)]
    return M.probe(S.write_video(frames, tmp_path / name, fps=fps, start=start))


def test_card_events_on_synthetic_clip(tmp_path, ffmpeg):
    m = _card_clip(tmp_path, [(0.5, 1.2)])
    ev = Y.card_events(m, SPEC)
    assert [e.direction for e in ev] == ["up", "down"]
    # the true change lies inside the bracket between the two frames (to timestamp precision)
    assert abs(ev[0].t - 0.5) <= ev[0].uncertainty_s + 1e-3
    assert abs(ev[1].t - 1.2) <= ev[1].uncertainty_s + 1e-3


def test_light_events_on_synthetic_clip(tmp_path, ffmpeg):
    times = S.frame_times(20, 10.0)
    frames = [np.full((60, 80, 3), 40 if 0.75 <= t < 1.25 else 160, np.uint8) for t in times]
    m = M.probe(S.write_video(frames, tmp_path / "l.mp4", fps=10.0))
    ev = Y.light_events(m)
    assert [e.direction for e in ev] == ["down", "up"]
    assert abs(ev[0].t - 0.75) <= 0.05 + 1e-6 and abs(ev[1].t - 1.25) <= 0.05 + 1e-6


def ev(kind, d, t, u=0.02):
    return Y.SyncEvent(kind=kind, direction=d, t=t, uncertainty_s=u)


def test_align_two_matched_events():
    top = [ev("card", "down", 1.10), ev("card", "down", 5.20)]
    side = [ev("card", "down", 1.47), ev("card", "down", 5.58)]
    r = Y.align(top, side)
    assert r.ok
    assert r.offset_s == pytest.approx(0.375, abs=1e-3)
    assert r.residual_s == pytest.approx(0.0071, abs=1e-3)
    assert float(r.to_top(r.to_side(2.0))) == pytest.approx(2.0)


def test_separate_takes_refused():
    """Two performances: the spacing between start and end events differs."""
    top = [ev("card", "down", 1.10), ev("card", "down", 5.20)]
    side = [ev("card", "down", 0.90), ev("card", "down", 6.40)]
    r = Y.align(top, side)
    assert not r.ok
    assert any("separate" in x or "correspond" in x for x in r.reasons)


def test_single_event_refused():
    r = Y.align([ev("card", "down", 1.1)], [ev("card", "down", 1.5)])
    assert not r.ok and any("fewer than two" in x for x in r.reasons)


def test_no_events_refused():
    r = Y.align([], [ev("card", "down", 1.5)])
    assert not r.ok and "refusing" in r.reasons[0]


def test_event_missed_by_one_camera_refused():
    top = [ev("card", "down", 1.1), ev("card", "down", 3.0), ev("card", "down", 5.2)]
    side = [ev("card", "down", 1.5), ev("card", "down", 5.6)]
    r = Y.align(top, side)
    assert not r.ok and any("no partner" in x for x in r.reasons)


def test_kinds_and_directions_must_agree():
    top = [ev("card", "up", 1.0), ev("card", "down", 2.0)]
    side = [ev("light", "up", 1.4), ev("light", "down", 2.4)]
    assert not Y.align(top, side).ok


def test_clock_rate_mismatch_refused():
    top = [ev("card", "down", 1.0), ev("card", "down", 31.0), ev("card", "down", 61.0)]
    side = [ev("card", "down", 1.5), ev("card", "down", 31.65), ev("card", "down", 61.8)]   # 5 ms/s: edited or slowed
    assert not Y.align(top, side).ok


def test_small_clock_drift_estimated():
    top = [ev("card", "down", 1.0, 0.005), ev("card", "down", 31.0, 0.005), ev("card", "down", 61.0, 0.005)]
    side = [ev("card", "down", 1.5, 0.005), ev("card", "down", 31.53, 0.005), ev("card", "down", 61.56, 0.005)]   # 1 ms/s
    r = Y.align(top, side)
    assert r.ok and r.drift_estimated
    assert r.scale == pytest.approx(1.001, abs=1e-5)


def test_residual_too_large_refused(tmp_path, ffmpeg):
    top_m = _card_clip(tmp_path, [(0.5, 1.2)], n=20, name="a.mp4")
    top = [ev("card", "down", 1.0, 0.01), ev("card", "down", 5.0, 0.01)]
    side = [ev("card", "down", 1.4, 0.01), ev("card", "down", 5.5, 0.01)]    # 100 ms disagreement
    r = Y.align(top, side, top_meta=top_m)
    assert not r.ok and any("uncertainty" in x for x in r.reasons)
