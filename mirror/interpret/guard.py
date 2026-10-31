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
