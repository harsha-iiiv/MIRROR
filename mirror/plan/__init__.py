"""Task and motion planning for MIRROR's kit-packing task family.

Public interface (docs/CONTRACTS.md):

    ground(spec, scene) -> Grounding
    plan(spec, scene, *, prefer=None) -> PlanResult
    candidate_plans(spec, scene, k=2) -> list[PlanResult]
"""
from .config import DEFAULT_CONFIG, PLANNER_NAME, PlannerConfig
from .grounding import Grounding, ground
from .planner import plan, plan_core
from .candidates import candidate_plans

__all__ = ["ground", "plan", "candidate_plans", "Grounding", "PlannerConfig", "DEFAULT_CONFIG",
           "PLANNER_NAME", "plan_core"]
