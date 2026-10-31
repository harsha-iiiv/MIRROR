"""Active clarification: show two behaviours that are both valid under the
current specification, compute what differs, and turn the human's answer into
specification ops.

The comparison uses only the candidate plans (planned waypoints), not
simulation results, so tilt values here are planned, not measured.
"""
from __future__ import annotations

import hashlib
import math
from typing import Optional

import numpy as np

from mirror.contracts import CorrectionRecord, PlanResult, SceneState, SpecOp, TaskSpec
from mirror.geometry import DEFAULT_UPRIGHT_TOL_DEG
from mirror.interpret.correct import compile_correction
from mirror.interpret.drafts import CorrectionDraft, DraftOp, DraftRuleSpec, correction_to_ops
from mirror.interpret.geom import quat_conj, quat_mul, tilt_deg
from mirror.interpret.llm import LLMClient
from mirror.interpret.offline import _KIND_RE, _TILT_RE, _UPRIGHT_RE, parse_yes_no
from mirror.interpret.specops import apply_ops
from mirror.interpret.types import (CandidateDigest, Clarification, Difference, InterpretError,
                                    NeedsClarification)

QUESTION = "Would both of these still count as doing the task correctly?"
TILT_DIFF_DEG = 10.0


def planned_carry_tilts(plan: PlanResult, scene: SceneState) -> dict[str, float]:
    """Max planned tilt per object while held, assuming a rigid grasp:
    object orientation = gripper * inverse(gripper at grasp) * object at start."""
    start = {o.id: o.pose.quat for o in scene.objects}
    out: dict[str, float] = {}
    held: Optional[str] = None
    q_grasp = None
    last_q = None
    for step in plan.steps:
        if step.kind == "grasp" and step.object_id:
            if step.waypoints:
                last_q = step.waypoints[-1].quat
            held, q_grasp = step.object_id, last_q
            if held in start:
                out.setdefault(held, tilt_deg(start[held]))
            continue
        for wp in step.waypoints:
            last_q = wp.quat
            if held and q_grasp is not None and held in start:
                q_obj = quat_mul(quat_mul(wp.quat, quat_conj(q_grasp)), start[held])
                out[held] = max(out.get(held, 0.0), tilt_deg(q_obj))
        if step.kind == "release" and (step.object_id is None or step.object_id == held):
            held, q_grasp = None, None
    return {k: round(v, 1) for k, v in out.items()}


def _path_length(plan: PlanResult) -> float:
    pts = [np.asarray(wp.pos) for s in plan.steps for wp in s.waypoints]
    return round(float(sum(np.linalg.norm(b - a) for a, b in zip(pts, pts[1:]))), 3)


def _routes(plan: PlanResult) -> list[str]:
    """One label per transported object, e.g. "cup_0: through_slot:wall_0:0:pitched+90"."""
    labels = []
    for s in plan.steps:
        if s.kind != "transport" or not s.note:
            continue
        note = s.note
        for part in note.split(";"):
            part = part.strip()
            if part.startswith("route="):
                note = part[len("route="):]
                break
        labels.append(f"{s.object_id}: {note}" if s.object_id else note)
    if labels:
        return labels
    return [f"{r.object_id}: {r.label}" if r.object_id else r.label
            for r in plan.routes if r.feasible and r.cost is not None and r.rejected_reason is None][:3]


def _digest(i: int, label: str, p: PlanResult, scene: SceneState) -> CandidateDigest:
    return CandidateDigest(index=i, label=label, object_order=list(p.object_order), routes=_routes(p),
                           planned_max_tilt_deg=planned_carry_tilts(p, scene),
                           gripper_path_length_m=_path_length(p), plan_status=p.status)


def _pair_score(spec: TaskSpec, scene: SceneState, a: CandidateDigest, b: CandidateDigest,
                focus_kind: Optional[str]) -> Optional[float]:
    kind_of = {o.id: o.kind for o in scene.objects}
    beads = {o.kind: o.beads for o in scene.objects}
    open_q = {u.related_constraint for u in spec.uncertainties if u.status == "open"}
    enforced = {(c.type, c.phase, c.applies_to.kind) for c in spec.enforced_constraints()}
    tilt_kinds = [kind_of[o] for o in sorted(kind_of)
                  if abs(a.planned_max_tilt_deg.get(o, 0.0) - b.planned_max_tilt_deg.get(o, 0.0)) >= TILT_DIFF_DEG]
    if focus_kind and focus_kind not in tilt_kinds:
        return None
    if any(("upright", "transport", k) in enforced for k in tilt_kinds):
        return None  # cannot both be valid under the spec
    order_diff = [kind_of.get(i, i) for i in a.object_order] != [kind_of.get(i, i) for i in b.object_order]
    n = len(tilt_kinds) + int(order_diff)
    if n == 0:
        return None
    score = 0.0
    for k in tilt_kinds:
        score += 3.0 if f"c_upright_transport_{k}" in open_q else 1.0
        score += 1.0 if beads.get(k, 0) > 0 else 0.0
    if order_diff:
        score += 1.0 + sum(1.0 for o in spec.ordering if o.id in open_q)
    return score - 4.0 * (n - 1)  # prefer pairs that differ in exactly one way


