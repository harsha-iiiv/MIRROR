"""Task and motion planning for kit packing.

plan(spec, scene) orders the objects, chooses grasps and tray spots, and for
every motion enumerates labelled route candidates (see routes.py), keeping
all of them in PlanResult.routes with feasibility, cost and the reason a
candidate was rejected. Only spec.enforced_constraints() restrict planning.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..contracts import (Constraint, ObjectSpec, PlanResult, PlanStep, Pose, RouteOption,
                         SceneState, Status, TaskSpec)
from ..geometry import DEFAULT_UPRIGHT_TOL_DEG, GRIPPER
from . import certificate as cert_mod
from .collision import Box, World, check_segment, make_envelope
from .config import DEFAULT_CONFIG, PLANNER_NAME, UPRIGHT, Mode, PlannerConfig
from .grasp import GraspModel, grasp_model, grasp_yaws
from .grounding import Grounding, destination_tray, ground
from .placement import Placement, place_spot
from .routes import Candidate, Query, RouteSearch, Waypoint, path_cost, separates, wall_yaw
from .se3 import gripper_rot, mat_to_quat, quat_to_mat, tilt_deg_of, wrap, yaw_of
from .world import object_box, scene_world


KIND_PRIORITY = {"cup": 0, "block": 1, "marker": 2}


@dataclass
class ObjectPlan:
    obj: ObjectSpec
    gm: GraspModel
    grasp_yaw: float
    grasp_tcp: np.ndarray
    pregrasp: np.ndarray
    placement: Optional[Placement]
    transit: list[Candidate]
    transport: list[Candidate]
    banned: dict
    allowed: list[Mode]
    failure: Optional[str] = None
    infeasibility: Optional[dict] = None


@dataclass
class Core:
    spec: TaskSpec
    scene: SceneState
    cfg: PlannerConfig
    grounding: Grounding
    order: list[str]
    objects: list[ObjectPlan] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    start_pos: Optional[np.ndarray] = None
    start_R: Optional[np.ndarray] = None
    t0: float = 0.0
    status: str = "ok"
    reason: str = ""


# ------------------------------------------------------------ ordering ---


def order_objects(spec: TaskSpec, g: Grounding, scene: SceneState) -> tuple[Optional[list[str]], str]:
    kinds = {o.id: o.kind for o in scene.objects}
    ids = list(g.objects_to_pack)
    edges: set[tuple[str, str]] = set()
    for oc in spec.ordering:
        if oc.status != Status.confirmed:
            continue
        before, after = g.ordering_objects.get(oc.id, ([], []))
        for a in before:
            for b in after:
                if a in ids and b in ids and a != b:
                    edges.add((a, b))
    key = lambda i: (KIND_PRIORITY.get(kinds[i], 9), i)
    remaining = sorted(ids, key=key)
    out: list[str] = []
    while remaining:
        free = [i for i in remaining if not any(b == i and a in remaining for a, b in edges)]
        if not free:
            return None, "confirmed ordering constraints form a cycle"
        nxt = sorted(free, key=key)[0]
        out.append(nxt)
        remaining.remove(nxt)
    return out, ""


# ---------------------------------------------------------- constraints ---


def mode_restrictions(spec: TaskSpec, g: Grounding, obj_id: str, cfg: PlannerConfig):
    allowed = list(cfg.modes)
    banned: dict[str, str] = {}
    margin_extra = 0.0
    notes = []
    for c in spec.enforced_constraints():
        if obj_id not in g.constraint_objects.get(c.id, []):
            continue
        if c.type == "upright" and c.phase in ("transport", "always"):
            lim = float(c.params.get("max_tilt_deg", DEFAULT_UPRIGHT_TOL_DEG))
            for m in list(allowed):
                if m.tilt_deg > lim + 1e-9:
                    allowed.remove(m)
                    banned[m.name] = (f"violates {c.id} (object tilt {m.tilt_deg:.0f} deg > "
                                      f"max_tilt_deg {lim:g} during {c.phase})")
        elif c.type == "keep_clearance":
            margin_extra = max(margin_extra, float(c.params.get("min_m", 0.0)))
        elif c.type == "upright" and c.phase == "final":
            notes.append(f"{c.id}: objects are always placed upright")
        elif c.type == "forbidden_contact":
            notes.append(f"{c.id}: planned motions keep {obj_id} clear of obstacles and other objects "
                         "by the safety margin")
    return allowed, banned, margin_extra, notes


# ----------------------------------------------------------- the core ---


def _cfg_with_margin(cfg: PlannerConfig, extra: float) -> PlannerConfig:
    if extra <= cfg.margin:
        return cfg
    from dataclasses import replace
    return replace(cfg, margin=extra)


def build_core(spec: TaskSpec, scene: SceneState, cfg: PlannerConfig = DEFAULT_CONFIG,
               order_override: Optional[list[str]] = None, allow_rrt: bool = True) -> Core:
    t0 = time.perf_counter()
    g = ground(spec, scene)
    core = Core(spec, scene, cfg, g, [], t0=t0)
    if not g.ok:
        core.status, core.reason = "invalid_spec", "; ".join(dict.fromkeys(g.errors))
        return core
    if not g.objects_to_pack:
        core.status, core.reason = "invalid_spec", "specification has no active goal: nothing to pack"
        return core
    tray_id = destination_tray(g)
    tray = next(t for t in scene.trays if t.id == tray_id)
    order, err = order_objects(spec, g, scene)
    if order is None:
        core.status, core.reason = "invalid_spec", err
        return core
    objs = {o.id: o for o in scene.objects}
    # skip objects already resting inside the tray
    for oid in list(order):
        o = objs[oid]
        if _inside_tray(o, tray):
            order.remove(oid)
            core.notes.append(f"{oid} already inside {tray.id}")
    if order_override is not None:
        order = [o for o in order_override if o in order]
    core.order = order
    static = scene_world(scene)
    current = {o.id: object_box(o) for o in scene.objects}
    sp = np.array(scene.gripper_start.pos, float)
    sR = quat_to_mat(scene.gripper_start.quat)
    core.start_pos, core.start_R = sp, sR
    cur_pos, cur_yaw = sp, yaw_of(scene.gripper_start.quat)
    if tilt_deg_of(scene.gripper_start.quat) > 1.0:
        core.notes.append("gripper start is not upright; the first move rotates it upright in place")
    sep_walls = [w for w in scene.obstacles if w.kind == "wall"]
    placed: list[Box] = []
    prev_open = GRIPPER.max_opening
    for oid in order:
        obj = objs[oid]
        gm = grasp_model(obj)
        allowed, banned, mextra, cnotes = mode_restrictions(spec, g, oid, cfg)
        core.notes += cnotes
        ocfg = _cfg_with_margin(cfg, mextra)
        if tilt_deg_of(obj.pose.quat) > 10.0:
            op = ObjectPlan(obj, gm, 0.0, np.zeros(3), np.zeros(3), None, [], [], banned, allowed,
                            failure=f"{oid} is not standing upright (tilt {tilt_deg_of(obj.pose.quat):.0f} deg); "
                                    "only top grasps of upright objects are supported")
            core.objects.append(op)
            continue
        oyaw = yaw_of(obj.pose.quat)
        obj_xy = np.array(obj.pose.pos[:2])
        tray_xy = np.array(tray.pose.pos[:2])
        # preferred jaw yaw: along a wall separating the object from the tray (slot portals need it)
        ref = 0.0
        far_from = None
        for w in sep_walls:
            if separates(w, obj_xy, tray_xy):
                ref = wall_yaw(w)
                wR = quat_to_mat(w.pose.quat)
                far_from = (np.array(w.pose.pos[:2], float), wR[:2, 0])
                break
        yaws = sorted(grasp_yaws(obj, oyaw), key=lambda y: (round(abs(wrap(2 * (y - ref))) / 2, 9), y))
        others_now = World(static.boxes + [b for k, b in current.items() if k != oid], 0.0)
        centre = np.array(obj.pose.pos, float)
        chosen = None
        for gy in yaws:
            grasp_tcp = centre + quat_to_mat(obj.pose.quat) @ np.array([0.0, 0.0, gm.tcp_offset_z])
            pre = grasp_tcp + np.array([0.0, 0.0, cfg.approach_height])
            env_open = make_envelope(gm.open_width)
            if check_segment(others_now, env_open, pre, gripper_rot(gy), grasp_tcp, gripper_rot(gy),
                             margin=cfg.fine_margin, ground_margin=cfg.fine_ground_margin) is None:
                chosen = (gy, grasp_tcp, pre)
                break
        if chosen is None:
            core.objects.append(ObjectPlan(obj, gm, yaws[0], np.zeros(3), np.zeros(3), None, [], [], banned,
                                           allowed, failure=f"no collision-free top grasp approach for {oid}"))
            continue
        gy, grasp_tcp, pre = chosen
        rel = wrap(oyaw - gy)
        # transit (empty jaw) from the current pose to the pre-grasp pose
        env_t = make_envelope(max(prev_open, gm.open_width))
        world_t = World(static.boxes + list(current.values()), 0.0)
        qt = Query(world_t, env_t, cur_pos, cur_yaw, pre, gy, list(cfg.modes), {}, scene, cfg,
                   start_fine=False, goal_fine=True, seed_key=f"transit:{oid}")
        transit = RouteSearch(qt).run()
        transit = [_prefix(c, "transit") for c in transit]
        if not any(c.feasible for c in transit) and allow_rrt:
            transit += [_prefix(c, "transit") for c in _rrt(qt)]
        # placement
        world_p = World(static.boxes + placed, 0.0)
        pl = place_spot(obj, gm, rel, tray, world_p, placed, cfg, prefer_gripper_yaw=ref if far_from else gy,
                        far_from=far_from)
        if pl is None:
            core.objects.append(ObjectPlan(obj, gm, gy, grasp_tcp, pre, None, transit, [], banned, allowed,
                                           failure=f"no free spot for {oid} in {tray.id}"))
            continue
        held = gm.held(oid, rel)
        env_h = make_envelope(gm.width, held)
        world_h = World(static.boxes + [b for k, b in current.items() if k != oid], 0.0)
        qh = Query(world_h, env_h, grasp_tcp, gy, pl.tcp_pos, pl.gripper_yaw, allowed, banned, scene, ocfg,
                   seed_key=f"transport:{oid}")
        transport = RouteSearch(qh).run()
        op = ObjectPlan(obj, gm, gy, grasp_tcp, pre, pl, transit, transport, banned, allowed)
        if not any(c.feasible for c in transport):
            proof = cert_mod.certify(spec, g, scene, obj, gm, tray, banned, allowed, cfg)
            op.infeasibility = proof
            if proof is not None and proof.get("proven"):
                transport.append(Candidate("rrt", "rrt", False,
                                           reason="not attempted: infeasibility is proven (see certificate)"))
            elif allow_rrt:
                transport += _rrt(qh)
            if not any(c.feasible for c in transport):
                op.failure = f"no feasible transport route for {oid}"
        core.objects.append(op)
        # world update: the object now rests at its spot
        current[oid] = pl.box
        placed.append(pl.box)
        cur_pos = pl.tcp_pos + np.array([0.0, 0.0, cfg.retreat_height])
        cur_yaw = pl.gripper_yaw
        prev_open = gm.open_width
    return core


def _rrt(q: Query) -> list[Candidate]:
    try:
        from .rrt import rrt_candidate
    except ImportError:  # pragma: no cover
        return []
    return [rrt_candidate(q)]


def _prefix(c: Candidate, p: str) -> Candidate:
    c.label = f"{p}:{c.label}"
    return c


def _inside_tray(o: ObjectSpec, tray) -> bool:
    Rt = quat_to_mat(tray.pose.quat)
    c = Rt.T @ (np.array(o.pose.pos) - np.array(tray.pose.pos))
    return abs(c[0]) < tray.inner_half[0] and abs(c[1]) < tray.inner_half[1] and c[2] > 0


# ------------------------------------------------------------ choosing ---


def feasible_sorted(cands: list[Candidate]) -> list[int]:
    idx = [i for i, c in enumerate(cands) if c.feasible]
    return sorted(idx, key=lambda i: (round(cands[i].cost, 9), cands[i].label))


def default_choices(core: Core, prefer: Optional[str] = None) -> dict[str, int]:
    out = {}
    for op in core.objects:
        fs = feasible_sorted(op.transport)
        if not fs:
            continue
        pick = fs[0]
        if prefer:
            target_obj, pat = None, prefer
            if ":" in prefer and prefer.split(":", 1)[0] in {o.obj.id for o in core.objects}:
                target_obj, pat = prefer.split(":", 1)
            if target_obj in (None, op.obj.id):
                match = [i for i in fs if pat in op.transport[i].label or pat == op.transport[i].mode]
                if match:
                    pick = match[0]
        out[op.obj.id] = pick
    return out


# ------------------------------------------------------------ assembly ---


def _pose(p, R) -> Pose:
    return Pose(pos=tuple(float(round(v, 6)) for v in p), quat=mat_to_quat(R))


def _split_transport(wps: list[Waypoint]):
    """Split transport waypoints (starting at the grasp pose) into lift,
    transport and place parts."""
    start = wps[0]
    i = 1
    lift = []
    while i < len(wps) and np.linalg.norm(wps[i].pos[:2] - start.pos[:2]) < 1e-9 \
            and np.allclose(wps[i].R, start.R):
        lift.append(wps[i])
        i += 1
    rest = wps[i:]
    goal = rest[-1] if rest else (lift[-1] if lift else start)
    j = len(rest)
    while j > 0 and np.linalg.norm(rest[j - 1].pos[:2] - goal.pos[:2]) < 1e-9 \
            and np.allclose(rest[j - 1].R, goal.R) and rest[j - 1].tag != "rotate":
        j -= 1
    mid, place = rest[:j], rest[j:]
    if not mid and place:  # the last horizontal arrival belongs to transport
        mid, place = place[:1], place[1:]
    return lift, mid, place


def assemble(core: Core, choices: dict[str, int]) -> PlanResult:
    cfg = core.cfg
    routes: list[RouteOption] = []
    executable = core.status == "ok" and not any(op.failure or op.obj.id not in choices for op in core.objects)
    for op in core.objects:
        ft = feasible_sorted(op.transit) if executable else []
        transit_pick = ft[0] if ft else None
        for i, c in enumerate(op.transit):
            routes.append(RouteOption(label=c.label, object_id=op.obj.id, feasible=c.feasible,
                                      cost=None if c.cost is None else round(c.cost, 6),
                                      rejected_reason=c.reason, chosen=(i == transit_pick)))
        pick = choices.get(op.obj.id) if executable else None
        for i, c in enumerate(op.transport):
            routes.append(RouteOption(label=c.label, object_id=op.obj.id, feasible=c.feasible,
                                      cost=None if c.cost is None else round(c.cost, 6),
                                      rejected_reason=c.reason, chosen=(i == pick)))
    base = dict(spec_version=core.spec.version, scene_id=core.scene.scene_id, planner=PLANNER_NAME,
                routes=routes, object_order=list(core.order))
    if core.status != "ok":
        return PlanResult(status=core.status, reason=core.reason,
                          planning_time_s=round(time.perf_counter() - core.t0, 4), **base)
    failures = [op for op in core.objects if op.failure or op.obj.id not in choices]
    if failures:
        msgs = []
        proof = None
        for op in failures:
            msg = op.failure or f"no feasible route for {op.obj.id}"
            rej = [f"{c.label}: {c.reason}" for c in op.transport if not c.feasible]
            if rej:
                msg += " [" + "; ".join(rej) + "]"
            msgs.append(msg)
            if op.infeasibility is not None and (proof is None or (op.infeasibility.get("proven")
                                                                  and not proof.get("proven"))):
                proof = op.infeasibility
        if proof is None or not proof.get("proven"):
            msgs.append("the planner found no plan; this is not a proof that none exists")
        return PlanResult(status="no_plan", reason=" | ".join(msgs), infeasibility=proof,
                          planning_time_s=round(time.perf_counter() - core.t0, 4), **base)
    steps: list[PlanStep] = []
    summary = []
    for op in core.objects:
        tr = op.transit[feasible_sorted(op.transit)[0]]
        mv = [_pose(w.pos, w.R) for w in tr.waypoints[1:]]
        if not steps and core.start_R is not None and tilt_deg_of(mat_to_quat(core.start_R)) > 1.0:
            mv.insert(0, _pose(core.start_pos, tr.waypoints[0].R))
        steps.append(PlanStep(kind="move", object_id=op.obj.id, waypoints=mv, gripper_width=op.gm.open_width,
                              max_speed=cfg.speed_move, note=f"route={tr.label}"))
        Rg = gripper_rot(op.grasp_yaw)
        steps.append(PlanStep(kind="approach", object_id=op.obj.id, waypoints=[_pose(op.grasp_tcp, Rg)],
                              gripper_width=op.gm.open_width, max_speed=cfg.speed_approach))
        steps.append(PlanStep(kind="grasp", object_id=op.obj.id, waypoints=[_pose(op.grasp_tcp, Rg)],
                              gripper_width=op.gm.close_width, max_speed=cfg.speed_approach,
                              note=f"top grasp, object width {op.gm.width:.3f} m"))
        c = op.transport[choices[op.obj.id]]
        lift, mid, place = _split_transport(c.waypoints)
        steps.append(PlanStep(kind="lift", object_id=op.obj.id, waypoints=[_pose(w.pos, w.R) for w in lift],
                              max_speed=cfg.speed_lift))
        steps.append(PlanStep(kind="transport", object_id=op.obj.id,
                              waypoints=[_pose(w.pos, w.R) for w in mid], max_speed=cfg.speed_transport,
                              note=f"route={c.label}; mode={c.mode}; max_tilt_deg={c.max_tilt_deg:.0f}"))
        steps.append(PlanStep(kind="place", object_id=op.obj.id, waypoints=[_pose(w.pos, w.R) for w in place],
                              max_speed=cfg.speed_place))
        Rp = gripper_rot(op.placement.gripper_yaw)
        steps.append(PlanStep(kind="release", object_id=op.obj.id, waypoints=[_pose(op.placement.tcp_pos, Rp)],
                              gripper_width=op.gm.open_width, max_speed=cfg.speed_place))
        top = op.placement.tcp_pos + np.array([0.0, 0.0, cfg.retreat_height])
        steps.append(PlanStep(kind="retreat", object_id=op.obj.id, waypoints=[_pose(top, Rp)],
                              gripper_width=op.gm.open_width, max_speed=cfg.speed_retreat))
        summary.append(f"{op.obj.id}: {c.label}")
    return PlanResult(status="ok", reason="; ".join(summary) + (" | " + "; ".join(core.notes) if core.notes else ""),
                      steps=steps, planning_time_s=round(time.perf_counter() - core.t0, 4), **base)


# --------------------------------------------------------------- public ---


def plan(spec: TaskSpec, scene: SceneState, *, prefer: Optional[str] = None,
         config: Optional[PlannerConfig] = None) -> PlanResult:
    """Plan the whole kit. ``prefer`` optionally selects, among feasible
    candidates, routes whose label contains the given text (e.g. "around",
    "through_slot", "pitched") or whose mode equals it; prefix it with an
    object id ("cup_0:around") to apply it to one object only. Without a
    feasible matching route the cheapest feasible route is used."""
    cfg = config or DEFAULT_CONFIG
    t0 = time.perf_counter()
    try:
        core = solve(spec, scene, cfg)
        return assemble(core, default_choices(core, prefer))
    except Exception as e:  # report, never hide
        return PlanResult(status="error", reason=f"{type(e).__name__}: {e}", spec_version=spec.version,
                          scene_id=scene.scene_id, planner=PLANNER_NAME,
                          planning_time_s=round(time.perf_counter() - t0, 4))


def plan_core(spec: TaskSpec, scene: SceneState, config: Optional[PlannerConfig] = None) -> Core:
    return solve(spec, scene, config or DEFAULT_CONFIG)


def _valid_order(order: list[str], spec: TaskSpec, g: Grounding) -> bool:
    pos = {o: i for i, o in enumerate(order)}
    for oc in spec.ordering:
        if oc.status != Status.confirmed:
            continue
        before, after = g.ordering_objects.get(oc.id, ([], []))
        for a in before:
            for b in after:
                if a in pos and b in pos and pos[a] > pos[b]:
                    return False
    return True


def _primary_blocker(reason: Optional[str]) -> str:
    """The first obstacle named in a rejection reason ("collides with X" or
    "(blocked by X, ...)"), or ""."""
    if not reason:
        return ""
    for key in ("collides with ", "blocked by "):
        if key in reason:
            rest = reason.split(key, 1)[1]
            return rest.replace(")", "").split(",")[0].strip()
    return ""


def solve(spec: TaskSpec, scene: SceneState, cfg: PlannerConfig) -> Core:
    """Plan with the heuristic object order; if an object fails and its
    rejected routes name another object that has not been moved yet, retry
    the other admissible orders (structured routes only) and keep the first
    order that plans every object. Deterministic."""
    import itertools
    core = build_core(spec, scene, cfg)
    if core.status != "ok":
        return core
    fails = [op for op in core.objects if op.failure]
    if not fails or any(op.infeasibility for op in fails):
        return core
    order = core.order
    blocked_by_unmoved = False
    for op in fails:
        later = order[order.index(op.obj.id) + 1:] if op.obj.id in order else []
        if any(_primary_blocker(c.reason) in later for c in op.transport + op.transit if not c.feasible):
            blocked_by_unmoved = True
    if not blocked_by_unmoved:
        return core
    for perm in itertools.permutations(order):
        perm = list(perm)
        if perm == order or not _valid_order(perm, spec, core.grounding):
            continue
        alt = build_core(spec, scene, cfg, order_override=perm, allow_rrt=False)
        if alt.status == "ok" and not any(op.failure for op in alt.objects):
            alt.notes.append(f"object order changed from {order} because an unmoved object blocked a route")
            alt.t0 = core.t0
            return alt
    return core
