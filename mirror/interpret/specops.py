"""Deterministic operations on task specifications.

Model output never edits a TaskSpec directly. It is compiled into a list of
``SpecOp`` values, which this module validates and applies to produce the next
version. Applying the same ops to the same spec always gives the same result.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from mirror.contracts import (Constraint, CorrectionRecord, Goal, ObjectSelector,
                              OrderingConstraint, SpecOp, Status, TaskSpec)
from mirror.interpret.types import InterpretError
from mirror.rules_support import unsupported_reason

CONTACT_GROUPS = ("obstacle", "table_outside_tray", "other_objects")


# ------------------------------------------------------------------ ids ---

def goal_id(kind: str) -> str:
    return f"g_inside_{kind}"


def constraint_id(ctype: str, phase: str, kind: Optional[str]) -> str:
    return f"c_{ctype}_{phase}_{kind or 'all'}"


def ordering_id(before: str, after: str) -> str:
    return f"o_{before}_before_{after}"


def uncertainty_id(related: str) -> str:
    return f"u_{related.split('_', 1)[1] if '_' in related else related}"


# ------------------------------------------------------------ queries ---

def find_item(spec: TaskSpec, item_id: str):
    for coll in (spec.constraints, spec.goals, spec.ordering, spec.uncertainties):
        for it in coll:
            if it.id == item_id:
                return it
    return None


def effective_kind(sel: ObjectSelector, spec: TaskSpec) -> Optional[str]:
    """The object kind a selector names, resolving a role through the bindings.
    A selector covers every object of that kind in the scene."""
    if sel.kind:
        return sel.kind
    if sel.role:
        for b in spec.bindings:
            if b.role == sel.role:
                return b.kind
    return None


def find_constraint(spec: TaskSpec, ctype: str, phase: str, kind: Optional[str]) -> Optional[Constraint]:
    for c in spec.constraints:
        if c.type == ctype and c.phase == phase and effective_kind(c.applies_to, spec) == kind:
            return c
    return None


def find_goal(spec: TaskSpec, kind: str):
    for g in spec.goals:
        if effective_kind(g.object, spec) == kind:
            return g
    return None


def active_goal_kinds(spec: TaskSpec) -> list[str]:
    out = []
    for g in spec.goals:
        k = effective_kind(g.object, spec)
        if k and g.status != Status.rejected and k not in out:
            out.append(k)
    return out


def selector_kinds(sel: ObjectSelector, spec: TaskSpec) -> set[str]:
    kinds = {b.kind for b in spec.bindings}
    if sel.kind:
        return {sel.kind} & kinds
    if sel.role:
        return {b.kind for b in spec.bindings if b.role == sel.role}
    return kinds


# --------------------------------------------------------- validation ---

def validate_spec(spec: TaskSpec) -> list[str]:
    """Return a list of problems; an empty list means the spec is valid."""
    errs: list[str] = []
    if spec.version < 1:
        errs.append("version must be >= 1")
    if spec.parent_version is not None and spec.parent_version >= spec.version:
        errs.append("parent_version must be lower than version")
    if not spec.bindings:
        errs.append("spec has no object bindings")
    roles = [b.role for b in spec.bindings]
    if len(set(roles)) != len(roles):
        errs.append("binding roles must be unique")
    for b in spec.bindings:
        if b.count < 1:
            errs.append(f"binding {b.role}: count must be >= 1")
    bound_kinds = {b.kind for b in spec.bindings}
    bound_roles = set(roles)

    ids: list[str] = []
    for coll in (spec.goals, spec.constraints, spec.ordering, spec.uncertainties):
        ids += [it.id for it in coll]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        errs.append(f"duplicate ids: {dup}")

    def check_sel(where: str, sel: ObjectSelector, allow_all: bool) -> None:
        if sel.kind and sel.kind not in bound_kinds:
            errs.append(f"{where}: kind {sel.kind!r} is not bound in the spec")
        if sel.role and sel.role not in bound_roles:
            errs.append(f"{where}: role {sel.role!r} is not bound in the spec")
        if sel.kind and sel.role:
            match = [b for b in spec.bindings if b.role == sel.role]
            if match and match[0].kind != sel.kind:
                errs.append(f"{where}: role {sel.role!r} is bound to {match[0].kind}, not {sel.kind}")
        if not allow_all and not (sel.kind or sel.role or sel.ids):
            errs.append(f"{where}: selector must name a kind, role or ids")

    if not spec.goals:
        errs.append("spec has no goals")
    for g in spec.goals:
        check_sel(f"goal {g.id}", g.object, allow_all=False)
        if g.target != "destination":
            errs.append(f"goal {g.id}: only the 'destination' tray role is supported")

    for c in spec.constraints:
        where = f"constraint {c.id}"
        check_sel(where, c.applies_to, allow_all=True)
        if c.type == "upright":
            v = c.params.get("max_tilt_deg")
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not (0 < v <= 180):
                errs.append(f"{where}: upright needs params.max_tilt_deg in (0, 180]")
            extra = set(c.params) - {"max_tilt_deg"}
            if extra:
                errs.append(f"{where}: unknown params {sorted(extra)}")
        elif c.type == "forbidden_contact":
            w = c.params.get("with")
            if not isinstance(w, list) or not w or any(x not in CONTACT_GROUPS for x in w):
                errs.append(f"{where}: forbidden_contact needs params.with, a non-empty subset of {list(CONTACT_GROUPS)}")
        elif c.type == "keep_clearance":
            m = c.params.get("min_m")
            if not isinstance(m, (int, float)) or isinstance(m, bool) or not (0 < m <= 0.5):
                errs.append(f"{where}: keep_clearance needs params.min_m in (0, 0.5]")
            extra = set(c.params) - {"min_m", "from"}
            if extra:
                errs.append(f"{where}: unknown params {sorted(extra)}")
        if c.status != Status.rejected:
            why = unsupported_reason(c)
            if why:
                errs.append(f"{where}: unsupported rule: {why}")

    seen: dict[tuple, str] = {}
    for c in spec.constraints:
        if c.status != Status.confirmed:
            continue
        key = (c.type, c.phase, c.applies_to.kind, c.applies_to.role,
               tuple(c.applies_to.ids or ()))
        if key in seen:
            errs.append(f"constraints {seen[key]} and {c.id} are both confirmed for the same rule")
        seen[key] = c.id

    edges: dict[str, set[str]] = {}
    for o in spec.ordering:
        where = f"ordering {o.id}"
        check_sel(where, o.before, allow_all=False)
        check_sel(where, o.after, allow_all=False)
        if o.before == o.after:
            errs.append(f"{where}: before and after select the same objects")
        if o.status != Status.rejected:
            why = unsupported_reason(o)
            if why:
                errs.append(f"{where}: unsupported rule: {why}")
        if o.status == Status.confirmed and o.before.kind and o.after.kind:
            edges.setdefault(o.before.kind, set()).add(o.after.kind)
    if _has_cycle(edges):
        errs.append("confirmed ordering constraints form a cycle")

    item_ids = {it.id for coll in (spec.goals, spec.constraints, spec.ordering) for it in coll}
    for u in spec.uncertainties:
        if u.related_constraint and u.related_constraint not in item_ids:
            errs.append(f"uncertainty {u.id}: related item {u.related_constraint!r} does not exist")
        if u.status == "resolved" and not u.resolution:
            errs.append(f"uncertainty {u.id}: resolved without a resolution")
    last = 0
    for h in spec.history:
        if h.version <= last or h.version > spec.version:
            errs.append("history versions must increase and not exceed the spec version")
            break
        last = h.version
    return errs


def _has_cycle(edges: dict[str, set[str]]) -> bool:
    state: dict[str, int] = {}

    def visit(n: str) -> bool:
        state[n] = 1
        for m in edges.get(n, ()):
            if state.get(m) == 1 or (state.get(m) is None and visit(m)):
                return True
        state[n] = 2
        return False

    return any(state.get(n) is None and visit(n) for n in list(edges))


# ----------------------------------------------------------- applying ---

def _status(v: Any) -> Status:
    try:
        return Status(v)
    except ValueError as e:
        raise InterpretError(f"unknown status {v!r}") from e


def apply_ops(spec: TaskSpec, ops: Iterable[SpecOp], *, text: str, kind: str,
              model: Optional[str] = None) -> tuple[TaskSpec, CorrectionRecord]:
    """Apply ``ops`` to a copy of ``spec`` and return version + 1 with the
    correction recorded in its history. Raises InterpretError on any op that
    does not fit the spec, and on an invalid result."""
    ops = list(ops)
    new = spec.model_copy(deep=True)
    new.version = spec.version + 1
    new.parent_version = spec.version
    v = new.version
    for op in ops:
        _apply_one(new, op, v)
    record = CorrectionRecord(version=v, text=text, kind=kind, diff=ops, model=model)
    new.history = list(new.history) + [record]
    errs = validate_spec(new)
    if errs:
        raise InterpretError("result is not a valid spec: " + "; ".join(errs))
    return new, record


def _apply_one(spec: TaskSpec, op: SpecOp, version: int) -> None:
    val = dict(op.value or {})
    t = op.target_id
    if op.op == "add_constraint":
        val.setdefault("provenance", {"source": "user_correction"})
        val["provenance"] = {**val["provenance"], "version_introduced": version}
        c = Constraint.model_validate(val)
        if find_item(spec, c.id):
            raise InterpretError(f"add_constraint: id {c.id} already exists")
        spec.constraints.append(c)
    elif op.op == "modify_constraint":
        c = _get(spec.constraints, t, op.op)
        if "params" in val:
            c.params = {**c.params, **val.pop("params")}
        for k in ("description", "phase", "applies_to"):
            if k in val:
                setattr(c, k, ObjectSelector.model_validate(val.pop(k)) if k == "applies_to" else val.pop(k))
        if "status" in val:
            c.status = _status(val.pop("status"))
        if val:
            raise InterpretError(f"modify_constraint: unsupported fields {sorted(val)}")
    elif op.op == "remove_constraint":
        spec.constraints.remove(_get(spec.constraints, t, op.op))
        _drop_uncertainties(spec, t)
    elif op.op == "set_status":
        item = None
        for coll in (spec.constraints, spec.goals, spec.ordering):
            item = next((x for x in coll if x.id == t), None) or item
        if item is None:
            raise InterpretError(f"set_status: no goal, constraint or ordering with id {t!r}")
        item.status = _status(val.get("status"))
    elif op.op == "add_goal":
        val.setdefault("provenance", {"source": "user_correction"})
        val["provenance"] = {**val["provenance"], "version_introduced": version}
        g = Goal.model_validate(val)
        if find_item(spec, g.id):
            raise InterpretError(f"add_goal: id {g.id} already exists")
        spec.goals.append(g)
    elif op.op == "remove_goal":
        spec.goals.remove(_get(spec.goals, t, op.op))
        _drop_uncertainties(spec, t)
    elif op.op == "add_ordering":
        val.setdefault("provenance", {"source": "user_correction"})
        val["provenance"] = {**val["provenance"], "version_introduced": version}
        o = OrderingConstraint.model_validate(val)
        if find_item(spec, o.id):
            raise InterpretError(f"add_ordering: id {o.id} already exists")
        spec.ordering.append(o)
    elif op.op == "remove_ordering":
        spec.ordering.remove(_get(spec.ordering, t, op.op))
        _drop_uncertainties(spec, t)
    elif op.op == "resolve_uncertainty":
        u = _get(spec.uncertainties, t, op.op)
        u.status = "resolved"
        u.resolution = str(val.get("resolution") or op.rationale or "resolved")
    else:  # pragma: no cover - SpecOp's Literal prevents this
        raise InterpretError(f"unsupported op {op.op}")


def _get(coll, item_id, opname):
    for it in coll:
        if it.id == item_id:
            return it
    raise InterpretError(f"{opname}: no item with id {item_id!r}")


def _drop_uncertainties(spec: TaskSpec, item_id: str) -> None:
    spec.uncertainties = [u for u in spec.uncertainties if u.related_constraint != item_id]


def resolution_ops(spec: TaskSpec, touched: Iterable[str], resolution: str) -> list[SpecOp]:
    """resolve_uncertainty ops for open questions about any touched item."""
    touched = set(touched)
    return [SpecOp(op="resolve_uncertainty", target_id=u.id, value={"resolution": resolution},
                   rationale="answered by this change")
            for u in spec.uncertainties
            if u.status == "open" and u.related_constraint in touched]


def confirm_items(spec: TaskSpec, confirm: Iterable[str] = (), reject: Iterable[str] = (),
                  text: str = "") -> tuple[TaskSpec, CorrectionRecord]:
    """Human confirmation of proposed goals, constraints or orderings."""
    confirm, reject = list(confirm), list(reject)
    both = set(confirm) & set(reject)
    if both:
        raise InterpretError(f"items both confirmed and rejected: {sorted(both)}")
    ops: list[SpecOp] = []
    for ids, st in ((confirm, "confirmed"), (reject, "rejected")):
        for i in ids:
            ops.append(SpecOp(op="set_status", target_id=i, value={"status": st},
                              rationale=f"{st} by the user"))
    for i in confirm:
        ops += resolution_ops(spec, [i], "required (confirmed by the user)")
    for i in reject:
        ops += resolution_ops(spec, [i], "incidental (rejected by the user)")
    if not text:
        parts = []
        if confirm:
            parts.append("confirmed " + ", ".join(confirm))
        if reject:
            parts.append("rejected " + ", ".join(reject))
        text = "; ".join(parts) or "no change"
    return apply_ops(spec, ops, text=text, kind="confirmation")



# ------------------------------------------------------------ reverting ---

def _dump(item) -> dict:
    return item.model_dump(mode="json")


def revert_ops(current: TaskSpec, target: TaskSpec) -> list[SpecOp]:
    """Ops that turn ``current``'s goals, constraints and orderings into
    ``target``'s. Uncertainties are left as they are (an answered question is
    not re-opened)."""
    ops: list[SpecOp] = []
    why = f"revert to version {target.version}"
    cur_c, tgt_c = {c.id: c for c in current.constraints}, {c.id: c for c in target.constraints}
    for cid in cur_c:
        if cid not in tgt_c:
            ops.append(SpecOp(op="remove_constraint", target_id=cid, rationale=why))
    for cid, t in tgt_c.items():
        c = cur_c.get(cid)
        if c is None:
            ops.append(SpecOp(op="add_constraint", target_id=cid, value=_dump(t), rationale=why))
            continue
        if c.type != t.type or set(c.params) - set(t.params):
            ops += [SpecOp(op="remove_constraint", target_id=cid, rationale=why),
                    SpecOp(op="add_constraint", target_id=cid, value=_dump(t), rationale=why)]
            continue
        val: dict = {}
        if c.params != t.params:
            val["params"] = dict(t.params)
        if c.phase != t.phase:
            val["phase"] = t.phase
        if c.applies_to != t.applies_to:
            val["applies_to"] = t.applies_to.model_dump(mode="json")
        if c.description != t.description:
            val["description"] = t.description
        if c.status != t.status:
            val["status"] = t.status.value
        if val:
            ops.append(SpecOp(op="modify_constraint", target_id=cid, value=val, rationale=why))
    cur_g, tgt_g = {g.id: g for g in current.goals}, {g.id: g for g in target.goals}
    for gid in cur_g:
        if gid not in tgt_g:
            ops.append(SpecOp(op="remove_goal", target_id=gid, rationale=why))
    for gid, t in tgt_g.items():
        g = cur_g.get(gid)
        if g is None:
            ops.append(SpecOp(op="add_goal", target_id=gid, value=_dump(t), rationale=why))
        elif g.object != t.object or g.target != t.target:
            ops += [SpecOp(op="remove_goal", target_id=gid, rationale=why),
                    SpecOp(op="add_goal", target_id=gid, value=_dump(t), rationale=why)]
        elif g.status != t.status:
            ops.append(SpecOp(op="set_status", target_id=gid, value={"status": t.status.value}, rationale=why))
    cur_o, tgt_o = {o.id: o for o in current.ordering}, {o.id: o for o in target.ordering}
    for oid in cur_o:
        if oid not in tgt_o:
            ops.append(SpecOp(op="remove_ordering", target_id=oid, rationale=why))
    for oid, t in tgt_o.items():
        o = cur_o.get(oid)
        if o is None:
            ops.append(SpecOp(op="add_ordering", target_id=oid, value=_dump(t), rationale=why))
        elif (o.before, o.after, o.before_event, o.after_event) != (t.before, t.after, t.before_event, t.after_event):
            ops += [SpecOp(op="remove_ordering", target_id=oid, rationale=why),
                    SpecOp(op="add_ordering", target_id=oid, value=_dump(t), rationale=why)]
        elif o.status != t.status:
            ops.append(SpecOp(op="set_status", target_id=oid, value={"status": t.status.value}, rationale=why))
    return ops


def revert_to(current: TaskSpec, target: TaskSpec, text: str = "") -> tuple[TaskSpec, CorrectionRecord]:
    """A new version (current + 1) whose rules equal ``target``'s. History is
    appended, never rewritten: version numbers are not reused."""
    if target.spec_id != current.spec_id:
        raise InterpretError("cannot revert across specifications")
    if target.version >= current.version:
        raise InterpretError(f"version {target.version} is not earlier than the current version {current.version}")
    ops = revert_ops(current, target)
    if not ops:
        raise InterpretError(f"version {target.version} has the same rules as the current version")
    new, rec = apply_ops(current, ops, text=text or f"revert to version {target.version}", kind="revert")
    return new, rec
