"""Bind a task specification to the objects and tray of one scene."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from ..contracts import ObjectSelector, SceneState, Status, TaskSpec


class Grounding(BaseModel):
    roles: dict[str, list[str]] = Field(default_factory=dict)        # role -> object ids
    goal_objects: dict[str, list[str]] = Field(default_factory=dict)  # goal id -> object ids
    destination: dict[str, str] = Field(default_factory=dict)        # tray role -> tray id
    constraint_objects: dict[str, list[str]] = Field(default_factory=dict)
    ordering_objects: dict[str, tuple[list[str], list[str]]] = Field(default_factory=dict)
    objects_to_pack: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _match(sel: ObjectSelector, obj_id: str, kind: str, roles: dict[str, list[str]]) -> bool:
    if sel.kind is not None and sel.kind != kind:
        return False
    if sel.ids is not None and obj_id not in sel.ids:
        return False
    if sel.role is not None and obj_id not in roles.get(sel.role, []):
        return False
    return True


def select(sel: ObjectSelector, scene: SceneState, roles: dict[str, list[str]]) -> list[str]:
    return sorted(o.id for o in scene.objects if _match(sel, o.id, o.kind, roles))


def ground(spec: TaskSpec, scene: SceneState) -> Grounding:
    """Resolve roles and selectors to scene instance ids.

    A role covers every scene object of the kind(s) bound to it, and a
    selector covers every object matching it (ObjectBinding.count is
    descriptive only). A required binding with no matching object is an
    error. A goal target names a tray role; with a single tray (or a target
    equal to a tray id) it binds directly. Goals that are rejected are
    ignored; proposed and confirmed goals define what must be packed.
    """
    g = Grounding()
    by_kind: dict[str, list[str]] = {}
    for o in sorted(scene.objects, key=lambda o: o.id):
        by_kind.setdefault(o.kind, []).append(o.id)
    for b in spec.bindings:
        ids = by_kind.get(b.kind, [])
        if not ids:
            msg = f"role {b.role!r} needs a {b.kind}; the scene has none"
            (g.errors if b.required else g.warnings).append(msg)
        cur = g.roles.setdefault(b.role, [])
        cur.extend(i for i in ids if i not in cur)
        cur.sort()
        if b.count != len(ids) and ids:
            g.warnings.append(f"role {b.role!r}: binding count {b.count} is descriptive; "
                              f"{len(ids)} {b.kind}(s) in the scene are covered")
    tray_ids = sorted(t.id for t in scene.trays)
    pack: list[str] = []
    for goal in spec.goals:
        if goal.status == Status.rejected:
            continue
        ids = select(goal.object, scene, g.roles)
        if not ids:
            g.errors.append(f"goal {goal.id} selects no object in scene {scene.scene_id}")
        g.goal_objects[goal.id] = ids
        if goal.target not in g.destination:
            if goal.target in tray_ids:
                g.destination[goal.target] = goal.target
            elif len(tray_ids) >= 1:
                g.destination[goal.target] = tray_ids[0]
                if len(tray_ids) > 1:
                    g.warnings.append(f"target {goal.target!r} bound to {tray_ids[0]} of {len(tray_ids)} trays")
            else:
                g.errors.append("scene has no tray")
        for i in ids:
            if i not in pack:
                pack.append(i)
    if len(set(g.destination.values())) > 1:
        g.errors.append("goals name different destination trays; one tray per task is supported")
    for c in spec.constraints:
        g.constraint_objects[c.id] = select(c.applies_to, scene, g.roles)
    for oc in spec.ordering:
        g.ordering_objects[oc.id] = (select(oc.before, scene, g.roles), select(oc.after, scene, g.roles))
    g.objects_to_pack = sorted(pack)
    return g


def destination_tray(g: Grounding) -> Optional[str]:
    vals = sorted(set(g.destination.values()))
    return vals[0] if vals else None
