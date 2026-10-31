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


def _model_context(context: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not context:
        return None
    out = {k: v for k, v in context.items() if k not in ("scene", "kind") and v is not None}
    return out or None


def compile_correction(spec: TaskSpec, text: str, client: Optional[LLMClient] = None,
                       context: Optional[dict[str, Any]] = None) -> tuple[TaskSpec, CorrectionRecord]:
    """Compile ``text`` into scoped ops and apply them, returning version + 1.

    ``context`` (all optional): ``scene`` (SceneState or dict) to detect objects
    that are not present, ``focus_kind`` for the object currently selected in
    the interface, ``kind`` for the record kind.

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
        return apply_ops(spec, ops, text=text, kind=kind, model=OFFLINE_LABEL)

    def validate(d: CorrectionDraft) -> list[str]:
        try:
            correction_to_ops(d, spec, text, scene_kinds)
        except (NeedsClarification, CorrectionRejected):
            return []
        except InterpretError as e:
            return [str(e)]
        return []

    res = client.complete_json(
        correct_messages(spec, text, sorted(scene_kinds) if scene_kinds else None, _model_context(context)),
        CorrectionDraft, name="compile_correction", validate=validate)
    ops = correction_to_ops(res.data, spec, text, scene_kinds)
    return apply_ops(spec, ops, text=text, kind=kind, model=res.model)
