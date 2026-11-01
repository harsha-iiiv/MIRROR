"""Rendered demonstration videos with per-frame simulator ground truth.

``render_demo_video`` records a scripted teleoperation demonstration (the
same operator as the reference demonstration) in the physics simulation and
renders it from fixed cameras. Alongside each MP4 it writes the ground truth
for every video frame: object and gripper poses, jaw width, the held object,
cup tilt, and one binary mask per labelled thing per camera, taken from
MuJoCo segmentation rendering of the same frame. The video shows the scene
as it is (no markers, no captions, opaque walls, objects in their simulator
colours).
"""
from __future__ import annotations

import contextlib
import gzip
import json
import math
from pathlib import Path
from typing import Optional, Sequence, Union

import mujoco
import numpy as np
from pydantic import BaseModel

from mirror.contracts import DemoTrace, RolloutFrame, SceneState, VisualScene
from mirror.perception.cameras import CAMERAS, CameraInfo, camera_info
from mirror.perception.masks import mask_bbox, mask_centroid, rle_encode

SIM_BEAD_RGBA = (0.95, 0.75, 0.15, 1.0)      # the beads' simulator colour
TRAY_RGBA = (0.30, 0.33, 0.36, 1.0)          # dark grey plastic tray
GRIPPER_RGBA = (0.62, 0.64, 0.67, 1.0)       # brushed-metal grey gripper


def _noise(shape, seed, scales=(64, 16, 4)):
    rng = np.random.default_rng(seed)
    out = np.zeros(shape)
    for sc in scales:
        small = rng.random((shape[0] // sc + 2, shape[1] // sc + 2))
        from scipy import ndimage
        out += ndimage.zoom(small, sc, order=1)[:shape[0], :shape[1]] / len(scales)
    return out


def _wood_png() -> bytes:
    """A procedural light-oak texture (grain along the table's long side)."""
    import io
    from PIL import Image
    h, w = 512, 1024
    y = np.linspace(0, 1, h)[:, None]
    x = np.linspace(0, 1, w)[None, :]
    n = _noise((h, w), 7, (128, 32, 8))
    rings = 0.5 + 0.5 * np.sin(2 * np.pi * (y * 40 + 1.5 * n + 0.1 * np.sin(6 * np.pi * x)))
    base = np.array([0.80, 0.64, 0.45])
    dark = np.array([0.70, 0.53, 0.35])
    t = (0.35 * rings + 0.25 * n)[..., None]
    img = (base * (1 - t) + dark * t)
    img *= (0.94 + 0.06 * _noise((h, w), 3, (4, 2)))[..., None]
    buf = io.BytesIO()
    Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8)).save(buf, "PNG")
    return buf.getvalue()


def _concrete_png() -> bytes:
    import io
    from PIL import Image
    n = _noise((512, 512), 11, (64, 16, 4, 2))
    img = (0.26 + 0.05 * n)[..., None] * np.array([1.0, 1.0, 1.02])
    buf = io.BytesIO()
    Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8)).save(buf, "PNG")
    return buf.getvalue()