def make_clarification(spec: TaskSpec, scene: SceneState, candidates: list[PlanResult], *,
                       focus_kind: Optional[str] = None) -> Clarification:
    """Build the "would both still count?" question from candidate plans.

    With more than two valid candidates, the pair whose difference best
    targets an open question (preferring a single difference, and objects
    with loose contents) is chosen. ``focus_kind`` restricts the choice to
    pairs that differ in how that object is carried."""
    ok = [p for p in candidates if p.status == "ok"]
    if len(ok) < 2:
        raise InterpretError("clarification needs at least two valid candidate plans")
    all_d = [_digest(i, "", p, scene) for i, p in enumerate(ok)]
    best, best_score = None, None
    for i in range(len(ok)):
        for j in range(i + 1, len(ok)):
            sc = _pair_score(spec, scene, all_d[i], all_d[j], focus_kind)
            if sc is not None and (best_score is None or sc > best_score):
                best, best_score = (i, j), sc
    if best is None:
        raise InterpretError("no pair of candidates differs in tilt or order in a way the "
                             "specification could express" + (f" for the {focus_kind}" if focus_kind else ""))
    kind_of = {o.id: o.kind for o in scene.objects}
    digests = [_digest(n, "AB"[n], ok[k], scene) for n, k in enumerate(best)]
    diffs: list[Difference] = []
    for oid in sorted(kind_of):
        vals = [d.planned_max_tilt_deg.get(oid, 0.0) for d in digests]
        if max(vals) - min(vals) >= TILT_DIFF_DEG:
            k = kind_of[oid]
            diffs.append(Difference(
                feature="carry_tilt", object_kind=k, object_id=oid, values=vals,
                statement=(f"The {k} is carried at up to {vals[0]:g} degrees of tilt in A "
                           f"and {vals[1]:g} degrees in B.")))
    orders = [[kind_of.get(i, i) for i in d.object_order] for d in digests]
    if orders[0] != orders[1]:
        diffs.append(Difference(feature="order", values=orders,
                                statement=f"A packs {', '.join(orders[0])}; B packs {', '.join(orders[1])}."))
    if digests[0].routes != digests[1].routes:
        ra = [x for x in digests[0].routes if x not in digests[1].routes]
        rb = [x for x in digests[1].routes if x not in digests[0].routes]
        diffs.append(Difference(feature="route", values=[ra, rb],
                                statement=f"A: {'; '.join(ra) or 'same routes'}. B: {'; '.join(rb) or 'same routes'}."))
    actionable = [d for d in diffs if d.feature in ("carry_tilt", "order")]
    if not actionable:
        raise InterpretError("the candidates do not differ in tilt or order, so the specification "
                             "cannot express a preference between them")
    if_no, if_yes, options = [], [], ["Yes, both count."]
    note = ""
    for d in actionable:
        if d.feature == "carry_tilt":
            lo, hi = min(d.values), max(d.values)
            tol = DEFAULT_UPRIGHT_TOL_DEG if lo < DEFAULT_UPRIGHT_TOL_DEG else round((lo + hi) / 2.0, 1)
            no_draft = CorrectionDraft(outcome="apply", interpretation="", question=None, options=[], ops=[DraftOp(
                action="require_rule", target_id=None, before_kind=None, after_kind=None, goal_kind=None,
                rationale=f"Only the low-tilt candidate counts: {d.statement}",
                rule=DraftRuleSpec(type="upright", object_kind=d.object_kind, phase="transport",
                                   max_tilt_deg=tol, contact_with=None, min_clearance_m=None))])
            yes_draft = CorrectionDraft(outcome="apply", interpretation="", question=None, options=[], ops=[DraftOp(
                action="relax_rule", target_id=None, before_kind=None, after_kind=None, goal_kind=None,
                rationale=f"Both candidates count: {d.statement}",
                rule=DraftRuleSpec(type="upright", object_kind=d.object_kind, phase="transport",
                                   max_tilt_deg=None, contact_with=None, min_clearance_m=None))])
            if_no += correction_to_ops(no_draft, spec, f"{d.object_kind} must stay upright while carried")
            if_yes += correction_to_ops(yes_draft, spec, f"{d.object_kind} may tilt while carried")
            options.append(f"No: the {d.object_kind} must stay upright while it is carried.")
        else:
            for o in spec.ordering:
                if o.status.value == "rejected":
                    continue
                sat = [(o.before.kind in ord_ and o.after.kind in ord_ and
                        ord_.index(o.before.kind) < ord_.index(o.after.kind)) for ord_ in d.values]
                if sat[0] != sat[1]:
                    if_no.append(SpecOp(op="set_status", target_id=o.id, value={"status": "confirmed"},
                                        rationale=f"Only the candidate that keeps {o.before.kind} before "
                                                  f"{o.after.kind} counts"))
                    if_yes.append(SpecOp(op="set_status", target_id=o.id, value={"status": "rejected"},
                                         rationale="Both orders count"))
            options.append("No: the packing order matters.")
    if len(actionable) > 1:
        note = ("The candidates differ in more than one way; a plain 'no' does not say which difference "
                "matters, so it will be followed by a question.")
    ops_ids = [op.target_id for op in if_yes + if_no if op.op != "resolve_uncertainty"]
    if_yes = _with_resolutions(spec, if_yes, "both behaviours count (clarification)")
    if_no = _with_resolutions(spec, if_no, "only the demonstrated behaviour counts (clarification)")
    cid = hashlib.sha256(f"{spec.spec_id}:{spec.version}:{scene.scene_id}:{[d.model_dump_json() for d in digests]}"
                         .encode()).hexdigest()[:12]
    return Clarification(clarification_id=f"clar_{cid}", spec_id=spec.spec_id, spec_version=spec.version,
                         scene_id=scene.scene_id, question=QUESTION, candidates=digests, differences=diffs,
                         options=options, if_yes=if_yes, if_no=if_no, note=note)


