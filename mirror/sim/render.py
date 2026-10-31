"""Offscreen video of rollouts and demonstrations.

``render_rollout`` draws a saved Rollout (contracts.Rollout: VisualScene plus
RolloutFrames) or a live ExecutionResult to an MP4 with MuJoCo's own
renderer. The scene is rebuilt from the VisualScene alone: static bodies are
drawn where they are, every moving body gets a free joint in a display-only
model and is placed at its recorded pose. Nothing is simulated, so a video
shows exactly what was recorded.

Options: named camera presets, a trail of each carried object's path
coloured by its tilt, high-visibility beads and a small caption overlay.

Needs an offscreen OpenGL backend: MUJOCO_GL=osmesa (software; needs the
system OSMesa library) or MUJOCO_GL=egl (GPU). Encoding uses imageio with
its ffmpeg plugin.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Iterable, Optional, Sequence, Union

import numpy as np

os.environ.setdefault("MUJOCO_GL", "osmesa")
if os.environ["MUJOCO_GL"] == "osmesa":
    os.environ.setdefault("PYOPENGL_PLATFORM", "osmesa")

import mujoco  # noqa: E402  (backend must be chosen first)

from mirror.contracts import Rollout, RolloutFrame, SceneState, VisualScene  # noqa: E402

WIDTH, HEIGHT, FPS = 1280, 720, 30.0
BEAD_RGBA = (0.93, 0.10, 0.62, 1.0)          # vivid magenta, distinct from every kit colour
CARRY_PHASES = ("lift", "transport", "place")
NEUTRAL_TRAIL = (0.45, 0.45, 0.50, 0.85)     # paths of objects not highlighted
TRAIL_SPACING = 0.012                         # m between trail points (colour changes always kept)

# Camera presets. ``lookat`` may be "slot" (the point where carried objects
# cross the wall, else the middle of the wall) or "scene" (table centre).
CAMERAS = {
    "overview": dict(lookat=(0.0, -0.02, 0.06), distance=1.22, azimuth=-58.0, elevation=-36.0),
    "side": dict(lookat="slot", distance=1.05, azimuth=148.0, elevation=-24.0),
    "top": dict(lookat=(0.0, 0.0, 0.0), distance=1.45, azimuth=90.0, elevation=-89.0),
}
WALL_ALPHA = 0.62     # walls drawn translucent so paths behind them stay visible


# --------------------------------------------------------------- inputs

def _load(rollout_or_path) -> tuple[VisualScene, list[RolloutFrame], dict]:
    """(visual, frames, info) from a Rollout, a JSON path, or a
    (VisualScene, frames) pair."""
    info: dict = {}
    if isinstance(rollout_or_path, (str, Path)):
        rollout_or_path = Rollout.model_validate_json(Path(rollout_or_path).read_text())
    if isinstance(rollout_or_path, Rollout):
        if rollout_or_path.visual is None:
            raise ValueError("rollout has no visual scene (planning failed?)")
        r = rollout_or_path.report
        info = dict(scene_id=r.scene_id, condition=r.condition, status=r.status,
                    spec_version=r.spec_version)
        return rollout_or_path.visual, list(rollout_or_path.frames), info
    if isinstance(rollout_or_path, tuple) and len(rollout_or_path) == 2:
        vis, frames = rollout_or_path
        return vis, list(frames), info
    raise TypeError("expected a Rollout, a path to Rollout JSON, or (VisualScene, frames)")


# ---------------------------------------------------------- display model

def _f(v) -> str:
    return " ".join(f"{float(x):.6g}" for x in v)


_TYPES = {"box": "box", "sphere": "sphere", "cylinder": "cylinder", "capsule": "capsule",
          "plane": "plane"}


def display_xml(visual: VisualScene, bead_rgba=BEAD_RGBA, wall_alpha: float = WALL_ALPHA) -> str:
    """MJCF that draws a VisualScene: static geoms in place, one free body per
    moving body. Wall segments are drawn with ``wall_alpha`` opacity."""
    statics, moving = [], []
    for body in visual.bodies:
        geoms = []
        for g in body.geoms:
            t = _TYPES.get(g.type)
            if t is None:
                continue
            rgba = g.rgba
            extra = ""
            if g.name and "/bead" in g.name:
                rgba, extra = bead_rgba, ' material="bead"'
            elif g.name and "/seg" in g.name:
                rgba = (*g.rgba[:3], wall_alpha)
            geoms.append(f'<geom type="{t}" size="{_f(g.size)}" pos="{_f(g.pos)}" quat="{_f(g.quat)}" '
                         f'rgba="{_f(rgba)}" contype="0" conaffinity="0"{extra}/>')
        if body.static:
            statics += geoms
        else:
            moving.append(f'<body name="{body.name}"><freejoint/>{"".join(geoms)}</body>')
    return f"""<mujoco model="display">
  <visual>
    <global offwidth="{WIDTH}" offheight="{HEIGHT}"/>
    <headlight ambient="0.58 0.58 0.58" diffuse="0.38 0.38 0.38" specular="0.05 0.05 0.05"/>
    <quality shadowsize="4096" numslices="18" numstacks="10"/>
    <map znear="0.01"/>
  </visual>
  <asset>
    <texture name="sky" type="skybox" builtin="gradient" rgb1="0.97 0.97 0.96" rgb2="0.86 0.87 0.88" width="64" height="64"/>
    <material name="bead" emission="0.45" specular="0.3"/>
  </asset>
  <worldbody>
    <light pos="0.6 -0.9 2.0" dir="-0.25 0.45 -1" diffuse="0.50 0.50 0.50" specular="0.05 0.05 0.05" castshadow="true"/>
    {"".join(statics)}
    {"".join(moving)}
  </worldbody>