def _realistic(xml: str) -> tuple[str, dict]:
    """Studio-like materials for the perception videos: a wooden table, a
    concrete floor, a neutral grey backdrop, a dark plastic tray, a metal
    gripper and two lights. Geometry is unchanged."""
    import re
    assets = {"wood.png": _wood_png(), "concrete.png": _concrete_png()}
    xml = xml.replace('<texture name="sky" type="skybox" builtin="gradient" rgb1="0.98 0.97 0.95" rgb2="0.89 0.87 0.84" width="64" height="64"/>',
                      '<texture name="sky" type="skybox" builtin="gradient" rgb1="0.66 0.67 0.68" rgb2="0.48 0.49 0.50" width="64" height="64"/>'
                      '<texture name="wood" type="2d" file="wood.png"/>'
                      '<material name="wood" texture="wood" texrepeat="1 1" texuniform="false" specular="0.15" shininess="0.3"/>'
                      '<texture name="concrete" type="2d" file="concrete.png"/>'
                      '<material name="concrete" texture="concrete" texrepeat="6 6" texuniform="true" specular="0.05"/>'
                      '<material name="metal" specular="0.6" shininess="0.6" reflectance="0.05"/>'
                      '<material name="plastic" specular="0.25" shininess="0.4"/>')

    def geom_sub(m):
        g = m.group(0)
        name = re.search(r'name="([^"]+)"', g)
        name = name.group(1) if name else ""
        if name == "table":
            g = re.sub(r'rgba="[^"]+"', 'rgba="1 1 1 1" material="wood"', g)
        elif name == "floor":
            g = re.sub(r'rgba="[^"]+"', 'rgba="1 1 1 1" material="concrete"', g)
        elif name.startswith("tray"):
            g = re.sub(r'rgba="[^"]+"', 'rgba="%s" material="plastic"' % " ".join(map(str, TRAY_RGBA)), g)
        return g
    xml = re.sub(r"<geom [^>]*/>", geom_sub, xml)
    # gripper bodies: metal
    def body_sub(m):
        b = m.group(0)
        return re.sub(r'rgba="[^"]+"', 'rgba="%s" material="metal"' % " ".join(map(str, GRIPPER_RGBA)), b)
    xml = re.sub(r'<body name="gripper[^"]*">.*?</body>', body_sub, xml, flags=re.S)
    xml = xml.replace('<light pos="0.6 -0.9 2.0"',
                      '<light pos="-0.9 -0.6 1.6" dir="0.5 0.35 -1" diffuse="0.25 0.25 0.25" specular="0.05 0.05 0.05" castshadow="false"/>'
                      '<light pos="0.6 -0.9 2.0"')
    return xml, assets
VIDEO_W, VIDEO_H, VIDEO_FPS = 960, 540, 15.0


class DemoVideo(BaseModel):
    demo_id: str
    scene_id: str
    source: str
    fps: float
    duration_s: float
    n_frames: int
    videos: dict[str, str]          # camera -> mp4 path (relative to the manifest)
    cameras: dict[str, CameraInfo]
    labels: list[str]
    ground_truth: str               # json.gz path (relative)
    trace: str                      # the true DemoTrace (relative)
    scene: str                      # SceneState JSON (relative)
    notes: str = ""

    def path(self, root: Union[str, Path], rel: str) -> Path:
        return Path(root) / rel


# ----------------------------------------------------------------- source


@contextlib.contextmanager
def _capture_teleop():
    """Record the Teleop instance the scripted operator creates, so its full
    rendered frames (fingers, beads) are available. Behaviour is unchanged."""
    import mirror.sim.demo as demo_mod
    base = demo_mod.Teleop
    box = {}

    class _Captured(base):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            box["tele"] = self

    demo_mod.Teleop = _Captured
    try:
        yield box
    finally:
        demo_mod.Teleop = base


def scene_for(source, split: Optional[str] = None) -> SceneState:
    if isinstance(source, SceneState):
        return source
    if isinstance(source, DemoTrace):
        return source.scene
    if source in (None, "reference"):
        from mirror.scenes import reference_scene
        return reference_scene("demo")
    if isinstance(source, int):
        from mirror.scenes import sample_layout
        return sample_layout(source, split or ("dev" if source < 10000 else "eval"))
    raise ValueError(f"unsupported demonstration source {source!r}")


def record_demo(source, split: Optional[str] = None):
    """(scene, true DemoTrace, rendered frames at 30 Hz, VisualScene)."""
    from mirror.sim.demo import record_scripted_demo
    scene = scene_for(source, split)
    with _capture_teleop() as box:
        trace = record_scripted_demo(scene)
    tele = box["tele"]
    return scene, trace, tele.rollout_frames(), tele.visual_scene()


# --------------------------------------------------------------- cameras


def _label_of(name: str, body: str) -> Optional[str]:
    if body and body != "world":
        if "/bead" in body:
            return "beads"
        if body.startswith("gripper"):
            return "gripper"
        return body.split("/")[0]
    if not name:
        return None
    if name == "floor":
        return None
    return name.split("/")[0]


