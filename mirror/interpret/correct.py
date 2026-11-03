"""Natural-language correction -> scoped SpecOps -> next spec version."""
from __future__ import annotations

from typing import Any, Optional

from mirror.contracts import CorrectionRecord, SceneState, TaskSpec
from mirror.interpret.drafts import CorrectionDraft, correction_to_ops
from mirror.interpret.llm import LLMClient
from mirror.interpret.offline import OFFLINE_LABEL, compile_offline
from mirror.interpret.prompts import correct_messages
from mirror.interpret.specops import apply_ops
from mirror.interpret.types import CorrectionRejected, InterpretError, NeedsClarification

_KINDS = ("correction", "clarification_answer", "confirmation")


def _scene_kinds(context: Optional[dict[str, Any]]) -> Optional[set[str]]:
    if not context:
        return None
    scene = context.get("scene")
    if isinstance(scene, SceneState):
        return {o.kind for o in scene.objects}
    if isinstance(scene, dict) and "objects" in scene:
        return {o["kind"] for o in scene["objects"]}
    if context.get("scene_kinds") is not None:
        return set(context["scene_kinds"])
    return None


_LOCAL_KEYS = ("scene", "kind", "validation_layer")  # never sent to the model


def _model_context(context: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not context:
        return None
    out = {k: v for k, v in context.items() if k not in _LOCAL_KEYS and v is not None}
    return out or None


def prompt_revision(explicit: Optional[str] = None) -> str:
    """Correction prompt revision: explicit argument, else MIRROR_PROMPT_REVISION,
    else "3" (the revision every recorded response was made with)."""
    import os
    rev = explicit or os.environ.get("MIRROR_PROMPT_REVISION") or "3"
    if rev not in ("3", "4"):
        raise ValueError(f"unknown correction prompt revision {rev!r}")
    return rev


def compile_correction(spec: TaskSpec, text: str, client: Optional[LLMClient] = None,
                       context: Optional[dict[str, Any]] = None,
                       prompt_revision_: Optional[str] = None) -> tuple[TaskSpec, CorrectionRecord]:
    """Compile ``text`` into scoped ops and apply them, returning version + 1.

    ``context`` (all optional): ``scene`` (SceneState or dict) to detect objects
    that are not present, ``focus_kind`` for the object currently selected in
    the interface, ``kind`` for the record kind, ``validation_layer`` (default
    True) to run the deterministic checks of mirror.interpret.guard after the
    interpreter.

    Raises NeedsClarification (ambiguous referent, unknown object,
    contradiction) with a question to show the user, CorrectionRejected when the
    text is not a supported change, and InterpretError for invalid output."""
    if not text or not text.strip():
        raise CorrectionRejected("empty correction")
    kind = (context or {}).get("kind", "correction")
    if kind not in _KINDS:
        raise ValueError(f"record kind must be one of {_KINDS}")
    scene_kinds = _scene_kinds(context)
    if client is None or client.offline:
        draft = compile_offline(spec, text, context)
        ops = correction_to_ops(draft, spec, text, scene_kinds)
        _validation_layer(spec, text, context)
        return apply_ops(spec, ops, text=text, kind=kind, model=OFFLINE_LABEL)

    def validate(d: CorrectionDraft) -> list[str]:
        try:
            correction_to_ops(d, spec, text, scene_kinds)
        except (NeedsClarification, CorrectionRejected):
            return []
        except InterpretError as e:
            return [str(e)]
        return []

    rev = prompt_revision(prompt_revision_)
    if rev == "4":
        from mirror.interpret.drafts import CorrectionDraftV4
        from mirror.interpret.offline import revert_request
        from mirror.interpret.prompts import correct_messages_v4
        if revert_request(text) is not None:
            raise revert_request(text)
        messages = correct_messages_v4(spec, text, sorted(scene_kinds) if scene_kinds else None,
                                       _model_context(context))
        schema = CorrectionDraftV4
    else:
        messages = correct_messages(spec, text, sorted(scene_kinds) if scene_kinds else None,
                                    _model_context(context))
        schema = CorrectionDraft
    res = client.complete_json(messages, schema, name="compile_correction", validate=validate)
    ops = correction_to_ops(res.data, spec, text, scene_kinds)
    _validation_layer(spec, text, context)
    return apply_ops(spec, ops, text=text, kind=kind, model=res.model)


def _validation_layer(spec: TaskSpec, text: str, context: Optional[dict[str, Any]]) -> None:
    """Deterministic checks after the interpreter, before anything is applied."""
    if context and context.get("validation_layer") is False:
        return
    from mirror.interpret.guard import absent_object_question, unknown_object_question
    scene = (context or {}).get("scene")
    q = absent_object_question(text, spec, scene if isinstance(scene, SceneState) else None)
    if q is not None:
        raise q
    q, _ = unknown_object_question(text, spec, scene if isinstance(scene, SceneState) else None,
                                   focus_kind=(context or {}).get("focus_kind"))
    if q is not None:
        raise q



def apply_correction(versions: list[TaskSpec], text: str, client: Optional[LLMClient] = None,
                     context: Optional[dict[str, Any]] = None,
                     prompt_revision_: Optional[str] = None) -> tuple[TaskSpec, CorrectionRecord]:
    """Multi-turn entry point: ``versions`` is the version history (oldest
    first, the last one current). Undo requests ("undo that", "go back to
    version 2") produce a new version equal to the earlier one, through
    explicit ops recorded with kind "revert"; everything else goes through
    compile_correction. Version numbers only increase."""
    from mirror.interpret.offline import revert_request
    from mirror.interpret.specops import revert_to
    from mirror.interpret.types import RevertRequested
    if not versions:
        raise ValueError("no specification versions")
    cur = versions[-1]
    rv = revert_request(text)
    if rv is None:
        try:
            return compile_correction(cur, text, client=client, context=context,
                                      prompt_revision_=prompt_revision_)
        except RevertRequested as e:   # offline path detects it too
            rv = e
    by_v = {s.version: s for s in versions}
    if rv.to_version is not None:
        target = by_v.get(rv.to_version)
        if target is None:
            raise NeedsClarification(f"There is no version {rv.to_version}. Which version should I go back to?",
                                     [f"Version {v}" for v in sorted(by_v) if v < cur.version],
                                     reason="unknown version")
    else:
        chain, v = [], cur
        for _ in range(rv.steps):
            if v.parent_version is None or v.parent_version not in by_v:
                break
            v = by_v[v.parent_version]
            chain.append(v)
        if not chain:
            raise CorrectionRejected("there is no earlier version to go back to")
        target = chain[-1]
    return revert_to(cur, target, text=text)
