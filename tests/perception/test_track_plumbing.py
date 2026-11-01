"""track_videos plumbing with a stand-in client (no network): grounding boxes
in 0-1000 crop coordinates map back to frame pixels, box prompts are built,
SAM masks land under the right labels, static parts are held."""
import json

import numpy as np
import pytest

from mirror.meta.sam import SamBox, SamFrame, SamObject, SamResult
from mirror.perception.track import DEFAULT_OBJECTS, track_videos


class _Res:
    def __init__(self, data):
        self._d = data
        self.model, self.latency_s, self.usage, self.from_cassette = "muse-spark-1.3", 0.0, {}, True

    def json(self):
        return self._d


class FakeClient:
    """Answers grounding with true boxes (normalised to the image sent) and
    SAM with rectangular masks inside the prompted boxes."""

    def __init__(self, truth, size):
        self.truth, self.size, self.calls = truth, size, []

    def responses(self, model, parts, schema=None, schema_name=None, **kw):
        from PIL import Image
        self.calls.append(schema_name)
        imgs = [p for p in parts if p.get("type") == "input_image"]
        if schema_name == "boxes":
            return _Res({"boxes": [{"image_index": k, "found": True,
                                    "bbox": {"x_min": 300, "y_min": 300, "x_max": 700, "y_max": 700}}
                                   for k in range(len(imgs))]})
        W, H = self.size
        out = []
        for lab, name in DEFAULT_OBJECTS:
            if lab in self.truth:
                x0, y0, x1, y1 = self.truth[lab]
                out.append({"object_name": name, "bbox": {"x_min": int(x0 / W * 1000), "y_min": int(y0 / H * 1000),
                                                          "x_max": int(x1 / W * 1000), "y_max": int(y1 / H * 1000)}})
        d = {"results": out}
        if schema_name == "grounding" and "small_yellow_balls_in_cup" in json.dumps(schema):
            d["small_yellow_balls_in_cup"] = {"points": [], "count": 7}
        return _Res(d)

    def sam_segment(self, media, prompt, video=None, **kw):
        import re
        W, H = self.size
        objs = []
        for oid, x1, y1, x2, y2 in re.findall(r"(\d+)<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+)", prompt):
            x1, y1, x2, y2 = map(int, (x1, y1, x2, y2))
            objs.append((int(oid), x1, y1, x2 + 1, y2 + 1))
        n = 3 if video else 1
        frames = [SamFrame(index=i, objects=[SamObject(object_id=o, frame=i, box=SamBox(a, b, c, d), confidence=0.9,
                                                       mask=np.ones((d - b, c - a), np.uint8)) for o, a, b, c, d in objs])
                  for i in range(n)]
        return SamResult(prompt=prompt, media="video" if video else "image", width=W, height=H, frames=frames,
                         raw_text="")


def test_track_videos_with_stand_in_client(tmp_path):
    import imageio.v2 as imageio
    W, H = 320, 180
    vid = tmp_path / "top.mp4"
    with imageio.get_writer(vid, fps=15, codec="libx264", macro_block_size=2) as w:
        for _ in range(3):
            w.append_data(np.full((H, W, 3), 120, np.uint8))
    truth = {"cup_0": (100, 100, 120, 125), "block_0": (60, 40, 72, 52), "table": (20, 10, 300, 170),
             "tray_0": (200, 30, 260, 80), "gripper": (90, 60, 110, 95)}
    c = FakeClient(truth, (W, H))
    t = track_videos({"top": vid}, fps=15.0, n_frames=3, client=c, work=tmp_path)
    assert c.calls.count("grounding") == 2 and c.calls.count("boxes") == 1
    assert t.extra["ball_count"] == 7
    assert {"cup_0", "block_0", "gripper"} <= set(t.masks["top"]) and "tray_0" in t.static
    m = t.get("top", "tray_0", 2)          # static: frame 0 mask held
    assert m is not None and m[50, 230] and not m[5, 5]
    assert t.get("top", "cup_0", 2) is not None
    assert any(l.startswith("table") for l in t.masks["top"])
