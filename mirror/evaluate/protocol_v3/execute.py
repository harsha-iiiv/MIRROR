"""Execution of an authored specification and its independent verdicts.

The same planner, simulator and checker serve every condition. An authored
spec is executed once per scene (identical enforced state on the same scene is
deterministic, so its rollout is reused and marked ``reused``). Each rollout's
step log is then checked twice: under the authored spec (what the system
believed) and under the gold spec (what the author meant). The base spec's
rollout on the same scene is re-checked under the gold spec to show whether
the old behaviour is independently rejected.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from mirror.contracts import PlanResult, SceneState, TaskSpec
from mirror.evaluate.checker import check
from mirror.evaluate.trial import execute_trial

from .edits import state_of


def spec_signature(spec: TaskSpec) -> str:
    return hashlib.sha256(json.dumps(state_of(spec), sort_keys=True).encode()).hexdigest()[:16]


def path_length(plan: Optional[PlanResult]) -> Optional[float]:
    if plan is None or plan.status != "ok":
        return None
    pts = [w.pos for s in plan.steps for w in s.waypoints]
    return round(sum(math.dist(a, b) for a, b in zip(pts, pts[1:])), 4) if len(pts) > 1 else 0.0


def unverified_orderings(rep, spec: TaskSpec) -> list[str]:
    """Confirmed orderings of ``spec`` that the checker's report says nothing
    about. A report that never mentions an ordering id has not verified it, so
    such a requirement can never count as met (it is reported as unknown)."""
    from mirror.contracts import Status
    body = rep.model_dump_json()
    return sorted(o.id for o in spec.ordering if o.status == Status.confirmed and o.id not in body)


def _verdict(rep, spec: Optional[TaskSpec] = None) -> dict[str, Any]:
    return {"status": rep.status, "completion": rep.completion,
            "violations": sorted({v.constraint_id for v in rep.violations}),
            "advisory": sorted({v.constraint_id for v in rep.advisory_violations}),
            "unverified": unverified_orderings(rep, spec) if spec is not None else []}


@dataclass
class Execution:
    signature: str
    plan_status: str
    plan_reason: str
    proven_infeasible: bool
    authored: dict[str, Any]
    gold: Optional[dict[str, Any]]
    route_labels: list[str]
    planning_time_s: float
    sim_time_s: float
    wall_time_s: float
    path_length_m: Optional[float]
    beads: Optional[list[int]] = None       # [in container, total] for the cup, if any
    reused: bool = False

    def public(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class Executor:
    """Caches (scene key, spec signature) -> (rollout report, step log, plan)."""
    frame_hz: float = 1.0          # frames are for display only; verdicts come from the step log
    max_sim_time: float = 120.0
    cache: dict = field(default_factory=dict)

    def _run(self, scene: SceneState, spec: TaskSpec):
        key = (scene.scene_id, spec_signature(spec))
        if key in self.cache:
            return self.cache[key], True
        t0 = time.perf_counter()
        ro, log, plan = execute_trial(scene, spec, condition=f"v3:{key[1]}",
                                      frame_hz=self.frame_hz, max_sim_time=self.max_sim_time)
        self.cache[key] = (ro.report, log, plan, time.perf_counter() - t0)
        return self.cache[key], False

    def execute(self, scene: SceneState, spec: TaskSpec, gold: Optional[TaskSpec]) -> Execution:
        (rep, log, plan, wall), reused = self._run(scene, spec)
        gold_v = None
        if gold is not None and log is not None:
            gold_v = _verdict(check(log, gold, scene, condition="gold", plan=plan,
                                    execution_status=rep.status if rep.status in ("execution_failed", "timeout")
                                    else None), gold)
        cup = next((o for o in rep.outcomes if o.beads_total), None)
        inf = (plan.infeasibility or {}) if plan is not None else {}
        return Execution(
            signature=spec_signature(spec), plan_status=rep.plan_status or (plan.status if plan else ""),
            plan_reason=(rep.plan_reason or "")[:300], proven_infeasible=bool(inf.get("proven")),
            authored=_verdict(rep), gold=gold_v, route_labels=list(rep.route_labels),
            planning_time_s=round(rep.planning_time_s, 3), sim_time_s=round(rep.execution_time_s, 3),
            wall_time_s=round(wall, 3), path_length_m=path_length(plan),
            beads=[cup.beads_in_container, cup.beads_total] if cup else None, reused=reused)

    def old_run_rejected(self, scene: SceneState, base: TaskSpec, gold: TaskSpec) -> dict[str, Any]:
        """Base-spec rollout on this scene: its own verdict and its re-check under gold."""
        (rep, log, plan, _), reused = self._run(scene, base)
        if log is None:
            return {"base_status": rep.status, "under_gold": None, "rejected": None,
                    "note": "base specification did not execute; nothing to re-check"}
        g = check(log, gold, scene, condition="recheck", plan=plan)
        unv = unverified_orderings(g, gold)
        return {"base_status": rep.status, "under_gold": g.status if not unv else f"{g.status} (unverified: {unv})",
                "unverified": unv,
                "rejected": None if unv and g.status == "success" else (rep.status == "success" and g.status != "success"),
                "violations": sorted({v.constraint_id for v in g.violations})}
