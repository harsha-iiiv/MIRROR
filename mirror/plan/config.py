"""Planner configuration. One configuration is used for every specification
version so that original and corrected specifications are planned by the
same machinery."""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Mode:
    """Transport orientation mode: the gripper is pitched by ``pitch`` radians
    about its finger closing axis. The held object's tilt equals |pitch|."""

    name: str
    pitch: float

    @property
    def tilt_deg(self) -> float:
        return abs(math.degrees(self.pitch))


UPRIGHT = Mode("upright", 0.0)
PITCHED_POS = Mode("pitched+90", math.pi / 2)
PITCHED_NEG = Mode("pitched-90", -math.pi / 2)


@dataclass(frozen=True)
class PlannerConfig:
    margin: float = 0.010             # obstacle inflation for free-space motion (m)
    fine_margin: float = 0.003        # inflation for approach, grasp, place, release, retreat
    ground_margin: float = 0.004      # clearance from the table in free-space motion
    fine_ground_margin: float = -0.002   # near contact: the held object may rest on the table
    rotation_penalty: float = 0.05    # route cost per radian of gripper rotation (m/rad)
    approach_height: float = 0.060    # pre-grasp height above the grasp pose
    retreat_height: float = 0.060
    place_drop: float = 0.004         # object bottom above the tray floor at release
    tray_wall_clearance: float = 0.006
    place_grid_step: float = 0.010
    slot_extra_clearance: float = 0.005  # added to rotation clearance in front of a slot
    modes: tuple = (UPRIGHT, PITCHED_POS, PITCHED_NEG)
    level_dedupe: float = 0.008
    lazy_iterations: int = 300
    # sampling-based fallback (RRT-Connect over position, yaw and modes)
    rrt_iterations: int = 2500
    rrt_time_budget_s: float = 6.0
    rrt_step: float = 0.05
    rrt_mode_switch_prob: float = 0.15
    rrt_goal_bias: float = 0.10
    rrt_smoothing_iters: int = 150
    rrt_seed: int = 7
    # speed caps written into plan steps (m/s)
    speed_move: float = 0.25
    speed_approach: float = 0.08
    speed_lift: float = 0.08
    speed_transport: float = 0.15
    speed_place: float = 0.05
    speed_retreat: float = 0.10


DEFAULT_CONFIG = PlannerConfig()
PLANNER_NAME = "mirror.plan geometric route planner v1"
