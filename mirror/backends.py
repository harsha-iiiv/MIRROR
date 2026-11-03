"""Execution backends: who carries out a plan in simulation.

  gripper (default, the baseline): the free-flying parallel-jaw gripper of
      :mod:`mirror.sim` (six actuated axes, no arm).
  arm: a UR10e arm carrying the same gripper (:mod:`mirror.arm`): inverse
      kinematics, joint limits, arm/world and self-collision checks, joint
      servo execution.

Both use the same scenes, specifications, task planner and independent
checker; ``execute_trial(..., planner=b.plan, env_factory=b.env)`` runs either.
"""
from __future__ import annotations

from typing import Optional

from mirror.contracts import PlanResult, SceneState, TaskSpec

BACKENDS = ("gripper", "arm")
DEFAULT_BACKEND = "gripper"


class GripperBackend:
    name = "gripper"
    label = "baseline: free-flying gripper (no arm)"

    def plan(self, spec: TaskSpec, scene: SceneState, *, prefer: Optional[str] = None) -> PlanResult:
        from mirror.plan import plan
        return plan(spec, scene, prefer=prefer)

    def env(self, scene: SceneState):
        from mirror.sim import SimEnv
        return SimEnv(scene, seed=0)


def get_backend(name: str = DEFAULT_BACKEND, **kw):
    if name == "gripper":
        return GripperBackend()
    if name == "arm":
        from mirror.arm.backend import ArmBackend
        return ArmBackend(**kw)
    raise ValueError(f"unknown backend {name!r}; expected one of {BACKENDS}")