class _Cam:
    def __init__(self, visual: VisualScene, info: CameraInfo, shadows: bool = True):
        from mirror.sim.render import display_xml
        self.info = info
        xml, assets = _realistic(display_xml(visual, bead_rgba=SIM_BEAD_RGBA, wall_alpha=1.0, name_geoms=True))
        self.model = mujoco.MjModel.from_xml_string(xml, assets)
        self.model.vis.global_.offwidth = max(info.width, int(self.model.vis.global_.offwidth))
        self.model.vis.global_.offheight = max(info.height, int(self.model.vis.global_.offheight))
        self.model.vis.global_.fovy = info.fovy
        self.data = mujoco.MjData(self.model)
        self.r = mujoco.Renderer(self.model, info.height, info.width, max_geom=4000)
        self.shadows = shadows
        self.cam = mujoco.MjvCamera()
        self.cam.lookat[:] = info.lookat
        self.cam.distance, self.cam.azimuth, self.cam.elevation = info.distance, info.azimuth, info.elevation
        self.adr = {self.model.body(self.model.jnt_bodyid[j]).name: int(self.model.jnt_qposadr[j])
                    for j in range(self.model.njnt)}
        names = []
        for g in range(self.model.ngeom):
            body = self.model.body(self.model.geom_bodyid[g]).name
            names.append(_label_of(self.model.geom(g).name, body))
        self.labels = sorted({n for n in names if n})
        self.lut = np.zeros(self.model.ngeom + 1, dtype=np.int32)
        for g, n in enumerate(names):
            self.lut[g] = self.labels.index(n) + 1 if n else 0

    def pose(self, frame: RolloutFrame) -> None:
        q = self.data.qpos
        for name, p in frame.bodies.items():
            a = self.adr.get(name)
            if a is not None:
                q[a:a + 3] = p.pos
                q[a + 3:a + 7] = p.quat
        mujoco.mj_kinematics(self.model, self.data)

    def render(self, frame: RolloutFrame):
        self.pose(frame)
        self.r.update_scene(self.data, camera=self.cam)
        self.r.scene.flags[mujoco.mjtRndFlag.mjRND_SHADOW] = int(self.shadows)
        rgb = self.r.render()
        self.r.enable_segmentation_rendering()
        try:
            seg = self.r.render()
        finally:
            self.r.disable_segmentation_rendering()
        ids = seg[:, :, 0]
        is_geom = (seg[:, :, 1] == int(mujoco.mjtObj.mjOBJ_GEOM)) & (ids >= 0) & (ids < self.model.ngeom)
        lab = np.where(is_geom, self.lut[np.clip(ids, 0, self.model.ngeom)], 0)
        return rgb, lab

    def close(self):
        self.r.close()


# ------------------------------------------------------------ ground truth


def _tilt_deg(q) -> float:
    w, x, y, z = q
    return math.degrees(math.acos(max(-1.0, min(1.0, 1.0 - 2.0 * (x * x + y * y)))))


def _held_at(t: float, events) -> Optional[str]:
    held = None
    for e in sorted(events, key=lambda e: e.t):
        if e.t > t:
            break
        if e.type == "grasp":
            held = e.object_id
        elif e.type == "release" and e.object_id == held:
            held = None
    return held


