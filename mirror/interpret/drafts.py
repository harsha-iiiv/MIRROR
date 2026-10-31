"""Model-facing output schemas and their deterministic compilation.

The model (or the offline rules) fills in a small, closed *draft* schema. The
functions here check the draft against the demonstration summary and the
current specification and compile it into a TaskSpec or a list of SpecOps.
Identifiers, statuses and provenance are assigned here, never by the model:
an inferred rule is always ``proposed`` and only a human statement can make a
rule ``confirmed``.
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from mirror.contracts import (Constraint, DemoTrace, Goal, ObjectBinding, ObjectKind,
                              ObjectSelector, OrderingConstraint, Provenance, SpecOp,
                              Status, TaskSpec, Uncertainty)
from mirror.geometry import DEFAULT_UPRIGHT_TOL_DEG
from mirror.interpret import specops as so
from mirror.interpret.types import (CorrectionRejected, DemoSummary, InterpretError,
                                    NeedsClarification)

ROLE = {"cup": "container", "block": "block", "marker": "marker"}
ContactGroup = Literal["obstacle", "table_outside_tray", "other_objects"]
RuleType = Literal["upright", "forbidden_contact", "keep_clearance"]
PhaseT = Literal["final", "transport", "always"]
Lean = Literal["likely_required", "likely_incidental", "unclear"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------ proposal ---

class DraftObject(_M):
    kind: ObjectKind
    include_in_task: bool
    evidence: str
    regularity_ids: list[str]


class DraftRule(_M):
    type: RuleType
    object_kind: Optional[ObjectKind]
    phase: PhaseT
    max_tilt_deg: Optional[float]
    contact_with: Optional[list[ContactGroup]]
    min_clearance_m: Optional[float]
    regularity_ids: list[str]
    shown_by_demo: str
    assumption: str
    lean: Lean
    question: str


class DraftOrdering(_M):
    before_kind: ObjectKind
    after_kind: ObjectKind
    regularity_ids: list[str]
    shown_by_demo: str
    lean: Lean
    question: str


class DraftDismissal(_M):
    regularity_id: str
    reason: str


class ProposalDraft(_M):
    summary: str
    observations: list[str]
    assumptions: list[str]
    objects: list[DraftObject]
    rules: list[DraftRule]
    ordering: list[DraftOrdering]
    dismissed: list[DraftDismissal]


def _kind_objects(summary: DemoSummary, kind: str):
    return [o for o in summary.objects if o.kind == kind]


def check_proposal(d: ProposalDraft, summary: DemoSummary) -> list[str]:
    errs: list[str] = []
    scene_kinds = {o.kind for o in summary.objects}
    reg_ids = {r.id for r in summary.regularities}
    kinds_listed = [o.kind for o in d.objects]
    for k in sorted(scene_kinds - set(kinds_listed)):
        errs.append(f"objects: the scene contains a {k}; list it (include_in_task true or false)")
    for k in kinds_listed:
        if k not in scene_kinds:
            errs.append(f"objects: there is no {k} in the scene")
    if len(set(kinds_listed)) != len(kinds_listed):
        errs.append("objects: list each kind once")
    included = {o.kind for o in d.objects if o.include_in_task}
    if not included:
        errs.append("objects: at least one object must be part of the task")
    for o in d.objects:
        if o.include_in_task and not any(s.ended_in_tray for s in _kind_objects(summary, o.kind)):
            errs.append(f"objects: the {o.kind} did not end in a tray in the demonstration; "
                        "it cannot be inferred as a packing goal")
    seen = set()
    for i, r in enumerate(d.rules):
        where = f"rules[{i}]"
        if r.object_kind is None:
            errs.append(f"{where}: rules inferred from a demonstration must name an object kind")
            continue
        if r.object_kind not in included:
            errs.append(f"{where}: {r.object_kind} is not an object included in the task")
            continue
        key = (r.type, r.phase, r.object_kind)
        if key in seen:
            errs.append(f"{where}: duplicate rule {key}")
        seen.add(key)
        if r.type == "upright":
            tol = r.max_tilt_deg if r.max_tilt_deg is not None else DEFAULT_UPRIGHT_TOL_DEG
            if not (0 < tol <= 90):
                errs.append(f"{where}: max_tilt_deg must be in (0, 90]")
            for s in _kind_objects(summary, r.object_kind):
                shown = []
                if r.phase in ("transport", "always") and s.carry_max_tilt_deg is not None:
                    shown.append(s.carry_max_tilt_deg)
                if r.phase in ("final", "always"):
                    shown.append(s.end_tilt_deg)
                if r.phase == "always":
                    shown.append(s.start_tilt_deg)
                if shown and max(shown) > tol:
                    errs.append(f"{where}: the demonstration itself violates this rule "
                                f"({r.object_kind} reached {max(shown):g} degrees > {tol:g})")
        elif r.type == "forbidden_contact":
            if not r.contact_with:
                errs.append(f"{where}: forbidden_contact needs contact_with")
        elif r.type == "keep_clearance":
            if r.min_clearance_m is None or not (0 < r.min_clearance_m <= 0.5):
                errs.append(f"{where}: keep_clearance needs min_clearance_m in (0, 0.5]")
            else:
                for s in _kind_objects(summary, r.object_kind):
                    if s.min_obstacle_distance_m is not None and s.min_obstacle_distance_m < r.min_clearance_m:
                        errs.append(f"{where}: the demonstration came within "
                                    f"{s.min_obstacle_distance_m:g} m, closer than {r.min_clearance_m:g} m")
        for rid in r.regularity_ids:
            if rid not in reg_ids:
                errs.append(f"{where}: unknown regularity id {rid}")
        if not r.question.strip():
            errs.append(f"{where}: every inferred rule needs a question for the user")
    by_id = {o.object_id: o for o in summary.objects}
    order_kinds = [by_id[i].kind for i in summary.order]
    oseen = set()
    for i, o in enumerate(d.ordering):
        where = f"ordering[{i}]"
        if o.before_kind == o.after_kind:
            errs.append(f"{where}: before and after must differ")
            continue
        for k in (o.before_kind, o.after_kind):
            if k not in included:
                errs.append(f"{where}: {k} is not an object included in the task")
        if (o.before_kind in order_kinds and o.after_kind in order_kinds
                and order_kinds.index(o.before_kind) > order_kinds.index(o.after_kind)):
            errs.append(f"{where}: the demonstration moved the {o.after_kind} before the {o.before_kind}")
        if (o.before_kind, o.after_kind) in oseen:
            errs.append(f"{where}: duplicate ordering")
        oseen.add((o.before_kind, o.after_kind))
        for rid in o.regularity_ids:
            if rid not in reg_ids:
                errs.append(f"{where}: unknown regularity id {rid}")
    covered = set()
    for o in d.objects:
        covered |= set(o.regularity_ids)
    for r in d.rules:
        covered |= set(r.regularity_ids)
    for o in d.ordering:
        covered |= set(o.regularity_ids)
    for x in d.dismissed:
        if x.regularity_id not in reg_ids:
            errs.append(f"dismissed: unknown regularity id {x.regularity_id}")
        covered.add(x.regularity_id)
    missing = sorted(reg_ids - covered, key=lambda s: int(s[1:]) if s[1:].isdigit() else 0)
    if missing:
        errs.append("every regularity must be used as evidence or explicitly dismissed; "
                    f"not addressed: {missing}")
    return errs


def proposal_to_spec(d: ProposalDraft, summary: DemoSummary, trace: DemoTrace,
                     interpreter: str) -> TaskSpec:
    errs = check_proposal(d, summary)
    if errs:
        raise InterpretError("proposal rejected: " + "; ".join(errs))
    source_model = None if interpreter == "offline-rules" else interpreter
    counts: dict[str, int] = {}
    for o in trace.scene.objects:
        counts[o.kind] = counts.get(o.kind, 0) + 1
    included = {o.kind: o for o in d.objects if o.include_in_task}
    bindings = [ObjectBinding(role=ROLE[k], kind=k, required=k in included, count=counts[k])
                for k in sorted(counts, key=lambda k: list(ROLE).index(k))]
    goals = [Goal(id=so.goal_id(k), type="inside", object=ObjectSelector(kind=k),
                  status=Status.proposed,
                  provenance=Provenance(source="demo_inference", evidence=o.evidence,
                                        model=source_model))
             for k, o in sorted(included.items(), key=lambda kv: list(ROLE).index(kv[0]))]
    constraints, uncertainties, ordering = [], [], []
    for r in d.rules:
        cid = so.constraint_id(r.type, r.phase, r.object_kind)
        params: dict = {}
        if r.type == "upright":
            params["max_tilt_deg"] = float(r.max_tilt_deg if r.max_tilt_deg is not None
                                           else DEFAULT_UPRIGHT_TOL_DEG)
        elif r.type == "forbidden_contact":
            params["with"] = sorted(set(r.contact_with or []))
        else:
            params["min_m"] = float(r.min_clearance_m)
        constraints.append(Constraint(
            id=cid, type=r.type, applies_to=ObjectSelector(kind=r.object_kind), phase=r.phase,
            params=params, status=Status.proposed,
            provenance=Provenance(source="demo_inference", model=source_model,
                                  evidence=f"Shown: {r.shown_by_demo} Assumed: {r.assumption}".strip()),
            description=describe_rule(r.type, r.phase, r.object_kind, params)))
        uncertainties.append(Uncertainty(
            id=so.uncertainty_id(cid), question=r.question.strip(),
            options=_options(r.lean), related_constraint=cid))
    for o in d.ordering:
        oid = so.ordering_id(o.before_kind, o.after_kind)
        ordering.append(OrderingConstraint(
            id=oid, before=ObjectSelector(kind=o.before_kind), after=ObjectSelector(kind=o.after_kind),
            status=Status.proposed,
            provenance=Provenance(source="demo_inference", evidence=o.shown_by_demo, model=source_model)))
        uncertainties.append(Uncertainty(id=so.uncertainty_id(oid), question=o.question.strip(),
                                         options=_options(o.lean), related_constraint=oid))
    spec = TaskSpec(spec_id=f"spec_{trace.demo_id}", version=1, summary=d.summary.strip(),
                    bindings=bindings, goals=goals, ordering=ordering, constraints=constraints,
                    uncertainties=uncertainties, demo_id=trace.demo_id, interpreter=interpreter)
    errs = so.validate_spec(spec)
    if errs:
        raise InterpretError("proposal produced an invalid spec: " + "; ".join(errs))
    return spec


def _options(lean: str) -> list[str]:
    req, inc = "Required", "Incidental"
    if lean == "likely_incidental":
        return [inc + " (interpreter's lean)", req]
    if lean == "likely_required":
        return [req + " (interpreter's lean)", inc]
    return [req, inc]


PHASE_TEXT = {"final": "once placed", "transport": "while carried", "always": "at all times"}


def describe_rule(ctype: str, phase: str, kind: Optional[str], params: dict) -> str:
    who = f"the {kind}" if kind else "every object"
    if ctype == "upright":
        return f"Keep {who} within {params.get('max_tilt_deg', DEFAULT_UPRIGHT_TOL_DEG):g} degrees of upright {PHASE_TEXT[phase]}."
    if ctype == "forbidden_contact":
        return f"{who.capitalize()} must not touch {', '.join(params.get('with', []))} {PHASE_TEXT[phase]}."
    return f"Keep {who} at least {params.get('min_m', 0):g} m from obstacles {PHASE_TEXT[phase]}."


# ---------------------------------------------------------- correction ---

class DraftRuleSpec(_M):
    type: RuleType
    object_kind: Optional[ObjectKind]
    phase: Optional[PhaseT]
    max_tilt_deg: Optional[float]
    contact_with: Optional[list[ContactGroup]]
    min_clearance_m: Optional[float]


class DraftOp(_M):
    action: Literal["require_rule", "relax_rule", "confirm_item", "reject_item",
                    "require_order", "relax_order", "add_goal", "remove_goal"]
    rule: Optional[DraftRuleSpec]
    target_id: Optional[str]
    before_kind: Optional[ObjectKind]
    after_kind: Optional[ObjectKind]
    goal_kind: Optional[ObjectKind]
    rationale: str


class CorrectionDraft(_M):
    outcome: Literal["apply", "clarify", "reject"]
    interpretation: str
    question: Optional[str]
    options: list[str] = Field(default_factory=list)
    ops: list[DraftOp]


_UNIVERSAL = re.compile(r"\b(all|every|everything|each|any object|anything|all objects|nothing)\b")


def correction_to_ops(d: CorrectionDraft, spec: TaskSpec, text: str,
                      scene_kinds: Optional[set[str]] = None) -> list[SpecOp]:
    """Compile a correction draft into SpecOps. Raises NeedsClarification or
    CorrectionRejected when the draft says so, and InterpretError when the
    draft does not fit the spec."""
    if d.outcome == "clarify":
        raise NeedsClarification(d.question or "Could you say which object and rule you mean?",
                                 d.options, reason="interpreter asked", interpretation=d.interpretation)
    if d.outcome == "reject":
        raise CorrectionRejected(d.interpretation or "not a supported change to the task")
    if not d.ops:
        raise CorrectionRejected("the correction did not map to any change")
    bound = {b.kind for b in spec.bindings}
    errs: list[str] = []
    ops: list[SpecOp] = []
    touched: list[str] = []
    require_keys: set[tuple] = set()
    relax_keys: set[tuple] = set()
    universal = bool(_UNIVERSAL.search(text.lower()))

    def kind_ok(k: Optional[str], where: str) -> bool:
        if k is None:
            return True
        if k not in bound:
            where_s = "in this scene" if not scene_kinds or k not in scene_kinds else "in this task"
            errs.append(f"{where}: there is no {k} {where_s}")
            return False
        return True

    for i, op in enumerate(d.ops):
        where = f"ops[{i}] {op.action}"
        if op.action in ("require_rule", "relax_rule"):
            r = op.rule
            if r is None:
                errs.append(f"{where}: rule is required")
                continue
            if not kind_ok(r.object_kind, where):
                continue
            if r.object_kind is None and not universal:
                errs.append(f"{where}: the rule applies to every object but the text does not say so; "
                            "scope it to the object the text names")
                continue
            if op.action == "require_rule":
                if r.phase is None:
                    errs.append(f"{where}: a required rule needs a phase")
                    continue
                params, perr = _params(r)
                if perr:
                    errs.append(f"{where}: {perr}")
                    continue
                require_keys.add((r.type, r.phase, r.object_kind))
                cid = so.constraint_id(r.type, r.phase, r.object_kind)
                existing = so.find_constraint(spec, r.type, r.phase, r.object_kind)
                desc = describe_rule(r.type, r.phase, r.object_kind, params)
                if existing is not None:
                    cid = existing.id
                    changed = {k: v for k, v in params.items() if existing.params.get(k) != v}
                    if changed:
                        ops.append(SpecOp(op="modify_constraint", target_id=cid,
                                          value={"params": changed, "description": desc},
                                          rationale=op.rationale))
                    if existing.status != Status.confirmed:
                        ops.append(SpecOp(op="set_status", target_id=cid, value={"status": "confirmed"},
                                          rationale=op.rationale))
                else:
                    ops.append(SpecOp(op="add_constraint", target_id=cid, rationale=op.rationale, value=Constraint(
                        id=cid, type=r.type, applies_to=ObjectSelector(kind=r.object_kind), phase=r.phase,
                        params=params, status=Status.confirmed, description=desc,
                        provenance=Provenance(source="user_correction", evidence=text)).model_dump(mode="json")))
                touched.append(cid)
            else:
                phases = [r.phase] if r.phase else ["final", "transport", "always"]
                for ph in phases:
                    relax_keys.add((r.type, ph, r.object_kind))
                matches = [c for c in spec.constraints
                           if c.type == r.type and c.applies_to.kind == r.object_kind and c.phase in phases]
                for c in matches:
                    if c.status != Status.rejected:
                        ops.append(SpecOp(op="set_status", target_id=c.id, value={"status": "rejected"},
                                          rationale=op.rationale))
                    touched.append(c.id)
                if not matches:
                    ph = r.phase or "transport"
                    params, perr = _params(r)
                    cid = so.constraint_id(r.type, ph, r.object_kind)
                    ops.append(SpecOp(op="add_constraint", target_id=cid, rationale=op.rationale, value=Constraint(
                        id=cid, type=r.type, applies_to=ObjectSelector(kind=r.object_kind), phase=ph,
                        params=params if not perr else _default_params(r.type), status=Status.rejected,
                        description="Not required (user): " + describe_rule(
                            r.type, ph, r.object_kind, params if not perr else _default_params(r.type)),
                        provenance=Provenance(source="user_correction", evidence=text)).model_dump(mode="json")))
                    touched.append(cid)
        elif op.action in ("confirm_item", "reject_item"):
            item = so.find_item(spec, op.target_id or "")
            if item is None or op.target_id.startswith("u_"):
                errs.append(f"{where}: no goal, constraint or ordering with id {op.target_id!r}")
                continue
            st = "confirmed" if op.action == "confirm_item" else "rejected"
            if isinstance(item, Constraint):
                key = (item.type, item.phase, item.applies_to.kind)
                (require_keys if st == "confirmed" else relax_keys).add(key)
            if item.status != Status(st):
                ops.append(SpecOp(op="set_status", target_id=item.id, value={"status": st},
                                  rationale=op.rationale))
            touched.append(item.id)
        elif op.action == "require_order":
            if not (op.before_kind and op.after_kind) or op.before_kind == op.after_kind:
                errs.append(f"{where}: needs two different kinds")
                continue
            if not (kind_ok(op.before_kind, where) and kind_ok(op.after_kind, where)):
                continue
            require_keys.add(("order", op.before_kind, op.after_kind))
            oid = so.ordering_id(op.before_kind, op.after_kind)
            rev = so.ordering_id(op.after_kind, op.before_kind)
            for o in spec.ordering:
                if o.id == rev and o.status != Status.rejected:
                    ops.append(SpecOp(op="set_status", target_id=rev, value={"status": "rejected"},
                                      rationale="replaced by the opposite order"))
                    touched.append(rev)
            existing = so.find_item(spec, oid)
            if existing is not None:
                if existing.status != Status.confirmed:
                    ops.append(SpecOp(op="set_status", target_id=oid, value={"status": "confirmed"},
                                      rationale=op.rationale))
            else:
                ops.append(SpecOp(op="add_ordering", target_id=oid, rationale=op.rationale, value=OrderingConstraint(
                    id=oid, before=ObjectSelector(kind=op.before_kind), after=ObjectSelector(kind=op.after_kind),
                    status=Status.confirmed, provenance=Provenance(source="user_correction", evidence=text)
                ).model_dump(mode="json")))
            touched.append(oid)
        elif op.action == "relax_order":
            if (op.before_kind is None) != (op.after_kind is None):
                kinds = {op.before_kind or op.after_kind}
                matches = [o for o in spec.ordering if {o.before.kind, o.after.kind} & kinds]
            elif op.before_kind is None:
                matches = list(spec.ordering)
            else:
                pair = {op.before_kind, op.after_kind}
                matches = [o for o in spec.ordering if {o.before.kind, o.after.kind} == pair]
            if not matches:
                errs.append(f"{where}: there is no ordering rule to relax")
                continue
            for o in matches:
                relax_keys.add(("order", o.before.kind, o.after.kind))
                if o.status != Status.rejected:
                    ops.append(SpecOp(op="set_status", target_id=o.id, value={"status": "rejected"},
                                      rationale=op.rationale))
                touched.append(o.id)
        elif op.action in ("add_goal", "remove_goal"):
            k = op.goal_kind
            if k is None or not kind_ok(k, where):
                if k is None:
                    errs.append(f"{where}: goal_kind is required")
                continue
            gid = so.goal_id(k)
            g = so.find_item(spec, gid)
            if op.action == "add_goal":
                if g is None:
                    ops.append(SpecOp(op="add_goal", target_id=gid, rationale=op.rationale, value=Goal(
                        id=gid, type="inside", object=ObjectSelector(kind=k), status=Status.confirmed,
                        provenance=Provenance(source="user_correction", evidence=text)).model_dump(mode="json")))
                elif g.status != Status.confirmed:
                    ops.append(SpecOp(op="set_status", target_id=gid, value={"status": "confirmed"},
                                      rationale=op.rationale))
            else:
                if g is None:
                    errs.append(f"{where}: the {k} is not a goal of this task")
                    continue
                if g.status != Status.rejected:
                    ops.append(SpecOp(op="set_status", target_id=gid, value={"status": "rejected"},
                                      rationale=op.rationale))
            touched.append(gid)
    clash = sorted(require_keys & relax_keys, key=str)
    if clash:
        raise NeedsClarification(
            "Your correction both requires and relaxes the same rule ("
            + ", ".join(_key_text(k) for k in clash) + "). Which do you mean?",
            ["Required", "Not required"], reason="contradictory correction")
    if errs:
        raise InterpretError("; ".join(errs))
    ops = _dedupe(ops)
    ops += so.resolution_ops(spec, touched, f"settled by the user: {text.strip()}")
    if not ops:
        raise CorrectionRejected("the specification already says this; nothing to change")
    return ops


def _key_text(k: tuple) -> str:
    if k[0] == "order":
        return f"{k[1]} before {k[2]}"
    return f"{k[0]} {k[1]} for the {k[2] or 'all objects'}"


def _default_params(ctype: str) -> dict:
    return {"upright": {"max_tilt_deg": DEFAULT_UPRIGHT_TOL_DEG},
            "forbidden_contact": {"with": ["obstacle"]},
            "keep_clearance": {"min_m": 0.02}}[ctype]


def _params(r: DraftRuleSpec) -> tuple[dict, str]:
    if r.type == "upright":
        v = r.max_tilt_deg if r.max_tilt_deg is not None else DEFAULT_UPRIGHT_TOL_DEG
        if not (0 < v <= 90):
            return {}, "max_tilt_deg must be in (0, 90]"
        return {"max_tilt_deg": float(v)}, ""
    if r.type == "forbidden_contact":
        if not r.contact_with:
            return {}, "contact_with is required"
        return {"with": sorted(set(r.contact_with))}, ""
    if r.min_clearance_m is None or not (0 < r.min_clearance_m <= 0.5):
        return {}, "min_clearance_m in (0, 0.5] is required"
    return {"min_m": float(r.min_clearance_m)}, ""


def _dedupe(ops: list[SpecOp]) -> list[SpecOp]:
    out, seen = [], set()
    for op in ops:
        key = (op.op, op.target_id, repr(sorted((op.value or {}).items())))
        if key not in seen:
            seen.add(key)
            out.append(op)
    return out
