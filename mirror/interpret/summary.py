"""Deterministic features of a demonstration trace.

Nothing here calls a model. The summary is what the interpreter (model or
offline rules) reasons over, and every number in it can be traced back to the
recorded frames and events.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from mirror.contracts import DemoTrace, ObstacleSpec, Pose, SceneState, TraySpec
from mirror.geometry import DEFAULT_UPRIGHT_TOL_DEG
from mirror.interpret.geom import point_box_distance, tilt_deg, to_local
from mirror.interpret.types import DemoSummary, ObjectSummary, Regularity

UPRIGHT_TOL_DEG = DEFAULT_UPRIGHT_TOL_DEG
_MOVE_EPS_M = 0.02          # an object displaced less than this did not move
_MOTION_SPEED = 0.03        # m/s, used only when the trace has no grasp events
_MOTION_NEAR_M = 0.10       # gripper within this distance counts as holding it


def _r(x: float, nd: int = 3) -> float:
    return float(round(float(x), nd))


def _pose(p: Pose) -> Pose:
    return Pose(pos=tuple(_r(c) for c in p.pos), quat=tuple(_r(c, 4) for c in p.quat))


def in_tray(pos, tray: TraySpec, margin: float = 0.0) -> bool:
    local = to_local(np.asarray(pos), tray.pose.pos, tray.pose.quat)
    hx, hy = tray.inner_half
    return (abs(local[0]) <= hx + margin and abs(local[1]) <= hy + margin
            and -0.01 <= local[2] <= tray.wall_height + 0.20)


def obstacle_box(ob: ObstacleSpec) -> tuple[np.ndarray, tuple, tuple]:
    """(centre, quat, half extents) of the obstacle's solid envelope. Wall slots
    are ignored here: distances are to the full wall slab."""
    if ob.kind == "wall":
        h = float(ob.height or 0.0)
        c = np.array([ob.pose.pos[0], ob.pose.pos[1], h / 2.0])
        half = (float(ob.thickness or 0.0) / 2.0, float(ob.length or 0.0) / 2.0, h / 2.0)
        return c, ob.pose.quat, half
    return np.asarray(ob.pose.pos, dtype=float), ob.pose.quat, tuple(ob.half or (0, 0, 0))


def _carry_intervals_from_events(trace: DemoTrace, oid: str) -> list[tuple[float, float]]:
    evs = sorted((e for e in trace.events if e.object_id == oid and e.type in ("grasp", "release")),
                 key=lambda e: e.t)
    out, start = [], None
    for e in evs:
        if e.type == "grasp" and start is None:
            start = e.t
        elif e.type == "release" and start is not None:
            out.append((start, e.t))
            start = None
    if start is not None and trace.frames:
        out.append((start, trace.frames[-1].t))
    return out


def _carry_intervals_from_motion(trace: DemoTrace, oid: str) -> list[tuple[float, float]]:
    fr = trace.frames
    moving = []
    for a, b in zip(fr, fr[1:]):
        if oid not in a.objects or oid not in b.objects:
            moving.append(False)
            continue
        pa, pb = np.asarray(a.objects[oid].pos), np.asarray(b.objects[oid].pos)
        dt = max(b.t - a.t, 1e-9)
        near = np.linalg.norm(np.asarray(b.gripper.pos) - pb) < _MOTION_NEAR_M
        moving.append(bool(np.linalg.norm(pb - pa) / dt > _MOTION_SPEED and near))
    out, start, last = [], None, None
    gap = 0.25
    for i, m in enumerate(moving):
        t = fr[i].t
        if m:
            if start is None:
                start = t
            elif last is not None and t - last > gap:
                out.append((start, last))
                start = t
            last = fr[i + 1].t
    if start is not None:
        out.append((start, last))
    return out


def _wall_crossings(points: list[np.ndarray], ob: ObstacleSpec) -> list[str]:
    out = []
    if len(points) < 2:
        return out
    loc = [to_local(p, ob.pose.pos, ob.pose.quat) for p in points]
    half_len = float(ob.length or 0.0) / 2.0
    for a, b in zip(loc, loc[1:]):
        if a[0] == 0.0 or np.sign(a[0]) == np.sign(b[0]):
            continue
        s = a[0] / (a[0] - b[0])
        y = a[1] + s * (b[1] - a[1])
        z = a[2] + s * (b[2] - a[2])
        if z > float(ob.height or 0.0):
            label = "over"
        elif y > half_len:
            label = "around_+y"
        elif y < -half_len:
            label = "around_-y"
        elif any(sl.y_min <= y <= sl.y_max and sl.z_min <= z <= sl.z_max for sl in ob.slots):
            label = "through_slot"
        else:
            label = "through_wall"
        out.append(f"{ob.id}:{label}")
    return out


def summarize_demo(trace: DemoTrace) -> DemoSummary:
    scene: SceneState = trace.scene
    frames = trace.frames
    if not frames:
        raise ValueError("demonstration has no frames")
    has_events = any(e.type == "grasp" for e in trace.events)
    times = np.array([f.t for f in frames])
    notes: list[str] = []
    if not has_events:
        notes.append("No grasp events in the trace; carry intervals were inferred from "
                     "object motion near the gripper.")
    if any(ob.kind == "wall" and ob.slots for ob in scene.obstacles):
        notes.append("Obstacle distances are measured to the full wall slab; slots are ignored.")

    summaries: list[ObjectSummary] = []
    first_grasp: dict[str, float] = {}
    for obj in scene.objects:
        oid = obj.id
        poses = [(f.t, f.objects[oid]) for f in frames if oid in f.objects]
        if not poses:
            notes.append(f"{oid} never appears in the trace frames.")
            start = end = obj.pose
        else:
            start, end = poses[0][1], poses[-1][1]
        intervals = (_carry_intervals_from_events(trace, oid) if has_events
                     else _carry_intervals_from_motion(trace, oid))
        disp = float(np.linalg.norm(np.asarray(end.pos) - np.asarray(start.pos)))
        moved = disp > _MOVE_EPS_M or bool(intervals)
        carry = [(t, p) for (t, p) in poses if any(a <= t <= b for a, b in intervals)]
        tilts = [tilt_deg(p.quat) for _, p in carry]
        pts = [np.asarray(p.pos, dtype=float) for _, p in carry]
        path = float(sum(np.linalg.norm(b - a) for a, b in zip(pts, pts[1:])))
        min_d, nearest = None, None
        crossings: list[str] = []
        for ob in scene.obstacles:
            c, q, half = obstacle_box(ob)
            if pts:
                d = min(point_box_distance(p, c, q, half) for p in pts)
                if min_d is None or d < min_d:
                    min_d, nearest = d, ob.id
            if ob.kind == "wall":
                crossings += _wall_crossings(pts, ob)
        tray_id: Optional[str] = None
        for tr in scene.trays:
            if in_tray(end.pos, tr):
                tray_id = tr.id
                break
        if intervals:
            first_grasp[oid] = intervals[0][0]
        grasps = [_r(a, 3) for a, _ in intervals]
        releases = [_r(b, 3) for _, b in intervals]
        summaries.append(ObjectSummary(
            object_id=oid, kind=obj.kind, moved=moved,
            grasp_times=grasps, release_times=releases,
            start_pose=_pose(start), end_pose=_pose(end),
            start_tilt_deg=_r(tilt_deg(start.quat), 1), end_tilt_deg=_r(tilt_deg(end.quat), 1),
            ended_in_tray=tray_id,
            carry_duration_s=_r(sum(b - a for a, b in intervals), 3),
            carry_max_tilt_deg=_r(max(tilts), 1) if tilts else None,
            carry_mean_tilt_deg=_r(float(np.mean(tilts)), 1) if tilts else None,
            carry_path_length_m=_r(path),
            carry_max_height_m=_r(max(p[2] for p in pts)) if pts else None,
            min_obstacle_distance_m=_r(min_d) if min_d is not None else None,
            nearest_obstacle=nearest,
            wall_crossings=crossings,
        ))

    order = [oid for oid, _ in sorted(first_grasp.items(), key=lambda kv: (kv[1], kv[0]))]
    kinds = [o.kind for o in scene.objects]
    if len(set(kinds)) != len(kinds):
        notes.append("Several objects share a kind; kind-level rules apply to all of them.")
    summary = DemoSummary(
        demo_id=trace.demo_id, source=trace.source,
        duration_s=_r(float(times[-1] - times[0]), 3),
        event_source="events" if has_events else "motion",
        order=order, objects=summaries,
        trays=[t.id for t in scene.trays],
        obstacles=[{"id": ob.id, "kind": ob.kind, "slots": len(ob.slots)} for ob in scene.obstacles],
        regularities=[], notes=notes,
    )
    summary.regularities = derive_regularities(summary)
    return summary


def derive_regularities(s: DemoSummary) -> list[Regularity]:
    regs: list[Regularity] = []

    def add(**kw):
        regs.append(Regularity(id=f"R{len(regs) + 1}", **kw))

    by_id = {o.object_id: o for o in s.objects}
    moved = [by_id[i] for i in s.order] + [o for o in s.objects if o.moved and o.object_id not in s.order]
    for o in moved:
        if o.carry_max_tilt_deg is not None:
            if o.carry_max_tilt_deg <= UPRIGHT_TOL_DEG:
                add(kind="carry_upright", object_kind=o.kind, value=o.carry_max_tilt_deg,
                    statement=(f"The {o.kind} stayed within {o.carry_max_tilt_deg:g} degrees of upright "
                               f"for the whole time it was carried (mean {o.carry_mean_tilt_deg:g})."))
            else:
                add(kind="carry_tilted", object_kind=o.kind, value=o.carry_max_tilt_deg,
                    statement=f"The {o.kind} was tilted up to {o.carry_max_tilt_deg:g} degrees while carried.")
        if o.end_tilt_deg <= UPRIGHT_TOL_DEG:
            add(kind="final_upright", object_kind=o.kind, value=o.end_tilt_deg,
                statement=f"The {o.kind} ended upright ({o.end_tilt_deg:g} degrees of tilt).")
        if o.ended_in_tray:
            add(kind="inside", object_kind=o.kind,
                statement=f"The {o.kind} ended inside {o.ended_in_tray}.")
        for c in o.wall_crossings:
            wall, label = c.split(":", 1)
            add(kind="route", object_kind=o.kind,
                statement=f"The {o.kind} crossed {wall} by going {label.replace('_', ' ')}.")
        if o.min_obstacle_distance_m is not None and o.nearest_obstacle:
            add(kind="clearance", object_kind=o.kind, value=o.min_obstacle_distance_m,
                statement=(f"While carried, the {o.kind}'s centre stayed at least "
                           f"{o.min_obstacle_distance_m:g} m from {o.nearest_obstacle}."))
    if len(s.order) >= 2:
        names = ", ".join(by_id[i].kind for i in s.order)
        add(kind="order", statement=f"Objects were picked up in the order: {names}.")
    return regs
