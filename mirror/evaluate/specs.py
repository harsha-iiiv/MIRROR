"""Hand-written reference specification pair for evaluation runs.

These are the original and corrected versions of the kit-packing task in
their canonical form. Runs normally load the specifications produced by an
authoring session (JSON files); these are used when none are given, and the
report records which specifications were evaluated by hash.
"""
from __future__ import annotations

from mirror.contracts import (Constraint, Goal, ObjectBinding, ObjectSelector, Provenance,
                              Status, TaskSpec)
from mirror.geometry import DEFAULT_UPRIGHT_TOL_DEG


def reference_specs() -> tuple[TaskSpec, TaskSpec]:
    bindings = [ObjectBinding(role="container", kind="cup"),
                ObjectBinding(role="part", kind="block"),
                ObjectBinding(role="tool", kind="marker")]
    goals = [Goal(id=f"g_{role}_inside", type="inside", object=ObjectSelector(role=role),
                  provenance=Provenance(source="manual", evidence="reference task"))
             for role in ("container", "part", "tool")]
    final = Constraint(id="c_container_upright_final", type="upright",
                       applies_to=ObjectSelector(role="container"), phase="final",
                       params={"max_tilt_deg": DEFAULT_UPRIGHT_TOL_DEG}, status=Status.confirmed,
                       provenance=Provenance(source="manual", evidence="reference task"),
                       description="The container ends upright in the tray.")
    original = TaskSpec(spec_id="kit_packing_reference", version=1, bindings=bindings, goals=goals,
                        constraints=[final], summary="Pack the cup, block and marker into the tray; "
                        "the cup ends upright.", interpreter="manual")
    transport = Constraint(id="c_container_upright_transport", type="upright",
                           applies_to=ObjectSelector(role="container"), phase="transport",
                           params={"max_tilt_deg": DEFAULT_UPRIGHT_TOL_DEG}, status=Status.confirmed,
                           provenance=Provenance(source="user_correction", version_introduced=2,
                                                 evidence="This container must remain upright "
                                                          "throughout transport, not just at the destination."),
                           description="The container stays upright while it is carried.")
    corrected = original.model_copy(deep=True)
    corrected.version = 2
    corrected.parent_version = 1
    corrected.constraints = [final, transport]
    corrected.summary = original.summary + " The cup stays upright while carried."
    return original, corrected
