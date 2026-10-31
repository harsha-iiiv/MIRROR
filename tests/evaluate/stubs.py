"""Stand-in planner and executor for evaluation-pipeline tests.

They produce synthetic step logs from tests/evaluate/synth.py so the paired
evaluation, statistics and report code can be tested without the simulator.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from mirror.contracts import PlanResult, PlanStep, RouteOption, SceneState, TaskSpec

from .synth import pack_all


def _cup_upright_rule(spec: TaskSpec) -> bool:
    return any(c.type == "upright" and c.phase in ("transport", "always") and
               (c.applies_to.kind == "cup" or c.applies_to.role == "container")
               for c in spec.enforced_constraints())


def stub_planner(spec: TaskSpec, scene: SceneState) -> PlanResult:
    full = any(o.kind == "wall" and o.length >= 0.8 for o in scene.obstacles)
    upright = _cup_upright_rule(spec)
    if full and upright:
        return PlanResult(status="no_plan", reason="every route for cup_0 violates c_upright_transport",
                          routes=[RouteOption(label="through_slot", object_id="cup_0", feasible=False,
                                              rejected_reason="violates c_upright_transport")],
                          infeasibility={"proven": True, "argument": "slot lower than upright cup"},
                          planner="stub", planning_time_s=0.01)
    route = "around_-y" if upright else "through_slot"
    return PlanResult(status="ok", spec_version=spec.version, scene_id=scene.scene_id,
                      steps=[PlanStep(kind="transport", object_id="cup_0", note=route)],
                      routes=[RouteOption(label=route, object_id="cup_0", feasible=True, cost=1.0)],
                      planner="stub", planning_time_s=0.01)


@dataclass
class _Result:
    log: object
    status: str = "completed"
    reason: str = ""
    frames: list = field(default_factory=list)
    sim_time: float = 0.0


class StubEnv:
    fail_seeds: set = set()

    def __init__(self, scene: SceneState):
        self.scene = scene

    def visual_scene(self):
        return None

    def execute(self, plan: PlanResult, frame_hz=30.0, max_sim_time=120.0):
        route = plan.routes[0].label
        b = pack_all(self.scene, cup_route="slot" if route == "through_slot" else "around",
                     marker_tilt=90.0)
        log = b.build()
        seed = self.scene.seed
        if seed in self.fail_seeds and route != "through_slot":
            return _Result(log=log, status="execution_failed", reason="cup dropped during transport")
        return _Result(log=log, sim_time=float(log.t[-1]))
