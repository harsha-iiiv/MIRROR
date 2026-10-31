"""Distinct valid behaviours for the clarification step.

candidate_plans returns the cheapest plan plus up to k-1 alternatives. Each
alternative changes the transport route of one object to its cheapest
feasible route of a different behaviour (route family and maximum tilt).
Every returned plan is valid under the current specification. Alternatives
that would satisfy a proposed (not yet confirmed) upright rule where the
cheapest plan does not, or vice versa, come first, because those are the
differences a human answer can settle; ties are broken by cost.
"""
from __future__ import annotations

from typing import Optional

from ..contracts import PlanResult, SceneState, Status, TaskSpec
from ..geometry import DEFAULT_UPRIGHT_TOL_DEG
from .config import DEFAULT_CONFIG, PlannerConfig
from .planner import assemble, default_choices, feasible_sorted, solve


def _behaviour(c) -> tuple:
    return (c.family, round(c.max_tilt_deg))


def _proposed_upright(spec: TaskSpec, grounding, obj_id: str) -> list[tuple[str, float]]:
    out = []
    for c in spec.constraints:
        if c.status != Status.proposed or c.type != "upright" or c.phase not in ("transport", "always"):
            continue
        if obj_id in grounding.constraint_objects.get(c.id, []):
            out.append((c.id, float(c.params.get("max_tilt_deg", DEFAULT_UPRIGHT_TOL_DEG))))
    return out


def candidate_plans(spec: TaskSpec, scene: SceneState, k: int = 2, *,
                    config: Optional[PlannerConfig] = None) -> list[PlanResult]:
    cfg = config or DEFAULT_CONFIG
    core = solve(spec, scene, cfg)
    base = default_choices(core)
    first = assemble(core, base)
    if first.status != "ok" or k <= 1:
        return [first] if first.status == "ok" else []
    alts = []
    for op in core.objects:
        oid = op.obj.id
        chosen = op.transport[base[oid]]
        seen = {_behaviour(chosen)}
        props = _proposed_upright(spec, core.grounding, oid)
        for i in feasible_sorted(op.transport):
            c = op.transport[i]
            b = _behaviour(c)
            if b in seen:
                continue
            seen.add(b)
            flips = sum(1 for _, lim in props
                        if (chosen.max_tilt_deg > lim) != (c.max_tilt_deg > lim))
            alts.append((-flips, round(c.cost - chosen.cost, 9), oid, c.label, i))
    alts.sort()
    out = [first]
    for _, _, oid, _, i in alts[: k - 1]:
        ch = dict(base)
        ch[oid] = i
        r = assemble(core, ch)
        r.reason = f"alternative for {oid}: {core.objects[[o.obj.id for o in core.objects].index(oid)].transport[i].label} | " + r.reason
        out.append(r)
    return out
