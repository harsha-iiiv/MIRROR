"""Authoring conditions. Each turns one turn into an outcome and, for
"apply", the next TaskSpec version. Nothing here plans, executes or scores.

  mirror_model      the product path: compile_correction with a model client
                    (Muse Spark in the paid run; replay or a stand-in in dry runs)
  offline_rules     compile_correction with the deterministic offline rules
  manual_structured an independent editor's structured edit, applied directly

Spoken turns: only mirror_model hears audio (transcribed first); the other
conditions receive the typed text.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from mirror.contracts import SceneState, TaskSpec
from mirror.interpret import CorrectionRejected, InterpretError, NeedsClarification, compile_correction
from mirror.interpret.llm import CassetteMiss

from .edits import apply_edit
from .schema import Turn

CONDITIONS = ("mirror_model", "offline_rules", "manual_structured")


@dataclass
class TurnResult:
    outcome: str                      # apply | asks | rejects | abstain | invalid | error | not_measured
    spec: Optional[TaskSpec] = None   # the spec after this turn (unchanged unless "apply")
    detail: str = ""
    model: Optional[str] = None
    latency_s: float = 0.0
    usage: list[dict] = field(default_factory=list)
    transcript: Optional[dict] = None
    interpreter: str = ""

    def public(self) -> dict[str, Any]:
        return {"outcome": self.outcome, "detail": self.detail[:400], "model": self.model,
                "latency_s": round(self.latency_s, 3), "interpreter": self.interpreter,
                "transcript": self.transcript, "calls": len(self.usage),
                "network_calls": sum(1 for u in self.usage if not u.get("from_cassette"))}


def _compile(spec: TaskSpec, text: str, client, scene: SceneState, kind: str) -> TurnResult:
    t0 = time.monotonic()
    n0 = len(client.usage) if client is not None else 0
    label = client.label if client is not None else "offline-rules"
    try:
        new, rec = compile_correction(spec, text, client, context={"scene": scene, "kind": kind}
                                      if kind != "correction" else {"scene": scene})
        res = TurnResult("apply", new, model=rec.model)
    except CassetteMiss as e:          # replay without a recording: not measured, never a fallback
        res = TurnResult("not_measured", spec, detail=f"no recording: {e}")
    except NeedsClarification as e:
        res = TurnResult("asks", spec, detail=e.question)
    except CorrectionRejected as e:
        res = TurnResult("rejects", spec, detail=str(e))
    except InterpretError as e:
        res = TurnResult("error", spec, detail=f"{type(e).__name__}: {e}")
    res.latency_s = time.monotonic() - t0
    res.interpreter = label
    if client is not None:
        res.usage = [dict(u) for u in client.usage[n0:]]
    return res


@dataclass
class Condition:
    name: str
    run_turn: Callable[[TaskSpec, Turn, SceneState, Path], TurnResult]
    provenance: str                   # fresh model call | replayed cassette | offline interpreter | manual edit


def mirror_model(client, *, transcriber: Optional[Callable] = None, label: Optional[str] = None) -> Condition:
    """``client``: an LLMClient (live/record/replay) or, for dry runs only, any
    object with the same complete_json/usage interface. ``transcriber(path)``
    returns a mirror.interpret.voice.Transcript for spoken turns."""
    mode = getattr(client, "mode", "replay")
    prov = {"live": "fresh model call", "record": "fresh model call", "replay": "replayed cassette"}.get(
        mode, "offline interpreter")

    def run(spec: TaskSpec, turn: Turn, scene: SceneState, base_dir: Path) -> TurnResult:
        text, tr = turn.text, None
        if turn.speech is not None:
            if transcriber is None:
                return TurnResult("error", spec, detail="spoken turn but no transcriber configured",
                                  interpreter=label or getattr(client, "label", "?"))
            p = Path(turn.speech.audio)
            p = p if p.is_absolute() else base_dir / p
            try:
                t = transcriber(p)
            except Exception as e:  # a failed transcription is a counted failure, not a fallback
                return TurnResult("error", spec, detail=f"transcription failed: {type(e).__name__}: {e}",
                                  interpreter=label or getattr(client, "label", "?"))
            tr = {"text": t.text, "model": t.model, "from_cassette": t.from_cassette,
                  "speaker": turn.speech.speaker, "speaker_id": turn.speech.speaker_id,
                  "latency_s": t.latency_s, "audio_sha256": t.source_sha256}
            text = t.text
        res = _compile(spec, text, client, scene, turn.kind)
        res.transcript = tr
        if label:
            res.interpreter = label
        return res
    return Condition("mirror_model", run, prov)


def offline_rules() -> Condition:
    def run(spec: TaskSpec, turn: Turn, scene: SceneState, base_dir: Path) -> TurnResult:
        return _compile(spec, turn.text, None, scene, turn.kind)
    return Condition("offline_rules", run, "offline interpreter")


def manual_structured() -> Condition:
    def run(spec: TaskSpec, turn: Turn, scene: SceneState, base_dir: Path) -> TurnResult:
        m = turn.manual
        if m is None:
            return TurnResult("error", spec, detail="no manual edit was collected for this turn",
                              interpreter="manual")
        if m.edit is None or m.edit.empty():
            return TurnResult("abstain", spec, detail=f"editor {m.editor_id} made no change",
                              interpreter="manual", latency_s=m.seconds or 0.0)
        try:
            new, _ = apply_edit(spec, m.edit, text=turn.text, model=None)
        except (InterpretError, ValueError) as e:
            return TurnResult("invalid", spec, detail=f"{type(e).__name__}: {e}", interpreter="manual",
                              latency_s=m.seconds or 0.0)
        return TurnResult("apply", new, interpreter=f"manual:{m.editor_id}", latency_s=m.seconds or 0.0)
    return Condition("manual_structured", run, "manual edit")
