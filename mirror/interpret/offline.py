"""Deterministic rule-based interpreter (no model, no network).

It produces the same draft structures as the model path, so everything
downstream (identifiers, validation, op compilation) is shared. Its language
coverage is deliberately narrow: it understands the supported rule types,
the three object kinds and their common synonyms, phases, negation and
permission, explicit numbers, and simple ordering phrases. Anything outside
that is answered with a clarifying question or a rejection, not a guess.
Output is labelled interpreter="offline-rules".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from mirror.contracts import SceneState, Status, TaskSpec
from mirror.interpret.specops import effective_kind
from mirror.interpret.drafts import (CorrectionDraft, CorrectionDraftV4, DraftDismissal, DraftObject,
                                     DraftOp, DraftOpV4, DraftOrdering, DraftRule, DraftRuleSpec,
                                     ProposalDraft)
from mirror.interpret.types import CorrectionRejected, DemoSummary, NeedsClarification

OFFLINE_LABEL = "offline-rules"

KIND_WORDS = {
    "cup": ["cup", "cups", "container", "mug", "glass", "beaker", "beads", "contents"],
    "block": ["block", "blocks", "cube", "brick"],
    "marker": ["marker", "markers", "pen", "cylinder"],
}
UNKNOWN_OBJECTS = ["bowl", "plate", "bottle", "spoon", "fork", "knife", "phone", "ball", "jar",
                   "book", "lid", "can", "banana", "apple", "screwdriver", "sponge", "toy", "box"]
_KIND_RE = {k: re.compile(r"\b(" + "|".join(v) + r")\b") for k, v in KIND_WORDS.items()}
_UNKNOWN_RE = re.compile(r"\bthe (" + "|".join(UNKNOWN_OBJECTS) + r")s?\b")
_PRONOUN_RE = re.compile(r"\b(it|its|it's|this one|that one|the object|the thing|the item|this|that)\b")
_UPRIGHT_RE = re.compile(r"\b(upright|vertical|vertically|level|right side up|right-side up|opening up|straight up|"
                         r"standing up|stand up|stands up|standing)\b")
_TILT_RE = re.compile(r"\b(tilt|tilts|tilted|tilting|tip|tips|tipped|tipping|on its side|sideways|lie|lies|lying|"
                      r"flip|flipped|turn over|turned over|spill|spills|spilled|spilling|pitch|pitched|lean|leans)\b")
_CONTACT_RE = re.compile(r"\b(touch|touches|touching|hit|hits|hitting|bump|bumps|bumping|contact|collide|"
                         r"collides|scrape|scrapes|brush|brushes)\b")
_CLEAR_RE = re.compile(r"\b(clearance|away from|distance from|margin|clear of|keep \w+ ?\w* (at least|\d))")
_CARE_RE = re.compile(r"\b(careful(ly)?|gentl[ey]|gentleness)\b")
_GRASP_ORDER_RE = re.compile(r"\b(pick(s|ed|ing)? up|grab(s|bed|bing)?|grasp(s|ed|ing)?|start with|begin with|"
                             r"start by|begin by)\b")
_PLACED_ORDER_RE = re.compile(r"\b(in the tray before|into the tray before|is placed|are placed|gets placed|"
                              r"before (the \w+ )?(is |gets )?(placed|put in|set down))\b")
_ORDER_RE = re.compile(r"\b(order|first|before|after|last|sequence|start with|then)\b")
_GOAL_RE = re.compile(r"\b(pack|packed|packing|go in the tray|goes in the tray|go into the tray|"
                      r"in the tray|into the tray|leave|left out|put .* in the tray)\b")
_NEG_RE = re.compile(r"(\bnot\b|n't\b|\bnever\b|\bno need\b|\bwithout\b|\bavoid\b|\bcannot\b|\bnor\b|\bno\b|\bnothing\b)")
_PERMIT_RE = re.compile(r"\b(can|may|allowed|fine|ok|okay|free to|is acceptable|doesn't matter|does not matter|"
                        r"irrelevant|optional|either way)\b")
_MATTERS_RE = re.compile(r"\b(matters|counts|is important|is what matters|care about)\b")
_IRRELEVANT_RE = re.compile(r"(doesn't matter|does not matter|don't care|do not care|irrelevant|optional|either way|"
                            r"\bforget (about )?|never mind|don't worry about|do not worry about|\bignore\b|no longer|"
                            r"is fine|is ok|is okay|not important|no matter)")
_TRANSPORT_RE = re.compile(r"(transport|\bcarr(y|ies|ied|ying)\b|\bmov(e|es|ed|ing)\b|in transit|on the way|en route|"
                           r"whole way|whole time|entire time|throughout|while (it is |it's )?(held|moving|being moved|"
                           r"in the gripper|grasped)|\bduring\b|in the air|\blift)")
_FINAL_RE = re.compile(r"(destination|at the end|in the tray|into the tray|when (it is |it's )?(placed|put down|released|"
                       r"set down|dropped off)|once (it is |it's )?placed|\bfinal(ly)?\b|when you put|after (placing|release)|"
                       r"\bwhen it lands|at rest|when (you )?(set|put|place|lay|drop)s? (it|them|the \w+) (down|in)|"
                       r"\b(set|put|lay)(ting)? (it |them )?down\b|when placing|once (it is |it's )?(down|released|in the tray))")
_ALWAYS_RE = re.compile(r"(at all times|\balways\b|\bever\b|\bnever\b)")
_DEG_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:°|deg\b|degs\b|degrees?\b)")
_DIST_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(cm|mm|m)\b")
_UNIVERSAL_RE = re.compile(r"\b(all|every|everything|each|any object|all objects|nothing)\b")
_YES_RE = re.compile(r"^\s*(yes|yeah|yep|sure|both|either|they both|both count|both are fine|that's fine|fine|ok|okay)\b")
_NO_RE = re.compile(r"^\s*(no|nope|not both|neither|only one|only the)\b")
_UNSURE_RE = re.compile(r"\b(maybe|not sure|unsure|don't know|do not know|depends|perhaps|i guess)\b")


def _kinds_in(text: str) -> list[str]:
    found = []
    for k, rx in _KIND_RE.items():
        m = rx.search(text)
        if m:
            found.append((m.start(), k))
    return [k for _, k in sorted(found)]


def _phrase_kind(kind: str) -> str:
    return {"cup": "cup", "block": "block", "marker": "marker"}[kind]


# ---------------------------------------------------------------- proposal ---

def propose_offline(summary: DemoSummary, scene: SceneState) -> ProposalDraft:
    regs = summary.regularities
    beads = {o.kind: o.beads for o in scene.objects}
    included = []
    objects = []
    for kind in ("cup", "block", "marker"):
        objs = [o for o in summary.objects if o.kind == kind]
        if not objs:
            continue
        inc = all(o.ended_in_tray for o in objs)
        rids = [r.id for r in regs if r.kind == "inside" and r.object_kind == kind]
        if inc:
            included.append(kind)
        objects.append(DraftObject(
            kind=kind, include_in_task=inc, regularity_ids=rids,
            evidence=(f"The {kind} ended inside the tray." if inc else f"The {kind} did not end in a tray.")))
    rules, dismissed = [], []
    for r in regs:
        k = r.object_kind
        if r.kind == "carry_upright" and k in included:
            lean = "likely_required" if beads.get(k, 0) > 0 else "unclear"
            rules.append(DraftRule(
                type="upright", object_kind=k, phase="transport", max_tilt_deg=15.0, contact_with=None,
                min_clearance_m=None, regularity_ids=[r.id], shown_by_demo=r.statement,
                assumption=(f"Scene knowledge, not demonstration evidence: the scene description lists loose "
                            f"beads in the {k}, so its orientation may matter while it moves."
                            if beads.get(k, 0) > 0 else
                            f"Carrying the {k} upright may simply have been convenient."),
                lean=lean,
                question=(f"The {k} stayed upright the whole time it was carried. Must it stay upright "
                          f"during transport, or only once it is placed?"
                          + (f" (The lean towards 'required' comes from the scene description, which lists "
                             f"loose beads in the {k}; the demonstration itself does not show that it matters.)"
                             if beads.get(k, 0) > 0 else ""))))
        elif r.kind == "final_upright" and k in included:
            rules.append(DraftRule(
                type="upright", object_kind=k, phase="final", max_tilt_deg=15.0, contact_with=None,
                min_clearance_m=None, regularity_ids=[r.id], shown_by_demo=r.statement,
                assumption=f"Objects are normally packed standing the way they started.",
                lean="likely_required",
                question=f"Must the {k} end upright in the tray?"))
        elif r.kind == "route":
            dismissed.append(DraftDismissal(regularity_id=r.id, reason=(
                "The route follows from where the obstacles are; it is not treated as a rule. "
                "Any requirement about how the object is carried is captured by the orientation rules.")))
        elif r.kind == "clearance":
            dismissed.append(DraftDismissal(regularity_id=r.id, reason=(
                "A distance from the obstacle was observed, but nothing indicates a required margin.")))
        elif r.kind == "carry_tilted":
            dismissed.append(DraftDismissal(regularity_id=r.id, reason=(
                "The object was tilted while carried, so no upright requirement is inferred for it.")))
        elif r.kind in ("carry_upright", "final_upright"):
            dismissed.append(DraftDismissal(regularity_id=r.id, reason="Object is not part of the task."))
    rules += _unobserved_tilt_rules(summary, regs, included, rules)
    by_id = {o.object_id: o for o in summary.objects}
    order = [by_id[i].kind for i in summary.order if by_id[i].kind in included]
    ordering = []
    order_reg = [r.id for r in regs if r.kind == "order"]
    for a, b in zip(order, order[1:]):
        if a == b:
            continue
        ordering.append(DraftOrdering(
            before_kind=a, after_kind=b, regularity_ids=order_reg,
            shown_by_demo=f"The {a} was packed before the {b}.", lean="unclear",
            question=f"The {a} was packed before the {b}. Is that order required?"))
    if order_reg and not ordering:
        dismissed += [DraftDismissal(regularity_id=i, reason="Only one task object was moved.") for i in order_reg]
    names = ", ".join(included[:-1]) + (" and " if len(included) > 1 else "") + included[-1] if included else ""
    return ProposalDraft(
        summary=f"Pack the {names} into the tray." if names else "No packing task was demonstrated.",
        observations=[r.statement for r in regs],
        assumptions=[ru.assumption for ru in rules] + [
            "Whether the demonstrated order is required is unknown." if ordering else ""][: 1 if ordering else 0],
        objects=objects, rules=rules, ordering=ordering, dismissed=dismissed)


# --------------------------------------------------------------- correction ---

@dataclass
class _Clause:
    text: str
    kinds: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    pronoun: bool = False
    prop: Optional[str] = None
    positive: Optional[bool] = None
    phases: list[str] = field(default_factory=list)
    only: bool = False
    scope_only: bool = False
    deg: Optional[float] = None
    dist: Optional[float] = None


def _normalize(text: str) -> str:
    t = text.lower().replace("’", "'").replace("—", ", ").replace("–", ", ")
    t = re.sub(r"\bnot (just|only|merely|simply)\b", "and also", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _split(t: str) -> list[str]:
    parts = re.split(r"[.;!?]+|,|\bbut\b|\bhowever\b|\bwhereas\b", t)
    return [p.strip() for p in parts if p and p.strip()]


def _polarity(c: _Clause, t: str) -> Optional[bool]:
    neg = bool(_NEG_RE.search(t))
    permit = bool(_PERMIT_RE.search(t))
    irrelevant = bool(_IRRELEVANT_RE.search(t))
    if c.prop == "upright":
        up, tilt = bool(_UPRIGHT_RE.search(t)), bool(_TILT_RE.search(t))
        if irrelevant:
            return False
        if tilt and c.deg is not None and re.search(
                r"\b(at most|up to|no more than|not more than|maximum|max|within|less than|no further)\b|no more\b", t):
            return True              # may tilt at most N degrees: a bound, so a requirement
        if tilt and not up:
            if neg:
                return True          # must not tilt / can't tip / never spill
            if permit:
                return False         # can tilt / may lie on its side
            return False if re.search(r"\b(is|are) (fine|ok|okay)\b", t) else None
        if up:
            if neg and not re.search(r"\b(never|not) (tilt|tip)", t):
                return False         # doesn't need to stay upright
            return True
    if c.prop == "contact":
        if neg:
            return True
        if permit or irrelevant:
            return False
        return None
    if c.prop == "clearance":
        if irrelevant or (permit and re.search(r"\bclose\b", t)):
            return False
        return True
    if c.prop == "order":
        if irrelevant or re.search(r"\bany order\b", t):
            return False
        if neg:
            return False
        return True
    if c.prop == "goal":
        if re.search(r"\bleave\b|\bleft out\b", t) or neg:
            return False
        return True
    if c.prop is None and (c.kinds or c.pronoun):
        if irrelevant:
            return False
        if _MATTERS_RE.search(t) or re.search(r"\b(must|should|has to|needs to|required)\b", t):
            return True
    return None


def _analyze(t: str) -> _Clause:
    c = _Clause(text=t)
    c.kinds = _kinds_in(t)
    c.unknown = [m.group(1) for m in _UNKNOWN_RE.finditer(t)]
    c.pronoun = bool(_PRONOUN_RE.search(t)) and not c.kinds
    if _UPRIGHT_RE.search(t) or _TILT_RE.search(t):
        c.prop = "upright"
    elif _CONTACT_RE.search(t):
        c.prop = "contact"
    elif _CLEAR_RE.search(t) or (_DIST_RE.search(t) and re.search(r"\b(from|away)\b", t)):
        c.prop = "clearance"
    elif (_ORDER_RE.search(t) or _GRASP_ORDER_RE.search(t)) and (len(c.kinds) >= 1 or "order" in t):
        c.prop = "order"
    elif _GOAL_RE.search(t):
        c.prop = "goal"
    phases = []
    if _TRANSPORT_RE.search(t):
        phases.append("transport")
    if _FINAL_RE.search(t) and c.prop != "goal":
        phases.append("final")
    if not phases and _ALWAYS_RE.search(t):
        phases.append("always")
    c.phases = phases
    c.only = bool(re.search(r"\bonly\b", t))
    c.only_object = bool(re.search(r"\bonly (the |for the )?(" + "|".join(
        w for ws in KIND_WORDS.values() for w in ws) + r")\b", t))
    c.scope_only = c.only and c.prop is None and bool(c.kinds) and bool(_MATTERS_RE.search(t) or re.search(r"\bonly (the|for)\b", t))
    m = _DEG_RE.search(t)
    c.deg = float(m.group(1)) if m else None
    m = _DIST_RE.search(t)
    if m:
        v = float(m.group(1))
        c.dist = v / 100 if m.group(2) == "cm" else v / 1000 if m.group(2) == "mm" else v
    c.positive = _polarity(c, t)
    return c


def _contact_groups(t: str) -> list[str]:
    g = []
    if re.search(r"\b(wall|obstacle|obstacles|barrier|box|boxes)\b", t):
        g.append("obstacle")
    if re.search(r"\btable\b", t):
        g.append("table_outside_tray")
    if re.search(r"\b(other objects|each other|other items|the others)\b", t):
        g.append("other_objects")
    return g or ["obstacle"]


_REVERT_RE = re.compile(r"^(please )?(undo|revert|roll back|go back|take back|cancel)\b|"
                        r"\b(undo (that|the last|my last)|go back to (version|v) ?\d+|revert to (version|v) ?\d+|"
                        r"the way it was( before)?|as it was before)\b")


def revert_request(text: str):
    """RevertRequested if the text asks to undo earlier changes, else None."""
    from mirror.interpret.types import RevertRequested
    t = _normalize(text)
    if not _REVERT_RE.search(t):
        return None
    m = re.search(r"\b(?:version|v) ?(\d+)\b", t)
    if m:
        return RevertRequested(to_version=int(m.group(1)), text=text)
    m = re.search(r"\b(two|2|three|3) (changes|corrections|steps)\b", t)
    steps = {"two": 2, "2": 2, "three": 3, "3": 3}[m.group(1)] if m else 1
    return RevertRequested(steps=steps, text=text)


def compile_offline(spec: TaskSpec, text: str, context: Optional[dict] = None) -> CorrectionDraft:
    context = context or {}
    t = _normalize(text)
    if not t:
        raise CorrectionRejected("empty correction")
    rv = revert_request(text)
    if rv is not None:
        raise rv
    from mirror.interpret.specops import active_goal_kinds
    included = active_goal_kinds(spec) or [b.kind for b in spec.bindings]
    bound = [b.kind for b in spec.bindings]
    clauses = [_analyze(p) for p in _split(t)]
    text_prop = next((c.prop for c in clauses if c.prop), None)
    universal = bool(_UNIVERSAL_RE.search(t))
    text_dists = sorted({c.dist for c in clauses if c.dist is not None})

    for c in clauses:
        if c.unknown and not c.kinds:
            noun = c.unknown[0]
            raise NeedsClarification(
                f"There is no {noun} in this scene. Which object do you mean: "
                + ", ".join(f"the {k}" for k in bound) + "?",
                [f"The {k}" for k in bound], reason=f"unknown object '{noun}'")
        for k in c.kinds:
            if k not in bound:
                raise NeedsClarification(
                    f"There is no {k} in this scene. Which object do you mean: "
                    + ", ".join(f"the {b}" for b in bound) + "?",
                    [f"The {b}" for b in bound], reason=f"{k} not in scene")

    from mirror.interpret.guard import unknown_object_question
    q, alias = unknown_object_question(text, spec, context.get("scene") if isinstance(context.get("scene"), SceneState) else None,
                                       focus_kind=context.get("focus_kind"))
    if q is not None:
        raise q
    alias_note = (f"\"the {alias['word']}\" taken as the {alias['resolved_to']} ({alias['source']})" if alias else "")
    if not text_prop and _CARE_RE.search(t):
        kinds = [k for c in clauses for k in c.kinds] or ([context.get("focus_kind")] if context.get("focus_kind") else [])
        who = f"the {kinds[0]}" if kinds else "it"
        raise NeedsClarification(
            f"I can keep {who} upright (while carried, once placed, or always), keep it a set distance from the "
            f"wall and other obstacles, stop it touching obstacles, or change the packing order. Which of these "
            f"matters for {who}?",
            ["Keep it upright while carried", "Keep it away from obstacles (say how far)",
             "Do not let it touch obstacles", "Pack it first"],
            reason="vague instruction: no supported rule named")
    ops: list[DraftOp] = []
    notes: list[str] = []
    last_kinds: list[str] = []
    focus = context.get("focus_kind")
    only_kinds: list[str] = []
    explicit: dict[tuple, bool] = {}
    prev_positive: Optional[bool] = None
    pending_phases: list[str] = []
    for c in clauses:
        if c.phases and not c.kinds and not c.prop and not c.pronoun and not last_kinds:
            pending_phases = list(c.phases)  # "Once placed, the cup must ..."
            continue
        if pending_phases and not c.phases:
            c.phases = pending_phases
        pending_phases = []
        continuation = bool(c.phases) and bool(last_kinds) and not c.kinds
        prop = c.prop or (text_prop if (c.kinds or c.pronoun or c.scope_only or continuation) else None)
        if prop is None:
            continue
        if c.prop is None:
            c.prop = prop
            pol = _polarity(c, c.text)
            if pol is None and c.text:
                if _IRRELEVANT_RE.search(c.text):
                    pol = False
                elif c.scope_only or _MATTERS_RE.search(c.text):
                    pol = True
                elif prev_positive is not None:
                    pol = (not prev_positive) if _NEG_RE.search(c.text) else prev_positive
            if pol is None:
                # A clause that only names an object ("look at the cup") sets the referent.
                if c.kinds:
                    last_kinds = list(c.kinds)
                continue
            c.positive = pol
        if c.positive is not None:
            prev_positive = c.positive
        kinds = list(c.kinds)
        if prop == "order":
            ops += _order_ops(c, included, notes)
            last_kinds = kinds or last_kinds
            continue
        if not kinds:
            if universal and prop in ("upright", "contact", "clearance"):
                kinds = [None]  # type: ignore[list-item]
            elif last_kinds:
                kinds = list(last_kinds)
            elif focus and focus in bound:
                kinds = [focus]
            elif len(included) == 1:
                kinds = list(included)
            else:
                ref = "it" if c.pronoun else "this rule"
                raise NeedsClarification(
                    f"Which object does {ref!s} refer to: " + ", ".join(f"the {k}" for k in included) + "?",
                    [f"The {k}" for k in included], reason="ambiguous referent")
        last_kinds = [k for k in kinds if k]
        if c.positive is None:
            raise NeedsClarification(
                f"I could not tell whether \"{c.text}\" adds a requirement or removes one. "
                "Should this be required?", ["Required", "Not required"], reason="unclear polarity")
        if (c.scope_only or c.only_object) and c.positive:
            only_kinds += [k for k in kinds if k]
        for k in kinds:
            if prop == "goal":
                ops.append(DraftOp(action="add_goal" if c.positive else "remove_goal", rule=None,
                                   target_id=None, before_kind=None, after_kind=None, goal_kind=k,
                                   rationale=f"User: \"{c.text}\""))
                continue
            rtype = {"upright": "upright", "contact": "forbidden_contact", "clearance": "keep_clearance"}[prop]
            phases = list(c.phases)
            if rtype == "keep_clearance" and c.dist is None and len(text_dists) == 1:
                c.dist = text_dists[0]   # "While carrying the cup, keep it 3 cm from the wall"
            if rtype == "keep_clearance" and c.positive:
                _clearance_questions(c, k)
            if c.positive:
                if not phases:
                    phases = ["transport"]
                    notes.append("No phase was stated; the rule applies while the object is carried, "
                                 "the only time the robot changes its orientation or position.")
                for ph in phases:
                    explicit[(rtype, ph, k)] = True
                    ops.append(DraftOp(action="require_rule", target_id=None, before_kind=None, after_kind=None,
                                       goal_kind=None, rationale=f"User: \"{c.text}\"",
                                       rule=DraftRuleSpec(type=rtype, object_kind=k, phase=ph,
                                                          max_tilt_deg=c.deg if rtype == "upright" else None,
                                                          contact_with=_contact_groups(c.text) if rtype == "forbidden_contact" else None,
                                                          min_clearance_m=c.dist if rtype == "keep_clearance" else None)))
                if (rtype == "upright" and c.only and phases == ["final"]):
                    ops.append(DraftOp(action="relax_rule", target_id=None, before_kind=None, after_kind=None,
                                       goal_kind=None, rationale=f"User: \"{c.text}\" (only at the destination)",
                                       rule=DraftRuleSpec(type="upright", object_kind=k, phase="transport",
                                                          max_tilt_deg=None, contact_with=None, min_clearance_m=None)))
            else:
                for ph in (phases or [None]):
                    explicit[(rtype, ph, k)] = False
                    ops.append(DraftOp(action="relax_rule", target_id=None, before_kind=None, after_kind=None,
                                       goal_kind=None, rationale=f"User: \"{c.text}\"",
                                       rule=DraftRuleSpec(type=rtype, object_kind=k, phase=ph, max_tilt_deg=None,
                                                          contact_with=None, min_clearance_m=None)))
    if only_kinds and text_prop in ("upright", "contact", "clearance"):
        rtype = {"upright": "upright", "contact": "forbidden_contact", "clearance": "keep_clearance"}[text_prop]
        for k in included:
            if k in only_kinds or any(key[2] == k for key in explicit):
                continue
            if any(cn.type == rtype and effective_kind(cn.applies_to, spec) == k and cn.status != Status.rejected
                   for cn in spec.constraints):
                ops.append(DraftOp(action="relax_rule", target_id=None, before_kind=None, after_kind=None,
                                   goal_kind=None, rationale=f"User said only the {', '.join(only_kinds)} matters",
                                   rule=DraftRuleSpec(type=rtype, object_kind=k, phase=None, max_tilt_deg=None,
                                                      contact_with=None, min_clearance_m=None)))
    ops = _merge_requirements(ops)
    if not ops:
        ops = _follow_up_ops(spec, t, clauses)
    if not ops:
        raise CorrectionRejected(
            "This does not describe a supported change: object orientation (upright), contact with "
            "obstacles, clearance, packing order, or which objects go in the tray.")
    interp = "; ".join(_describe_op(o) for o in ops)
    if notes:
        interp += ". " + " ".join(dict.fromkeys(notes))
    if alias_note:
        interp += f" ({alias_note})"
    ops = [o if isinstance(o, DraftOpV4) else DraftOpV4(**o.model_dump(), order_event=None) for o in ops]
    return CorrectionDraftV4(outcome="apply", interpretation=interp, question=None, options=[], ops=ops)


def _tilt_unobserved(o, phase: str) -> bool:
    """True when the evidence did not see the object's tilt in this phase:
    an explicit end/start tilt of None, or observability coverage under 80 %
    of the carry (perception evidence for real or rendered video)."""
    if phase == "final":
        return getattr(o, "end_tilt_deg", 0.0) is None
    obs = getattr(o, "observability", None) or {}
    tilt = obs.get("tilt") if isinstance(obs, dict) else getattr(obs, "tilt", None)
    if isinstance(tilt, dict):
        frames, seen = tilt.get("carry_frames") or 0, tilt.get("carry_observed_frames") or 0
        return frames > 0 and seen / frames < 0.8
    return False


def _unobserved_tilt_rules(summary, regs, included, rules) -> list[DraftRule]:
    """Ask instead of asserting: an included object whose tilt was not seen
    gets an upright question (lean unclear, no regularity) rather than no rule."""
    have = {(r.object_kind, r.phase) for r in rules if r.type == "upright"}
    seen_kinds = {(r.object_kind, r.kind) for r in regs}
    out = []
    for o in summary.objects:
        k = o.kind
        if k not in included:
            continue
        if (k, "transport") not in have and (k, "carry_tilted") not in seen_kinds and o.moved \
                and _tilt_unobserved(o, "transport"):
            out.append(DraftRule(
                type="upright", object_kind=k, phase="transport", max_tilt_deg=15.0, contact_with=None,
                min_clearance_m=None, regularity_ids=[], shown_by_demo="",
                assumption=f"The {k}'s tilt was not observed while it was carried, so nothing is inferred.",
                lean="unclear",
                question=(f"The {k}'s tilt was not observed while it was carried (the cameras did not see it). "
                          f"Must it stay upright during transport?")))
            have.add((k, "transport"))
        if (k, "final") not in have and _tilt_unobserved(o, "final"):
            out.append(DraftRule(
                type="upright", object_kind=k, phase="final", max_tilt_deg=15.0, contact_with=None,
                min_clearance_m=None, regularity_ids=[], shown_by_demo="",
                assumption=f"The {k}'s final tilt was not observed, so nothing is inferred.",
                lean="unclear",
                question=f"The {k}'s tilt in the tray was not observed. Must it end upright?"))
            have.add((k, "final"))
    return out


def _merge_requirements(ops: list[DraftOp]) -> list[DraftOp]:
    """One require_rule per (type, phase, object): clauses that restate the same
    rule are merged, an explicit parameter beating an unstated one. Two
    different explicit values are a contradiction and get a question."""
    out: list[DraftOp] = []
    seen: dict[tuple, int] = {}
    for op in ops:
        r = op.rule
        if op.action != "require_rule" or r is None:
            out.append(op)
            continue
        key = (r.type, r.phase, r.object_kind)
        if key not in seen:
            seen[key] = len(out)
            out.append(op)
            continue
        prev = out[seen[key]].rule
        merged = {}
        for f in ("max_tilt_deg", "min_clearance_m"):
            a, b = getattr(prev, f), getattr(r, f)
            if a is not None and b is not None and a != b:
                raise NeedsClarification(
                    f"You gave two different values for the same rule ({a:g} and {b:g}). Which one should hold?",
                    [f"{a:g}", f"{b:g}"], reason="contradictory values")
            merged[f] = a if a is not None else b
        if prev.type == "forbidden_contact":
            merged["contact_with"] = sorted(set((prev.contact_with or []) + (r.contact_with or []))) or None
        out[seen[key]] = out[seen[key]].model_copy(update={"rule": prev.model_copy(update=merged)})
    return out


def _clearance_questions(c: _Clause, k: Optional[str]) -> None:
    """Clearance is measured from obstacles only, and needs a distance."""
    who = f"the {k}" if k else "every object"
    tail = re.split(r"\b(?:away from|distance from|clear of|from|between)\b", c.text, maxsplit=1)
    target_kinds = [x for x in _kinds_in(tail[1]) if x != k] if len(tail) > 1 else []
    if target_kinds:
        raise NeedsClarification(
            f"I can keep {who} a set distance from the wall and other obstacles, but not from the "
            f"{target_kinds[0]}. Should it keep its distance from the obstacles instead?",
            [f"Yes, keep {who} away from obstacles (say how far)", "No"],
            reason="unsupported rule: clearance from another object")
    if c.dist is None:
        raise NeedsClarification(
            f"How much room should the robot leave between {who} and the wall or other obstacles?",
            ["2 cm", "5 cm", "10 cm"], reason="clearance without a distance")


def _follow_up_ops(spec: TaskSpec, t: str, clauses: list[_Clause]) -> list[DraftOp]:
    """A bare number after an earlier correction ("make that 30 degrees",
    "actually 8 cm") adjusts the rule the previous version changed."""
    from mirror.interpret.specops import effective_kind as ek
    if not spec.history:
        return []
    deg = next((c.deg for c in clauses if c.deg is not None), None)
    dist = next((c.dist for c in clauses if c.dist is not None), None)
    if deg is None and dist is None:
        return []
    want = "upright" if deg is not None else "keep_clearance"
    by_id = {x.id: x for x in spec.constraints}
    for rec in reversed(spec.history):
        for d in reversed(rec.diff):
            cn = by_id.get(d.target_id or "")
            if cn is not None and cn.type == want and cn.status != Status.rejected:
                return [DraftOp(action="require_rule", target_id=None, before_kind=None, after_kind=None,
                                goal_kind=None, rationale=f"User: \"{t}\" (adjusts {cn.id} from version {rec.version})",
                                rule=DraftRuleSpec(type=want, object_kind=ek(cn.applies_to, spec), phase=cn.phase,
                                                   max_tilt_deg=deg if want == "upright" else None,
                                                   contact_with=None,
                                                   min_clearance_m=dist if want == "keep_clearance" else None))]
    return []


def _order_event(t: str) -> str:
    if _GRASP_ORDER_RE.search(t):
        return "start_before_start"
    if _PLACED_ORDER_RE.search(t):
        return "placed_before_placed"
    return "finish_before_start"


def _order_ops(c: _Clause, included: list[str], notes: list[str]) -> list[DraftOp]:
    t = c.text
    ev = _order_event(t)
    mk = lambda action, b=None, a=None: DraftOpV4(action=action, rule=None, target_id=None, before_kind=b,
                                                  after_kind=a, goal_kind=None, rationale=f"User: \"{t}\"",
                                                  order_event=ev if action == "require_order" else None)
    if c.positive is False:
        if len(c.kinds) >= 2:
            return [mk("relax_order", c.kinds[0], c.kinds[1])]
        if len(c.kinds) == 1:
            return [mk("relax_order", c.kinds[0], None)]
        return [mk("relax_order")]
    if len(c.kinds) >= 2:
        a, b = c.kinds[0], c.kinds[1]
        if re.search(r"\bafter\b", t) and not re.search(r"\bbefore\b", t):
            a, b = b, a
        return [mk("require_order", a, b)]
    if len(c.kinds) == 1:
        k = c.kinds[0]
        others = [o for o in included if o != k]
        if re.search(r"\b(last|after everything|at the end)\b", t):
            return [mk("require_order", o, k) for o in others]
        if re.search(r"\b(first|start with|begin with|start by|begin by|before everything|before the others)\b", t):
            return [mk("require_order", k, o) for o in others]
    raise NeedsClarification("Which order do you want? Name the object that should go first.",
                             [f"The {k} first" for k in included], reason="unclear ordering")


def _describe_op(o: DraftOp) -> str:
    if o.rule is not None:
        r = o.rule
        who = f"the {r.object_kind}" if r.object_kind else "every object"
        ph = r.phase or "any phase"
        verb = "require" if o.action == "require_rule" else "do not require"
        extra = f" (max {r.max_tilt_deg:g} degrees)" if r.max_tilt_deg else ""
        return f"{verb} {r.type} for {who} during {ph}{extra}"
    if o.action in ("require_order", "relax_order"):
        verb = "require" if o.action == "require_order" else "do not require"
        if o.before_kind and o.after_kind:
            return f"{verb} the {o.before_kind} before the {o.after_kind}"
        return f"{verb} a packing order"
    if o.goal_kind:
        return f"{'pack' if o.action == 'add_goal' else 'do not pack'} the {o.goal_kind}"
    return f"{o.action} {o.target_id or ''}".strip()


# ------------------------------------------------------------------ answers ---

def parse_yes_no(answer: str) -> tuple[Optional[str], str]:
    """Return ("yes" | "no" | None, remaining text)."""
    a = _normalize(answer)
    if _UNSURE_RE.search(a):
        return None, a
    m = _NO_RE.match(a)
    if m:
        rest = a[m.end():].strip(" ,.-:")
        if m.group(1).startswith("only"):
            rest = a
        return "no", rest
    m = _YES_RE.match(a)
    if m:
        return "yes", a[m.end():].strip(" ,.-:")
    if re.search(r"\b(must|should|has to|needs to|never|must not|can't|cannot)\b", a):
        return "no", a
    return None, a
