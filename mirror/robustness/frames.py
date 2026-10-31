"""Ground-truth frames for the robustness study.

A frame is an RGB image rendered by the simulator's own offscreen renderer
plus, for each measured object, the mask of its visible pixels from a
segmentation render of the same camera and pose. Frames come from real
executed rollouts on development layouts (objects at rest, the cup being
carried, the packed tray), so the gripper occludes objects as it would in
a demonstration video.

Walls are drawn opaque here (the studio draws them translucent), so every
pixel belongs to exactly one surface.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

os.environ.setdefault("MUJOCO_GL", "osmesa")
if os.environ["MUJOCO_GL"] == "osmesa":
    os.environ.setdefault("PYOPENGL_PLATFORM", "osmesa")

from scipy import ndimage  # noqa: E402

WIDTH, HEIGHT = 1024, 576
# Camera presets for this study: a close top view and an oblique view along
# the wall, so objects on both sides of it stay visible.
CAMERA_PRESETS = {
    "top": dict(lookat=(0.0, 0.0, 0.0), distance=1.02, azimuth=90.0, elevation=-89.0),
    "oblique": dict(lookat=(0.0, 0.0, 0.03), distance=0.98, azimuth=-90.0, elevation=-52.0),
}
CAMERAS = tuple(CAMERA_PRESETS)
MOMENTS = ("start", "carry", "end")
CLASSES = ("cup", "block", "marker", "gripper")


@dataclass
class GTFrame:
    """One rendered frame with per-class visible-pixel masks."""
    frame_id: str
    rgb: np.ndarray                          # HxWx3 uint8
    masks: dict[str, np.ndarray]             # class -> HxW bool (visible pixels)
    meta: dict = field(default_factory=dict)

    @property
    def size(self) -> tuple[int, int]:
        return int(self.rgb.shape[1]), int(self.rgb.shape[0])

    def png(self) -> bytes:
        return encode_png(self.rgb)

    def save(self, directory: Path | str) -> Path:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{self.frame_id}.png").write_bytes(self.png())
        np.savez_compressed(d / f"{self.frame_id}_masks.npz", **{k: v for k, v in self.masks.items()})
        (d / f"{self.frame_id}.json").write_text(json.dumps(self.meta, indent=1, sort_keys=True))
        return d / f"{self.frame_id}.png"

    @classmethod
    def load(cls, directory: Path | str, frame_id: str) -> "GTFrame":
        d = Path(directory)
        rgb = decode_png((d / f"{frame_id}.png").read_bytes())
        with np.load(d / f"{frame_id}_masks.npz") as z:
            masks = {k: z[k].astype(bool) for k in z.files}
        meta = json.loads((d / f"{frame_id}.json").read_text())
        return cls(frame_id, rgb, masks, meta)


def encode_png(rgb: np.ndarray) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(np.asarray(rgb, dtype=np.uint8)).save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def decode_png(data: bytes) -> np.ndarray:
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        return np.asarray(im.convert("RGB")).copy()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _class_of(body: str) -> Optional[str]:
    if body.startswith("gripper"):
        return "gripper"
    for k in ("cup", "block", "marker"):
        if body.startswith(k + "_") and "/bead" not in body:
            return k
    return None


def render_masks(rr, frame) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """(rgb, masks) for one RolloutFrame with a RolloutRenderer ``rr``."""
    import mujoco
    rgb = rr.render(frame)
    r = rr.renderer
    r.enable_segmentation_rendering()
    try:
        seg = r.render()
    finally:
        r.disable_segmentation_rendering()
    m = rr.model
    geom_class = np.full(m.ngeom, -1, dtype=int)
    bead_owner = np.full(m.ngeom, -1, dtype=int)
    for g in range(m.ngeom):
        body = m.body(m.geom_bodyid[g]).name
        c = _class_of(body)
        if c is not None:
            geom_class[g] = CLASSES.index(c)
        elif "/bead" in body:
            bead_owner[g] = CLASSES.index("cup")
    is_geom = seg[:, :, 1] == int(mujoco.mjtObj.mjOBJ_GEOM)
    ids = np.where(is_geom, seg[:, :, 0], -1)
    valid = (ids >= 0) & (ids < m.ngeom)
    cls = np.full(ids.shape, -1, dtype=int)
    cls[valid] = geom_class[ids[valid]]
    beads = np.zeros(ids.shape, bool)
    beads[valid] = bead_owner[ids[valid]] >= 0
    masks = {}
    for i, c in enumerate(CLASSES):
        mk = cls == i
        if c == "cup":
            # Beads seen through the open top belong to the cup's silhouette.
            mk = ndimage.binary_fill_holes(mk | (beads & ndimage.binary_fill_holes(mk)))
        masks[c] = mk
    return rgb, masks


def _pick_moments(frames) -> dict[str, int]:
    idx = {"start": 0, "end": len(frames) - 1}
    carry = [i for i, f in enumerate(frames) if (f.phase or "").startswith("transport:cup")]
    if carry:
        idx["carry"] = carry[len(carry) // 2]
    return idx


def rollout_frames(seed: int, split: str = "dev", *, spec=None, frame_hz: float = 10.0, family: str = "mixed"):
    """Execute the reference task on ``sample_layout(seed, split, family)`` and
    return (scene, visual scene, RolloutFrames, execution status). Eval-split
    layouts are recorded in the evaluation ledger by ``sample_layout``."""
    from mirror.evaluate.specs import reference_specs
    from mirror.plan import plan
    from mirror.scenes import sample_layout
    from mirror.sim import SimEnv
    from mirror.sim.env import visual_scene
    from mirror.sim.model import build_model
    if split not in ("dev", "eval"):
        raise ValueError(f"unsupported split {split!r}")
    scene = sample_layout(seed, split, family)
    spec = spec or reference_specs()[0]
    p = plan(spec, scene)
    if p.status != "ok":
        raise RuntimeError(f"seed {seed}: plan {p.status}: {p.reason}")
    res = SimEnv(scene).execute(p, frame_hz=frame_hz)
    model, index = build_model(scene)
    return scene, visual_scene(model, index), res.frames, res.status


def _render(seed: int, split: str, family: str, cameras, moments, width, height, out_dir) -> list[GTFrame]:
    from mirror.sim.render import RolloutRenderer
    scene, vis, frames, status = rollout_frames(int(seed), split, family=family)
    picks = _pick_moments(frames)
    out = []
    prefix = "dev" if split == "dev" else split
    for cam in cameras:
        rr = RolloutRenderer(vis, frames, camera=dict(CAMERA_PRESETS[cam]), width=width, height=height,
                             wall_alpha=1.0, outline=False)
        try:
            for mom in moments:
                if mom not in picks:
                    continue
                f = frames[picks[mom]]
                rgb, masks = render_masks(rr, f)
                fid = f"{prefix}{seed}_{cam}_{mom}"
                meta = dict(frame_id=fid, seed=int(seed), split=split, family=family, scene_id=scene.scene_id,
                            camera=cam, moment=mom, t=float(f.t), phase=f.phase,
                            execution_status=status, width=width, height=height,
                            pixels={k: int(v.sum()) for k, v in masks.items()},
                            renderer="mujoco offscreen (opaque walls, shadows)")
                g = GTFrame(fid, rgb, masks, meta)
                g.meta["png_sha256"] = sha256(g.png())
                if out_dir is not None:
                    g.save(out_dir)
                out.append(g)
        finally:
            rr.close()
    return out


MIN_VISIBLE_PIXELS = 120
MEASURED = ("cup", "block", "marker")


class FramesNotVisible(RuntimeError):
    pass


def frames_for_seeds(seeds: Sequence[int], out_dir: Path | str, *, split: str = "eval", family: str = "mixed",
                     cameras: Sequence[str] = CAMERAS, moment: str = "carry", width: int = WIDTH,
                     height: int = HEIGHT, min_pixels: int = MIN_VISIBLE_PIXELS,
                     require_visible: bool = True) -> dict:
    """Render the ``moment`` frame (default: the cup mid-carry) from every
    camera for each seed, with ground-truth masks, into ``out_dir``.

    Deterministic: the same seeds give byte-identical PNGs (recorded as
    sha256 in the manifest). Every measured object (cup, block, marker) must
    have at least ``min_pixels`` visible pixels in every frame; the manifest
    ``frames_manifest.json`` is written first, then, with ``require_visible``,
    ``FramesNotVisible`` is raised naming the failing frames.
    The simulator is used read-only (its renderer and a segmentation render)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    frames: list[GTFrame] = []
    for seed in seeds:
        rendered = _render(int(seed), split, family, cameras, (moment,), width, height, out)
        if len(rendered) != len(cameras):
            raise RuntimeError(f"seed {seed}: no '{moment}' moment in the rollout")
        frames += rendered
    entries, failures = [], []
    for g in frames:
        vis = {k: int(g.masks[k].sum()) for k in MEASURED}
        ok = all(v >= min_pixels for v in vis.values())
        if not ok:
            failures.append(f"{g.frame_id}: " + ", ".join(f"{k} {v} px" for k, v in vis.items() if v < min_pixels))
        masks_file = out / f"{g.frame_id}_masks.npz"
        entries.append(dict(frame_id=g.frame_id, seed=g.meta["seed"], camera=g.meta["camera"], moment=moment,
                            scene_id=g.meta["scene_id"], png=f"{g.frame_id}.png", png_sha256=g.meta["png_sha256"],
                            masks=masks_file.name, masks_sha256=sha256(masks_file.read_bytes()),
                            visible_pixels=vis, all_visible=ok))
    manifest = dict(seeds=[int(s) for s in seeds], split=split, family=family, cameras=list(cameras),
                    moment=moment, width=width, height=height, min_visible_pixels=min_pixels,
                    camera_presets={c: CAMERA_PRESETS[c] for c in cameras}, frames=entries,
                    all_visible=not failures, failures=failures,
                    mujoco_version=__import__("mujoco").__version__, numpy_version=np.__version__,
                    code="mirror.robustness.frames.frames_for_seeds")
    (out / "frames_manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))
    if failures and require_visible:
        raise FramesNotVisible("objects not visible in: " + "; ".join(failures))
    return manifest


def dev_frames(seeds: Sequence[int], *, cameras: Sequence[str] = CAMERAS,
               moments: Sequence[str] = MOMENTS, width: int = WIDTH, height: int = HEIGHT,
               out_dir: Optional[Path | str] = None) -> list[GTFrame]:
    """Ground-truth frames for development seeds: cameras x moments per seed."""
    out: list[GTFrame] = []
    for seed in seeds:
        out += _render(int(seed), "dev", "mixed", cameras, moments, width, height, out_dir)
    return out


def load_frames(directory: Path | str) -> list[GTFrame]:
    d = Path(directory)
    return [GTFrame.load(d, p.stem) for p in sorted(d.glob("*.json")) if p.name != "frames_manifest.json"]