def render_demo_video(source=None, out_dir: Union[str, Path] = "demo_videos", *,
                      cams: Sequence[str] = ("top", "side"), fps: float = VIDEO_FPS,
                      width: int = VIDEO_W, height: int = VIDEO_H, shadows: bool = True,
                      max_seconds: Optional[float] = None, split: Optional[str] = None) -> tuple[Path, DemoVideo]:
    """Record a scripted demonstration for ``source`` (None/"reference", a dev
    seed, a SceneState or a DemoTrace's scene) and render it.

    Returns (manifest path, DemoVideo). Files in ``out_dir/<demo_id>/``:
    <cam>.mp4, ground_truth.json.gz, trace.json, scene.json, video.json.
    """
    import imageio.v2 as imageio

    scene, trace, frames, visual = record_demo(source, split)
    demo_id = trace.demo_id
    root = Path(out_dir) / demo_id
    root.mkdir(parents=True, exist_ok=True)
    infos = {c: camera_info(c, width, height) for c in cams}
    cams_r = {c: _Cam(visual, infos[c], shadows) for c in cams}
    labels = cams_r[cams[0]].labels
    t0 = frames[0].t
    t_end = frames[-1].t - t0 if max_seconds is None else min(max_seconds, frames[-1].t - t0)
    n = int(math.floor(t_end * fps)) + 1
    writers = {c: imageio.get_writer(root / f"{c}.mp4", fps=fps, codec="libx264", quality=8,
                                     pixelformat="yuv420p", macro_block_size=2) for c in cams}
    gt_frames = []
    k = 0
    try:
        for i in range(n):
            t = t0 + i / fps
            while k + 1 < len(frames) and frames[k + 1].t <= t + 1e-9:
                k += 1
            f = frames[k]
            rec = {"i": i, "t": round(i / fps, 4), "sim_t": round(f.t - t0, 4),
                   "gripper": {"pos": list(f.bodies["gripper"].pos), "quat": list(f.bodies["gripper"].quat)},
                   "gripper_width": f.gripper_width,
                   "objects": {o.id: {"pos": list(f.bodies[o.id].pos), "quat": list(f.bodies[o.id].quat)}
                               for o in scene.objects if o.id in f.bodies},
                   "held": _held_at(f.t - t0 + trace.frames[0].t, trace.events),
                   "cup_tilt_deg": {o.id: round(_tilt_deg(f.bodies[o.id].quat), 3)
                                    for o in scene.objects if o.kind == "cup" and o.id in f.bodies},
                   "masks": {}}
            for c, cr in cams_r.items():
                rgb, lab = cr.render(f)
                writers[c].append_data(rgb)
                per = {}
                for li, name in enumerate(cr.labels, start=1):
                    m = lab == li
                    if m.any():
                        per[name] = {"rle": rle_encode(m), "area": int(m.sum()), "bbox": mask_bbox(m),
                                     "centroid": mask_centroid(m)}
                rec["masks"][c] = per
            gt_frames.append(rec)
    finally:
        for w in writers.values():
            w.close()
        for cr in cams_r.values():
            cr.close()
    tf0 = trace.frames[0].t
    events = [{"t": round(e.t - tf0, 4), "type": e.type, "object_id": e.object_id}
              for e in trace.events if e.type in ("grasp", "release")]
    gt = {"demo_id": demo_id, "fps": fps, "labels": labels, "events": events,
          "objects": [{"id": o.id, "kind": o.kind} for o in scene.objects],
          "trays": [t.id for t in scene.trays], "obstacles": [ob.id for ob in scene.obstacles],
          "frames": gt_frames}
    with gzip.open(root / "ground_truth.json.gz", "wt") as fh:
        json.dump(gt, fh, separators=(",", ":"))
    (root / "trace.json").write_text(trace.model_dump_json())
    (root / "scene.json").write_text(scene.model_dump_json())
    dv = DemoVideo(demo_id=demo_id, scene_id=scene.scene_id, source=trace.source, fps=fps,
                   duration_s=round((n - 1) / fps, 3), n_frames=n,
                   videos={c: f"{c}.mp4" for c in cams}, cameras=infos, labels=labels,
                   ground_truth="ground_truth.json.gz", trace="trace.json", scene="scene.json",
                   notes=("Scripted teleoperation in the physics simulation, rendered from fixed cameras. "
                          "Ground truth (poses, masks from segmentation rendering, events) is for "
                          "measurement only."))
    man = root / "video.json"
    man.write_text(dv.model_dump_json(indent=1))
    return man, dv


def load_demo_video(manifest: Union[str, Path]) -> tuple[DemoVideo, dict]:
    """(DemoVideo, ground truth dict)."""
    manifest = Path(manifest)
    dv = DemoVideo.model_validate_json(manifest.read_text())
    with gzip.open(manifest.parent / dv.ground_truth, "rt") as fh:
        gt = json.load(fh)
    return dv, gt


def render_frame(source=None, t: float = 0.5, cam: str = "top", *, fraction: bool = True,
                 width: int = VIDEO_W, height: int = VIDEO_H, split: Optional[str] = None):
    """One frame of a demonstration: (PNG bytes, {label: bool mask}, frame info).

    ``t`` is a fraction of the demonstration (default) or seconds."""
    import io
    from PIL import Image
    scene, trace, frames, visual = record_demo(source, split)
    t0, t1 = frames[0].t, frames[-1].t
    target = t0 + (t * (t1 - t0) if fraction else t)
    f = min(frames, key=lambda fr: abs(fr.t - target))
    info = camera_info(cam, width, height)
    cr = _Cam(visual, info)
    try:
        rgb, lab = cr.render(f)
        masks = {name: lab == li for li, name in enumerate(cr.labels, start=1) if (lab == li).any()}
    finally:
        cr.close()
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, "PNG")
    return buf.getvalue(), masks, {"t": f.t - t0, "camera": info.model_dump(), "scene_id": scene.scene_id,
                                   "objects": {o.id: list(f.bodies[o.id].pos) for o in scene.objects}}
