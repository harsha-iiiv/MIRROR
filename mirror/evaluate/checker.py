"""Independent rollout checker.

``check`` is a pure function of the dense step log, the task specification
and the scene. It never reads planner explanations or model output. Every
condition is evaluated at every logged physics step with explicit
tolerances, and the places where simulator ground truth is used are listed in
the report.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Iterable, Optional

import numpy as np

from mirror.contracts import (Constraint, ObjectOutcome, PlanResult, RolloutReport,
                              SceneState, Status, TaskSpec, ViolationInterval)
from mirror.geometry import DEFAULT_UPRIGHT_TOL_DEG
from mirror.statelog import StateLog, tilt_deg

from . import geom
from .grounding import destination_tray, select

CHECKER_ID = "mirror.evaluate.check/1"
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
                "constraint_violated", "task_incomplete", "success"]


@dataclass
class _Ctx:
    log: StateLog
    spec: TaskSpec
    scene: SceneState
    tilt: np.ndarray            # (N, K)
    speed: np.ndarray           # (N, K) linear speed, m/s
    window: np.ndarray          # (N,) bool, final settle window
    notes: list[str] = field(default_factory=list)


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
    v = np.linalg.norm(np.diff(log.obj_pos, axis=0), axis=-1) / dt[:, None]
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


def _transport_mask(log: StateLog, idx: int) -> np.ndarray:
    """Steps from grasp to release of object ``idx``: the per-step held label,
    with gaps shorter than TRANSPORT_GAP_FILL_S between held runs filled when
    no other object is held during the gap."""
    held = np.asarray(log.held)
    m = held == idx
    runs = _intervals(m)
    for (s0, e0), (s1, _) in zip(runs, runs[1:]):
        gap = slice(e0 + 1, s1)
        if log.t[s1] - log.t[e0] <= TRANSPORT_GAP_FILL_S and not np.any((held[gap] >= 0) & (held[gap] != idx)):
            m[gap] = True
    return m


def _resolve(ctx: _Ctx, c: Constraint) -> list[str]:
    ids = select(c.applies_to, ctx.spec, ctx.scene)
    present = [i for i in ids if i in ctx.log.object_ids]
    missing = [i for i in ids if i not in ctx.log.object_ids]
    if missing:
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
        return other == "table"
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
    min_m = float(c.params.get("min_m", 0.0))
    boxes = [b for o in ctx.scene.obstacles for b in geom.obstacle_boxes(o)]
    objs = geom.objects_by_id(ctx.scene)
    out = []
    if not boxes:
        return out
    for oid in _resolve(ctx, c):
        k = ctx.log.index(oid)
        r = geom.bounding_radius(objs[oid]) if oid in objs else 0.0
        p = ctx.log.obj_pos[:, k, :]
        dist = np.min(np.stack([geom.point_box_distance(p, b) for b in boxes]), axis=0) - r
        mask = _phase_mask(ctx, k, c.phase) & (dist < min_m)
        for s, e in _intervals(mask):
            out.append(ViolationInterval(
                constraint_id=c.id, object_id=oid, t_start=float(ctx.log.t[s]),
                t_end=float(ctx.log.t[e]), peak=float(dist[s:e + 1].min()),
                detail=f"clearance below {min_m:g} m (bounding-sphere estimate) during {c.phase}"))
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
    confirmed = [g for g in ctx.spec.goals if g.status == Status.confirmed]
    for g in ctx.spec.goals:
        if g.status != Status.confirmed:
            ctx.notes.append(f"goal {g.id} is {g.status.value}; not required")
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
        ctx.notes.append("specification has no confirmed goal; completion cannot be established")
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
          rollout_id: Optional[str] = None) -> RolloutReport:
    """Check a rollout's step log against ``spec`` in ``scene``.

    Only ``spec.enforced_constraints()`` can make the status
    ``constraint_violated``. Rules from ``extra_rules`` (for example the
    constraints of a newer specification version) and, with
    ``include_unenforced``, the spec's own proposed or rejected constraints are
    checked as well but reported separately (see ``advisory_violations``) and
    never change the status.
    """
    ctx = _context(log, spec, scene)
    exec_status = execution_status or log.meta.get("status") or "completed"
    enforced = spec.enforced_constraints()
    violations, contacts = evaluate_rules(ctx, enforced)

    req, trays, all_bound = _required_objects(ctx)
    order = req + [o for o in log.object_ids if o not in req]
    outcomes = [_outcome(ctx, oid, trays.get(oid) or destination_tray("destination", scene)[0])
                for oid in order]
    by_id = {o.object_id: o for o in outcomes}
    completion = all_bound and bool(req) and all(
        by_id[i].in_target and by_id[i].released and by_id[i].settled for i in req)

    if exec_status == "execution_failed":
        status = "execution_failed"
    elif exec_status == "timeout":
        status = "timeout"
    elif violations:
        status = "constraint_violated"
    elif not completion:
        status = "task_incomplete"
    else:
        status = "success"

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

    adv = advisory_violations(log, spec, scene, extra_rules=extra_rules,
                              include_unenforced=include_unenforced, _ctx=ctx)
    for v in adv:
        notes.append(f"advisory (not enforced in v{spec.version}): {v.constraint_id} on {v.object_id} "
                     f"{v.t_start:.2f}-{v.t_end:.2f} s peak {v.peak:.1f}")

    rid = rollout_id or hashlib.sha1(
        f"{scene.scene_id}|{spec.spec_id}|{spec.version}|{condition}|{log.n}".encode()).hexdigest()[:12]
    return RolloutReport(
        rollout_id=rid, scene_id=scene.scene_id, spec_id=spec.spec_id, spec_version=spec.version,
        condition=condition, status=status,
        plan_status=plan.status if plan else "", plan_reason=plan.reason if plan else "",
        route_labels=chosen_routes(plan) if plan else [],
        outcomes=outcomes, violations=violations, forbidden_contacts=contacts,
        completion=completion, planning_time_s=plan.planning_time_s if plan else 0.0,
        execution_time_s=float(log.t[-1] - log.t[0]) if log.n else 0.0,
        physics_steps=log.n, checker=CHECKER_ID, ground_truth_use=list(GROUND_TRUTH_USE),
        notes=notes)


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
