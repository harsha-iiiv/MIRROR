"""Conditions of registration objective_v1.

Every condition is executed with the same controller (mirror.sim.SimEnv,
seed 0), the same checker and the same scene per seed; conditions differ only
in the planner call:

  original@<lam>   original specification, PlannerConfig(rotation_penalty=lam)
  never_tilt@<lam> conservative baseline: every carried object (cup, block,
                   marker) restricted to upright transport whatever the
                   specification says; the empty gripper's transits are not
                   restricted. Implemented by adding confirmed upright
                   transport rules for every object kind to a copy of the
                   specification given to the planner only; the trial is
                   checked against the unmodified original specification.
  corrected@<lam>  corrected specification (the cup stays upright in transport)
"""
from __future__ import annotations

from typing import Callable

from mirror.contracts import ObjectSelector, Provenance, Status, TaskSpec
from mirror.geometry import DEFAULT_UPRIGHT_TOL_DEG

from .common import config_with, lam_key

KINDS = ("cup", "block", "marker")


def never_tilt_spec(spec: TaskSpec) -> TaskSpec:
    """Copy of ``spec`` with a confirmed upright-in-transport rule for every
    object kind (planning only)."""
    from mirror.contracts import Constraint
    out = spec.model_copy(deep=True)
    for k in KINDS:
        out.constraints.append(Constraint(
            id=f"c_baseline_never_tilt_{k}", type="upright", applies_to=ObjectSelector(kind=k), phase="transport",
            params={"max_tilt_deg": DEFAULT_UPRIGHT_TOL_DEG}, status=Status.confirmed,
            provenance=Provenance(source="manual", evidence="never-tilt planning baseline (objective_v1)"),
            description="Baseline: carried upright."))
    return out


def condition_names(lam_cal: float, sweep=(0.05, 0.10, 0.20, 0.40, 1.0)) -> list[str]:
    return ([f"original@{lam_key(lam_cal)}"] + [f"original@{lam_key(x)}" for x in sweep]
            + [f"never_tilt@{lam_key(lam_cal)}", f"corrected@{lam_key(lam_cal)}", "corrected@0.05"])


def parse(name: str) -> tuple[str, float]:
    kind, lam = name.split("@")
    return kind, float(lam)


def planner_for(name: str, lam_value: float) -> Callable:
    """Planner callable (spec, scene) -> PlanResult for a condition. ``lam_value``
    is the exact rotation price (the name carries a rounded key)."""
    from mirror.plan import plan
    kind, _ = parse(name)
    cfg = config_with(lam_value)
    if kind == "never_tilt":
        return lambda spec, scene: plan(never_tilt_spec(spec), scene, config=cfg)
    return lambda spec, scene: plan(spec, scene, config=cfg)


def spec_for(name: str, original: TaskSpec, corrected: TaskSpec) -> TaskSpec:
    """Specification the trial is CHECKED against (and planned with, except
    never_tilt, which plans with an augmented copy)."""
    return corrected if parse(name)[0] == "corrected" else original
