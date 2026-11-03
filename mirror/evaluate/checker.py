"""Independent rollout checker.

``check`` is a pure function of the dense step log, the task specification
and the scene. It never reads planner explanations or model output. Every
condition is evaluated at every logged physics step with explicit
tolerances, and the places where simulator ground truth is used are listed in
the report.

Since checker 2 (contract 1.2) the report also carries one RuleResult per
goal, constraint and ordering rule. Ordering rules are monitored from the
evidence (mirror.evaluate.evidence), never from the plan's declared order. A
confirmed rule the evidence cannot settle is ``unknown`` and makes an
otherwise complete rollout ``unverified``, never ``success``.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Iterable, Optional

import numpy as np

from mirror.contracts import (Constraint, ObjectOutcome, OrderingConstraint, PlanResult,
                              RolloutReport, RuleResult, SceneState, Status, TaskSpec,
                              ViolationInterval)
from mirror.geometry import DEFAULT_UPRIGHT_TOL_DEG
from mirror.statelog import StateLog, tilt_deg

from . import geom
from .grounding import destination_tray, select

CHECKER_ID = "mirror.evaluate.check/2"
SETTLE_WINDOW_S = 0.3
# Short gaps in the per-step held label (finger contact flickering while an
# object is carried) are bridged so a tilt during the gap is still checked.
TRANSPORT_GAP_FILL_S = 0.5
BEAD_TOL_M = 0.002

GROUND_TRUTH_USE = [
    "object positions and orientations: simulator body state at every physics step (tilt, placement, settling)",
    "held object: executor's finger-contact detection logged per step (transport phase boundaries)",
    "bead positions: simulator body state (bead retention)",
    "contacts: simulator contact list, classified by geom group (forbidden contacts)",
    "scene geometry: SceneState dimensions (tray footprint, cup interior, obstacle boxes)",
]

STATUS_ORDER = ["planning_failed", "execution_failed", "timeout",
                "constraint_violated", "task_incomplete", "unverified", "success"]

ORDERING_GROUND_TRUTH_USE = (
    "ordering events: grasp start from the held label or lift-off, placement completion from object "
    "position and speed after the last release (never the plan's declared order or phase labels)")


@dataclass
class _Ctx:
    log: StateLog
    spec: TaskSpec
    scene: SceneState
    tilt: np.ndarray            # (N, K)
    speed: np.ndarray           # (N, K) linear speed, m/s
    window: np.ndarray          # (N,) bool, final settle window
    notes: list[str] = field(default_factory=list)
    missing: dict = field(default_factory=dict)   # rule id -> object ids absent from the log
    _events: dict = field(default_factory=dict)


def _intervals(mask: np.ndarray) -> list[tuple[int, int]]:
    """Inclusive (start, end) index pairs of True runs."""
    m = np.asarray(mask, bool)
    if not m.any():
        return []
    d = np.diff(np.concatenate([[0], m.astype(np.int8), [0]]))
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1) - 1
    return list(zip(starts.tolist(), ends.tolist()))


def _speeds(log: StateLog) -> np.ndarray:
    n, k = log.obj_pos.shape[:2]
    if n < 2:
        return np.zeros((n, k))
    dt = np.diff(log.t)
    dt = np.where(dt > 0, dt, log.dt)
    step = np.linalg.norm(np.diff(log.obj_pos, axis=0), axis=-1)
    q = float(log.meta.get("position_quantum_m", 0.0) or 0.0)
    if q > 0:   # playback positions are rounded: a step of one quantum is not resolvable motion
        step = np.where(step <= q * np.sqrt(3) + 1e-12, 0.0, step)
    v = step / dt[:, None]
    return np.vstack([v[:1], v])


def _context(log: StateLog, spec: TaskSpec, scene: SceneState) -> _Ctx:
    tilt = tilt_deg(log.obj_quat) if log.n else np.zeros((0, len(log.object_ids)))
    speed = _speeds(log)
    window = np.zeros(log.n, bool)
    if log.n:
        window = log.t >= (log.t[-1] - SETTLE_WINDOW_S - 1e-9)
    return _Ctx(log, spec, scene, tilt, speed, window)


def _phase_mask(ctx: _Ctx, idx: int, phase: str) -> np.ndarray:
    if phase == "transport":
        return _transport_mask(ctx.log, idx)
    if phase == "final":
        held = np.asarray(ctx.log.held) == idx
        after = np.zeros(ctx.log.n, bool)
        if held.any():
            last = int(np.flatnonzero(held)[-1])
            after[last + 1:] = True
        else:
            after[:] = True
        return ctx.window & after
    if phase == "always":
        return np.ones(ctx.log.n, bool)
    raise ValueError(f"unknown phase {phase}")


LIFT_OFF_M = 0.005   # above its resting height and touching no support


def _support_steps(log: StateLog, oid: str) -> np.ndarray:
    """Steps at which object ``oid`` touches a support: the table, a tray or
    another movable object."""
    out = np.zeros(log.n, bool)
    objs = set(log.object_ids)
    for c in log.contacts:
        for me, other in ((c.object_id, c.other), (c.other, c.object_id)):
            if me == oid and (other in ("table", "floor") or other.startswith("tray:")
                              or (other in objs and other != oid)):
                if 0 <= c.step < log.n:
                    out[c.step] = True
    return out


def _held_mask(log: StateLog, idx: int) -> np.ndarray:
    held = np.asarray(log.held)
    m = held == idx
    runs = _intervals(m)
    for (s0, e0), (s1, _) in zip(runs, runs[1:]):
        gap = slice(e0 + 1, s1)
        if log.t[s1] - log.t[e0] <= TRANSPORT_GAP_FILL_S and not np.any((held[gap] >= 0) & (held[gap] != idx)):
            m[gap] = True
    return m


def _transport_mask(log: StateLog, idx: int, *, with_lift: bool = True) -> np.ndarray:
    """Steps from grasp to release of object ``idx``.

    Primary signal: the executor's per-step held label, with gaps shorter
    than TRANSPORT_GAP_FILL_S filled when no other object is held. Between the
    first and last held step, steps where the object is lifted off (more than
    LIFT_OFF_M above its resting height and touching no support) also count,
    so a carry that the finger label misses is still checked."""
    m = _held_mask(log, idx)
    if not with_lift or not m.any() or log.n == 0:
        return m
    first, last = np.flatnonzero(m)[[0, -1]]
    z = log.obj_pos[:, idx, 2]
    lifted = (z > z[0] + LIFT_OFF_M) & ~_support_steps(log, log.object_ids[idx])
    bracket = np.zeros(log.n, bool)
    bracket[first:last + 1] = True
    return m | (lifted & bracket)


def _resolve(ctx: _Ctx, c: Constraint) -> list[str]:
    ids = select(c.applies_to, ctx.spec, ctx.scene)
    present = [i for i in ids if i in ctx.log.object_ids]
    missing = [i for i in ids if i not in ctx.log.object_ids]
    if missing:
        ctx.missing.setdefault(c.id, []).extend(m for m in missing if m not in ctx.missing.get(c.id, []))
        ctx.notes.append(f"{c.id}: objects {missing} are not in the step log")
    if not ids:
        ctx.notes.append(f"{c.id}: selector matched no object in scene {ctx.scene.scene_id}")
    return present


# ------------------------------------------------------------ monitors ---

def _upright(ctx: _Ctx, c: Constraint) -> list[ViolationInterval]:
    tol = float(c.params.get("max_tilt_deg", DEFAULT_UPRIGHT_TOL_DEG))
    out = []
    for oid in _resolve(ctx, c):
        k = ctx.log.index(oid)
        mask = _phase_mask(ctx, k, c.phase)
        over = mask & (ctx.tilt[:, k] > tol)
        for s, e in _intervals(over):
            seg = ctx.tilt[s:e + 1, k]
            dur = float(ctx.log.t[e] - ctx.log.t[s]) + float(ctx.log.dt)
            out.append(ViolationInterval(
                constraint_id=c.id, object_id=oid,
                t_start=float(ctx.log.t[s]), t_end=float(ctx.log.t[e]),
                peak=float(seg.max()),
                detail=f"tilt > {tol:g} deg during {c.phase} for {dur:.3f} s ({e - s + 1} steps)"))
    return out


def _group_match(group: str, other: str, self_id: str, object_ids: set[str]) -> bool:
    if group == "obstacle":
        return other.startswith("obstacle:")
    if group == "table_outside_tray":
        return other in ("table", "floor")   # the room floor is outside the tray too
    if group == "other_objects":
        return other in object_ids and other != self_id
    if group == "tray":
        return other.startswith("tray:")
    if group == "gripper":
        return other == "gripper"
    return other == group or other.startswith(group + ":")


def _forbidden(ctx: _Ctx, c: Constraint) -> tuple[list[ViolationInterval], list[dict]]:
    groups = list(c.params.get("with", []))
    known = {"obstacle", "table_outside_tray", "other_objects", "tray", "gripper"}
    for g in groups:
        if g not in known:
            ctx.notes.append(f"{c.id}: contact group {g!r} is not a standard group; matched literally")
    targets = _resolve(ctx, c)
    obj_set = set(ctx.log.object_ids)
    hits: dict[tuple[str, str], list[tuple[int, float]]] = {}
    masks = {oid: _phase_mask(ctx, ctx.log.index(oid), c.phase) for oid in targets}
    for rec in ctx.log.contacts:
        for me, other in ((rec.object_id, rec.other), (rec.other, rec.object_id)):
            if me not in masks:
                continue
            if not (0 <= rec.step < ctx.log.n) or not masks[me][rec.step]:
                continue
            if any(_group_match(g, other, me, obj_set) for g in groups):
                hits.setdefault((me, other), []).append((rec.step, rec.depth))
    intervals, records = [], []
    for (me, other), lst in sorted(hits.items()):
        steps = np.zeros(ctx.log.n, bool)
        depth = np.zeros(ctx.log.n)
        for s, d in lst:
            steps[s] = True
            depth[s] = max(depth[s], d)
        for s, e in _intervals(steps):
            rec = dict(constraint_id=c.id, object_id=me, other=other,
                       t_start=float(ctx.log.t[s]), t_end=float(ctx.log.t[e]),
                       steps=int(steps[s:e + 1].sum()), max_depth=float(depth[s:e + 1].max()))
            records.append(rec)
            intervals.append(ViolationInterval(
                constraint_id=c.id, object_id=me, t_start=rec["t_start"], t_end=rec["t_end"],
                peak=rec["max_depth"], detail=f"contact with {other} during {c.phase}"))
    return intervals, records


def _clearance(ctx: _Ctx, c: Constraint) -> list[ViolationInterval]:
    """Distance from the object's solid shape (box; capsule for the marker) to
    every obstacle box stays >= min_m during the phase. A bounding sphere gives
    a cheap lower bound; steps where that bound is below min_m are measured
    exactly (checker 2; checker 1 used the bounding-sphere estimate alone)."""
    min_m = float(c.params.get("min_m", 0.0))
    boxes = [b for o in ctx.scene.obstacles for b in geom.obstacle_boxes(o)]
    objs = geom.objects_by_id(ctx.scene)
    out = []
    if not boxes:
        return out
    for oid in _resolve(ctx, c):
        k = ctx.log.index(oid)
        p = ctx.log.obj_pos[:, k, :]
        mask = _phase_mask(ctx, k, c.phase)
        if oid not in objs:
            ctx.notes.append(f"{c.id}: {oid} has no geometry in the scene; clearance not measured")
            continue
        r = geom.bounding_radius(objs[oid])
        lower = np.min(np.stack([geom.point_box_distance(p, b) for b in boxes]), axis=0) - r
        dist = np.where(mask, np.maximum(lower, 0.0), np.inf)
        need = np.flatnonzero(mask & (lower < min_m))
        if need.size:
            dist[need] = geom.object_obstacle_distance(objs[oid], p[need], ctx.log.obj_quat[need, k], boxes)
        bad = mask & (dist < min_m)
        for s, e in _intervals(bad):
            out.append(ViolationInterval(
                constraint_id=c.id, object_id=oid, t_start=float(ctx.log.t[s]),
                t_end=float(ctx.log.t[e]), peak=float(dist[s:e + 1].min()),
                detail=f"clearance below {min_m:g} m (closest {float(dist[s:e + 1].min()):.4f} m, solid shape) during {c.phase}"))
    return out


def evaluate_rules(ctx: _Ctx, rules: Iterable[Constraint]) -> tuple[list[ViolationInterval], list[dict]]:
    violations: list[ViolationInterval] = []
    contacts: list[dict] = []
    for c in rules:
        if c.type == "upright":
            violations += _upright(ctx, c)
        elif c.type == "forbidden_contact":
            v, r = _forbidden(ctx, c)
            violations += v
            contacts += r
        elif c.type == "keep_clearance":
            violations += _clearance(ctx, c)
        else:  # pragma: no cover - contracts restrict the literal
            ctx.notes.append(f"{c.id}: unsupported constraint type {c.type}")
    return violations, contacts


# ------------------------------------------------------------ ordering ---

def _events(ctx: _Ctx, oid: str):
    from .evidence import object_events
    if oid not in ctx._events:
        tray, _ = destination_tray("destination", ctx.scene)
        tol = float(ctx.spec.tolerances.get("settle_speed_mps", 0.01))
        ctx._events[oid] = object_events(ctx.log, ctx.scene, oid, tray, settle_speed=tol)
    return ctx._events[oid]


def _event_text(event: str) -> str:
    return {"grasp_start": "was grasped", "placement_complete": "was placed"}[event]


def monitor_ordering(ctx: _Ctx, oc: OrderingConstraint) -> tuple[str, str, dict, list[ViolationInterval]]:
    """Judge one ordering rule from the evidence.

    Every object matched by ``before`` must reach ``before_event`` strictly
    before any object matched by ``after`` reaches ``after_event``; every pair
    (a, b) with a != b is checked. Per pair:
      after event never happened                         -> holds (vacuously)
      after event happened, before event never happened  -> violated
      both happened, separated by more than the margin   -> holds or violated
      within the margin (a tie at the evidence resolution) -> unknown
      either object missing, or an event unresolved       -> unknown
    Returns (verdict, detail, evidence, violation intervals)."""
    from .evidence import evidence_source, resolution_s
    before = select(oc.before, ctx.spec, ctx.scene)
    after = select(oc.after, ctx.spec, ctx.scene)
    res = resolution_s(ctx.log)
    margin = 2.0 * res
    evidence = {"source": evidence_source(ctx.log), "resolution_s": round(res, 6), "margin_s": round(margin, 6),
                "before_event": oc.before_event, "after_event": oc.after_event,
                "before_objects": before, "after_objects": after, "pairs": [], "events": {}}
    if not before or not after:
        side = "before" if not before else "after"
        return "not_applicable", f"the {side} selector matches no object in scene {ctx.scene.scene_id}", evidence, []
    pairs = [(a, b) for a in before for b in after if a != b]
    if not pairs:
        return "not_applicable", "before and after select the same single object", evidence, []
    for oid in dict.fromkeys(before + after):
        evidence["events"][oid] = _events(ctx, oid).summary()
    verdicts, violations, unknown_why, vacuous = [], [], [], 0
    for a, b in pairs:
        sa, ta = _events(ctx, a).time(oc.before_event)
        sb, tb = _events(ctx, b).time(oc.after_event)
        pair = {"before": a, "after": b, "before_state": sa, "before_t": ta, "after_state": sb, "after_t": tb}
        if "missing" in (sa, sb):
            v, why = "unknown", f"{a if sa == 'missing' else b} is not in the evidence"
        elif sb == "unresolved" or (sa == "unresolved" and sb != "absent"):
            who = b if sb == "unresolved" else a
            v, why = "unknown", f"the {('after' if who == b else 'before')} event of {who} could not be resolved"
        elif sb == "absent":
            v, why = "satisfied", f"{b} never {_event_text(oc.after_event).split(' ', 1)[1]}"
            vacuous += 1
        elif sa == "absent":
            v, why = "violated", f"{b} {_event_text(oc.after_event)} at {tb:.2f} s but {a} never {_event_text(oc.before_event).split(' ', 1)[1]}"
            violations.append(ViolationInterval(constraint_id=oc.id, object_id=b, t_start=float(tb),
                                                t_end=float(ctx.log.t[-1]), peak=float(ctx.log.t[-1] - tb),
                                                detail=f"order: {why}"))
        elif ta < tb - margin:
            v, why = "satisfied", f"{a} {_event_text(oc.before_event)} at {ta:.2f} s, {b} {_event_text(oc.after_event)} at {tb:.2f} s"
        elif ta > tb + margin:
            v, why = "violated", f"{b} {_event_text(oc.after_event)} at {tb:.2f} s, before {a} {_event_text(oc.before_event)} at {ta:.2f} s"
            violations.append(ViolationInterval(constraint_id=oc.id, object_id=b, t_start=float(tb),
                                                t_end=float(ta), peak=float(ta - tb), detail=f"order: {why}"))
        else:
            v, why = "unknown", (f"{a} {_event_text(oc.before_event)} at {ta:.3f} s and {b} "
                                 f"{_event_text(oc.after_event)} at {tb:.3f} s are within the "
                                 f"{margin:.3f} s timing margin")
        pair.update(verdict=v, why=why)
        evidence["pairs"].append(pair)
        verdicts.append(v)
        if v == "unknown":
            unknown_why.append(why)
    if "violated" in verdicts:
        return "violated", "; ".join(p["why"] for p in evidence["pairs"] if p["verdict"] == "violated"), evidence, violations
    if "unknown" in verdicts:
        return "unknown", "; ".join(unknown_why), evidence, []
    detail = "; ".join(p["why"] for p in evidence["pairs"])
    if vacuous == len(pairs):
        detail = "holds vacuously: " + detail
    return "satisfied", detail, evidence, []


def _ordering_text(oc: OrderingConstraint) -> str:
    sel = lambda s: s.kind or s.role or ",".join(s.ids or []) or "any"
    return f"{sel(oc.before)} {oc.before_event} before {sel(oc.after)} {oc.after_event}"


# -------------------------------------------------------- rule results ---

def _constraint_result(ctx: _Ctx, c: Constraint, violations: list[ViolationInterval],
                       enforced: bool) -> RuleResult:
    from .evidence import contacts_logged, evidence_source
    ids = select(c.applies_to, ctx.spec, ctx.scene)
    mine = [v for v in violations if v.constraint_id == c.id]
    missing = [i for i in ids if i not in ctx.log.object_ids]
    ev = {"source": evidence_source(ctx.log), "phase": c.phase, "params": dict(c.params)}
    base = dict(rule_id=c.id, kind="constraint", type=c.type, status=c.status, enforced=enforced, objects=ids,
                evidence=ev)
    if c.status == Status.rejected and not enforced:
        return RuleResult(verdict="not_evaluated", detail="rejected by the user; recorded, not checked", **base)
    if mine:
        return RuleResult(verdict="violated", detail="; ".join(dict.fromkeys(
            f"{v.object_id}: {v.detail}" for v in mine)), **base)
    if not ids:
        return RuleResult(verdict="not_applicable", detail=f"no object in scene {ctx.scene.scene_id} matches", **base)
    if missing:
        return RuleResult(verdict="unknown", detail=f"objects {missing} are not in the evidence", **base)
    if c.type == "forbidden_contact" and not contacts_logged(ctx.log):
        return RuleResult(verdict="unknown", detail="contacts are not recorded in this evidence", **base)
    if c.type == "upright" and c.phase == "transport":
        carried = [i for i in ids if _transport_mask(ctx.log, ctx.log.index(i)).any()]
        if not carried:
            return RuleResult(verdict="satisfied", detail="holds vacuously: the object was never carried", **base)
    if ev["source"] != "dense_state_log":
        ev["note"] = "checked at the playback frames only; an excursion between frames would be missed"
    return RuleResult(verdict="satisfied", **base)


def _goal_results(ctx: _Ctx, by_id: dict[str, ObjectOutcome]) -> list[RuleResult]:
    out = []
    for g in ctx.spec.goals:
        ids = select(g.object, ctx.spec, ctx.scene)
        base = dict(rule_id=g.id, kind="goal", type=g.type, status=g.status,
                    enforced=g.status != Status.rejected, objects=ids)
        if g.status == Status.rejected:
            out.append(RuleResult(verdict="not_evaluated", detail="rejected by the user; not required", **base))
            continue
        if not ids:
            out.append(RuleResult(verdict="violated", detail="no object matches; completion cannot be established",
                                  **base))
            continue
        missing = [i for i in ids if i not in by_id]
        if missing:
            out.append(RuleResult(verdict="unknown", detail=f"objects {missing} are not in the evidence", **base))
            continue
        bad = [i for i in ids if not (by_id[i].in_target and by_id[i].released and by_id[i].settled)]
        out.append(RuleResult(verdict="violated" if bad else "satisfied",
                              detail=(f"not packed: {bad}" if bad else "inside the tray, released and settled"),
                              **base))
    return out


# ------------------------------------------------------------ outcomes ---

def _outcome(ctx: _Ctx, oid: str, tray) -> ObjectOutcome:
    log = ctx.log
    objs = geom.objects_by_id(ctx.scene)
    k = log.index(oid)
    held = _transport_mask(log, k)
    win = ctx.window
    released = bool(log.n) and not bool(held[win].any())
    tol = float(ctx.spec.tolerances.get("settle_speed_mps", 0.01))
    settled = bool(log.n) and float(ctx.speed[win, k].max()) < tol
    margin = float(ctx.spec.tolerances.get("inside_margin_m", 0.0))
    in_target = False
    if tray is not None and log.n:
        in_target = bool(geom.inside_tray(tray, log.obj_pos[-1, k], margin))
    max_tr = float(ctx.tilt[held, k].max()) if held.any() else 0.0
    beads_total = beads_in = 0
    if oid in log.beads and oid in objs and log.n:
        b = log.beads[oid][-1]
        beads_total = int(b.shape[0])
        beads_in = int(geom.beads_inside_cup(objs[oid], log.obj_pos[-1, k], log.obj_quat[-1, k],
                                             b, BEAD_TOL_M).sum())
    elif oid in objs:
        beads_total = int(objs[oid].beads)
        if beads_total:
            ctx.notes.append(f"{oid}: bead positions not logged; bead retention unknown")
    return ObjectOutcome(object_id=oid, in_target=in_target, released=released, settled=settled,
                         final_tilt_deg=float(ctx.tilt[-1, k]) if log.n else 0.0,
                         max_transport_tilt_deg=max_tr, beads_total=beads_total,
                         beads_in_container=beads_in)


def _required_objects(ctx: _Ctx) -> tuple[list[str], dict[str, object], bool]:
    """Ids of objects named by confirmed goals, their trays, and whether every
    goal could be bound to at least one object."""
    req: list[str] = []
    trays: dict[str, object] = {}
    all_bound = True
    confirmed = ctx.spec.active_goals()   # goals are pursued unless a human rejected them
    for g in ctx.spec.goals:
        if g.status == Status.rejected:
            ctx.notes.append(f"goal {g.id} is rejected; not required")
    for g in confirmed:
        ids = select(g.object, ctx.spec, ctx.scene)
        tray, note = destination_tray(g.target, ctx.scene)
        if note:
            ctx.notes.append(f"goal {g.id}: {note}")
        if not ids:
            ctx.notes.append(f"goal {g.id}: no object in the scene matches its selector")
            all_bound = False
        for i in ids:
            if i not in ctx.log.object_ids:
                ctx.notes.append(f"goal {g.id}: {i} missing from step log")
                all_bound = False
                continue
            if i not in req:
                req.append(i)
                trays[i] = tray
    if not confirmed:
        ctx.notes.append("specification has no active goal; completion cannot be established")
        all_bound = False
    return req, trays, all_bound


def spec_hash(spec: TaskSpec) -> str:
    return hashlib.sha256(spec.model_dump_json().encode()).hexdigest()


def check(log: StateLog, spec: TaskSpec, scene: SceneState, *,
          condition: str = "",
          execution_status: Optional[str] = None,
          execution_reason: str = "",
          plan: Optional[PlanResult] = None,
          extra_rules: Optional[list[Constraint]] = None,
          include_unenforced: bool = False,
          rollout_id: Optional[str] = None,
          extra_ordering: Optional[list[OrderingConstraint]] = None) -> RolloutReport:
    """Check a rollout's step log against ``spec`` in ``scene``.

    Only ``spec.enforced_constraints()`` can make the status
    ``constraint_violated``. Required objects are those named by
    ``spec.active_goals()``. Rules from ``extra_rules`` (for example the
    constraints of a newer specification version) and, with
    ``include_unenforced``, the spec's own proposed or rejected constraints are
    checked as well and reported in ``RolloutReport.advisory_violations``; they
    never change the status.

    Ordering rules: confirmed orderings are monitored from the evidence and
    gate the status like constraints; proposed orderings and ``extra_ordering``
    (e.g. a newer version's) are monitored and reported in ``rule_results``
    (their violations also in ``advisory_violations``) without affecting the
    status; rejected ones are recorded as not evaluated. A confirmed
    constraint or ordering whose verdict is ``unknown`` turns an otherwise
    successful rollout into ``unverified``.
    """
    from .evidence import evidence_source
    ctx = _context(log, spec, scene)
    exec_status = execution_status or log.meta.get("status") or "completed"
    enforced = spec.enforced_constraints()
    violations, contacts = evaluate_rules(ctx, enforced)
    rule_results: list[RuleResult] = []
    order_adv: list[ViolationInterval] = []
    for oc in spec.ordering:
        if oc.status == Status.rejected:
            rule_results.append(RuleResult(rule_id=oc.id, kind="ordering", type="ordering", status=oc.status,
                                           enforced=False, verdict="not_evaluated",
                                           detail=f"rejected by the user ({_ordering_text(oc)}); recorded, not checked"))
            continue
        verdict, detail, ev, vio = monitor_ordering(ctx, oc)
        confirmed = oc.status == Status.confirmed
        if confirmed:
            violations += vio
        else:
            order_adv += vio
        rule_results.append(RuleResult(rule_id=oc.id, kind="ordering", type="ordering", status=oc.status,
                                       enforced=confirmed, verdict=verdict, detail=detail, evidence=ev,
                                       objects=list(dict.fromkeys(ev["before_objects"] + ev["after_objects"]))))
    own_ids = {o.id for o in spec.ordering}
    for oc in extra_ordering or []:
        if oc.id in own_ids:
            continue
        verdict, detail, ev, vio = monitor_ordering(ctx, oc)
        order_adv += vio
        rule_results.append(RuleResult(rule_id=oc.id, kind="ordering", type="ordering", status=oc.status,
                                       enforced=False, verdict=verdict, detail=detail, evidence=ev,
                                       objects=list(dict.fromkeys(ev["before_objects"] + ev["after_objects"]))))

    req, trays, all_bound = _required_objects(ctx)
    order = req + [o for o in log.object_ids if o not in req]
    outcomes = [_outcome(ctx, oid, trays.get(oid) or destination_tray("destination", scene)[0])
                for oid in order]
    by_id = {o.object_id: o for o in outcomes}
    completion = all_bound and bool(req) and all(
        by_id[i].in_target and by_id[i].released and by_id[i].settled for i in req)

    proposed = [c for c in spec.constraints if c.status == Status.proposed]
    proposed_v, _ = evaluate_rules(_context(log, spec, scene), proposed) if proposed else ([], [])
    c_results = []
    for c in spec.constraints:
        is_enf = c.status == Status.confirmed
        c_results.append(_constraint_result(ctx, c, violations if is_enf else proposed_v, is_enf))
    rule_results = _goal_results(ctx, by_id) + c_results + rule_results
    unknown_enforced = [r for r in rule_results if r.enforced and r.verdict == "unknown" and r.kind != "goal"]

    if exec_status == "execution_failed":
        status = "execution_failed"
    elif exec_status == "timeout":
        status = "timeout"
    elif violations:
        status = "constraint_violated"
    elif not completion:
        status = "task_incomplete"
    elif unknown_enforced:
        status = "unverified"
    else:
        status = "success"

    for oid in log.object_ids:
        k = log.index(oid)
        extra = int((_transport_mask(log, k) & ~_held_mask(log, k)).sum())
        if extra:
            ctx.notes.append(f"{oid}: {extra} step(s) lifted off its support without the finger-contact "
                             f"held label; counted as transport")
    notes = list(dict.fromkeys(ctx.notes))
    notes.append(f"required objects: {req}")
    for i in req:
        o = by_id[i]
        if not (o.in_target and o.released and o.settled):
            miss = [n for n, ok in (("in_target", o.in_target), ("released", o.released),
                                    ("settled", o.settled)) if not ok]
            notes.append(f"{i} incomplete: {', '.join(miss)}")
    if execution_reason:
        notes.append(f"executor: {execution_reason}")
    for r in unknown_enforced:
        notes.append(f"{r.rule_id} could not be verified: {r.detail}")

    adv = advisory_violations(log, spec, scene, extra_rules=extra_rules,
                              include_unenforced=include_unenforced, _ctx=ctx) + order_adv
    if adv:
        notes.append(f"{len(adv)} advisory violation(s) of rules not enforced in v{spec.version}; "
                     f"see advisory_violations")

    rid = rollout_id or hashlib.sha1(
        f"{scene.scene_id}|{spec.spec_id}|{spec.version}|{condition}|{log.n}".encode()).hexdigest()[:12]
    return RolloutReport(
        rollout_id=rid, scene_id=scene.scene_id, spec_id=spec.spec_id, spec_version=spec.version,
        condition=condition, status=status,
        plan_status=plan.status if plan else "", plan_reason=plan.reason if plan else "",
        route_labels=chosen_routes(plan) if plan else [],
        outcomes=outcomes, violations=violations, advisory_violations=adv,
        forbidden_contacts=contacts,
        completion=completion, planning_time_s=plan.planning_time_s if plan else 0.0,
        execution_time_s=float(log.t[-1] - log.t[0]) if log.n else 0.0,
        physics_steps=log.n, checker=CHECKER_ID,
        ground_truth_use=list(GROUND_TRUTH_USE) + ([ORDERING_GROUND_TRUTH_USE] if spec.ordering or extra_ordering else []),
        notes=notes, rule_results=rule_results, evidence_source=evidence_source(log))


def chosen_routes(plan: PlanResult) -> list[str]:
    """Route labels the plan actually uses, read from its transport steps
    (``route=<label>`` in the step note). Falls back to the cheapest feasible
    route per object when the notes do not name one."""
    import re
    used: list[str] = []
    for s in plan.steps:
        if s.kind in ("transport", "move") and s.note:
            m = re.search(r"route=([^;]+)", s.note)
            if m and m.group(1).strip() not in used:
                used.append(m.group(1).strip())
    if used:
        return used
    best: dict[str, object] = {}
    for r in plan.routes:
        if r.feasible and (r.object_id not in best or (r.cost or 0) < (best[r.object_id].cost or 0)):
            best[r.object_id] = r
    return [r.label for r in best.values()]


def advisory_violations(log: StateLog, spec: TaskSpec, scene: SceneState, *,
                        extra_rules: Optional[list[Constraint]] = None,
                        include_unenforced: bool = False, _ctx: Optional[_Ctx] = None
                        ) -> list[ViolationInterval]:
    """Violations of rules that ``spec`` does not enforce: ``extra_rules``
    (duplicates of enforced ids are skipped) and, if requested, the spec's own
    proposed/rejected constraints. Used to re-check an old rollout under a
    newer specification without changing its recorded status."""
    ctx = _ctx or _context(log, spec, scene)
    enforced_ids = {c.id for c in spec.enforced_constraints()}
    rules = []
    if include_unenforced:
        rules += [c for c in spec.constraints if c.status != Status.confirmed]
    for c in extra_rules or []:
        if c.id not in enforced_ids and all(c.id != r.id for r in rules):
            rules.append(c)
    v, _ = evaluate_rules(ctx, rules)
    return v
