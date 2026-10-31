"""Hand-designed reference scenes used by the demonstration and the tests.

Layout convention: a wall at x = 0 whose normal is +x separates the start
region (x < 0, where the kit starts) from the destination region (x > 0,
where the tray is).
"""
from __future__ import annotations

from mirror.contracts import Pose, SceneState, Workspace
from mirror.geometry import WORLD
from mirror.scenes.objects import make_block, make_cup, make_marker, make_tray, make_wall

# The wall reaches past the workspace edge on one side so that the only way
# around it is past its other end.
_FAR = WORLD.workspace_y[1] + 0.10


def default_workspace() -> Workspace:
    return Workspace(x_range=WORLD.workspace_x, y_range=WORLD.workspace_y,
                     z_range=WORLD.workspace_z)


def default_gripper_start() -> Pose:
    return Pose(pos=(-0.15, 0.0, 0.30))


def _kit():
    return [make_cup("cup_0", -0.26, -0.18),
            make_block("block_0", -0.30, 0.06),
            make_marker("marker_0", -0.16, 0.16)]


def reference_scene(name: str) -> SceneState:
    """Return one of the reference scenes: "demo", "shortcut" or "blocked"."""
    if name == "demo":
        # No slot: everything is carried around the +y end of the wall.
        return SceneState(
            scene_id="ref_demo", split="demo", objects=_kit(),
            trays=[make_tray("tray_0", 0.25, 0.18)],
            obstacles=[make_wall("wall_0", 0.0, -_FAR, 0.14)],
            workspace=default_workspace(), gripper_start=default_gripper_start(),
            notes="Demonstration layout: wall without a slot; the operator goes around its +y end.")
    if name == "shortcut":
        # The tray moves next to the cup's line of travel and the wall gains a
        # slot on that line. Going around the +y end keeps the cup upright but
        # is about twice as long.
        return SceneState(
            scene_id="ref_shortcut", split="custom", objects=_kit(),
            trays=[make_tray("tray_0", 0.25, -0.18)],
            obstacles=[make_wall("wall_0", 0.0, -_FAR, 0.14, slots=[(-0.28, -0.08)])],
            workspace=default_workspace(), gripper_start=default_gripper_start(),
            notes="Changed world: the tray moved and the wall has a low slot on the direct line.")
    if name == "blocked":
        # The wall spans the whole workspace; the slot is the only opening.
        return SceneState(
            scene_id="ref_blocked", split="blocked", objects=_kit(),
            trays=[make_tray("tray_0", 0.25, -0.12)],
            obstacles=[make_wall("wall_0", 0.0, -_FAR, _FAR, slots=[(-0.28, -0.08)])],
            workspace=default_workspace(), gripper_start=default_gripper_start(),
            notes="Blocked: the slot is the only connection; the cup cannot pass it upright.")
    raise ValueError(f"unknown reference scene {name!r}; expected demo, shortcut or blocked")
