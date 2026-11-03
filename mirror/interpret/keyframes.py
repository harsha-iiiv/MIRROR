"""Optional rendered keyframes of a demonstration for the proposal prompt.

These are images rendered by the simulator from the recorded trace (the same
display path as the demonstration videos), not real video and not camera
footage. They are off by default. The measurements in the demonstration
summary remain the evidence the proposal is checked against; the images only
give the model a view of the scene.

Rendering needs an offscreen OpenGL backend (MUJOCO_GL=osmesa or egl).
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from mirror.contracts import DemoTrace


@dataclass(frozen=True)
class Keyframe:
    t: float
    label: str
    png: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.png).hexdigest()

    def data_url(self) -> str:
        return "data:image/png;base64," + base64.b64encode(self.png).decode("ascii")


def select_keyframes(trace: DemoTrace, n: int = 4) -> list[tuple[float, str]]:
    """Deterministic choice: the first frame, the middle of each carry in pick
    order, and the last frame, trimmed to ``n`` (start and end kept)."""
    if n < 1 or not trace.frames:
        return []
    kinds = {o.id: o.kind for o in trace.scene.objects}
    carries = []
    open_grasp: dict[str, float] = {}
    for e in sorted(trace.events, key=lambda e: e.t):
        if e.type == "grasp" and e.object_id:
            open_grasp[e.object_id] = e.t
        elif e.type == "release" and e.object_id in open_grasp:
            a = open_grasp.pop(e.object_id)
            carries.append(((a + e.t) / 2.0, f"carrying the {kinds.get(e.object_id, e.object_id)}"))
    t0, t1 = trace.frames[0].t, trace.frames[-1].t
    picks = [(t0, "start")] + carries + [(t1, "end")]
    if len(picks) > n:
        middle = carries[: max(0, n - 2)]
        picks = ([(t0, "start")] + middle + [(t1, "end")])[:n] if n >= 2 else [(t0, "start")]
    out = []
    for t, label in picks:
        f = min(trace.frames, key=lambda fr: abs(fr.t - t))
        out.append((round(f.t, 3), label))
    return out


def _png(rgb) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_keyframes(trace: DemoTrace, n: int = 4, width: int = 480, height: int = 270,
                     camera: str = "overview") -> list[Keyframe]:
    import os
    import sys
    if "mujoco" not in sys.modules:  # the backend must be chosen before mujoco is imported
        from mirror import glbackend
        glbackend.apply(glbackend.offscreen_backend())
    elif not os.environ.get("MUJOCO_GL") and sys.platform != "darwin":
        raise RuntimeError("mujoco was imported without an offscreen backend; set MUJOCO_GL=osmesa "
                           "(or egl, or cgl on macOS) in the environment before starting")
    from mirror.contracts import VisualScene
    from mirror.sim.env import visual_scene
    from mirror.sim.model import build_model
    from mirror.sim import render as rnd
    model, index = build_model(trace.scene)
    vis = visual_scene(model, index)
    vis = VisualScene(bodies=[b for b in vis.bodies if "/bead" not in b.name])
    frames = rnd.demo_frames(trace)
    if hasattr(rnd, "_with_fingers"):
        frames = [rnd._with_fingers(f) for f in frames]
    by_t = {round(f.t, 3): f for f in frames}
    renderer = rnd.RolloutRenderer(vis, frames, camera=camera, width=width, height=height)
    try:
        out = []
        for t, label in select_keyframes(trace, n):
            f = by_t.get(t) or min(frames, key=lambda fr: abs(fr.t - t))
            out.append(Keyframe(t=t, label=label, png=_png(renderer.render(f))))
        return out
    finally:
        renderer.close()


def save_keyframes(frames: list[Keyframe], directory: Path) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    index = []
    for i, k in enumerate(frames):
        name = f"{i:02d}.png"
        (directory / name).write_bytes(k.png)
        index.append({"file": name, "t": k.t, "label": k.label, "sha256": k.sha256})
    (directory / "index.json").write_text(json.dumps(index, indent=1) + "\n")
    return directory


def load_keyframes(directory: Path) -> list[Keyframe]:
    directory = Path(directory)
    index = json.loads((directory / "index.json").read_text())
    out = []
    for e in index:
        png = (directory / e["file"]).read_bytes()
        if hashlib.sha256(png).hexdigest() != e["sha256"]:
            raise ValueError(f"keyframe {e['file']} does not match its recorded hash")
        out.append(Keyframe(t=e["t"], label=e["label"], png=png))
    return out


def image_parts(frames: list[Keyframe]) -> list[dict]:
    parts: list[dict] = []
    for k in frames:
        parts.append({"type": "text", "text": f"Rendered simulator frame at t = {k.t:g} s ({k.label})."})
        parts.append({"type": "image_url", "image_url": {"url": k.data_url()}})
    return parts
