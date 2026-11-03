"""Structured outcome of interpreting one correction, for the studio and web.

``interpret`` never raises for a user-level outcome: every sentence ends as
apply, clarify, refuse or revert, with a reason code, the question or the
refusal text, the supported alternatives, the spec diff and the provenance of
the interpretation (offline interpreter, fresh model call, replayed cassette).
"""
from __future__ import annotations

import time
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from mirror.contracts import SpecOp, TaskSpec

SUPPORTED_CHANGES = [
    "keep an object upright (while carried, once placed, or at all times), optionally with a tilt limit in degrees",
    "keep an object a stated distance from the wall and other obstacles",
    "stop an object touching obstacles, other objects, or (once placed) the table",
    "packing order between objects (pack first, pick up first, or in the tray first)",
    "which objects go in the tray",
    "undo a change or go back to an earlier version",
]

Outcome = Literal["apply", "clarify", "refuse", "revert"]
Provenance = Literal["offline interpreter", "fresh model call", "replayed cassette", "no model call"]


class InterpretationOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Outcome
    reason_code: str
    message: str = ""                       # the question, the refusal, or the interpretation
    options: list[str] = Field(default_factory=list)
    interpretation: str = ""
    supported_changes: list[str] = Field(default_factory=list)
    spec_version: Optional[int] = None      # version produced (apply / revert)
    parent_version: Optional[int] = None
    ops: list[SpecOp] = Field(default_factory=list)
    diff: list[dict[str, Any]] = Field(default_factory=list)
    alias: Optional[dict[str, str]] = None  # {"word", "resolved_to", "source"}
    interpreter: str = ""
    provenance: Provenance = "offline interpreter"
    prompt_revision: Optional[str] = None
    latency_s: float = 0.0
    model_calls: list[dict[str, Any]] = Field(default_factory=list)   # usage records of this interpretation


def _provenance(client, calls: list[dict]) -> tuple[str, str]:
    """From what actually happened: the client's call records for this request."""
    if client is None or getattr(client, "offline", False):
        return "offline interpreter", "offline-rules"
    if not calls:
        return "no model call", getattr(client, "model", "")
    if any(not c.get("from_cassette") for c in calls):
        return "fresh model call", calls[-1].get("model") or getattr(client, "model", "")
    return "replayed cassette", calls[-1].get("model") or getattr(client, "model", "")


def interpret(versions: list[TaskSpec], text: str, client=None, context: Optional[dict] = None,
              prompt_revision: Optional[str] = None) -> tuple[InterpretationOutcome, Optional[TaskSpec]]:
    from mirror.interpret.correct import apply_correction, prompt_revision as _rev
    from mirror.interpret.diff import spec_diff
    from mirror.interpret.guard import unknown_object_question
    from mirror.interpret.types import CorrectionRejected, InterpretError, NeedsClarification
    t0 = time.perf_counter()
    n0 = len(getattr(client, "usage", []) or []) if client is not None else 0
    cur = versions[-1]

    def base():
        calls = list((getattr(client, "usage", []) or [])[n0:]) if client is not None else []
        prov, interp = _provenance(client, calls)
        rev = None if prov == "offline interpreter" else _rev(prompt_revision)
        return dict(interpreter=interp, provenance=prov, prompt_revision=rev, model_calls=calls)
    try:
        new, rec = apply_correction(versions, text, client=client, context=context, prompt_revision_=prompt_revision)
    except NeedsClarification as e:
        return InterpretationOutcome(outcome="clarify", reason_code=e.code, message=e.question, options=e.options,
                                     interpretation=e.interpretation,
                                     supported_changes=SUPPORTED_CHANGES if e.code in ("vague", "unsupported_rule") else [],
                                     latency_s=round(time.perf_counter() - t0, 4), **base()), None
    except CorrectionRejected as e:
        msg = str(e)
        code = "no_change" if "already says this" in msg else "not_supported"
        if code == "not_supported":
            msg = (f"I can't do that: {msg.rstrip('.')}. MIRROR supports these changes: "
                   + "; ".join(SUPPORTED_CHANGES) + ".")
        return InterpretationOutcome(outcome="refuse", reason_code=code, message=msg,
                                     supported_changes=SUPPORTED_CHANGES if code == "not_supported" else [],
                                     latency_s=round(time.perf_counter() - t0, 4), **base()), None
    except InterpretError as e:
        return InterpretationOutcome(outcome="refuse", reason_code="invalid", message=str(e),
                                     latency_s=round(time.perf_counter() - t0, 4), **base()), None
    _, alias = unknown_object_question(text, cur, (context or {}).get("scene") if hasattr((context or {}).get("scene"), "objects") else None,
                                       focus_kind=(context or {}).get("focus_kind"))
    out = InterpretationOutcome(
        outcome="revert" if rec.kind == "revert" else "apply", reason_code=rec.kind,
        message=_summary(rec, new), interpretation=_summary(rec, new), spec_version=new.version,
        parent_version=new.parent_version, ops=list(rec.diff), diff=spec_diff(cur, new), alias=alias,
        latency_s=round(time.perf_counter() - t0, 4), **base())
    return out, new


def _summary(rec, new: TaskSpec) -> str:
    items = {x.id: x for x in list(new.constraints) + list(new.ordering) + list(new.goals)}
    parts = []
    for o in rec.diff:
        it = items.get(o.target_id or "")
        if o.op == "resolve_uncertainty" or it is None:
            if o.op.startswith("remove"):
                parts.append(f"removed {o.target_id}")
            continue
        desc = getattr(it, "description", "") or it.id
        st = it.status.value
        parts.append(f"{desc} ({'required' if st == 'confirmed' else 'not required' if st == 'rejected' else st})")
    return f"Version {rec.version}: " + "; ".join(dict.fromkeys(parts))
