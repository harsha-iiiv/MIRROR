"""Per-step provenance shown beside every result of the guided test flow.

``kind`` says how the result came to exist *for this response*:

  fresh_model        a model was called now (live request)
  replayed_cassette  the interpreter ran now, but the model's response was replayed from a cassette
  recorded_model     model output shipped with a recording (static site, tour); nothing ran now
  offline_fallback   the offline rule-based interpreter produced it (configured, or because a model
                     could not be used; ``fallback_reason`` says which)
  new_simulation     MuJoCo executed now
  recorded_playback  simulation output shipped with a recording; nothing ran now
  new_check          the independent checker ran now on a recorded step log (no new simulation)
  fixture            interface fixture: key-framed motion, no physics and no model (development only)

A healthy backend says nothing about any of these: each response carries its own.
Latency is ``measured_now`` only when the work happened in this request; replayed
responses report the latency recorded with the cassette (``latency_source``), or null.
Cost is null unless MIRROR_LLM_USD_PER_1K_TOKENS is set (then an estimate, labelled so);
no price is ever invented.
"""
from __future__ import annotations

import os
from typing import Any, Optional

KINDS = ("fresh_model", "replayed_cassette", "recorded_model", "offline_fallback", "new_simulation",
         "recorded_playback", "new_check", "fixture")

LABELS = {
    "fresh_model": "Fresh model inference (called now)",
    "replayed_cassette": "Recorded model response, replayed now",
    "recorded_model": "Recorded model output (nothing ran now)",
    "offline_fallback": "Offline rule-based interpreter",
    "new_simulation": "New simulation (MuJoCo ran now)",
    "recorded_playback": "Recorded simulation playback (nothing ran now)",
    "new_check": "Checked now from the recorded step log",
    "fixture": "Interface fixture (no physics, no model)",
}


def interpreter_version() -> dict:
    try:
        from ..interpret.prompts import PROMPT_REVISION
    except Exception:  # noqa: BLE001
        PROMPT_REVISION = None
    return {"prompt_revision": os.environ.get("MIRROR_PROMPT_REVISION") or PROMPT_REVISION,
            "code_version": os.environ.get("MIRROR_CODE_VERSION") or None}


def cost_estimate(tokens: Optional[int]) -> tuple[Optional[float], Optional[str]]:
    price = os.environ.get("MIRROR_LLM_USD_PER_1K_TOKENS")
    if not tokens or not price:
        return None, None
    try:
        return round(float(price) * tokens / 1000.0, 6), "estimate from MIRROR_LLM_USD_PER_1K_TOKENS"
    except ValueError:
        return None, None


def model_step(source: str, *, model: Optional[str], latency_s: Optional[float], tokens: Optional[int] = None,
               fallback_reason: Optional[str] = None, latency_source: Optional[str] = None,
               status: Optional[str] = None, interpreter: Optional[str] = None) -> dict:
    """Provenance of an interpretation step from the studio's internal source label
    (live | recorded | offline rules | offline rules (fallback) | fixture)."""
    s = (source or "").lower()
    if s.startswith("fixture"):
        kind = "fixture"
    elif "offline" in s:
        kind = "offline_fallback"
    elif s in ("recorded", "replay", "replayed_cassette"):
        kind = "replayed_cassette"
    elif s in ("live", "record", "fresh_model", "fresh_model_call"):
        kind = "fresh_model"
    elif s in ("recorded_model", "recording"):
        kind = "recorded_model"
    else:
        kind = "offline_fallback"
    if kind == "offline_fallback" and not fallback_reason:
        fallback_reason = "the server is configured to use the offline rule-based interpreter"
    cost, cost_note = cost_estimate(tokens) if kind == "fresh_model" else (None, None)
    return {"kind": kind, "label": LABELS[kind], "model": model if kind != "offline_fallback" else None,
            "interpreter": interpreter or ("offline-rules" if kind == "offline_fallback" else model),
            "interpreter_version": interpreter_version(),
            "request_status": status or ("fallback" if fallback_reason and kind == "offline_fallback"
                                         and "configured" not in fallback_reason else "ok"),
            "latency_s": round(float(latency_s), 3) if latency_s is not None else None,
            "latency_source": latency_source or ("measured_now" if kind != "replayed_cassette" else
                                                 ("recorded_with_cassette" if latency_s is not None else None)),
            "tokens": tokens, "cost_usd": cost, "cost_note": cost_note,
            "fallback_reason": fallback_reason}


def sim_step(mode: str, *, wall_s: Optional[float] = None, status: str = "ok", recorded: bool = False) -> dict:
    kind = "fixture" if mode == "fixture" else ("recorded_playback" if recorded else "new_simulation")
    return {"kind": kind, "label": LABELS[kind], "model": None, "interpreter": None,
            "simulator": "fixture (key-framed)" if mode == "fixture" else "MuJoCo",
            "request_status": status, "latency_s": round(wall_s, 3) if wall_s is not None else None,
            "latency_source": None if recorded else "measured_now", "cost_usd": None, "fallback_reason": None}


def check_step(wall_s: Optional[float] = None, rollout_kind: Optional[str] = None) -> dict:
    return {"kind": "new_check", "label": LABELS["new_check"], "model": None, "interpreter": None,
            "checker": "mirror.evaluate.check (independent of the planner)", "request_status": "ok",
            "latency_s": round(wall_s, 3) if wall_s is not None else None, "latency_source": "measured_now",
            "rollout_provenance": rollout_kind, "cost_usd": None, "fallback_reason": None}


def media_step(source: Optional[str], *, model: Optional[str], latency_s: Optional[float],
               status: str = "ok") -> dict:
    """SAM tracking, video observation and speech-to-text steps (meta bridge sources)."""
    s = (source or "").lower()
    kind = ("fixture" if s.startswith("fixture") else "fresh_model" if s.startswith("live")
            else "replayed_cassette" if s in ("recorded", "replay") else "recorded_model" if s == "recording"
            else "offline_fallback")
    return {"kind": kind, "label": LABELS[kind], "model": model, "interpreter": None,
            "request_status": status, "latency_s": round(float(latency_s), 3) if latency_s is not None else None,
            "latency_source": "measured_now" if kind in ("fresh_model", "fixture") else
            ("recorded_with_cassette" if latency_s is not None else None),
            "cost_usd": None, "fallback_reason": None if kind != "offline_fallback" else f"source {source!r}"}


def as_recorded(p: Optional[dict]) -> Optional[dict]:
    """The same step, viewed later from a recording: nothing runs now."""
    if not p:
        return p
    kind = {"fresh_model": "recorded_model", "replayed_cassette": "recorded_model",
            "new_simulation": "recorded_playback"}.get(p.get("kind"), p.get("kind"))
    return {**p, "kind": kind, "label": LABELS.get(kind, p.get("label")), "recorded_from": p.get("kind")}
