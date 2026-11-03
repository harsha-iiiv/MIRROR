"""The structured-edit language of the manual baseline and the gold.

A StructuredEdit is turned into ordinary SpecOps and applied with the same
deterministic ``apply_ops`` the correction compiler uses, so every condition
produces a TaskSpec version through one code path.
"""
from __future__ import annotations

from typing import Any, Optional

from mirror.contracts import SpecOp, Status, TaskSpec
from mirror.interpret.specops import (apply_ops, constraint_id, find_item, goal_id, ordering_id)

from .schema import StructuredEdit


def _params(rule) -> dict[str, Any]:
    if rule.type == "upright":
        return {"max_tilt_deg": float(rule.max_tilt_deg if rule.max_tilt_deg is not None else 15.0)}
    if rule.type == "forbidden_contact":
        return {"with": sorted(rule.with_groups or [])}
    return {"min_m": float(rule.min_m)}


def edit_to_ops(spec: TaskSpec, edit: StructuredEdit) -> list[SpecOp]:
    """SpecOps that make ``spec`` match ``edit``. Raises ValueError for an edit
    that refers to an item that does not exist and cannot be created."""
    ops: list[SpecOp] = []
    for rule in edit.rules:
        kind = None if rule.object == "all" else rule.object
        cid = constraint_id(rule.type, rule.phase, kind)
        existing = find_item(spec, cid)
        if existing is not None:
            val: dict[str, Any] = {"status": rule.status}
            if rule.status == "confirmed":
                val["params"] = _params(rule)
            ops.append(SpecOp(op="modify_constraint", target_id=cid, value=val, rationale="structured edit"))
        elif rule.status == "confirmed":
            ops.append(SpecOp(op="add_constraint", value={
                "id": cid, "type": rule.type, "applies_to": {"kind": kind} if kind else {},
                "phase": rule.phase, "params": _params(rule), "status": "confirmed",
                "provenance": {"source": "manual", "evidence": "structured edit"},
                "description": f"{rule.type} {rule.object} {rule.phase}"}, rationale="structured edit"))
        # rejecting a rule that does not exist is a no-op
    for o in edit.orderings:
        oid = ordering_id(o.before, o.after)
        if find_item(spec, oid) is not None:
            ops.append(SpecOp(op="set_status", target_id=oid, value={"status": o.status}, rationale="structured edit"))
        elif o.status == "confirmed":
            ops.append(SpecOp(op="add_ordering", value={
                "id": oid, "before": {"kind": o.before}, "after": {"kind": o.after}, "status": "confirmed",
                "provenance": {"source": "manual", "evidence": "structured edit"}}, rationale="structured edit"))
    for g in edit.goals:
        gid = goal_id(g.object)
        if find_item(spec, gid) is None:
            raise ValueError(f"no goal {gid} in the specification")
        ops.append(SpecOp(op="set_status", target_id=gid, value={"status": g.status}, rationale="structured edit"))
    return ops


def apply_edit(spec: TaskSpec, edit: StructuredEdit, *, text: str, model: Optional[str] = None):
    """(new spec, record). Raises mirror.interpret.InterpretError on an invalid result."""
    return apply_ops(spec, edit_to_ops(spec, edit), text=text, kind="correction", model=model)


def _norm(params: dict) -> dict:
    out = {}
    for k, v in params.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = round(float(v), 6)
        elif isinstance(v, list):
            out[k] = sorted(v)
        else:
            out[k] = v
    return out


def state_of(spec: TaskSpec) -> dict[str, Any]:
    """What a correction can change and what is scored: enforced rules with
    params, confirmed orderings, rejected goals."""
    return {
        "enforced": {c.id: _norm(c.params) for c in spec.enforced_constraints()},
        "ordering": sorted(o.id for o in spec.ordering if o.status == Status.confirmed),
        "inactive_goals": sorted(g.id for g in spec.goals if g.status == Status.rejected),
    }


def semantic_state(spec_or_state) -> dict[str, Any]:
    """``state_of`` with every-object rules expanded to each object kind (the
    stricter limit wins where a per-kind rule also exists). Two specifications
    that enforce the same behaviour on every kind compare equal, whether they
    say "all objects" or name each one."""
    from .schema import KINDS
    st = spec_or_state if isinstance(spec_or_state, dict) else state_of(spec_or_state)
    out: dict[str, dict] = {}

    def merge(cid: str, p: dict) -> None:
        if cid not in out:
            out[cid] = dict(p)
            return
        q = out[cid]
        if "max_tilt_deg" in p:
            q["max_tilt_deg"] = min(q.get("max_tilt_deg", p["max_tilt_deg"]), p["max_tilt_deg"])
        if "min_m" in p:
            q["min_m"] = max(q.get("min_m", p["min_m"]), p["min_m"])
        if "with" in p:
            q["with"] = sorted(set(q.get("with", [])) | set(p["with"]))
    for cid, p in st["enforced"].items():
        head, kind = cid.rsplit("_", 1)
        for k in (KINDS if kind == "all" else (kind,)):
            merge(f"{head}_{k}", p)
    return {"enforced": dict(sorted(out.items())), "ordering": list(st["ordering"]),
            "inactive_goals": list(st["inactive_goals"])}


def _kind(item_id: str) -> str:
    return item_id.rsplit("_", 1)[1]


def changed_kinds(a: dict, b: dict) -> set[str]:
    """Object kinds ("all" for every-object rules) whose scored state differs."""
    ea, eb = a["enforced"], b["enforced"]
    kinds = {_kind(cid) for cid in set(ea) | set(eb) if ea.get(cid) != eb.get(cid)}
    for item in set(a["inactive_goals"]) ^ set(b["inactive_goals"]):
        kinds.add(_kind(item))
    for item in set(a["ordering"]) ^ set(b["ordering"]):
        parts = item.split("_")          # o_<before>_before_<after>
        kinds |= {parts[1], parts[3]}
    return kinds
