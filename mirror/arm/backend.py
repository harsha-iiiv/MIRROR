"""The arm backend: the planner's decisions, executed by an articulated arm.

``ArmBackend.plan(spec, scene)`` runs the unchanged task planner
(:mod:`mirror.plan`), so the specification decides object order, admissible
routes and orientation modes exactly as for the baseline backend. It then asks
whether the arm can execute the chosen plan (``motion.validate``: inverse
kinematics, joint ranges, arm/world and self-collision along every TCP
segment). If not, it tries, in this order and within a fixed budget of
validation attempts:

  1. the same plan with the free-space legs carried higher (``refine``),
  2. both with the tool turned by pi about its axis (``motion.flip_tool``),
  3. the planner's next-cheapest admissible route for the motion that failed
     (the failed route is marked infeasible "for the arm" with the reason,
     and the planner's own assembly picks the next one). When the arm
     collided passing a wall slot, the other candidates through that slot are
     skipped (a search heuristic, recorded as "not attempted").

Outcomes and how they are labelled:

  * the planner itself finds no plan: passed through unchanged, including a
    proven infeasibility certificate when the planner has one. A certificate
    about the gripper and the carried object also holds for the arm, which
    carries the same gripper and object along planner TCP paths and only adds
    constraints;
  * the planner has plans but none validates for the arm: ``no_plan`` with
    ``infeasibility = {"proven": False, "kind": "arm_motion_planning_failure"}``
    and the reason (IK failure, joint range, collision, or search budget
    exhausted). This is a planning failure of this backend, never a proof.
"""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from typing import Optional

from mirror.contracts import PlanResult, SceneState, TaskSpec
from mirror.plan.config import PLANNER_NAME

from .env import ArmEnv
from .model import DEFAULT_MOUNT, Mount
from .motion import ArmTrajectory, Failure, Validator, plan_key, validate
from .refine import raise_free_space

BACKEND_ID = "mirror.arm/1"
CARRY_HEIGHTS = (None, 0.40)    # None: as planned
MAX_ATTEMPTS = 40               # validation attempts per plan() call
MAX_ROUTE_SWITCHES = 8


@dataclass
class _Choice:
    traj: ArmTrajectory
    flip: bool


def _slot_portal(label: str) -> Optional[str]:
    """"<wall>:<slot>" for a route through a slot, else None."""
    parts = label.split(":")
    if "through_slot" in parts:
        i = parts.index("through_slot")
        if len(parts) >= i + 3:
            return f"{parts[i + 1]}:{parts[i + 2]}"
    return None


def _planner_label(mount: Mount) -> str:
    return f"{PLANNER_NAME} + {BACKEND_ID} ({mount.spec.name} motion validation)"


