"""Deterministic validation layer for corrections: objects absent from the scene.

Runs after the interpreter (model or offline rules), alongside schema and
specification checks. If the correction text names an object noun that
matches no object, kind or role in the scene, and names no object that is
present, the correction is not applied; the user is asked which scene object
they meant.

Detection uses a fixed lexicon of common tabletop object nouns. A noun outside
the lexicon is not detected, so this layer reduces, but does not eliminate,
corrections being applied to the wrong object.
"""
from __future__ import annotations

import re
from typing import Optional

from mirror.contracts import SceneState, TaskSpec
from mirror.interpret.offline import KIND_WORDS
from mirror.interpret.types import NeedsClarification

# Object nouns that are not kinds in the supported task family and are not
# synonyms used for them (see offline.KIND_WORDS). Words that are also common
# verbs, modals or adjectives ("can", "orange", "brush") are left out.
ABSENT_NOUNS = (
    "bowl", "plate", "dish", "saucer", "bottle", "jar", "tin", "jug", "pitcher", "flask",
    "vase", "pot", "pan", "kettle", "teapot", "lid", "spoon", "fork", "knife", "ladle", "spatula",
    "phone", "remote", "ball", "book", "box", "carton", "bag", "basket", "bucket", "banana", "apple",
    "egg", "screwdriver", "hammer", "wrench", "sponge", "toy", "doll", "candle", "battery",
    "pencil", "crayon", "scissors", "stapler", "tube", "sock", "shoe", "hat", "key",
)
SCENE_NOUNS = ("tray", "trays", "wall", "walls", "slot", "table", "obstacle", "obstacles", "gripper", "robot",
               "hand", "floor")
_ABSENT_RE = re.compile(r"\b(" + "|".join(ABSENT_NOUNS) + r")(e?s)?\b")


def _present_patterns(spec: TaskSpec, scene: Optional[SceneState]) -> list[re.Pattern]:
    words: set[str] = set(SCENE_NOUNS)
    kinds = {b.kind for b in spec.bindings}
    if scene is not None:
        kinds |= {o.kind for o in scene.objects}
        words |= {o.id.lower() for o in scene.objects}
        words |= {ob.kind for ob in scene.obstacles}  # e.g. "box" when a box obstacle exists
    for k in kinds:
        words |= set(KIND_WORDS.get(k, [k]))
    words |= {b.role.lower() for b in spec.bindings}
    return [re.compile(r"\b" + re.escape(w) + r"(e?s)?\b") for w in sorted(words)]


def absent_object_question(text: str, spec: TaskSpec,
                           scene: Optional[SceneState] = None) -> Optional[NeedsClarification]:
    """A NeedsClarification to raise instead of applying ``text``, or None."""
    t = text.lower()
    present = _present_patterns(spec, scene)
    absent = [m.group(1) for m in _ABSENT_RE.finditer(t)]
    absent = [a for a in absent if not any(p.fullmatch(a) for p in present)]
    if not absent:
        return None
    if any(p.search(t) for p in present if p.pattern not in {
            r"\b" + re.escape(w) + r"(e?s)?\b" for w in SCENE_NOUNS}):
        return None  # a scene object is named as well; leave it to the interpreter
    kinds = []
    for b in spec.bindings:
        if b.kind not in kinds:
            kinds.append(b.kind)
    if scene is not None:
        for o in scene.objects:
            if o.kind not in kinds:
                kinds.append(o.kind)
    listing = ", ".join(f"the {k}" for k in kinds)
    return NeedsClarification(
        f"There is no {absent[0]} in this scene; it has {listing}. Which one did you mean?",
        [f"The {k}" for k in kinds], reason=f"absent object '{absent[0]}' (validation layer)")



# ----------------------------------------------- words outside the lexicon ---

# Words that follow "the" in corrections without naming an object.
NON_OBJECT_WORDS = {
    "whole", "destination", "order", "one", "ones", "way", "end", "rest", "others", "other", "same", "time",
    "start", "beginning", "top", "bottom", "side", "sides", "edge", "carry", "move", "transport", "trip",
    "journey", "path", "route", "motion", "task", "first", "last", "next", "opening", "inside", "middle",
    "left", "right", "object", "objects", "thing", "things", "item", "items", "contents", "robot", "gripper",
    "arm", "distance", "margin", "gap", "clearance", "rule", "rules", "correction", "version", "change",
    "speed", "angle", "tilt", "process", "placement", "air", "previous", "last", "entire", "full", "front",
    "back", "center", "centre", "corner", "lane", "ground", "surface", "base", "goal", "plan", "demo",
    "demonstration", "requirement", "orientation", "position", "pose", "packing", "sequence", "kit",
    "previous", "earlier", "original", "spec", "specification", "result", "run", "moment", "instant",
}
DETERMINERS = r"(?:the|this|that|my|your|our)"


def unknown_object_nouns(text: str, spec: TaskSpec, scene: Optional[SceneState] = None) -> list[str]:
    """Words used as object nouns ("the thermos") that are neither a scene
    object, kind, alias, role, scene feature nor a known non-object word. They
    may name an absent object or be another word for a present one; only
    explicit context or the user can say which."""
    t = text.lower().replace("’", "'")
    present = _present_patterns(spec, scene)
    known = lambda w: any(p.fullmatch(w) for p in present) or _ABSENT_RE.fullmatch(w) is not None
    out = []
    for m in re.finditer(DETERMINERS + r"\s+([a-z][a-z'-]*)(?:\s+([a-z][a-z'-]*))?", t):
        w1, w2 = m.group(1), m.group(2)
        w1s = w1[:-2] if w1.endswith("'s") else w1
        if known(w1s) or w1s in NON_OBJECT_WORDS or _ABSENT_RE.fullmatch(w1s):
            continue
        if w2 and (known(w2.rstrip("s'")) or known(w2)):
            continue          # an adjective before a known noun ("the red block")
        out.append(w1s)
    return list(dict.fromkeys(out))


def unknown_object_question(text: str, spec: TaskSpec, scene: Optional[SceneState] = None,
                            focus_kind: Optional[str] = None) -> tuple[Optional[NeedsClarification], Optional[dict]]:
    """(question to ask, alias record). When the text names nothing the scene
    has but uses a word outside the lexicon, the word is resolved only through
    explicit context (the object selected in the interface): that returns an
    alias record. Without context it returns a question."""
    unknown = unknown_object_nouns(text, spec, scene)
    if not unknown:
        return None, None
    t = text.lower()
    named = [p for p in _present_patterns(spec, scene)
             if p.pattern not in {r"\b" + re.escape(w) + r"(e?s)?\b" for w in SCENE_NOUNS} and p.search(t)]
    if named:
        return None, None   # a scene object is named; the unknown word is not the referent
    kinds = []
    for b in spec.bindings:
        if b.kind not in kinds:
            kinds.append(b.kind)
    if focus_kind and focus_kind in kinds:
        return None, {"word": unknown[0], "resolved_to": focus_kind, "source": "object selected in the interface"}
    listing = ", ".join(f"the {k}" for k in kinds)
    return NeedsClarification(
        f"I don't recognise \"the {unknown[0]}\" as an object here. This scene has {listing}. Which one do you "
        f"mean, or is the {unknown[0]} not in this scene?",
        [f"The {k}" for k in kinds] + [f"The {unknown[0]} is not here"],
        reason=f"unrecognised object word '{unknown[0]}'", code="unknown_object"), None
