"""Task specifications used by the planning tests."""
from __future__ import annotations

from mirror.contracts import (Constraint, Goal, ObjectBinding, ObjectSelector, Provenance, Status,
                              TaskSpec)


def _prov(src="demo_inference", v=1):
    return Provenance(source=src, version_introduced=v)


def base_spec(transport_status: Status | None = None) -> TaskSpec:
    """Pack cup, block and marker into the tray; the cup ends upright.
    ``transport_status`` adds the cup's upright-during-transport rule with that status."""
    cons = [Constraint(id="c_cup_upright_final", type="upright", applies_to=ObjectSelector(kind="cup"),
                       phase="final", params={"max_tilt_deg": 15.0}, status=Status.confirmed,
                       provenance=_prov())]
    if transport_status is not None:
        cons.append(Constraint(id="c_cup_upright_transport", type="upright",
                               applies_to=ObjectSelector(kind="cup"), phase="transport",
                               params={"max_tilt_deg": 15.0}, status=transport_status,
                               provenance=_prov("user_correction", 2)))
    return TaskSpec(
        spec_id="kit_test", version=1 if transport_status is None else 2,
        bindings=[ObjectBinding(role="container", kind="cup"), ObjectBinding(role="part", kind="block"),
                  ObjectBinding(role="pen", kind="marker")],
        goals=[Goal(id=f"g_{k}", type="inside", object=ObjectSelector(kind=k), provenance=_prov())
               for k in ("cup", "block", "marker")],
        constraints=cons)


def original_spec() -> TaskSpec:
    return base_spec(None)


def corrected_spec() -> TaskSpec:
    return base_spec(Status.confirmed)


def proposed_spec() -> TaskSpec:
    return base_spec(Status.proposed)
