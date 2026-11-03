"""Challenge mode: a visitor edits a development scene, corrects the rule, and
the studio rechecks the old behaviour and executes the revision.

Challenge scenes come only from development scene families. ``fenced_seeds``
collects every seed named by a frozen evaluation (each ``results/*/freeze.json``
and, for registrations, the ``design.json`` / ``inputs.json`` frozen beside it);
such seeds are refused even inside the development range, and so is any seed at
or above the evaluation base.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from ..contracts import ObstacleSpec, Pose, SceneState, SlotSpec, TaskSpec
from ..geometry import WORLD
from ..lineage import scene_sha256, sha256_of

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_SEED_LIMIT = 10_000          # mirror.scenes.sampling.EVAL_SEED_BASE
CHALLENGE_SEED_RANGE = (0, 9999)  # every development seed; frozen-evaluation seeds are refused individually
FAMILIES = {
    "mixed": {"split": "dev", "family": "mixed"},
    "shortcut": {"split": "dev", "family": "shortcut"},
    "blocked": {"split": "blocked", "family": "mixed"},
}
REFERENCE_PRESETS = ("demo", "shortcut", "blocked")
MAX_EDITS = 8
EDIT_KEYS = ("tray", "object", "slot", "slot_y", "obstacle", "remove_obstacle")

# The bounded correction language (kept in step with the rule-support matrix).
SUPPORTED_RULES = [
    {"type": "upright", "phases": ["transport", "final", "always"], "params": ["max_tilt_deg"],
     "example": "keep the cup upright while carrying it"},
    {"type": "forbidden_contact", "phases": ["transport", "final", "always"], "params": ["with"],
     "example": "the marker must not touch the wall"},
    {"type": "keep_clearance", "phases": ["transport", "always"], "params": ["min_m"],
     "example": "keep the cup at least 3 cm from obstacles"},
    {"type": "ordering", "phases": [], "params": ["before", "after"], "example": "place the marker before the cup"},
    {"type": "status", "phases": [], "params": [], "example": "that rule is not required"},
]


class FenceError(RuntimeError):
    """The requested challenge scene would touch frozen evaluation material."""


def _seed_values(obj: Any, out: dict, src: str, path: str = "") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k)
            if key in ("seed", "seeds") or key.endswith("_seeds"):
                vals = v if isinstance(v, list) else [v]
                for x in vals:
                    if isinstance(x, int) and not isinstance(x, bool):
                        out.setdefault(x, set()).add(f"{src}:{path}/{key}")
            _seed_values(v, out, src, f"{path}/{key}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _seed_values(v, out, src, f"{path}[{i}]")


@lru_cache(maxsize=4)
def fenced_seeds(results_dir: str = str(REPO_ROOT / "results")) -> dict[int, tuple[str, ...]]:
    """seed -> the frozen files that name it."""
    out: dict[int, set] = {}
    root = Path(results_dir)
    for freeze in sorted(root.glob("*/freeze.json")):
        files = [freeze] + [freeze.parent / n for n in ("design.json", "inputs.json") if (freeze.parent / n).exists()]
        for f in files:
            try:
                _seed_values(json.loads(f.read_text()), out, str(f.relative_to(root.parent)))
            except (OSError, ValueError):
                # An unreadable freeze cannot vouch for any seed: refuse everything rather than guess.
                raise FenceError(f"cannot read frozen evaluation file {f.name}; challenge scenes are disabled")
    return {k: tuple(sorted(v)) for k, v in out.items()}


def fence_sources() -> list[str]:
    return sorted({s.split(":")[0] for v in fenced_seeds().values() for s in v})


def check_challenge_seed(family: str, seed: int) -> None:
    if family not in FAMILIES:
        raise ValueError(f"family must be one of {sorted(FAMILIES)} or 'reference'")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    lo, hi = CHALLENGE_SEED_RANGE
    if seed >= DEV_SEED_LIMIT:
        raise FenceError(f"seed {seed} is in the evaluation range (>= {DEV_SEED_LIMIT}); challenges use development seeds")
    if not lo <= seed <= hi:
        raise FenceError(f"challenge seeds are {lo}-{hi}")
    hit = fenced_seeds().get(seed)
    if hit:
        raise FenceError(f"seed {seed} is named by a frozen evaluation ({hit[0]}); pick another seed")


def options(audio: bool) -> dict:
    return {"families": [{"id": k, "split": v["split"], "seed_range": list(CHALLENGE_SEED_RANGE)} for k, v in FAMILIES.items()]
            + [{"id": "reference", "presets": list(REFERENCE_PRESETS)}],
            "edits": {"tray": {"x": "m", "y": "m"}, "object": {"id": "object id", "x": "m", "y": "m"},
                      "slot": "on|off", "slot_y": "m (with slot on)", "obstacle": {"x": "m", "y": "m"},
                      "remove_obstacle": "obstacle id"},
            "max_edits": MAX_EDITS,
            "correction": {"max_chars": 600, "audio": bool(audio), "supported": SUPPORTED_RULES,
                           "supported_changes": _supported_changes(), "matrix": _matrix(),
                           "note": "Corrections are compiled into this bounded rule language; anything else is "
                                   "answered with a question or refused, never executed as a free-form command."},
            "fenced_seed_count": len(fenced_seeds()), "fence_sources": fence_sources()}


def _matrix() -> Optional[dict]:
    """The rule-support matrix (mirror.rules_support) when that module is present."""
    try:
        from ..rules_support import matrix
        return matrix()
    except Exception:  # noqa: BLE001
        return None


def _supported_changes() -> list[str]:
    try:
        from ..interpret.outcome import SUPPORTED_CHANGES
        return list(SUPPORTED_CHANGES)
    except Exception:  # noqa: BLE001
        return [r["example"] for r in SUPPORTED_RULES]


# ---------------------------------------------------------------- scene edits

def apply_scene_edit(scene: SceneState, edit: dict) -> SceneState:
    """A new scene derived from ``scene`` by one edit (see EDIT_KEYS); ``scene`` is not changed."""
    unknown = set(edit) - set(EDIT_KEYS)
    if unknown:
        raise ValueError(f"unknown scene edit {sorted(unknown)}")
    sc = scene.model_copy(deep=True)
    ws = sc.workspace

    def num(v, what):
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{what} must be a number")
        if f != f or abs(f) > 10:
            raise ValueError(f"{what} out of range")
        return f

    def clamp(v, lo, hi, m=0.03):
        return float(min(hi - m, max(lo + m, v)))

    if "tray" in edit:
        t = sc.trays[0]
        x, y = num(edit["tray"].get("x"), "tray x"), num(edit["tray"].get("y"), "tray y")
        hx, hy = t.inner_half
        t.pose = Pose(pos=(clamp(x, ws.x_range[0] + hx, ws.x_range[1] - hx, 0.0),
                           clamp(y, ws.y_range[0] + hy, ws.y_range[1] - hy, 0.0), t.pose.pos[2]), quat=t.pose.quat)
    if "object" in edit:
        o = next((o for o in sc.objects if o.id == edit["object"].get("id")), None)
        if o is None:
            raise KeyError(f"no object {edit['object'].get('id')}")
        o.pose = Pose(pos=(clamp(num(edit["object"].get("x"), "object x"), *ws.x_range),
                           clamp(num(edit["object"].get("y"), "object y"), *ws.y_range), o.pose.pos[2]), quat=o.pose.quat)
    if "slot" in edit:
        if edit["slot"] not in ("on", "off"):
            raise ValueError("slot is 'on' or 'off'")
        for w in sc.obstacles:
            if w.kind != "wall":
                continue
            if edit["slot"] == "off":
                w.slots = []
            elif not w.slots:
                cy = edit.get("slot_y")
                if cy is None:
                    # Put the slot on the straight line from the cup to the tray.
                    cup = next((o for o in sc.objects if o.kind == "cup"), None)
                    tr = sc.trays[0].pose.pos
                    if cup is not None and abs(tr[0] - cup.pose.pos[0]) > 1e-6:
                        k = (w.pose.pos[0] - cup.pose.pos[0]) / (tr[0] - cup.pose.pos[0])
                        cy = cup.pose.pos[1] + k * (tr[1] - cup.pose.pos[1])
                    else:
                        cy = w.pose.pos[1]
                cy = num(cy, "slot y")
                half = WORLD.slot_length / 2
                lo = w.pose.pos[1] - w.length / 2 + 0.02
                hi = w.pose.pos[1] + w.length / 2 - 0.02
                cy = float(min(hi - half, max(lo + half, cy)))
                w.slots = [SlotSpec(y_min=cy - half, y_max=cy + half, z_min=WORLD.slot_z[0], z_max=WORLD.slot_z[1])]
    if "obstacle" in edit:
        n = sum(1 for o in sc.obstacles if o.kind == "box")
        half = tuple(float(v) for v in edit["obstacle"].get("half", (0.04, 0.04, 0.12)))
        if len(half) != 3 or not all(0.01 <= v <= 0.15 for v in half):
            raise ValueError("obstacle half extents are 0.01-0.15 m")
        sc.obstacles.append(ObstacleSpec(id=f"box_{n}", kind="box",
                                         pose=Pose(pos=(clamp(num(edit["obstacle"].get("x"), "obstacle x"), *ws.x_range),
                                                        clamp(num(edit["obstacle"].get("y"), "obstacle y"), *ws.y_range),
                                                        half[2])),
                                         half=half))
    if "remove_obstacle" in edit:
        sc.obstacles = [o for o in sc.obstacles if o.id != edit["remove_obstacle"] or o.kind == "wall"]
    return sc


def challenge_scene(family: str, seed: Optional[int], edits: list, *, preset: Optional[str] = None,
                    fixture: bool = False) -> tuple[SceneState, dict]:
    """Build the scene of a challenge and describe where it came from."""
    if not isinstance(edits, list) or len(edits) > MAX_EDITS:
        raise ValueError(f"edits is a list of at most {MAX_EDITS} edits")
    if family == "reference":
        if preset not in REFERENCE_PRESETS:
            raise ValueError(f"reference preset is one of {list(REFERENCE_PRESETS)}")
        if fixture:
            from .fixtures import fixture_scene
            base = fixture_scene(preset)
        else:
            from ..scenes import reference_scene
            base = reference_scene(preset)
        origin = {"family": "reference", "preset": preset, "split": base.split, "seed": None}
    else:
        check_challenge_seed(family, seed)
        f = FAMILIES[family]
        if fixture:
            from .fixtures import fixture_layout
            base = fixture_layout(int(seed))
            base.split, base.scene_id = "dev", f"fixture_{family}_{int(seed)}"   # kinematic stand-in layout
        else:
            from ..scenes import sample_layout
            base = sample_layout(int(seed), f["split"], f["family"])
        origin = {"family": family, "preset": None, "split": f["split"], "seed": int(seed)}
    sc = base
    for e in edits:
        if not isinstance(e, dict):
            raise ValueError("each edit is an object")
        sc = apply_scene_edit(sc, e)
    if edits:
        sc.scene_id = f"challenge_{family}_{seed if seed is not None else preset}_{sha256_of(edits)[:8]}"
        sc.split = "custom"
    origin.update({"edits": edits, "base_scene_id": base.scene_id, "base_scene_sha256": scene_sha256(base),
                   "scene_id": sc.scene_id, "content_sha256": scene_sha256(sc)})
    return sc, origin


# ---------------------------------------------------------------- scope review

def _sel_objects(sel, scene: Optional[SceneState]) -> list[str]:
    if sel is None:
        return []
    ids = getattr(sel, "ids", None)
    if ids:
        return list(ids)
    if scene is None:
        return [f"{getattr(sel, 'kind', None) or ''}{'/' + sel.role if getattr(sel, 'role', None) else ''}" or "any"]
    out = []
    for o in scene.objects:
        if getattr(sel, "kind", None) and o.kind != sel.kind:
            continue
        if getattr(sel, "role", None) and getattr(o, "role", None) not in (None, sel.role):
            continue
        out.append(o.id)
    return out


def _items(spec: TaskSpec) -> dict[str, tuple[str, Any]]:
    d: dict[str, tuple[str, Any]] = {}
    for c in spec.constraints:
        d[c.id] = ("constraint", c)
    for o in spec.ordering:
        d[o.id] = ("ordering", o)
    for g in spec.goals:
        d[g.id] = ("goal", g)
    return d


def _brief(kind: str, item) -> dict:
    data = item.model_dump(mode="json")
    data.pop("provenance", None)
    return {"kind": kind, **data}


def spec_scope(base: TaskSpec, new: TaskSpec, scene: Optional[SceneState]) -> tuple[dict, dict]:
    """What a proposed version changes: the diff and its object/phase scope, from the specs themselves."""
    a, b = _items(base), _items(new)
    added, removed, changed = [], [], []
    objects, phases, kinds, items = set(), set(), set(), set()

    def touch(kind, item):
        kinds.add(kind)
        items.add(item.id)
        if kind == "constraint":
            objects.update(_sel_objects(item.applies_to, scene))
            phases.add(item.phase)
        elif kind == "ordering":
            objects.update(_sel_objects(item.before, scene))
            objects.update(_sel_objects(item.after, scene))
        elif kind == "goal":
            objects.update(_sel_objects(item.object, scene))

    for k, (kind, item) in b.items():
        if k not in a:
            added.append(_brief(kind, item))
            touch(kind, item)
        else:
            old = _brief(*a[k])
            cur = _brief(kind, item)
            if old != cur:
                changed.append({"id": k, "kind": kind, "before": {f: old[f] for f in old if old.get(f) != cur.get(f)},
                                "after": {f: cur[f] for f in cur if old.get(f) != cur.get(f)}})
                touch(kind, item)
    for k, (kind, item) in a.items():
        if k not in b:
            removed.append(_brief(kind, item))
            touch(kind, item)
    untouched = sorted({o.id for o in scene.objects} - objects) if scene is not None else []
    scope = {"objects": sorted(objects), "phases": sorted(phases), "kinds": sorted(kinds), "items": sorted(items),
             "unaffected_objects": untouched}
    return {"added": added, "changed": changed, "removed": removed}, scope


class ChallengeState:
    """One visitor challenge inside a session (mutable until published)."""

    def __init__(self, generation_id: str, scene: SceneState, origin: dict, base_spec_version_id: str):
        self.id = "ch" + uuid.uuid4().hex[:10]
        self.created_at = time.time()
        self.generation_id = generation_id
        self.scene = scene
        self.origin = origin
        self.base_spec_version_id = base_spec_version_id
        self.state = "preparing"
        self.before: Optional[dict] = None
        self.proposal: Optional[dict] = None
        self.pending_spec: Optional[TaskSpec] = None
        self.pending_base_version: Optional[int] = None
        self.revised_spec_version_id: Optional[str] = None
        self.rechecks: list[dict] = []
        self.after: Optional[dict] = None
        self.record_id: Optional[str] = None
        self.preparation: list[dict] = []
        self.error: Optional[str] = None
        self.lock = threading.RLock()

    def to_state(self) -> dict:
        return {**self.public(), "scene_state": self.scene.model_dump(mode="json"), "origin": self.origin,
                "pending_spec": self.pending_spec.model_dump(mode="json") if self.pending_spec is not None else None,
                "pending_base_version": self.pending_base_version}

    @classmethod
    def from_state(cls, d: dict) -> "ChallengeState":
        ch = cls(d["generation_id"], SceneState.model_validate(d["scene_state"]), d["origin"], d["base_spec_version_id"])
        ch.id, ch.created_at, ch.state = d["id"], d["created_at"], d["state"]
        for k in ("before", "proposal", "revised_spec_version_id", "rechecks", "after", "record_id", "preparation",
                  "error", "pending_base_version"):
            setattr(ch, k, d.get(k))
        ch.rechecks = ch.rechecks or []
        ch.preparation = ch.preparation or []
        ch.pending_spec = TaskSpec.model_validate(d["pending_spec"]) if d.get("pending_spec") else None
        if ch.state == "preparing":
            ch.state, ch.error = "failed", "the server restarted while this challenge was being prepared"
        return ch

    def public(self) -> dict:
        return {"id": self.id, "state": self.state, "created_at": self.created_at, "generation_id": self.generation_id,
                "scene": {**self.origin, "scene": self.scene.model_dump(mode="json")},
                "base_spec_version_id": self.base_spec_version_id, "preparation": self.preparation,
                "before": self.before, "proposal": self.proposal,
                "revised_spec_version_id": self.revised_spec_version_id, "rechecks": self.rechecks,
                "after": self.after, "record_id": self.record_id, "error": self.error}
