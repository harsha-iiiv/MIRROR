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


def propose_spec_detailed(trace: DemoTrace, client: Optional[LLMClient] = None) -> Interpretation:
    summary = summarize_demo(trace)
    if client is None or client.offline:
        draft = propose_offline(summary, trace.scene)
        spec = proposal_to_spec(draft, summary, trace, OFFLINE_LABEL)
        return Interpretation(spec=spec, draft=draft, summary=summary, interpreter=OFFLINE_LABEL)
    res = client.complete_json(propose_messages(summary, trace.scene), ProposalDraft,
                               name="propose_spec", validate=lambda d: check_proposal(d, summary))
    spec = proposal_to_spec(res.data, summary, trace, res.model)
    return Interpretation(spec=spec, draft=res.data, summary=summary, interpreter=res.model,
                          usage=res.usage, from_cassette=res.from_cassette)


def propose_spec(trace: DemoTrace, client: Optional[LLMClient] = None) -> TaskSpec:
    """Propose a TaskSpec from a demonstration. Inferred rules and orderings are
    ``proposed`` with an open question each; nothing is enforced until a human
    confirms it."""
    return propose_spec_detailed(trace, client).spec