class ArmBackend:
    """Planner + arm motion validation + arm execution."""

    name = "arm"
    label = "articulated arm (UR10e, MuJoCo)"

    def __init__(self, mount: Mount = DEFAULT_MOUNT, *, max_attempts: int = MAX_ATTEMPTS,
                 carry_heights: tuple = CARRY_HEIGHTS):
        self.mount = mount
        self.max_attempts = max_attempts
        self.carry_heights = carry_heights
        self._cache: dict[str, _Choice] = {}

    # ------------------------------------------------------------ cache ---
    def cached(self, plan: PlanResult, scene: SceneState):
        hit = self._cache.get(plan_key(plan, scene, self.mount))
        return None if hit is None else (hit.traj, hit.flip)

    def env(self, scene: SceneState) -> ArmEnv:
        return ArmEnv(scene, mount=self.mount, backend=self)

    # --------------------------------------------------------- planning ---
    def plan(self, spec: TaskSpec, scene: SceneState, *, prefer: Optional[str] = None) -> PlanResult:
        t0 = time.perf_counter()
        from mirror.plan.config import DEFAULT_CONFIG
        from mirror.plan.planner import assemble, default_choices, feasible_sorted, plan_core
        try:
            core = plan_core(spec, scene, DEFAULT_CONFIG)
            base = assemble(core, default_choices(core, prefer))
        except Exception as exc:   # report, never hide
            return PlanResult(status="error", reason=f"{type(exc).__name__}: {exc}", spec_version=spec.version,
                              scene_id=scene.scene_id, planner=_planner_label(self.mount),
                              planning_time_s=round(time.perf_counter() - t0, 4))
        if base.status != "ok":
            base.planner = _planner_label(self.mount)
            base.reason = "planner (before arm validation): " + base.reason
            base.planning_time_s = round(time.perf_counter() - t0, 4)
            return base
        masked = copy.deepcopy(core)
        v = Validator(scene, self.mount)
        attempts = 0
        switches = 0
        history: list[str] = []
        last_failure: Optional[Failure] = None
        while True:
            # every object needs a feasible transit and transport after masking
            missing = [op.obj.id for op in masked.objects
                       if not feasible_sorted(op.transit) or not feasible_sorted(op.transport)]
            if missing:
                return self._fail(base, masked, assemble, default_choices, prefer, t0, attempts, history,
                                  last_failure, budget=False,
                                  why=f"no remaining admissible route for {', '.join(missing)} is executable by the arm")
            choices = default_choices(masked, prefer)
            pr = assemble(masked, choices)
            if pr.status != "ok":   # pragma: no cover - guarded above
                return self._fail(base, masked, assemble, default_choices, prefer, t0, attempts, history,
                                  last_failure, budget=False, why=pr.reason)
            best: Optional[Failure] = None
            for z in self.carry_heights:
                cand, notes = (pr, []) if z is None else raise_free_space(pr, scene.gripper_start, z)
                if z is not None and not notes:
                    continue
                for flip in (False, True):
                    if attempts >= self.max_attempts:
                        return self._fail(base, masked, assemble, default_choices, prefer, t0, attempts,
                                          history, best or last_failure, budget=True,
                                          why="search budget exhausted")
                    attempts += 1
                    tr = validate(cand, scene, self.mount, validator=v, flip=flip)
                    variant = f"{'as planned' if z is None else f'carried at z={z:.2f} m'}" + \
                              (", tool turned" if flip else "")
                    if tr.ok:
                        return self._success(cand, tr, flip, notes, variant, history, attempts, scene, t0)
                    history.append(f"{variant}: {tr.failure.text()}")
                    if best is None or tr.failure.progress > best.progress:
                        best = tr.failure
            last_failure = best
            # attribute the failure to the route that was being executed
            step = pr.steps[best.step_index] if 0 <= best.step_index < len(pr.steps) else None
            op = next((o for o in masked.objects if step is not None and o.obj.id == step.object_id), None)
            reason = f"arm backend ({self.mount.spec.name}): {best.text()}"
            if op is None or step.kind not in ("move", "lift", "transport", "place") \
                    or switches >= MAX_ROUTE_SWITCHES:
                why = ("the failing motion is not a route choice (grasp, release or start pose)"
                       if switches < MAX_ROUTE_SWITCHES else "route-switch budget exhausted")
                return self._fail(base, masked, assemble, default_choices, prefer, t0, attempts, history, best,
                                  budget=switches >= MAX_ROUTE_SWITCHES, why=why)
            if step.kind == "move":
                i = feasible_sorted(op.transit)[0]
                failed = op.transit[i]
            else:
                i = choices[op.obj.id]
                failed = op.transport[i]
            failed.feasible = False
            failed.reason = reason
            switches += 1
            portal = _slot_portal(failed.label)
            if portal and best.kind == "collision":
                # Search heuristic (budget, not a proof): skip every other candidate
                # through the same slot once the arm collided passing it.
                for o in masked.objects:
                    for c in o.transit + o.transport:
                        if c.feasible and _slot_portal(c.label) == portal:
                            c.feasible = False
                            c.reason = (f"arm backend ({self.mount.spec.name}): not attempted; the arm collided "
                                        f"passing the same slot ({portal}) for {op.obj.id}")

    # ---------------------------------------------------------- results ---
    def _success(self, plan: PlanResult, tr: ArmTrajectory, flip: bool, notes: list[str], variant: str,
                 history: list[str], attempts: int, scene: SceneState, t0: float) -> PlanResult:
        out = plan.model_copy(deep=True)
        out.planner = _planner_label(self.mount)
        extra = [f"arm: executable {variant}; start branch {tr.branch + 1}; {attempts} validation attempt(s)"]
        extra += [f"arm: {n}" for n in notes]
        if history:
            extra.append("arm: rejected earlier variants: " + " || ".join(history[-4:]))
        out.reason = out.reason + " | " + "; ".join(extra)
        out.planning_time_s = round(time.perf_counter() - t0, 4)
        self._cache[plan_key(out, scene, self.mount)] = _Choice(tr, flip)
        return out

    def _fail(self, base, masked, assemble, default_choices, prefer, t0, attempts, history,
              failure: Optional[Failure], *, budget: bool, why: str) -> PlanResult:
        try:
            shown = assemble(masked, default_choices(masked, prefer))
            routes = shown.routes
        except Exception:     # a masked core may not assemble; fall back to the planner's view
            routes = base.routes
        detail = failure.text() if failure else "no detail"
        kind = failure.kind if failure else "unknown"
        reason = (f"arm motion planning failed: {why}; last failure ({kind}): {detail}. "
                  "The task planner did find plans for the gripper; this is not a proof that no arm "
                  "motion exists")
        return PlanResult(
            status="no_plan", reason=reason, spec_version=base.spec_version, scene_id=base.scene_id,
            steps=[], object_order=base.object_order, routes=routes,
            planning_time_s=round(time.perf_counter() - t0, 4), planner=_planner_label(self.mount),
            infeasibility={"proven": False, "kind": "arm_motion_planning_failure", "backend": BACKEND_ID,
                           "robot": self.mount.spec.name, "failure_kind": kind, "failure": detail,
                           "search_budget_exhausted": bool(budget), "validation_attempts": attempts,
                           "attempt_budget": self.max_attempts, "attempts": history[-8:],
                           "statement": "No executable arm motion was found within the search; "
                                        "this is a planning failure, not a proof of infeasibility."})
