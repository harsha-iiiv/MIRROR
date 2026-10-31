"""Reference geometry shared by the simulator, planner and checker.

These numbers define the supported task family. The simulator builds its
MuJoCo model from them, and the planner's collision envelope uses the same
values, so planning and physics agree. Change them only together with the
simulator and planner tests.
"""
from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------- robot ---
# The robot is a free-flying parallel-jaw gripper actuated in six degrees of
# freedom (three prismatic axes and three revolute wrist axes, each driven by
# a position-controlled actuator) plus a two-finger jaw. The arm that would
# carry such a gripper is not modelled: the gripper body is the robot's
# entire collision geometry. This is a stated simplification (see
# docs/CLAIMS.md).
#
# Gripper frame: origin at the tool centre point (TCP), midway between the
# finger pads. Local -z is the approach direction (out of the fingertips),
# so the identity orientation points the fingers straight down. The fingers
# close along local y.


@dataclass(frozen=True)
class GripperGeometry:
    max_opening: float = 0.090          # distance between pads, fully open
    palm_half: tuple = (0.025, 0.055, 0.020)   # box half extents
    palm_center_z: float = 0.075        # palm centre, in gripper frame
    finger_half: tuple = (0.010, 0.006, 0.035)  # each finger box
    finger_center_z: float = 0.020      # finger box centre, in gripper frame
    pad_friction: float = 1.5


GRIPPER = GripperGeometry()

# ------------------------------------------------------------- objects ---


@dataclass(frozen=True)
class CupGeometry:
    outer_half: tuple = (0.030, 0.030, 0.050)
    wall: float = 0.004
    mass: float = 0.08
    beads: int = 8
    bead_radius: float = 0.0075
    bead_mass: float = 0.002


@dataclass(frozen=True)
class BlockGeometry:
    half: tuple = (0.020, 0.020, 0.020)
    mass: float = 0.05


@dataclass(frozen=True)
class MarkerGeometry:
    radius: float = 0.012
    half_length: float = 0.060
    mass: float = 0.03


CUP = CupGeometry()
BLOCK = BlockGeometry()
MARKER = MarkerGeometry()


@dataclass(frozen=True)
class TrayGeometry:
    inner_half: tuple = (0.120, 0.090)
    wall_height: float = 0.030
    wall_thickness: float = 0.008


TRAY = TrayGeometry()

# --------------------------------------------------------------- world ---


@dataclass(frozen=True)
class WorldGeometry:
    table_half: tuple = (0.60, 0.50, 0.02)   # table top surface at z = 0
    workspace_x: tuple = (-0.45, 0.45)
    workspace_y: tuple = (-0.40, 0.40)
    workspace_z: tuple = (0.005, 0.45)
    wall_height: float = 0.50     # taller than the workspace: no route over the top
    wall_thickness: float = 0.020
    # Reference slot: tall enough for the cup carried on its side (about
    # 0.065 m including the jaw), too low for the cup carried upright
    # (about 0.18 m including jaw and palm).
    slot_z: tuple = (0.090, 0.190)
    slot_length: float = 0.200


WORLD = WorldGeometry()

DEFAULT_UPRIGHT_TOL_DEG = 15.0
