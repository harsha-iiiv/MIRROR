"""Single trials: plan -> execute -> check."""
from __future__ import annotations

import time
from typing import Callable, Optional

import numpy as np

from mirror.contracts import Constraint, PlanResult, Rollout, RolloutReport, SceneState, TaskSpec
from mirror.statelog import StateLog

from .checker import CHECKER_ID, GROUND_TRUTH_USE, check

Planner = Callable[[TaskSpec, SceneState], PlanResult]


def _default_planner(spec: TaskSpec, scene: SceneState) -> PlanResult:
    from mirror.plan import plan
    return plan(spec, scene)


def _default_env(scene: SceneState):
    from mirror.sim import SimEnv
    return SimEnv(scene, seed=0)


def execute_trial(scene: SceneState, spec: TaskSpec, *, condition: str,
                  planner: Optional[Planner] = None, env_factory=None,
                  frame_hz: float = 30.0, max_sim_time: float = 120.0,
                  extra_rules: Optional[list[Constraint]] = None
                  ) -> tuple[Rollout, Optional[StateLog], PlanResult]:
    """Run one trial and return (rollout, step log or None, plan)."""
    planner = planner or _default_planner
    env_factory = env_factory or _default_env
    t0 = time.perf_counter()
    try:
        plan = planner(spec, scene)
    except Exception as exc:  # a crashing planner is a planning failure, reported as such
        plan = PlanResult(status="error", reason=f"{type(exc).__name__}: {exc}",
                          spec_version=spec.version, scene_id=scene.scene_id)
    if plan.status != "ok":
        report = RolloutReport(
            rollout_id=f"{scene.scene_id}:{condition}", scene_id=scene.scene_id,
            spec_id=spec.spec_id, spec_version=spec.version, condition=condition,
            status="planning_failed", plan_status=plan.status, plan_reason=plan.reason,
            planning_time_s=plan.planning_time_s, wall_time_s=time.perf_counter() - t0,
            checker=CHECKER_ID, ground_truth_use=list(GROUND_TRUTH_USE),
            notes=[f"planner: {plan.status}: {plan.reason}"] +
                  ([f"planner infeasibility record: {plan.infeasibility}"] if plan.infeasibility else []))
        return Rollout(report=report), None, plan
    env = env_factory(scene)
    visual = env.visual_scene() if hasattr(env, "visual_scene") else None
    result = env.execute(plan, frame_hz=frame_hz, max_sim_time=max_sim_time)
    report = check(result.log, spec, scene, condition=condition,
                   execution_status=result.status, execution_reason=result.reason or "",
                   plan=plan, extra_rules=extra_rules,
                   rollout_id=f"{scene.scene_id}:{condition}")
    return _finish(report, result, visual, frame_hz, t0), result.log, plan


def _finish(report, result, visual, frame_hz, t0):
    report.wall_time_s = time.perf_counter() - t0
    return Rollout(report=report, visual=visual, frame_hz=frame_hz, frames=list(result.frames or []))


def run_trial(scene: SceneState, spec: TaskSpec, *, condition: str, **kw) -> Rollout:
    """plan (mirror.plan) -> execute (mirror.sim) -> check. Returns the Rollout."""
    rollout, _, _ = execute_trial(scene, spec, condition=condition, **kw)
    return rollout


def tilt_trace(log: StateLog, object_id: str, hz: float = 20.0) -> list[list[float]]:
    from mirror.statelog import tilt_deg
    if log is None or object_id not in log.object_ids or log.n == 0:
        return []
    k = log.index(object_id)
    stride = max(1, int(round(1.0 / (hz * log.dt))))
    idx = np.arange(0, log.n, stride)
    tl = tilt_deg(log.obj_quat[idx, k])
    return [[round(float(t), 3), round(float(a), 2)] for t, a in zip(log.t[idx], tl)]