</mujoco>"""


def _tilt_deg(q) -> float:
    w, x, y, z = q
    return math.degrees(math.acos(max(-1.0, min(1.0, 1.0 - 2.0 * (x * x + y * y)))))


def tilt_rgba(tilt: float, tol: float = 15.0) -> tuple[float, float, float, float]:
    """Trail colour: green while within tolerance, amber to red beyond it."""
    if tilt <= tol:
        return (0.13, 0.62, 0.36, 1.0)
    s = min(1.0, (tilt - tol) / 45.0)
    a, b = np.array([0.96, 0.66, 0.10]), np.array([0.86, 0.13, 0.13])
    c = (1 - s) * a + s * b
    return (float(c[0]), float(c[1]), float(c[2]), 1.0)


def _carried(frame: RolloutFrame) -> Optional[str]:
    kind, _, obj = frame.phase.partition(":")
    return obj if kind in CARRY_PHASES and obj else None


class RolloutRenderer:
    """Draws frames of one VisualScene from a camera preset."""

    def __init__(self, visual: VisualScene, frames: Sequence[RolloutFrame], *,
                 camera: Union[str, dict] = "overview", width: int = WIDTH, height: int = HEIGHT,
                 shadows: bool = True, bead_rgba=BEAD_RGBA, wall_alpha: float = WALL_ALPHA):
        self.model = mujoco.MjModel.from_xml_string(display_xml(visual, bead_rgba, wall_alpha))
        self.view = mujoco.MjData(self.model)
        self.renderer = mujoco.Renderer(self.model, height, width, max_geom=20000)
        self.shadows = shadows
        self._adr = {}
        for j in range(self.model.njnt):
            self._adr[self.model.body(self.model.jnt_bodyid[j]).name] = int(self.model.jnt_qposadr[j])
        self.cam = self._camera(camera, visual, frames)

    # -- camera
    @staticmethod
    def _slot_point(visual: VisualScene, frames: Sequence[RolloutFrame]) -> np.ndarray:
        """Centre of the first wall slot, from the wall geometry (the gap
        between the segment below and the segment above it); falls back to
        where a carried object crosses the wall, then the wall's middle."""
        walls = [g for b in visual.bodies if b.static for g in b.geoms if g.name and "/seg" in g.name]
        wall_x = float(np.mean([g.pos[0] for g in walls])) if walls else 0.0
        below = [g for g in walls if 0.02 < g.pos[2] + g.size[2] < 0.30]
        for g in sorted(below, key=lambda g: g.pos[1]):
            top = g.pos[2] + g.size[2]
            above = [h for h in walls if abs(h.pos[1] - g.pos[1]) < 1e-6 and h.pos[2] - h.size[2] > top]
            if above:
                z_hi = min(h.pos[2] - h.size[2] for h in above)
                return np.array([wall_x, g.pos[1], 0.5 * (top + z_hi)])
        prev = None
        for f in frames:
            obj = _carried(f)
            if obj and obj in f.bodies:
                p = np.asarray(f.bodies[obj].pos)
                if prev is not None and (prev[0] - wall_x) * (p[0] - wall_x) <= 0:
                    return np.array([wall_x, p[1], p[2]])
                prev = p
            else:
                prev = None
        return np.array([wall_x, 0.0, 0.12])

    def _camera(self, camera, visual, frames) -> mujoco.MjvCamera:
        cfg = dict(CAMERAS[camera]) if isinstance(camera, str) else dict(camera)
        cam = mujoco.MjvCamera()
        look = cfg.get("lookat", "scene")
        if look == "slot":
            look = self._slot_point(visual, frames)
        elif look == "scene":
            look = (0.0, 0.0, 0.04)
        look = tuple(look)
        cam.lookat[:] = look
        cam.distance = cfg["distance"]
        cam.azimuth = cfg["azimuth"]
        cam.elevation = cfg["elevation"]
        return cam

    # -- drawing
    def _pose(self, frame: RolloutFrame) -> None:
        q = self.view.qpos
        for name, pose in frame.bodies.items():
            a = self._adr.get(name)
            if a is not None:
                q[a:a + 3] = pose.pos
                q[a + 3:a + 7] = pose.quat
        mujoco.mj_kinematics(self.model, self.view)

    def _add_trail(self, points: list[tuple[np.ndarray, tuple]], radius: float) -> None:
        scn = self.renderer.scene
        for (p0, _), (p1, rgba) in zip(points[:-1], points[1:]):
            if scn.ngeom >= scn.maxgeom:
                return
            if np.linalg.norm(p1 - p0) < 1e-5:
                continue
            g = scn.geoms[scn.ngeom]
            mujoco.mjv_initGeom(g, mujoco.mjtGeom.mjGEOM_CAPSULE, np.zeros(3), np.zeros(3),
                                np.eye(3).flatten(), np.asarray(rgba, dtype=np.float32))
            mujoco.mjv_connector(g, mujoco.mjtGeom.mjGEOM_CAPSULE, radius, p0, p1)
            g.emission = 0.35
            scn.ngeom += 1

    def render(self, frame: RolloutFrame, trails: Optional[dict] = None,
               trail_radius: float = 0.0045) -> np.ndarray:
        self._pose(frame)
        self.renderer.update_scene(self.view, camera=self.cam)
        self.renderer.scene.flags[mujoco.mjtRndFlag.mjRND_SHADOW] = int(self.shadows)
        for obj, pts in (trails or {}).items():
            thin = pts and pts[0] and pts[0][0][1] == NEUTRAL_TRAIL
            for seg in pts:
                self._add_trail(seg, trail_radius * (0.55 if thin else 1.0))
        return self.renderer.render()

    def close(self) -> None:
        self.renderer.close()


