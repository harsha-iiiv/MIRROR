"""Offscreen video of recorded rollouts (optional helper).

Renders RolloutFrames with MuJoCo's own renderer into an MP4. The frames are
drawn by placing every body at its recorded pose in a separate, display-only
MjData that is never stepped, so rendering cannot influence a rollout.

Needs an offscreen OpenGL backend: set MUJOCO_GL=osmesa (software, needs the
system OSMesa library) or MUJOCO_GL=egl (GPU). Encoding uses imageio with
imageio-ffmpeg.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional

import mujoco
import numpy as np

from mirror.contracts import RolloutFrame, SceneState
from mirror.sim import rotations as rot
from mirror.sim.model import GROUP_BEAD, build_model

DEFAULT_CAMERA = dict(lookat=(0.0, 0.0, 0.05), distance=1.45, azimuth=-70.0, elevation=-42.0)


def _ensure_backend() -> None:
    os.environ.setdefault("MUJOCO_GL", "osmesa")
    if os.environ["MUJOCO_GL"] == "osmesa":
        os.environ.setdefault("PYOPENGL_PLATFORM", "osmesa")


class FrameRenderer:
    """Draws recorded frames of one scene."""

    def __init__(self, scene: SceneState, width: int = 960, height: int = 540,
                 camera: Optional[dict] = None):
        _ensure_backend()
        self.model, self.index = build_model(scene)
        self.model.vis.global_.offwidth = max(width, self.model.vis.global_.offwidth)
        self.model.vis.global_.offheight = max(height, self.model.vis.global_.offheight)
        self.view = mujoco.MjData(self.model)
        self.renderer = mujoco.Renderer(self.model, height, width)
        self.cam = mujoco.MjvCamera()
        cfg = dict(DEFAULT_CAMERA, **(camera or {}))
        self.cam.lookat[:] = cfg["lookat"]
        self.cam.distance = cfg["distance"]
        self.cam.azimuth = cfg["azimuth"]
        self.cam.elevation = cfg["elevation"]
        self._free = {}
        m = self.model
        for j in range(m.njnt):
            if m.jnt_type[j] == mujoco.mjtJoint.mjJNT_FREE:
                self._free[self.index.body_names[m.jnt_bodyid[j]]] = int(m.jnt_qposadr[j])
        self._angles = (0.0, 0.0, 0.0)

    def _pose_view(self, frame: RolloutFrame) -> None:
        """Place every body of the display-only data at its recorded pose."""
        q = self.view.qpos
        for name, pose in frame.bodies.items():
            a = self._free.get(name)
            if a is not None:
                q[a:a + 3] = pose.pos
                q[a + 3:a + 7] = pose.quat
        g = frame.bodies.get("gripper")
        if g is not None:
            self._angles = rot.wrist_angles(g.quat, self._angles)
            for adr, v in zip(self.index.robot_qposadr, (*g.pos, *self._angles)):
                q[adr] = v
        for adr in self.index.finger_qposadr:
            q[adr] = frame.gripper_width / 2.0
        mujoco.mj_kinematics(self.model, self.view)

    def render(self, frame: RolloutFrame) -> np.ndarray:
        self._pose_view(frame)
        opt = mujoco.MjvOption()
        # Frames without bead poses (e.g. converted demonstration frames) hide
        # the beads rather than drawing them at stale positions.
        has_beads = any("/bead" in k for k in frame.bodies)
        opt.geomgroup[GROUP_BEAD] = 1 if has_beads else 0
        self.renderer.update_scene(self.view, camera=self.cam, scene_option=opt)
        return self.renderer.render()

    def close(self) -> None:
        self.renderer.close()


def render_video(scene: SceneState, frames: Iterable[RolloutFrame], out_path: str | Path, *,
                 fps: float = 30.0, width: int = 960, height: int = 540,
                 camera: Optional[dict] = None, stride: int = 1) -> Path:
    """Write an MP4 of the frames and return its path."""
    import imageio.v2 as imageio

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fr = FrameRenderer(scene, width, height, camera)
    try:
        with imageio.get_writer(out_path, fps=fps / stride, codec="libx264", quality=7,
                                macro_block_size=8) as w:
            for i, f in enumerate(frames):
                if i % stride == 0:
                    w.append_data(fr.render(f))
    finally:
        fr.close()
    return out_path


def demo_frames(trace) -> list[RolloutFrame]:
    """RolloutFrames from a DemoTrace (no bead poses; beads are hidden when drawn)."""
    return [RolloutFrame(t=f.t, bodies={"gripper": f.gripper, **f.objects},
                         gripper_width=f.gripper_width, phase="demo") for f in trace.frames]


def render_still(scene: SceneState, frame: RolloutFrame, out_path: str | Path, **kw) -> Path:
    import imageio.v2 as imageio

    fr = FrameRenderer(scene, kw.get("width", 960), kw.get("height", 540), kw.get("camera"))
    try:
        img = fr.render(frame)
    finally:
        fr.close()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    imageio.imwrite(out_path, img)
    return out_path


def main(argv=None) -> None:
    """python -m mirror.sim.render OUT.mp4 [--demo PATH] [--stride N] [--size WxH]"""
    import argparse

    from mirror.sim.demo import REFERENCE_DEMO_PATH, load_demo

    ap = argparse.ArgumentParser(description="Render a recorded demonstration to MP4.")
    ap.add_argument("out")
    ap.add_argument("--demo", default=str(REFERENCE_DEMO_PATH))
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--size", default="960x540")
    a = ap.parse_args(argv)
    w, h = (int(v) for v in a.size.split("x"))
    trace = load_demo(a.demo)
    path = render_video(trace.scene, demo_frames(trace), a.out, fps=trace.sample_hz,
                        width=w, height=h, stride=a.stride)
    print(path)


if __name__ == "__main__":
    main()
