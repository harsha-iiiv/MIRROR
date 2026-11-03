"""The M2 test matrix: what kinds of correction a test of understanding must
cover, how each is scored, and which cases cover it.

Every row is covered twice, kept apart: by developer-written development
cases (this package's dev files; they build the harness and are never
evidence) and, later, by independently authored held-out cases (protocol v3).
``coverage`` reports both per row. ``absent_term_status`` reads the live
lexicons of the compiler and the validation layer, so the choice of
"unseen" absent-object terms is checked against the code at freeze time
rather than assumed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from .schema import CaseFile

ROWS = {
    "paraphrase": {"what": "unprompted paraphrase of a supported requirement", "tags": ["paraphrase", "replay_anchor"],
                   "score": "semantic state equals gold"},
    "scope_object_vs_all": {"what": "object-specific versus every-object scope",
                            "tags": ["scope", "scope:all", "scope:only", "scope:except", "scope:pair"],
                            "score": "semantic state; wrong-scope edits counted"},
    "phase_transport_vs_final": {"what": "transport versus final-placement requirements",
                                 "tags": ["phase:final", "phase:transport", "phase-specific", "phase:always"],
                                 "score": "semantic state; wrong-phase changes counted"},
    "ordering": {"what": "packing order", "tags": ["ordering"],
                 "score": "semantic state; execution verdict unknown unless the checker reports the ordering id"},
    "clearance": {"what": "clearance from obstacles", "tags": ["type:keep_clearance"], "score": "semantic state and execution"},
    "multi_turn": {"what": "multi-turn changes and reversals", "tags": ["multi_turn", "reversal", "exception", "negation"],
                   "score": "per turn and final state"},
    "vague": {"what": "vague instructions (\"be careful\")", "tags": ["vague", "tolerance:vague", "ambiguity:referent"],
              "score": "gold asks which measurable requirement matters; adding a rule unasked is a false accept"},
    "absent_object": {"what": "objects absent from the scene", "tags": ["absent_object"],
                      "score": "gold asks; substitution onto a present object is a false accept. jar/bowl are "
                               "regression cases only; fresh terms must be outside every lexicon (absent_term_status)"},
    "alias_vs_absent": {"what": "legitimate alias versus absent object (\"the coffee\" for a selected mug)",
                        "tags": ["alias"], "score": "apply only with explicit context or after clarification; "
                                                    "unconditional noun substitution is wrong"},
    "unsupported": {"what": "unsupported requirements", "tags": [], "categories": ["unsupported"],
                    "score": "gold rejects"},
    "infeasible": {"what": "infeasible supported combinations", "tags": ["blocked"], "categories": ["infeasible"],
                   "score": "proven stop only with a valid certificate, else 'planner found no compliant route'; "
                            "never a safety guarantee"},
}
REGRESSION_ONLY_TERMS = ("jar", "bowl")     # seen in development failures; they shaped the validation layer


def lexicons() -> dict[str, set[str]]:
    from mirror.interpret import guard, offline
    return {"kind_alias": {w for ws in offline.KIND_WORDS.values() for w in ws},
            "validation_lexicon": set(guard.ABSENT_NOUNS),
            "offline_unknown_objects": set(getattr(offline, "UNKNOWN_OBJECTS", []))}


def absent_term_status(term: str) -> str:
    """kind_alias (a word for a scene object), regression (jar/bowl),
    lexicon (known to the compiler or validation layer: tests that code path,
    not generalisation) or unseen."""
    t = term.lower().strip()
    lx = lexicons()
    if t in lx["kind_alias"]:
        return "kind_alias"
    if t in REGRESSION_ONLY_TERMS:
        return "regression"
    if t in lx["validation_lexicon"] or t in lx["offline_unknown_objects"]:
        return "lexicon"
    return "unseen"


def _row_hits(case, row: dict) -> bool:
    tags = set(case.scope_tags)
    return bool(tags & set(row.get("tags", []))) or case.category in row.get("categories", [])


def coverage(files: Iterable[CaseFile]) -> dict[str, dict]:
    out = {k: {"dev_cases": [], "independent_cases": []} for k in ROWS}
    for cf in files:
        bucket = "dev_cases" if cf.split == "dev" else "independent_cases"
        for c in cf.cases:
            for k, row in ROWS.items():
                if _row_hits(c, row):
                    out[k][bucket].append(c.id)
    return out


def absent_terms_in(cf: CaseFile) -> dict[str, str]:
    """Every absent-object noun in the cases' text, with its status."""
    words = set()
    for c in cf.cases:
        if "absent_object" in c.scope_tags:
            for t in c.turns:
                words |= set(re.findall(r"[a-z]+", t.text.lower()))
    lx = lexicons()
    nouns = {w for w in words if w in lx["validation_lexicon"] | lx["offline_unknown_objects"] | set(REGRESSION_ONLY_TERMS)}
    return {w: absent_term_status(w) for w in sorted(nouns)}
