"""Tag benchmark cases whose wording overlaps the prompt's illustrative phrases.

Two texts overlap when they share a word bigram in which at least one word is
neither a stop word nor a scene noun (after lowercasing and removing punctuation).
"""
from __future__ import annotations

import re
from typing import Iterable

STOP = {"the", "a", "an", "of", "to", "at", "in", "on", "it", "is", "and", "or", "that", "this",
        "be", "for", "with", "as", "by", "its", "are", "was", "not", "just", "only", "when", "while",
        "you", "your", "must", "can", "may", "has", "have", "do", "does"}

# Illustrative phrases in prompt revision 1 (removed in revision 2), kept so
# that results produced with that revision can be tagged after the fact.
PROMPT_V1_EXAMPLES = (
    "open-top container that may hold loose beads",
    "a route forced by where the wall is", "an order that was simply convenient",
    "a clearance equal to the closest distance the demonstration happened to keep",
    "this container is the cup",
    "not just at the destination keeps the destination rule and adds the transport rule",
    "if an orientation requirement has no stated phase, use transport",
)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


# Scene and vocabulary nouns: "the cup" or "the wall" is how anyone names the
# objects, not borrowed phrasing.
NOUNS = {"cup", "block", "marker", "wall", "tray", "object", "objects", "slot", "gripper", "robot"}


def bigrams(text: str) -> set[tuple[str, str]]:
    t = _tokens(text)
    out = set()
    for a, b in zip(t, t[1:]):
        if (a in STOP or a in NOUNS) and (b in STOP or b in NOUNS):
            continue
        out.add((a, b))
    return out


def overlap(text: str, examples: Iterable[str]) -> list[str]:
    """The shared bigrams (as strings) between ``text`` and any example."""
    mine = bigrams(text)
    shared = set()
    for ex in examples:
        shared |= mine & bigrams(ex)
    return sorted(" ".join(b) for b in shared)