# --------------------------------------------------------------- overlay

def _cup_beads_in(frame: RolloutFrame, visual: VisualScene) -> Optional[tuple[int, int]]:
    cup = next((b for b in visual.bodies if any(g.name and g.name.endswith("/floor") and not b.static
                                                for g in b.geoms)), None)
    if cup is None or cup.name not in frame.bodies:
        return None
    floor = next(g for g in cup.geoms if g.name.endswith("/floor"))
    wall = next((g for g in cup.geoms if g.name.endswith("/wall_xp")), None)
    hx, hy, w2 = floor.size
    hz = (wall.size[2] + w2) if wall else 0.05
    p = np.asarray(frame.bodies[cup.name].pos)
    w, x, y, z = frame.bodies[cup.name].quat
    R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                  [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                  [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])
    beads = [np.asarray(v.pos) for k, v in frame.bodies.items() if k.startswith(cup.name + "/bead")]
    if not beads:
        return None
    loc = (np.array(beads) - p) @ R
    inner = (np.abs(loc[:, 0]) < hx - 2 * w2 + 1e-3) & (np.abs(loc[:, 1]) < hy - 2 * w2 + 1e-3) & \
            (np.abs(loc[:, 2]) < hz)
    return int(inner.sum()), len(beads)


def _legend_label(trail_objects) -> str:
    if trail_objects:
        return ", ".join(trail_objects) + " path, coloured by tilt"
    return "carried path, coloured by tilt"


