"""Manual structured specification editing (the usability pilot's baseline condition).

The form edits the specification directly in the bounded rule language, with no
model involved; each saved form becomes one specification version (record model
``manual-form``). Edit objects:

  {"action": "set_rule", "type": "upright"|"forbidden_contact"|"keep_clearance",
   "object": "cup"|"block"|"marker"|"all", "phase": "transport"|"final"|"always",
   "max_tilt_deg": 15 | "with": ["obstacle", ...] | "min_m": 0.03, "status": "confirmed"|"rejected"}
  {"action": "set_status", "id": "<constraint, ordering or goal id>", "status": "confirmed"|"rejected"|"proposed"}
  {"action": "remove_rule", "id": "<constraint id>"}
  {"action": "add_ordering", "before": "<kind>", "after": "<kind>"}
  {"action": "remove_ordering", "id": "<ordering id>"}

Rule ids follow the interpreter's convention ``c_<type>_<phase>_<kind|all>`` so the
same rule reached by a correction and by the form compares equal.
"""
from __future__ import annotations

from typing import Any

from ..contracts import SpecOp, TaskSpec

KINDS = ("cup", "block", "marker")
TYPES = {"upright": "max_tilt_deg", "forbidden_contact": "with", "keep_clearance": "min_m"}
PHASES = ("transport", "final", "always")
WITH = ("obstacle", "table_outside_tray", "other_objects")
MAX_EDITS = 12


class ManualEditError(ValueError):
    pass


def _param(rtype: str, e: dict) -> dict:
    key = TYPES[rtype]
    if key not in e:
        raise ManualEditError(f"{rtype} needs {key}")
    v = e[key]
    if key == "max_tilt_deg":
        v = float(v)
        if not 1.0 <= v <= 90.0:
            raise ManualEditError("max_tilt_deg is 1-90")
    elif key == "min_m":
        v = float(v)
        if not 0.005 <= v <= 0.2:
            raise ManualEditError("min_m is 0.005-0.2 m")
    else:
        v = [v] if isinstance(v, str) else list(v)
        if not v or any(x not in WITH for x in v):
            raise ManualEditError(f"with is a list of {list(WITH)}")
    return {key: v}


def manual_ops(spec: TaskSpec, edits: list[dict[str, Any]]) -> list[SpecOp]:
    if not isinstance(edits, list) or not edits or len(edits) > MAX_EDITS:
        raise ManualEditError(f"send 1-{MAX_EDITS} edits")
    ids = {c.id for c in spec.constraints} | {o.id for o in spec.ordering} | {g.id for g in spec.goals}
    ops: list[SpecOp] = []
    for e in edits:
        a = e.get("action")
        if a == "set_rule":
            rtype, obj, phase = e.get("type"), e.get("object"), e.get("phase")
            if rtype not in TYPES or obj not in KINDS + ("all",) or phase not in PHASES:
                raise ManualEditError("set_rule needs type, object (cup/block/marker/all) and phase")
            status = e.get("status", "confirmed")
            if status not in ("confirmed", "rejected", "proposed"):
                raise ManualEditError("status is confirmed, rejected or proposed")
            cid = f"c_{rtype}_{phase}_{obj}"
            params = _param(rtype, e)
            if cid in ids:
                ops.append(SpecOp(op="modify_constraint", target_id=cid, value={"params": params, "status": status},
                                  rationale="Manual edit."))
            else:
                sel = {} if obj == "all" else {"kind": obj}
                ops.append(SpecOp(op="add_constraint", value={
                    "id": cid, "type": rtype, "applies_to": sel, "phase": phase, "params": params, "status": status,
                    "provenance": {"source": "manual", "evidence": "manual specification form"},
                    "description": f"{rtype.replace('_', ' ')} for {obj} during {phase}"}, rationale="Manual edit."))
                ids.add(cid)
        elif a == "set_status":
            if e.get("id") not in ids or e.get("status") not in ("confirmed", "rejected", "proposed"):
                raise ManualEditError("set_status needs an existing id and a status")
            ops.append(SpecOp(op="set_status", target_id=e["id"], value={"status": e["status"]}, rationale="Manual edit."))
        elif a == "remove_rule":
            if e.get("id") not in {c.id for c in spec.constraints}:
                raise ManualEditError("remove_rule needs an existing constraint id")
            ops.append(SpecOp(op="remove_constraint", target_id=e["id"], rationale="Manual edit."))
        elif a == "add_ordering":
            b, f = e.get("before"), e.get("after")
            if b not in KINDS or f not in KINDS or b == f:
                raise ManualEditError("add_ordering needs two different object kinds")
            oid = f"o_{b}_before_{f}"
            if oid in ids:
                ops.append(SpecOp(op="set_status", target_id=oid, value={"status": "confirmed"}, rationale="Manual edit."))
            else:
                ops.append(SpecOp(op="add_ordering", value={
                    "id": oid, "before": {"kind": b}, "after": {"kind": f}, "status": "confirmed",
                    "provenance": {"source": "manual", "evidence": "manual specification form"}},
                    rationale="Manual edit."))
                ids.add(oid)
        elif a == "remove_ordering":
            if e.get("id") not in {o.id for o in spec.ordering}:
                raise ManualEditError("remove_ordering needs an existing ordering id")
            ops.append(SpecOp(op="remove_ordering", target_id=e["id"], rationale="Manual edit."))
        else:
            raise ManualEditError(f"unknown action {a!r}")
    return ops


def apply_manual(spec: TaskSpec, edits: list[dict], note: str = "", fixture: bool = False):
    """(new spec, record, validation errors). Nothing is committed here."""
    ops = manual_ops(spec, edits)
    text = note[:300] or "manual specification edit"
    if fixture:
        from .fixtures import _apply
        new, rec = _apply(spec, ops, text, "correction")
        rec.model = "manual-form"
        return new, rec, []
    from ..interpret import validate_spec
    from ..interpret.specops import apply_ops
    try:
        new, rec = apply_ops(spec, ops, text=text, kind="correction", model="manual-form")
    except Exception as exc:  # noqa: BLE001 - InterpretError: the form produced an invalid spec
        return None, None, [str(exc)]
    return new, rec, validate_spec(new)
