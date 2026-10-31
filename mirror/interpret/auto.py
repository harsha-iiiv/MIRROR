"""Choose the interpreter for command-line and studio sessions.

Rules:
  * MIRROR_LLM_MODE set: respect it (live, record, replay or offline).
  * otherwise, with MIRROR_LLM_MODEL set: replay that model's recorded
    responses (from MIRROR_CASSETTE_DIR) when they match this demonstration's
    proposal request, else use the offline rules. Another model's recordings
    are never substituted for the one asked for.
  * otherwise: replay whichever model's recorded responses match, else use the
    offline rules.
A live call is never made unless MIRROR_LLM_MODE asks for it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Mapping, Optional

from mirror.contracts import DemoTrace
from mirror.interpret.drafts import ProposalDraft
from mirror.interpret.llm import DEFAULT_CASSETTE_DIR, LLMClient, request_key, strict_schema
from mirror.interpret.prompts import propose_messages
from mirror.interpret.summary import summarize_demo


def cassette_dir(env: Mapping[str, str] = os.environ) -> Path:
    return Path(env.get("MIRROR_CASSETTE_DIR") or DEFAULT_CASSETTE_DIR)


def recorded_models(directory: Path) -> dict[str, str]:
    """Requested model id -> model id the API reported, over all cassettes."""
    out: dict[str, str] = {}
    for p in sorted(Path(directory).glob("*.json")):
        try:
            cas = json.loads(p.read_text(encoding="utf-8"))
            out[cas["request"]["model"]] = cas["response"].get("model") or cas["request"]["model"]
        except (OSError, ValueError, KeyError):
            continue
    return out


def proposal_recorded(trace: DemoTrace, model: str, directory: Path) -> bool:
    key = request_key(model, propose_messages(summarize_demo(trace), trace.scene),
                      strict_schema(ProposalDraft))
    p = Path(directory) / f"{key[:24]}.json"
    if not p.exists():
        return False
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("key") == key
    except ValueError:
        return False


def resolve_client(trace: Optional[DemoTrace] = None,
                   env: Mapping[str, str] = os.environ) -> tuple[LLMClient, str]:
    """Return (client, reason)."""
    if env.get("MIRROR_LLM_MODE"):
        client = LLMClient.from_env(env)
        return client, f"MIRROR_LLM_MODE={env['MIRROR_LLM_MODE']}"
    directory = cassette_dir(env)
    wanted = env.get("MIRROR_LLM_MODEL")
    if trace is not None:
        candidates = [wanted] if wanted else list(recorded_models(directory))
        for requested in candidates:
            if proposal_recorded(trace, requested, directory):
                return (LLMClient(mode="replay", model=requested, cassette_dir=directory),
                        f"recorded responses from {requested} match this demonstration")
        reason = (f"no recording from {wanted} in {directory} matches this demonstration" if wanted
                  else "no recording matches this demonstration")
    else:
        reason = "no demonstration to match against recordings"
    return LLMClient(mode="offline"), reason


def describe_client(client: LLMClient) -> str:
    if client.offline:
        return "offline rules (deterministic, no model)"
    if client.mode == "replay":
        reported = recorded_models(client.cassette_dir).get(client.model, client.model)
        return f"recorded responses from {reported} (replay, no network)"
    return f"{client.model} at {client.base_url} ({client.mode})"


def recordings_by_model(directories: list[Path], trace: Optional[DemoTrace] = None) -> list[dict]:
    """One row per (directory, requested model): how many recordings, the model id
    the API reported, and whether the demonstration's proposal is recorded."""
    rows = []
    for d in directories:
        counts: dict[str, int] = {}
        reported: dict[str, str] = {}
        for p in sorted(Path(d).glob("*.json")):
            try:
                cas = json.loads(p.read_text(encoding="utf-8"))
                m = cas["request"]["model"]
            except (OSError, ValueError, KeyError):
                continue
            counts[m] = counts.get(m, 0) + 1
            reported[m] = cas["response"].get("model") or m
        for m in sorted(counts):
            rows.append({"directory": str(d), "model": m, "reported": reported[m], "count": counts[m],
                         "matches_demo": proposal_recorded(trace, m, Path(d)) if trace is not None else None})
    return rows