def _overlay(img: np.ndarray, title: str, lines: list[str], legend: bool,
             legend_label: str = "carried path, coloured by tilt") -> np.ndarray:
    from PIL import Image, ImageDraw, ImageFont

    im = Image.fromarray(img)
    dr = ImageDraw.Draw(im, "RGBA")
    big = ImageFont.load_default(size=max(14, im.height // 30))
    small = ImageFont.load_default(size=max(12, im.height // 42))
    pad = im.height // 40
    ink = (24, 24, 27, 255)
    if title:
        tw = dr.textlength(title, font=big)
        dr.rounded_rectangle((pad - 8, pad - 6, pad + tw + 10, pad + big.size + 10), 6,
                             fill=(255, 255, 255, 215))
        dr.text((pad, pad), title, font=big, fill=ink)
    y = im.height - pad - len(lines) * (small.size + 6)
    if lines:
        wmax = max(dr.textlength(s, font=small) for s in lines)
        dr.rounded_rectangle((pad - 8, y - 6, pad + wmax + 10, im.height - pad + 4), 6,
                             fill=(255, 255, 255, 215))
    for s in lines:
        dr.text((pad, y), s, font=small, fill=ink)
        y += small.size + 6
    if legend:
        items = [("within 15°", tilt_rgba(0)), ("tilted", tilt_rgba(40)), ("on its side", tilt_rgba(90))]
        x = im.width - pad
        yy = im.height - pad - small.size
        widths = [dr.textlength(t, font=small) + 26 for t, _ in items]
        x0 = x - max(sum(widths), dr.textlength(legend_label, font=small)) - 10
        dr.rounded_rectangle((x0 - 8, yy - small.size - 16, x + 4, yy + small.size + 6), 6,
                             fill=(255, 255, 255, 215))
        dr.text((x0, yy - small.size - 10), legend_label, font=small,
                fill=(82, 82, 91, 255))
        for (t, c), wd in zip(items, widths):
            col = tuple(int(255 * v) for v in c[:3]) + (255,)
            dr.rounded_rectangle((x0, yy + 3, x0 + 16, yy + small.size - 1), 3, fill=col)
            dr.text((x0 + 22, yy), t, font=small, fill=ink)
            x0 += wd
    return np.asarray(im)


# ----------------------------------------------------------------- video

class _Playback:
    """Steps through frames at output times, accumulating carried-object trails."""

    def __init__(self, frames: Sequence[RolloutFrame], highlight: Optional[Sequence[str]] = None):
        self.frames = frames
        self.highlight = None if highlight is None else set(highlight)
        self.trails: dict[str, list[list[tuple]]] = {}
        self.k = 0
        self.last_obj = None

    def at(self, t_target: float) -> RolloutFrame:
        frames = self.frames
        while self.k < len(frames) and frames[self.k].t <= t_target + 1e-9:
            f = frames[self.k]
            obj = _carried(f)
            if obj and obj in f.bodies:
                tilt = _tilt_deg(f.bodies[obj].quat)
                segs = self.trails.setdefault(obj, [])
                if obj != self.last_obj or not segs:
                    segs.append([])
                col = tilt_rgba(tilt) if self.highlight is None or obj in self.highlight else NEUTRAL_TRAIL
                p = np.asarray(f.bodies[obj].pos, dtype=float)
                seg = segs[-1]
                if seg and seg[-1][1] == col and len(seg) > 1 and \
                        np.linalg.norm(p - seg[-2][0]) < TRAIL_SPACING:
                    seg[-1] = (p, col)          # extend the last segment instead of adding one
                else:
                    seg.append((p, col))
            self.last_obj = obj
            self.k += 1
        return frames[max(0, self.k - 1)]


def _caption_lines(f: RolloutFrame, t0: float, visual: VisualScene) -> list[str]:
    lines = [f"t = {f.t - t0:5.1f} s   {f.phase}"]
    obj = _carried(f)
    if obj and obj in f.bodies:
        lines.append(f"{obj} tilt {_tilt_deg(f.bodies[obj].quat):4.0f}°")
    bi = _cup_beads_in(f, visual)
    if bi is not None:
        lines.append(f"beads in cup {bi[0]}/{bi[1]}")
    return lines


def _default_title(info: dict) -> str:
    return " · ".join(str(v) for v in (info.get("scene_id"), info.get("condition")) if v)


def render_rollout(rollout_or_path, out_mp4: Union[str, Path], camera: Union[str, dict] = "overview",
                   trail: bool = True, *, fps: float = FPS, speed: float = 1.0,
                   width: int = WIDTH, height: int = HEIGHT, title: Optional[str] = None,
                   caption: bool = True, shadows: bool = True, hold_end: float = 1.0,
                   trail_objects: Optional[Sequence[str]] = None) -> Path:
    """Render a Rollout (object or JSON path) or a (VisualScene, frames) pair
    to an H.264 MP4 (default 1280x720, 30 fps). ``speed`` > 1 plays faster
    than real time. With ``trail`` each carried object's path is drawn,
    coloured by tilt; if ``trail_objects`` is given, only those objects are
    coloured and the others' paths are drawn thin and grey.
    """
    import imageio.v2 as imageio

    visual, frames, info = _load(rollout_or_path)
    if not frames:
        raise ValueError("no frames to render")
    out_mp4 = Path(out_mp4)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    rr = RolloutRenderer(visual, frames, camera=camera, width=width, height=height, shadows=shadows)
    title = _default_title(info) if title is None else title
    play = _Playback(frames, trail_objects)
    t0 = frames[0].t
    n_out = int(math.floor((frames[-1].t - t0) / speed * fps)) + 1
    img = None
    try:
        with imageio.get_writer(out_mp4, fps=fps, codec="libx264", quality=8,
                                pixelformat="yuv420p", macro_block_size=8) as writer:
            for i in range(n_out):
                f = play.at(t0 + i * speed / fps)
                img = rr.render(f, play.trails if trail else None)
                if caption:
                    img = _overlay(img, title, _caption_lines(f, t0, visual), legend=trail,
                                   legend_label=_legend_label(trail_objects))
                writer.append_data(img)
            for _ in range(int(hold_end * fps)):
                writer.append_data(img)
    finally:
        rr.close()
    return out_mp4


def render_stills(rollout_or_path, times: Iterable[float], out_dir: Union[str, Path],
                  camera: Union[str, dict] = "overview", trail: bool = True, *,
                  width: int = WIDTH, height: int = HEIGHT, title: Optional[str] = None,
                  caption: bool = True, prefix: str = "still",
                  trail_objects: Optional[Sequence[str]] = None) -> list[Path]:
    """PNG stills at the given rollout times (seconds from the first frame)."""
    import imageio.v2 as imageio

    visual, frames, info = _load(rollout_or_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rr = RolloutRenderer(visual, frames, camera=camera, width=width, height=height)
    title = _default_title(info) if title is None else title
    play = _Playback(frames, trail_objects)
    t0 = frames[0].t
    paths = []
    try:
        for t in sorted(times):
            f = play.at(t0 + t)
            img = rr.render(f, play.trails if trail else None)
            if caption:
                img = _overlay(img, title, _caption_lines(f, t0, visual), legend=trail,
                               legend_label=_legend_label(trail_objects))
            p = out_dir / f"{prefix}_{camera if isinstance(camera, str) else 'custom'}_{t:06.2f}.png"
            imageio.imwrite(p, img)
            paths.append(p)
    finally:
        rr.close()
    return paths


def render_result(result, scene: SceneState, out_mp4, **kw) -> Path:
    """Render a live ExecutionResult of ``scene``."""
    from mirror.sim.env import visual_scene
    from mirror.sim.model import build_model
    model, index = build_model(scene)
    return render_rollout((visual_scene(model, index), result.frames), out_mp4, **kw)


def demo_frames(trace) -> list[RolloutFrame]:
    """RolloutFrames from a DemoTrace. A trace has no bead or finger poses;
    phases are derived from its grasp/release events."""
    events = sorted((e.t, e.type, e.object_id) for e in trace.events if e.type in ("grasp", "release"))
    out, held, j = [], None, 0
    for f in trace.frames:
        while j < len(events) and events[j][0] <= f.t:
            held = events[j][2] if events[j][1] == "grasp" else None
            j += 1
        out.append(RolloutFrame(t=f.t, bodies={"gripper": f.gripper, **f.objects},
                                gripper_width=f.gripper_width,
                                phase=f"transport:{held}" if held else "demo"))
    return out


def render_demo(trace, out_mp4, **kw) -> Path:
    """Render a DemoTrace. Finger and bead poses are not part of a trace, so
    the fingers are drawn from the jaw width and beads are omitted."""
    from mirror.sim.env import visual_scene
    from mirror.sim.model import build_model
    model, index = build_model(trace.scene)
    vis = visual_scene(model, index)
    vis = VisualScene(bodies=[b for b in vis.bodies if "/bead" not in b.name])
    frames = [_with_fingers(f) for f in demo_frames(trace)]
    kw.setdefault("title", f"demonstration · {trace.source}")
    return render_rollout((vis, frames), out_mp4, **kw)


def _with_fingers(f: RolloutFrame) -> RolloutFrame:
    """Finger body poses from the gripper pose and jaw width."""
    from mirror.sim.rotations import quat_to_mat
    g = f.bodies["gripper"]
    R = quat_to_mat(g.quat)
    half = f.gripper_width / 2.0
    bodies = dict(f.bodies)
    for name, sgn in (("gripper_finger_l", 1.0), ("gripper_finger_r", -1.0)):
        p = np.asarray(g.pos) + R @ np.array([0.0, sgn * half, 0.0])
        bodies[name] = type(g)(pos=tuple(float(v) for v in p), quat=g.quat)
    return RolloutFrame(t=f.t, bodies=bodies, gripper_width=f.gripper_width, phase=f.phase)


def main(argv=None) -> None:
    """python -m mirror.sim.render ROLLOUT.json OUT.mp4 [--camera overview|side|top] [--no-trail]
    python -m mirror.sim.render --demo [DEMO.json] OUT.mp4"""
    import argparse

    ap = argparse.ArgumentParser(description="Render a saved rollout or demonstration to MP4.")
    ap.add_argument("inp", help="Rollout JSON (or demonstration JSON with --demo)")
    ap.add_argument("out")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--camera", default="overview", choices=sorted(CAMERAS))
    ap.add_argument("--no-trail", action="store_true")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--title", default=None)
    a = ap.parse_args(argv)
    if a.demo:
        from mirror.sim.demo import load_demo
        path = render_demo(load_demo(a.inp), a.out, camera=a.camera, trail=not a.no_trail,
                           speed=a.speed, **({"title": a.title} if a.title else {}))
    else:
        path = render_rollout(a.inp, a.out, camera=a.camera, trail=not a.no_trail, speed=a.speed,
                              title=a.title)
    print(path)


if __name__ == "__main__":
    main()
