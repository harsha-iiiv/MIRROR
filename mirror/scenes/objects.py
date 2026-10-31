"""Factories for the standard kit objects, tray and walls of the reference world."""
from __future__ import annotations

import math

from mirror.contracts import ObjectSpec, ObstacleSpec, Pose, SlotSpec, TraySpec
from mirror.geometry import BLOCK, CUP, MARKER, TRAY, WORLD


def yaw_quat(yaw: float) -> tuple[float, float, float, float]:
    """Quaternion (w, x, y, z) for a rotation of ``yaw`` radians about world +z."""
    return (math.cos(yaw / 2.0), 0.0, 0.0, math.sin(yaw / 2.0))


def make_cup(obj_id: str, x: float, y: float, yaw: float = 0.0) -> ObjectSpec:
    return ObjectSpec(
        id=obj_id, kind="cup",
        size={"outer_half": list(CUP.outer_half), "wall": CUP.wall},
        mass=CUP.mass, pose=Pose(pos=(x, y, CUP.outer_half[2]), quat=yaw_quat(yaw)),
        beads=CUP.beads, bead_radius=CUP.bead_radius,
        rgba=(0.93, 0.93, 0.90, 1.0))


def make_block(obj_id: str, x: float, y: float, yaw: float = 0.0) -> ObjectSpec:
    return ObjectSpec(
        id=obj_id, kind="block", size={"half": list(BLOCK.half)}, mass=BLOCK.mass,
        pose=Pose(pos=(x, y, BLOCK.half[2]), quat=yaw_quat(yaw)),
        rgba=(0.20, 0.45, 0.85, 1.0))


def make_marker(obj_id: str, x: float, y: float) -> ObjectSpec:
    return ObjectSpec(
        id=obj_id, kind="marker",
        size={"radius": MARKER.radius, "half_length": MARKER.half_length},
        mass=MARKER.mass, pose=Pose(pos=(x, y, MARKER.half_length)),
        rgba=(0.90, 0.35, 0.20, 1.0))


def make_tray(tray_id: str, x: float, y: float, yaw: float = 0.0) -> TraySpec:
    # The tray floor's top surface sits a few millimetres above the table.
    return TraySpec(id=tray_id, pose=Pose(pos=(x, y, TRAY_FLOOR_TOP), quat=yaw_quat(yaw)),
                    inner_half=tuple(TRAY.inner_half), wall_height=TRAY.wall_height,
                    wall_thickness=TRAY.wall_thickness)


TRAY_FLOOR_THICKNESS = 0.006
TRAY_FLOOR_TOP = TRAY_FLOOR_THICKNESS


def make_wall(wall_id: str, x: float, y_min: float, y_max: float,
              slots: list[tuple[float, float]] | None = None) -> ObstacleSpec:
    """A wall perpendicular to world x (its normal is +x) covering y_min..y_max.

    ``slots`` are (y_lo, y_hi) intervals in world y; each becomes a reference
    height slot (WORLD.slot_z). Slot coordinates are stored in the wall's
    local frame, whose y axis coincides with world y here.
    """
    yc = 0.5 * (y_min + y_max)
    spec_slots = [SlotSpec(y_min=lo - yc, y_max=hi - yc, z_min=WORLD.slot_z[0],
                           z_max=WORLD.slot_z[1]) for lo, hi in (slots or [])]
    return ObstacleSpec(id=wall_id, kind="wall", pose=Pose(pos=(x, yc, 0.0)),
                        length=y_max - y_min, height=WORLD.wall_height,
                        thickness=WORLD.wall_thickness, slots=spec_slots)


def make_box(box_id: str, x: float, y: float, half: tuple[float, float, float],
             yaw: float = 0.0) -> ObstacleSpec:
    return ObstacleSpec(id=box_id, kind="box", pose=Pose(pos=(x, y, half[2]), quat=yaw_quat(yaw)),
                        half=tuple(half))
