"""Independent resolution of specification selectors to scene objects.

The checker resolves selectors itself rather than reusing the planner's
grounding, so a grounding error in the planner cannot hide a violation.
"""
from __future__ import annotations

from mirror.contracts import ObjectSelector, SceneState, TaskSpec, TraySpec


def select(selector: ObjectSelector, spec: TaskSpec, scene: SceneState) -> list[str]:
    """Object ids in ``scene`` matched by ``selector`` (all given fields must match).

    A role matches objects of the kind bound to that role in ``spec.bindings``.
    An unknown role matches nothing."""
    out = []
    role_kinds = None
    if selector.role is not None:
        role_kinds = {b.kind for b in spec.bindings if b.role == selector.role}
    for o in scene.objects:
        if selector.kind is not None and o.kind != selector.kind:
            continue
        if role_kinds is not None and o.kind not in role_kinds:
            continue
        if selector.ids is not None and o.id not in selector.ids:
            continue
        out.append(o.id)
    return out


def destination_tray(target: str, scene: SceneState) -> tuple[TraySpec | None, str]:
    """Return (tray, note). A tray whose id equals the target wins; otherwise a
    single tray is the destination; with several trays and no id match the
    first is used and the note says so."""
    for t in scene.trays:
        if t.id == target:
            return t, ""
    if len(scene.trays) == 1:
        return scene.trays[0], ""
    if not scene.trays:
        return None, "scene has no tray"
    return scene.trays[0], f"{len(scene.trays)} trays and none named {target!r}; used {scene.trays[0].id}"
