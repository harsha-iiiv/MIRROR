"""Interpretation: demonstration summary, task proposal, corrections and
clarification. See docs/CONTRACTS.md."""
from mirror.interpret.clarify import apply_answer, make_clarification, planned_carry_tilts
from mirror.interpret.correct import compile_correction
from mirror.interpret.diff import spec_diff
from mirror.interpret.llm import CassetteMiss, LLMClient, LLMError
from mirror.interpret.offline import OFFLINE_LABEL
from mirror.interpret.propose import Interpretation, propose_spec, propose_spec_detailed
from mirror.interpret.specops import apply_ops, confirm_items, validate_spec
from mirror.interpret.summary import summarize_demo
from mirror.interpret.types import (CandidateDigest, Clarification, CorrectionRejected, DemoSummary,
                                    Difference, InterpretError, NeedsClarification, ObjectSummary,
                                    Regularity)

__all__ = [
    "summarize_demo", "propose_spec", "propose_spec_detailed", "Interpretation",
    "compile_correction", "make_clarification", "apply_answer", "planned_carry_tilts",
    "validate_spec", "apply_ops", "confirm_items", "spec_diff",
    "LLMClient", "LLMError", "CassetteMiss", "OFFLINE_LABEL",
    "DemoSummary", "ObjectSummary", "Regularity", "Clarification", "CandidateDigest", "Difference",
    "InterpretError", "NeedsClarification", "CorrectionRejected",
]
