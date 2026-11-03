"""Classical tracker on tiny SYNTHETIC clips (no model calls)."""
import numpy as np

from mirror.capture import media as M
from mirror.capture import synthetic as S
from mirror.capture import track as TR


def _clip(tmp_path, color, *, hide=None, n=30, name="t.mp4"):
    import cv2
    frames = []
    rng = np.random.default_rng(0)
    bg = np.clip(rng.normal(120, 6, (180, 320, 3)), 0, 255).astype(np.uint8)
    bg[..., 0] = np.clip(bg[..., 0].astype(int) + 30, 0, 255)   # brownish table
    for k in range(n):
        f = bg.copy()
        x = 60 + 6 * k
        cv2.rectangle(f, (x, 70), (x + 30, 110), color, -1)
        if hide and hide[0] <= k < hide[1]:
            cv2.rectangle(f, (x - 10, 40), (x + 40, 120), (60, 60, 60), -1)   # a dark occluder covers it
        frames.append(f)
    return M.probe(S.write_video(frames, tmp_path / name, fps=15.0))


def test_tracks_coloured_object(tmp_path, ffmpeg):
    m = _clip(tmp_path, (200, 40, 40))
    ts = TR.classical_track(m, [TR.Prompt(camera="c", label="obj", frame=0, box=(58, 68, 34, 44))], camera="c")
    obs = ts.tracks["obj"]
    assert all(o.status == "tracked" for o in obs)
    assert abs(obs[-1].cx - (60 + 6 * 29 + 15)) < 4
    assert ts.source == "classical" and ts.model is None


def test_tracks_white_object_by_saturation_value(tmp_path, ffmpeg):
    m = _clip(tmp_path, (245, 245, 245), name="w.mp4")
    ts = TR.classical_track(m, [TR.Prompt(camera="c", label="obj", frame=0, box=(58, 68, 34, 44))], camera="c")
    obs = ts.tracks["obj"]
    assert sum(o.seen for o in obs) >= 28
    assert abs(obs[-1].cx - (60 + 6 * 29 + 15)) < 6


def test_occlusion_loses_then_reacquires_with_identity_unconfirmed(tmp_path, ffmpeg):
    m = _clip(tmp_path, (200, 40, 40), hide=(10, 16), name="o.mp4")
    ts = TR.classical_track(m, [TR.Prompt(camera="c", label="obj", frame=0, box=(58, 68, 34, 44))], camera="c")
    obs = ts.tracks["obj"]
    assert any(o.status == "lost" for o in obs[10:16])
    later = [o for o in obs[17:] if o.seen]
    assert later and all(o.status == "reacquired" and not o.identity_confirmed for o in later)
    TR.confirm_identity(ts, "obj", 16)
    assert all(o.identity_confirmed for o in ts.tracks["obj"][17:] if o.seen)
