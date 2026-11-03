"""How a planning result may be described to a person.

Only a result carrying a valid infeasibility certificate may be shown as
"proven infeasible under these stated assumptions", together with those
assumptions. Any other failure is "the planner found no compliant route",
which is not a proof. Neither is a general physical-safety guarantee.
"""
from __future__ import annotations

from typing import Any, Optional

from mirror.contracts import PlanResult

CERTIFICATE_KINDS = {"separating_wall_slot_clearance", "clearance_at_start"}
NOT_A_SAFETY_GUARANTEE = ("This is a statement about the simulated task model under the listed assumptions, "
                          "not a guarantee about physical safety.")


def certificate_is_valid(cert: Optional[dict]) -> bool:
    return bool(cert) and cert.get("proven") is True and cert.get("kind") in CERTIFICATE_KINDS \
        and bool(cert.get("statement")) and bool(cert.get("assumptions"))


def planning_outcome(plan: PlanResult) -> dict[str, Any]:
    """{kind: planned | proven_infeasible | no_route_found | invalid_specification | error,
        headline, detail, assumptions, not_covered, certificate}"""
    base = {"assumptions": [], "not_covered": [], "certificate": None, "detail": plan.reason}
    if plan.status == "ok":
        return {**base, "kind": "planned", "headline": "Planned"}
    if plan.status == "invalid_spec":
        return {**base, "kind": "invalid_specification", "headline": "The specification cannot be planned as written"}
    if plan.status == "error":
        return {**base, "kind": "error", "headline": "The planner failed with an error"}
    cert = plan.infeasibility
    if certificate_is_valid(cert):
        return {**base, "kind": "proven_infeasible",
                "headline": "Proven infeasible under these stated assumptions",
                "detail": cert["statement"] + " " + NOT_A_SAFETY_GUARANTEE,
                "assumptions": list(cert["assumptions"]), "not_covered": list(cert.get("not_covered", [])),
                "certificate": cert}
    return {**base, "kind": "no_route_found",
            "headline": "Planner found no compliant route",
            "detail": "The planner searched and found none; this is not a proof that none exists. " + plan.reason}
