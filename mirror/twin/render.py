"""Render a rollout through a calibrated (or virtual) camera and composite it over real pixels.

The simulator draws only what the real footage cannot show: the gripper and the simulated
proxies. Table, tray and walls are "holdout" geometry: where they are the front-most surface the
real plate shows through (so real walls hide the robot), and their simulated shading is used only
to darken the plate with the robot's shadow.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from mirror.sim import render as R  # selects the offscreen GL backend before mujoco is imported
import mujoco  # noqa: E402

from mirror.contracts import RolloutFrame, VisualScene  # noqa: E402
from mirror.twin.camera import MujocoView, _Pinhole  # noqa: E402

BG, HOLD_TABLE, HOLD_TRAY, HOLD_WALL, ROBOT, OBJECT, BEAD = 0, 1, 2, 3, 4, 5, 6
HOLDOUT = (BG, HOLD_TABLE, HOLD_TRAY, HOLD_WALL)


def geom_category(name: str) -> int:
    n = name or ""
    if n in ("table", "floor") or not n:
        return HOLD_TABLE
    if n.startswith("tray"):
        return HOLD_TRAY
    if n.startswith("wall") or "/seg" in n:
        return HOLD_WALL
    if n.startswith("gripper"):
        return ROBOT
    if "/bead" in n:
        return BEAD
    return OBJECT


def recoloured(visual: VisualScene, colours: dict[str, Sequence[float]]) -> VisualScene:
    """Copy of ``visual`` with every geom of body ``obj_id`` drawn in ``colours[obj_id]`` (display only)."""
    bodies = []
    for b in visual.bodies:
        c = colours.get(b.name)
        if c is None:
            bodies.append(b)
            continue
        geoms = [g.model_copy(update={"rgba": (float(c[0]), float(c[1]), float(c[2]), 1.0)}) for g in b.geoms]
        bodies.append(b.model_copy(update={"geoms": geoms}))
    return visual.model_copy(update={"bodies": bodies})


@dataclass
class Occluder:
    """A real object that can hide the robot: its pixel mask in the output image and the vertical
    plane (sim) it stands on. A foreground geom inside the mask is hidden when its centre lies on the
    far side of the plane from the camera."""
    mask: np.ndarray                 # bool (H, W) in output pixels
    point: np.ndarray                # a point on the plane (sim)
    normal: np.ndarray               # plane normal (sim)
    label: str = ""


class TwinRenderer:
    def __init__(self, visual: VisualScene, cam: _Pinhole, *, sim_scale: float = 1.0, shadows: bool = True,
                 samples: int = 4):
        self.cam = cam
        self.view: MujocoView = cam.mujoco_view(sim_scale=sim_scale)
        xml = R.display_xml(visual, name_geoms=True, wall_alpha=1.0)
        xml = xml.replace("</worldbody>", self.view.camera_xml("twin_cam") + "\n  </worldbody>")
        # one overhead light: contact shadows straight down read as plausible under unknown room light
        import re
        xml = re.sub(r'<light pos="[^"]*" dir="[^"]*"', '<light pos="0 0 2.5" dir="0 0 -1"', xml, count=1)
        xml = xml.replace('<quality shadowsize="4096"', f'<quality offsamples="{int(samples)}" shadowsize="4096"')
        self.model = mujoco.MjModel.from_xml_string(xml)
        self.model.vis.global_.offwidth = max(self.view.width, int(self.model.vis.global_.offwidth))
        self.model.vis.global_.offheight = max(self.view.height, int(self.model.vis.global_.offheight))
        self.data = mujoco.MjData(self.model)
        self.renderer = mujoco.Renderer(self.model, self.view.height, self.view.width, max_geom=20000)
        self.shadows = shadows
        self.cam_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_CAMERA, "twin_cam")
        self._adr = {}
        for j in range(self.model.njnt):
            self._adr[self.model.body(self.model.jnt_bodyid[j]).name] = int(self.model.jnt_qposadr[j])
        names = [mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_GEOM, g) or "" for g in range(self.model.ngeom)]
        self.geom_names = names
        self.cat = np.array([geom_category(n) for n in names], dtype=np.uint8)
        self.geom_body = [self.model.body(self.model.geom_bodyid[g]).name for g in range(self.model.ngeom)]
        self._ref = None

    # -------------------------------------------------------------- frames
    def _pose(self, frame: Optional[RolloutFrame]) -> None:
        q = self.data.qpos
        if frame is None:            # every moving body far below the floor (empty-table reference)
            for name, a in self._adr.items():
                q[a:a + 3] = (0.0, 0.0, -50.0)
                q[a + 3:a + 7] = (1.0, 0.0, 0.0, 0.0)
        else:
            for name, pose in frame.bodies.items():
                a = self._adr.get(name)
                if a is not None:
                    q[a:a + 3] = pose.pos
                    q[a + 3:a + 7] = pose.quat
        mujoco.mj_kinematics(self.model, self.data)
        mujoco.mj_camlight(self.model, self.data)   # fixed-camera poses are not set by kinematics alone

    def _render(self, segmentation: bool) -> np.ndarray:
        self.renderer.update_scene(self.data, camera=self.cam_id)
        self.renderer.scene.flags[mujoco.mjtRndFlag.mjRND_SHADOW] = int(self.shadows)
        if segmentation:
            self.renderer.enable_segmentation_rendering()
            try:
                return self.renderer.render().copy()
            finally:
                self.renderer.disable_segmentation_rendering()
        return self.renderer.render().copy()

    def reference(self) -> np.ndarray:
        """Shaded render of the empty scene (no moving bodies), in output pixels (float RGB 0-1)."""
        if self._ref is None:
            self._pose(None)
            self._ref = self.view.resample(self._render(False)).astype(np.float32) / 255.0
        return self._ref

    def render(self, frame: RolloutFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(rgb float 0-1, category uint8, geom id int32 (-1 none)) in output pixels."""
        import cv2
        self._pose(frame)
        rgb = self._render(False)
        seg = self._render(True)
        gid = np.where(seg[..., 1] == int(mujoco.mjtObj.mjOBJ_GEOM), seg[..., 0], -1).astype(np.int32)
        rgb_o = self.view.resample(rgb).astype(np.float32) / 255.0
        gid_o = cv2.remap(gid.astype(np.float32), self.view.map_x, self.view.map_y, cv2.INTER_NEAREST,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=-1).astype(np.int32)
        if self.view.valid is not None:
            gid_o[~self.view.valid] = -1
        cat = np.where(gid_o >= 0, self.cat[np.clip(gid_o, 0, None)], BG).astype(np.uint8)
        return rgb_o, cat, gid_o

    def geom_centres(self) -> np.ndarray:
        return self.data.geom_xpos.copy()

    # ------------------------------------------------------------ composite
    def composite(self, plate: np.ndarray, frame: RolloutFrame, *, occluders: Sequence[Occluder] = (),
                  shadow_strength: float = 0.85, edge_soft: float = 0.7, hidden_alpha: float = 0.0
                  ) -> tuple[np.ndarray, dict]:
        """Robot + proxies over ``plate`` (uint8 RGB, output size). Returns (uint8 RGB, info)."""
        import cv2
        ref = self.reference()
        rgb, cat, gid = self.render(frame)
        fg = (cat == ROBOT) | (cat == OBJECT) | (cat == BEAD)
        hidden = np.zeros_like(fg)
        if occluders:
            centres = self.geom_centres()
            C = self.cam.centre
            for oc in occluders:
                side_cam = float(np.dot(C - oc.point, oc.normal))
                far = np.array([np.dot(centres[g] - oc.point, oc.normal) * side_cam < 0 for g in range(len(centres))])
                in_mask = oc.mask & fg
                if in_mask.any():
                    g = gid[in_mask]
                    hide = far[np.clip(g, 0, None)]
                    idx = np.nonzero(in_mask)
                    hidden[idx[0][hide], idx[1][hide]] = True
        vis = fg & ~hidden
        # shadow: how much darker the simulated holdout surface is now than in the empty scene
        lum = rgb.mean(-1)
        lref = np.maximum(ref.mean(-1), 1e-3)
        ratio = np.clip(lum / lref, 0.0, 1.0)
        ratio = np.where(np.isin(cat, HOLDOUT[1:]), ratio, 1.0).astype(np.float32)
        ratio = cv2.GaussianBlur(ratio, (0, 0), 1.2)
        shade = 1.0 - shadow_strength * (1.0 - ratio)
        base = plate.astype(np.float32) / 255.0 * shade[..., None]
        a = vis.astype(np.float32)
        if hidden_alpha > 0:
            a = np.maximum(a, hidden.astype(np.float32) * hidden_alpha)
        if edge_soft > 0:
            a = np.minimum(a, np.clip(cv2.GaussianBlur(a, (0, 0), edge_soft) * 1.6, 0, 1))   # soften inward only: no halo
        out = base * (1 - a[..., None]) + rgb * a[..., None]
        info = {"fg_px": int(fg.sum()), "hidden_px": int(hidden.sum())}
        return np.clip(out * 255.0 + 0.5, 0, 255).astype(np.uint8), info

    def close(self) -> None:
        try:
            self.renderer.close()
        except Exception:
            pass