def _with_resolutions(spec: TaskSpec, ops: list[SpecOp], resolution: str) -> list[SpecOp]:
    base = [op for op in ops if op.op != "resolve_uncertainty"]
    touched = {op.target_id for op in base}
    res = [SpecOp(op="resolve_uncertainty", target_id=u.id, value={"resolution": resolution},
                  rationale="answered by clarification")
           for u in spec.uncertainties if u.status == "open" and u.related_constraint in touched]
    out, seen = [], set()
    for op in base + res:
        key = (op.op, op.target_id)
        if key not in seen:
            seen.add(key)
            out.append(op)
    return out


def _substantive(text: str) -> bool:
    return any(rx.search(text) for rx in _KIND_RE.values()) or bool(
        _UPRIGHT_RE.search(text) or _TILT_RE.search(text))


def apply_answer(spec: TaskSpec, clarification: Clarification, answer: str,
                 client: Optional[LLMClient] = None) -> tuple[TaskSpec, CorrectionRecord]:
    """Map the human's answer to ops and apply them (record kind
    "clarification_answer")."""
    if clarification.spec_id != spec.spec_id or clarification.spec_version != spec.version:
        raise InterpretError("this clarification was made for a different specification version")
    record_text = f"Q: {clarification.question} A: {answer.strip()}"
    yn, rest = parse_yes_no(answer)
    tilt = [d for d in clarification.differences if d.feature == "carry_tilt"]
    if rest and _substantive(rest):
        focus = tilt[0].object_kind if len(tilt) == 1 else None
        ctx = {"kind": "clarification_answer",
               "clarification": {"question": clarification.question,
                                 "differences": [d.statement for d in clarification.differences],
                                 "answer_was": yn}}
        if focus:
            ctx["focus_kind"] = focus
        new, rec = compile_correction(spec, rest if client is None or client.offline else answer, client, ctx)
        rec.text = record_text
        new.history[-1] = rec
        return new, rec
    if yn == "yes":
        ops = clarification.if_yes
    elif yn == "no":
        actionable = [d for d in clarification.differences if d.feature in ("carry_tilt", "order")]
        if len(actionable) > 1:
            raise NeedsClarification("Which difference makes one of them wrong? "
                                     + " ".join(d.statement for d in actionable),
                                     [o for o in clarification.options if o.startswith("No")],
                                     reason="several differences")
        ops = clarification.if_no
    else:
        raise NeedsClarification(QUESTION + " Please answer yes or no, or say what matters.",
                                 clarification.options, reason="unclear answer")
    if not ops:
        raise InterpretError("this answer does not change the specification")
    model = "offline-rules" if client is None or client.offline else client.model
    return apply_ops(spec, ops, text=record_text, kind="clarification_answer", model=model)
