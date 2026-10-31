"""Human-readable difference between two specification versions (for the
studio's spec diff view)."""
from __future__ import annotations

from typing import Any

from mirror.contracts import TaskSpec


def _items(spec: TaskSpec) -> dict[str, tuple[str, dict[str, Any]]]:
    out: dict[str, tuple[str, dict[str, Any]]] = {}
    for kind, coll in (("goal", spec.goals), ("constraint", spec.constraints),
                       ("ordering", spec.ordering), ("uncertainty", spec.uncertainties)):
        for it in coll:
            out[it.id] = (kind, it.model_dump(mode="json"))
    return out


_FIELDS = {
    "goal": ("status", "object"),
    "constraint": ("status", "params", "phase", "applies_to", "description"),
    "ordering": ("status", "before", "after"),
    "uncertainty": ("status", "resolution"),
}


def spec_diff(old: TaskSpec, new: TaskSpec) -> list[dict[str, Any]]:
    """List of changes from ``old`` to ``new``, in a stable order:
    ``{"id", "item", "change": "added" | "removed" | <field>, "before", "after"}``."""
    a, b = _items(old), _items(new)
    changes: list[dict[str, Any]] = []
    for item_id in sorted(set(a) | set(b)):
        if item_id not in a:
            kind, val = b[item_id]
            changes.append({"id": item_id, "item": kind, "change": "added", "before": None, "after": val})
        elif item_id not in b:
            kind, val = a[item_id]
            changes.append({"id": item_id, "item": kind, "change": "removed", "before": val, "after": None})
        else:
            kind, va = a[item_id]
            _, vb = b[item_id]
            for f in _FIELDS[kind]:
                if va.get(f) != vb.get(f):
                    changes.append({"id": item_id, "item": kind, "change": f,
                                    "before": va.get(f), "after": vb.get(f)})
    return changes
