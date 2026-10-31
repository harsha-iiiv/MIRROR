"""Demonstration -> proposed TaskSpec."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from mirror.contracts import DemoTrace, TaskSpec
from mirror.interpret.drafts import ProposalDraft, check_proposal, proposal_to_spec
from mirror.interpret.llm import LLMClient
from mirror.interpret.offline import OFFLINE_LABEL, propose_offline
from mirror.interpret.prompts import propose_messages
from mirror.interpret.summary import summarize_demo
from mirror.interpret.types import DemoSummary


@dataclass
class Interpretation:
    spec: TaskSpec
    draft: ProposalDraft
    summary: DemoSummary
    interpreter: str
    usage: dict[str, Any] = field(default_factory=dict)
    from_cassette: bool = False
    keyframes_used: int = 0
    observations_model: Optional[str] = None


def propose_spec_detailed(trace: DemoTrace, client: Optional[LLMClient] = None, *,
                          keyframes: Optional[list] = None, observations=None) -> Interpretation:
    """``keyframes``: optional rendered simulator frames (mirror.interpret.keyframes)
    attached to the prompt as images. ``observations``: optional
    mirror.interpret.video_observe.VideoObservations added to the prompt as
    secondary evidence. Both are ignored by the offline rules; without them the
    request is identical to earlier versions."""
    summary = summarize_demo(trace)
    if client is None or client.offline:
        draft = propose_offline(summary, trace.scene)
        spec = proposal_to_spec(draft, summary, trace, OFFLINE_LABEL)
        return Interpretation(spec=spec, draft=draft, summary=summary, interpreter=OFFLINE_LABEL)
    view = observations.prompt_view() if observations is not None else None
    res = client.complete_json(propose_messages(summary, trace.scene, keyframes, view), ProposalDraft,
                               name="propose_spec", validate=lambda d: check_proposal(d, summary))
    spec = proposal_to_spec(res.data, summary, trace, res.model)
    return Interpretation(spec=spec, draft=res.data, summary=summary, interpreter=res.model,
                          usage=res.usage, from_cassette=res.from_cassette,
                          keyframes_used=len(keyframes or []),
                          observations_model=observations.model if observations is not None else None)


def propose_spec(trace: DemoTrace, client: Optional[LLMClient] = None, *,
                 keyframes: Optional[list] = None, observations=None) -> TaskSpec:
    """Propose a TaskSpec from a demonstration. Inferred rules and orderings are
    ``proposed`` with an open question each; nothing is enforced until a human
    confirms it."""
    return propose_spec_detailed(trace, client, keyframes=keyframes, observations=observations).spec
