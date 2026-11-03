"""Kinematic fixture session for developing the studio without the simulator.

Everything produced here is labelled ``fixture``: the motion is interpolated
key-framing, not physics. The studio shows a persistent banner when it runs on
fixtures, and ``scripts/export_static.py`` refuses to export them unless asked
explicitly. The fixture mirrors the public surface of ``mirror.pipeline.Session``
that the studio uses, so the studio code path is identical for both.
"""
from __future__ import annotations

import copy
import math
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..contracts import (Constraint, CorrectionRecord, DemoTrace, Goal,
                         ObjectBinding, ObjectSelector, ObjectSpec,
                         ObstacleSpec, OrderingConstraint, PlanResult, Pose,
                         Provenance, Rollout, RolloutFrame, RolloutReport,
                         RouteOption, SceneState, SlotSpec, SpecOp, Status,
                         TaskSpec, TraceEvent, TraceFrame, TraySpec,
                         Uncertainty, ViolationInterval, VisualBody,
                         VisualGeom, VisualScene, Workspace, ObjectOutcome)
from ..geometry import (BLOCK, CUP, DEFAULT_UPRIGHT_TOL_DEG, GRIPPER, MARKER,
                        TRAY, WORLD)
from ..pipeline import Session

FIXTURE_NOTE = "fixture: kinematic key-framed motion for interface development, not a physics simulation"

# ----------------------------------------------------------------- maths ---


def quat_mul(a, b):
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return (w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
            w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
            w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2)


def quat_axis(axis, angle):
    s = math.sin(angle / 2)
    return (math.cos(angle / 2), axis[0] * s, axis[1] * s, axis[2] * s)


def rotate(q, v):
    w, x, y, z = q
    vq = (0.0, v[0], v[1], v[2])
    r = quat_mul(quat_mul(q, vq), (w, -x, -y, -z))
    return (r[1], r[2], r[3])


def tilt_of(q) -> float:
    w, x, y, z = q
    zz = 1.0 - 2.0 * (x * x + y * y)
    return math.degrees(math.acos(max(-1.0, min(1.0, zz))))


def _r(v, n=4):
    return tuple(round(float(a), n) for a in v)


# ---------------------------------------------------------------- scenes ---


def _workspace() -> Workspace:
    return Workspace(x_range=WORLD.workspace_x, y_range=WORLD.workspace_y, z_range=WORLD.workspace_z)


def _objects(cup=(-0.26, -0.10), block=(-0.32, 0.10), marker=(-0.16, 0.16)) -> list[ObjectSpec]:
    return [
        ObjectSpec(id="cup_0", kind="cup", size={"outer_half": list(CUP.outer_half), "wall": CUP.wall},
                   mass=CUP.mass, pose=Pose(pos=(cup[0], cup[1], CUP.outer_half[2])), beads=CUP.beads,
                   bead_radius=CUP.bead_radius, rgba=(0.93, 0.93, 0.91, 1.0)),
        ObjectSpec(id="block_0", kind="block", size={"half": list(BLOCK.half)}, mass=BLOCK.mass,
                   pose=Pose(pos=(block[0], block[1], BLOCK.half[2])), rgba=(0.30, 0.45, 0.70, 1.0)),
        ObjectSpec(id="marker_0", kind="marker", size={"radius": MARKER.radius, "half_length": MARKER.half_length},
                   mass=MARKER.mass, pose=Pose(pos=(marker[0], marker[1], MARKER.half_length + MARKER.radius)),
                   rgba=(0.85, 0.62, 0.20, 1.0)),
    ]


def _wall(length=0.56, center_y=-0.12, slot=False, slot_y=(-0.10, 0.10)) -> ObstacleSpec:
    slots = [SlotSpec(y_min=slot_y[0], y_max=slot_y[1], z_min=WORLD.slot_z[0], z_max=WORLD.slot_z[1])] if slot else []
    return ObstacleSpec(id="wall_0", kind="wall", pose=Pose(pos=(0.0, center_y, 0.0)), length=length,
                        height=WORLD.wall_height, thickness=WORLD.wall_thickness, slots=slots)


def fixture_scene(name: str) -> SceneState:
    if name == "demo":
        return SceneState(scene_id="demo", split="demo", objects=_objects(),
                          trays=[TraySpec(id="tray_0", pose=Pose(pos=(0.27, 0.05, 0.004)), inner_half=TRAY.inner_half)],
                          obstacles=[_wall()], workspace=_workspace(), gripper_start=Pose(pos=(-0.10, -0.30, 0.30)),
                          notes=FIXTURE_NOTE)
    if name == "shortcut":
        return SceneState(scene_id="shortcut", split="dev", objects=_objects(),
                          trays=[TraySpec(id="tray_0", pose=Pose(pos=(0.25, -0.12, 0.004)), inner_half=TRAY.inner_half)],
                          obstacles=[_wall(slot=True, slot_y=(-0.22, -0.02))], workspace=_workspace(),
                          gripper_start=Pose(pos=(-0.10, -0.30, 0.30)), notes=FIXTURE_NOTE)
    if name == "blocked":
        return SceneState(scene_id="blocked", split="blocked", objects=_objects(),
                          trays=[TraySpec(id="tray_0", pose=Pose(pos=(0.25, -0.05, 0.004)), inner_half=TRAY.inner_half)],
                          obstacles=[_wall(length=0.80, center_y=0.0, slot=True, slot_y=(-0.15, 0.05))],
                          workspace=_workspace(), gripper_start=Pose(pos=(-0.10, -0.30, 0.30)), notes=FIXTURE_NOTE)
    raise KeyError(name)


def fixture_layout(seed: int) -> SceneState:
    rng = np.random.default_rng(seed)
    cup = (rng.uniform(-0.36, -0.14), rng.uniform(-0.28, 0.0))
    block = (rng.uniform(-0.38, -0.20), rng.uniform(0.04, 0.20))
    marker = (rng.uniform(-0.18, -0.08), rng.uniform(0.10, 0.28))
    side = 1 if rng.random() < 0.5 else -1
    length = rng.uniform(0.50, 0.62)
    center = -side * (0.40 - length / 2) if True else 0.0
    slot = rng.random() < 0.75
    sy = rng.uniform(-0.20, 0.0) if side > 0 else rng.uniform(0.0, 0.20)
    tray = (rng.uniform(0.22, 0.30), rng.uniform(-0.22, 0.22))
    sc = SceneState(scene_id=f"eval_{seed}", seed=seed, split="eval", objects=_objects(cup, block, marker),
                    trays=[TraySpec(id="tray_0", pose=Pose(pos=(tray[0], tray[1], 0.004)), inner_half=TRAY.inner_half)],
                    obstacles=[_wall(length=length, center_y=center, slot=slot, slot_y=(sy - 0.10, sy + 0.10))],
                    workspace=_workspace(), gripper_start=Pose(pos=(-0.10, -0.30, 0.30)), notes=FIXTURE_NOTE)
    return sc


# ---------------------------------------------------------------- visual ---


def visual_scene(scene: SceneState) -> VisualScene:
    bodies: list[VisualBody] = []
    t = WORLD.table_half
    bodies.append(VisualBody(name="table", static=True, geoms=[VisualGeom(
        type="box", size=t, pos=(0, 0, -t[2]), quat=(1, 0, 0, 0), rgba=(0.82, 0.82, 0.80, 1), name="table")]))
    for tr in scene.trays:
        x, y, z = tr.pose.pos
        hx, hy = tr.inner_half
        wt, wh = tr.wall_thickness, tr.wall_height
        g = [VisualGeom(type="box", size=(hx + wt, hy + wt, 0.002), pos=(x, y, z - 0.002), quat=(1, 0, 0, 0),
                        rgba=(0.55, 0.58, 0.60, 1), name=f"{tr.id}_floor")]
        for sx in (-1, 1):
            g.append(VisualGeom(type="box", size=(wt / 2, hy + wt, wh / 2), pos=(x + sx * (hx + wt / 2), y, z + wh / 2),
                                quat=(1, 0, 0, 0), rgba=(0.55, 0.58, 0.60, 1)))
        for sy in (-1, 1):
            g.append(VisualGeom(type="box", size=(hx, wt / 2, wh / 2), pos=(x, y + sy * (hy + wt / 2), z + wh / 2),
                                quat=(1, 0, 0, 0), rgba=(0.55, 0.58, 0.60, 1)))
        bodies.append(VisualBody(name=tr.id, static=True, geoms=g))
    for ob in scene.obstacles:
        if ob.kind == "box":
            bodies.append(VisualBody(name=ob.id, static=True, geoms=[VisualGeom(
                type="box", size=ob.half, pos=ob.pose.pos, quat=ob.pose.quat, rgba=(0.62, 0.60, 0.58, 1))]))
            continue
        x, y, _ = ob.pose.pos
        L, H, T = ob.length, ob.height, ob.thickness
        y0, y1 = y - L / 2, y + L / 2
        geoms = []
        col = (0.70, 0.69, 0.67, 0.92)
        cuts = sorted(ob.slots, key=lambda s: s.y_min)
        cursor = y0
        for s in cuts:
            a, b = y + s.y_min if False else s.y_min, s.y_max
            if a > cursor:
                geoms.append(VisualGeom(type="box", size=(T / 2, (a - cursor) / 2, H / 2), pos=(x, (a + cursor) / 2, H / 2), quat=(1, 0, 0, 0), rgba=col))
            geoms.append(VisualGeom(type="box", size=(T / 2, (b - a) / 2, s.z_min / 2), pos=(x, (a + b) / 2, s.z_min / 2), quat=(1, 0, 0, 0), rgba=col))
            geoms.append(VisualGeom(type="box", size=(T / 2, (b - a) / 2, (H - s.z_max) / 2), pos=(x, (a + b) / 2, (H + s.z_max) / 2), quat=(1, 0, 0, 0), rgba=col))
            cursor = b
        if cursor < y1:
            geoms.append(VisualGeom(type="box", size=(T / 2, (y1 - cursor) / 2, H / 2), pos=(x, (y1 + cursor) / 2, H / 2), quat=(1, 0, 0, 0), rgba=col))
        bodies.append(VisualBody(name=ob.id, static=True, geoms=geoms))
    for o in scene.objects:
        if o.kind == "cup":
            hx, hy, hz = o.size["outer_half"]
            w = o.size["wall"]
            c = o.rgba
            g = [VisualGeom(type="box", size=(hx, hy, w / 2), pos=(0, 0, -hz + w / 2), quat=(1, 0, 0, 0), rgba=c)]
            for sx in (-1, 1):
                g.append(VisualGeom(type="box", size=(w / 2, hy, hz), pos=(sx * (hx - w / 2), 0, 0), quat=(1, 0, 0, 0), rgba=c))
            for sy in (-1, 1):
                g.append(VisualGeom(type="box", size=(hx - w, w / 2, hz), pos=(0, sy * (hy - w / 2), 0), quat=(1, 0, 0, 0), rgba=c))
            bodies.append(VisualBody(name=o.id, static=False, geoms=g))
            for i in range(o.beads):
                bodies.append(VisualBody(name=f"{o.id}_bead_{i}", static=False, geoms=[VisualGeom(
                    type="sphere", size=(o.bead_radius,), pos=(0, 0, 0), quat=(1, 0, 0, 0), rgba=(0.80, 0.25, 0.20, 1))]))
        elif o.kind == "block":
            bodies.append(VisualBody(name=o.id, static=False, geoms=[VisualGeom(
                type="box", size=tuple(o.size["half"]), pos=(0, 0, 0), quat=(1, 0, 0, 0), rgba=o.rgba)]))
        else:
            bodies.append(VisualBody(name=o.id, static=False, geoms=[VisualGeom(
                type="cylinder", size=(o.size["radius"], o.size["half_length"]), pos=(0, 0, 0), quat=(1, 0, 0, 0), rgba=o.rgba)]))
    gc = (0.20, 0.21, 0.23, 1)
    bodies.append(VisualBody(name="gripper", static=False, geoms=[VisualGeom(
        type="box", size=GRIPPER.palm_half, pos=(0, 0, GRIPPER.palm_center_z), quat=(1, 0, 0, 0), rgba=gc)]))
    for side in ("left", "right"):
        bodies.append(VisualBody(name=f"gripper_finger_{side}", static=False, geoms=[VisualGeom(
            type="box", size=GRIPPER.finger_half, pos=(0, 0, 0), quat=(1, 0, 0, 0), rgba=gc)]))
    return VisualScene(bodies=bodies)


# ------------------------------------------------------------ kinematics ---

_SPEED = 0.30
_HZ = 30.0


@dataclass
class _Key:
    pos: tuple
    pitch: float = 0.0
    grip: Optional[str] = None   # "close" / "open" applied at this key
    phase: str = ""
    dwell: float = 0.0


def _route_keys(obj: ObjectSpec, tray_xy, slot_center: Optional[tuple], around_y: float, tilt: bool) -> list[_Key]:
    x, y, z = obj.pose.pos
    top = z + (obj.size.get("outer_half", [0, 0, 0])[2] if obj.kind == "cup" else
               obj.size.get("half_length", 0) if obj.kind == "marker" else obj.size.get("half", [0, 0, 0])[2])
    grasp_z = top - 0.025
    hold = z - grasp_z  # object centre relative to TCP along world z when upright
    carry = 0.30
    tx, ty = tray_xy
    keys = [_Key((x, y, carry), phase=f"approach:{obj.id}"),
            _Key((x, y, grasp_z), phase=f"approach:{obj.id}"),
            _Key((x, y, grasp_z), grip="close", phase=f"grasp:{obj.id}", dwell=0.4),
            _Key((x, y, carry), phase=f"lift:{obj.id}")]
    if tilt and slot_center is not None:
        sy, sz = slot_center
        keys += [_Key((-0.10, sy, sz), pitch=0.0, phase=f"transport:{obj.id}"),
                 _Key((-0.07, sy, sz), pitch=math.pi / 2, phase=f"transport:{obj.id}"),
                 _Key((0.07, sy, sz), pitch=math.pi / 2, phase=f"transport:{obj.id}"),
                 _Key((0.12, sy, sz + 0.05), pitch=0.0, phase=f"transport:{obj.id}")]
    else:
        keys += [_Key((-0.08, around_y, carry), phase=f"transport:{obj.id}"),
                 _Key((0.08, around_y, carry), phase=f"transport:{obj.id}")]
    keys += [_Key((tx, ty, carry), phase=f"transport:{obj.id}"),
             _Key((tx, ty, 0.004 + (z) - hold * 0 + (grasp_z - z) + 0.003), phase=f"place:{obj.id}"),
             _Key((tx, ty, 0.004 + grasp_z + 0.003), grip="open", phase=f"release:{obj.id}", dwell=0.4),
             _Key((tx, ty, carry), phase=f"retreat:{obj.id}")]
    return keys


def _play(scene: SceneState, plan_keys: list[tuple[str, list[_Key]]], start) -> tuple[list[RolloutFrame], dict]:
    """Interpolate keys into frames. Held objects ride with the gripper; cup
    beads leave a cup once it tilts past 50 degrees and fall to the table
    (kinematic stand-in only)."""
    objs = {o.id: o for o in scene.objects}
    pose = {o.id: (tuple(o.pose.pos), tuple(o.pose.quat)) for o in scene.objects}
    beads: dict[str, list] = {}
    rng = np.random.default_rng(7)
    for o in scene.objects:
        if o.kind == "cup":
            hx, hy, hz = o.size["outer_half"]
            pts = []
            for i in range(o.beads):
                pts.append({"local": (rng.uniform(-hx + 0.012, hx - 0.012), rng.uniform(-hy + 0.012, hy - 0.012),
                                      -hz + 0.012 + (i // 4) * 0.014), "free": None})
            beads[o.id] = pts
    frames: list[RolloutFrame] = []
    t = 0.0
    g_pos = tuple(start)
    g_pitch = 0.0
    width = GRIPPER.max_opening
    held: Optional[str] = None
    offset = None
    stats = {"max_tilt": {k: 0.0 for k in objs}, "spilled": {k: 0 for k in beads},
             "tilt_series": [], "violations": []}

    def emit(phase):
        nonlocal t
        q = quat_axis((0, 1, 0), g_pitch)
        bodies = {"gripper": Pose(pos=_r(g_pos), quat=_r(q))}
        for side, s in (("left", 1), ("right", -1)):
            fp = rotate(q, (0, s * (width / 2 + GRIPPER.finger_half[1]), GRIPPER.finger_center_z))
            bodies[f"gripper_finger_{side}"] = Pose(pos=_r((g_pos[0] + fp[0], g_pos[1] + fp[1], g_pos[2] + fp[2])), quat=_r(q))
        if held:
            p = rotate(q, offset)
            pose[held] = ((g_pos[0] + p[0], g_pos[1] + p[1], g_pos[2] + p[2]), q)
            stats["max_tilt"][held] = max(stats["max_tilt"][held], tilt_of(q))
        for oid, (p, qq) in pose.items():
            bodies[oid] = Pose(pos=_r(p), quat=_r(qq))
        for cid, pts in beads.items():
            cp, cq = pose[cid]
            tl = tilt_of(cq)
            for i, b in enumerate(pts):
                if b["free"] is None and tl > 50:
                    w = rotate(cq, b["local"])
                    b["free"] = [cp[0] + w[0], cp[1] + w[1], cp[2] + w[2], 0.0, 0.25 * (i % 3 - 1) * 0.3, 0.0]
                    stats["spilled"][cid] += 1
                if b["free"] is not None:
                    f = b["free"]
                    dt = 1 / _HZ
                    f[5] -= 9.81 * dt
                    f[0] += f[3] * dt
                    f[1] += f[4] * dt
                    f[2] = max(objs[cid].bead_radius, f[2] + f[5] * dt)
                    if f[2] <= objs[cid].bead_radius:
                        f[5] = 0.0
                        f[4] *= 0.8
                    bp = (f[0], f[1], f[2])
                else:
                    w = rotate(cq, b["local"])
                    bp = (cp[0] + w[0], cp[1] + w[1], cp[2] + w[2])
                bodies[f"{cid}_bead_{i}"] = Pose(pos=_r(bp), quat=(1.0, 0.0, 0.0, 0.0))
        frames.append(RolloutFrame(t=round(t, 4), bodies=bodies, gripper_width=round(width, 4), phase=phase))
        t += 1 / _HZ

    for _, keys in plan_keys:
        for k in keys:
            d = math.dist(g_pos, k.pos) + 0.08 * abs(k.pitch - g_pitch)
            n = max(1, int(d / _SPEED * _HZ))
            p0, a0 = g_pos, g_pitch
            for i in range(1, n + 1):
                s = i / n
                s = s * s * (3 - 2 * s)
                g_pos = tuple(p0[j] + (k.pos[j] - p0[j]) * s for j in range(3))
                g_pitch = a0 + (k.pitch - a0) * s
                emit(k.phase)
            if k.grip == "close":
                oid = k.phase.split(":")[1]
                target_w = 2 * (objs[oid].size.get("outer_half", [0.02])[0] if objs[oid].kind == "cup" else
                                objs[oid].size.get("radius", 0.0) if objs[oid].kind == "marker" else objs[oid].size["half"][0])
                for i in range(int(k.dwell * _HZ)):
                    width = max(target_w, width - 0.006)
                    emit(k.phase)
                held = oid
                p, _q = pose[oid]
                offset = (p[0] - g_pos[0], p[1] - g_pos[1], p[2] - g_pos[2])
            elif k.grip == "open":
                for i in range(int(k.dwell * _HZ)):
                    width = min(GRIPPER.max_opening, width + 0.006)
                    if i == 2 and held:
                        p, q = pose[held]
                        pose[held] = (p, (1.0, 0.0, 0.0, 0.0))
                        held = None
                    emit(k.phase)
    for _ in range(int(0.6 * _HZ)):
        emit("settle")
    return frames, stats


# ---------------------------------------------------------------- spec ---


def _prov(source, evidence="", v=1):
    return Provenance(source=source, evidence=evidence, version_introduced=v, model="offline-fixture")


def fixture_spec(demo_id: str) -> TaskSpec:
    return TaskSpec(
        spec_id="kit-pack", version=1, demo_id=demo_id, interpreter="offline-fixture",
        summary="Pack the cup, the block and the marker into the tray.",
        bindings=[ObjectBinding(role="container", kind="cup"), ObjectBinding(role="part", kind="block"),
                  ObjectBinding(role="tool", kind="marker")],
        goals=[Goal(id=f"g_{k}_in_tray", type="inside", object=ObjectSelector(kind=k),
                    provenance=_prov("demo_inference", f"{k} ended inside tray_0 at t≈{t}s"))
               for k, t in (("cup", 14.2), ("block", 26.0), ("marker", 37.5))],
        ordering=[OrderingConstraint(id="o_cup_first", before=ObjectSelector(kind="cup"), after=ObjectSelector(kind="block"),
                                     status=Status.proposed, provenance=_prov("demo_inference", "cup was moved first"))],
        constraints=[
            Constraint(id="c_upright_final", type="upright", applies_to=ObjectSelector(kind="cup"), phase="final",
                       params={"max_tilt_deg": DEFAULT_UPRIGHT_TOL_DEG}, status=Status.confirmed,
                       provenance=_prov("demo_inference", "cup final tilt 1.2°"),
                       description="The cup ends upright in the tray."),
            Constraint(id="c_upright_transport", type="upright", applies_to=ObjectSelector(kind="cup"), phase="transport",
                       params={"max_tilt_deg": DEFAULT_UPRIGHT_TOL_DEG}, status=Status.proposed,
                       provenance=_prov("demo_inference", "cup max tilt during carry 3.1°"),
                       description="The cup stays upright while it is carried."),
        ],
        uncertainties=[
            Uncertainty(id="u_transport", question="The cup stayed upright for the whole carry. Is that required, or incidental?",
                        options=["required", "incidental"], related_constraint="c_upright_transport"),
            Uncertainty(id="u_order", question="The cup was packed first. Must it always go first?",
                        options=["required", "any order"], related_constraint=None),
        ],
    )


def _apply(spec: TaskSpec, ops: list[SpecOp], text: str, kind: str) -> tuple[TaskSpec, CorrectionRecord]:
    new = spec.model_copy(deep=True)
    new.parent_version = spec.version
    new.version = spec.version + 1
    for op in ops:
        if op.op == "set_status":
            for c in list(new.constraints) + list(new.ordering) + list(new.goals):
                if c.id == op.target_id:
                    c.status = Status(op.value["status"])
        elif op.op == "add_constraint":
            new.constraints.append(Constraint.model_validate(op.value))
        elif op.op == "modify_constraint":
            for i, c in enumerate(new.constraints):
                if c.id == op.target_id:
                    d = c.model_dump()
                    d.update(op.value)
                    new.constraints[i] = Constraint.model_validate(d)
        elif op.op == "remove_constraint":
            new.constraints = [c for c in new.constraints if c.id != op.target_id]
        elif op.op == "resolve_uncertainty":
            for u in new.uncertainties:
                if u.id == op.target_id:
                    u.status = "resolved"
                    u.resolution = (op.value or {}).get("resolution", "")
    rec = CorrectionRecord(version=new.version, text=text, kind=kind, diff=ops, model="offline-fixture")
    new.history.append(rec)
    return new, rec


def fixture_correction_ops(spec: TaskSpec, text: str) -> list[SpecOp]:
    low = text.lower()
    if "upright" not in low and "tilt" not in low:
        return []
    ops = []
    existing = next((c for c in spec.constraints if c.id == "c_upright_transport"), None)
    if existing:
        ops.append(SpecOp(op="modify_constraint", target_id="c_upright_transport",
                          value={"status": "confirmed", "provenance": {"source": "user_correction", "evidence": text,
                                                                        "version_introduced": spec.version + 1, "model": "offline-fixture"}},
                          rationale="The correction says the cup must stay upright while carried, not only at the end."))
    ops.append(SpecOp(op="resolve_uncertainty", target_id="u_transport", value={"resolution": "required"},
                      rationale="Answered by the correction."))
    return ops


# -------------------------------------------------------------- session ---


def FixtureEntry(rollout, log, plan, scene, spec_version, stats=None):  # noqa: N802 - kept for old callers
    """Backward-compatible constructor: fixture rollouts are pipeline RolloutEntry objects."""
    from ..pipeline import RolloutEntry
    e = RolloutEntry(rollout, log, plan, scene, spec_version)
    e.stats = stats or {}
    return e


class FixtureSession(Session):
    """Duck-typed stand-in for mirror.pipeline.Session (subset used by the studio).

    Shares the pipeline's lineage (generations, immutable rollout ids, archive);
    only planning, execution and checking are kinematic stand-ins."""

    is_fixture = True

    def __init__(self, session_id: Optional[str] = None, client: Any = None, **kw):
        super().__init__(session_id=session_id or "fx" + uuid.uuid4().hex[:8], client=client, **kw)
        self._demo_frames: list = []

    # demonstration ------------------------------------------------------
    def record_demo(self, scene: Optional[SceneState] = None) -> DemoTrace:
        scene = scene or fixture_scene("demo")
        keys, *_ = self._keys(scene, tilt_cup=False)
        frames, _ = _play(scene, keys, scene.gripper_start.pos)
        trace_frames, events = [], []
        held_prev = None
        for f in frames[::3]:
            trace_frames.append(TraceFrame(t=f.t, gripper=f.bodies["gripper"], gripper_width=f.gripper_width,
                                           objects={o.id: f.bodies[o.id] for o in scene.objects}))
        for f in frames:
            ph = f.phase.split(":")
            if ph[0] in ("grasp", "release") and (held_prev != f.phase):
                if not events or events[-1].object_id != ph[1] or events[-1].type != ph[0]:
                    events.append(TraceEvent(t=f.t, type=ph[0], object_id=ph[1]))
            held_prev = f.phase
        self.set_demo(DemoTrace(demo_id="demo-fixture", scene=scene, source="teleop_scripted", source_note=FIXTURE_NOTE,
                                sample_hz=_HZ / 3, frames=trace_frames, events=events), reason="demo_recorded")
        self.scene = scene
        self._demo_frames = frames
        self._event("demo_recorded", demo_id=self.demo.demo_id, source=self.demo.source)
        return self.demo

    def demo_rollout(self) -> Rollout:
        if self.demo is None:
            self.record_demo()
        frames = self._demo_frames
        if not frames:   # a restored or replaced trace: play the trace itself
            frames = [RolloutFrame(t=f.t, bodies={**f.objects, "gripper": f.gripper}, gripper_width=f.gripper_width)
                      for f in self.demo.frames]
        rep = RolloutReport(rollout_id="demo", scene_id=self.demo.scene.scene_id, spec_id="", spec_version=0,
                            condition="demonstration", status="success", completion=True, checker="none",
                            notes=[FIXTURE_NOTE])
        return Rollout(report=rep, visual=visual_scene(self.demo.scene), frame_hz=_HZ, frames=frames)

    # interpretation -----------------------------------------------------
    def interpret(self) -> TaskSpec:
        if self.specs or self.rollouts:
            self._start_generation("reinterpreted", carry_demo=True)
        self.specs = [fixture_spec(self.demo.demo_id if self.demo else "demo-fixture")]
        self._event("spec_proposed", version=1, interpreter="offline-fixture")
        return self.spec

    def apply_ops(self, ops, text, kind="confirmation"):
        new, rec = _apply(self.spec, ops, text, kind)
        self.specs.append(new)
        self._event("spec_updated", version=new.version, kind=kind, text=text)
        return new

    def preview_correction(self, text, focus_kind=None):
        ops = fixture_correction_ops(self.spec, text)
        if not ops:
            rec = CorrectionRecord(version=self.spec.version, text=text, kind="correction", diff=[], model="offline-fixture")
            return self.spec, rec
        return _apply(self.spec, ops, text, "correction")

    def correct(self, text, focus_kind=None):
        new, rec = self.preview_correction(text)
        if new is self.spec:
            return new, rec
        self.specs.append(new)
        self._event("correction", version=new.version, text=text)
        return new, rec

    def clarify(self):
        spec, scene = self.spec, self.scene
        a = self._run(scene, spec, condition="candidate A", tilt_cup=False, force=True, kind="c")
        b = self._run(scene, spec, condition="candidate B", tilt_cup=True, force=True, kind="c")
        self.clarification = {
            "id": "clar_1", "question": "Would both of these still count as doing the task correctly?",
            "spec_version": spec.version,
            "differences": [{"object_id": "cup_0", "metric": "max_transport_tilt_deg",
                             "values": [round(a.stats["max_tilt"]["cup_0"], 1), round(b.stats["max_tilt"]["cup_0"], 1)]}],
            "candidates": [
                {"label": "Upright, around the wall", "rollout_id": a.rollout.report.rollout_id,
                 "route": "around", "summary": "Carries the cup upright around the end of the wall."},
                {"label": "Tilted, through the slot", "rollout_id": b.rollout.report.rollout_id,
                 "route": "through_slot", "summary": "Carries the cup on its side through the slot, then sets it upright."},
            ],
            "related_constraint": "c_upright_transport",
            "answers": [{"text": "No. The cup must stay upright while it is carried.", "effect": "confirm c_upright_transport"},
                        {"text": "Yes. Both count.", "effect": "reject c_upright_transport"}],
        }
        self._event("clarification", n_candidates=2)
        return self.clarification

    def answer(self, text):
        low = text.lower()
        status = "rejected" if low.startswith("yes") else "confirmed"
        ops = [SpecOp(op="set_status", target_id="c_upright_transport", value={"status": status},
                      rationale="Answer to the clarification."),
               SpecOp(op="resolve_uncertainty", target_id="u_transport",
                      value={"resolution": "required" if status == "confirmed" else "incidental"})]
        new, rec = _apply(self.spec, ops, text, "clarification_answer")
        self.specs.append(new)
        self.clarification = None
        return new, rec

    # execution ----------------------------------------------------------
    def set_scene(self, scene):
        if isinstance(scene, str):
            scene = fixture_scene(scene)
        self.scene = scene
        self._event("scene_changed", scene_id=scene.scene_id)
        return scene

    def _keys(self, scene: SceneState, tilt_cup: bool):
        wall = next((o for o in scene.obstacles if o.kind == "wall"), None)
        tray = scene.trays[0]
        slot_center = None
        around_y = 0.36
        if wall is not None:
            if wall.slots:
                s = wall.slots[0]
                slot_center = ((s.y_min + s.y_max) / 2, (s.z_min + s.z_max) / 2)
            y0 = wall.pose.pos[1] - wall.length / 2
            y1 = wall.pose.pos[1] + wall.length / 2
            around_y = y1 + 0.07 if (0.40 - y1) > (y0 + 0.40) else y0 - 0.07
        plans = []
        spots = [(-0.06, -0.03), (0.06, -0.04), (0.0, 0.05)]
        for i, o in enumerate(scene.objects):
            tx, ty = tray.pose.pos[0] + spots[i][0], tray.pose.pos[1] + spots[i][1]
            # The marker carries no orientation rule, so it may use the slot whenever one exists.
            tilt = (o.kind == "cup" and tilt_cup) or (o.kind == "marker" and slot_center is not None)
            plans.append((o.id, _route_keys(o, (tx, ty), slot_center, around_y, tilt)))
        return plans, slot_center, around_y, wall

    def run(self, *, spec_version=None, condition="", frame_hz=30.0, backend="gripper"):
        if backend != "gripper":
            raise ValueError(f"the {backend} backend needs the live simulator; the fixture backend has no physics")
        spec = self.spec_version(spec_version)
        enforced = {c.id for c in spec.enforced_constraints()}
        has_slot = bool(any(o.kind == "wall" and o.slots for o in self.scene.obstacles))
        tilt = has_slot and "c_upright_transport" not in enforced
        return self._run(self.scene, spec, condition, tilt_cup=tilt)

    def _run(self, scene, spec, condition, tilt_cup, force=False, kind="r"):
        rid = ""
        keys, slot_center, around_y, wall = self._keys(scene, tilt_cup)
        enforced = {c.id for c in spec.enforced_constraints()}
        blocked = wall is not None and abs(wall.length) >= 0.79
        routes = [RouteOption(label="through_slot:pitched_90", object_id="cup_0", feasible=slot_center is not None and
                              ("c_upright_transport" not in enforced or force),
                              cost=0.62 if slot_center else None,
                              rejected_reason=None if slot_center is not None and ("c_upright_transport" not in enforced or force)
                              else ("violates c_upright_transport" if slot_center else "no slot")),
                  RouteOption(label="around_wall_end", object_id="cup_0", feasible=not blocked, cost=None if blocked else 0.95,
                              rejected_reason="wall spans the workspace" if blocked else None)]
        visual = visual_scene(scene)
        if blocked and not tilt_cup:
            plan = PlanResult(status="no_plan", reason="No route keeps the cup within 15° of upright: the slot is the only opening and it is lower than the upright cup in the gripper.",
                              spec_version=spec.version, scene_id=scene.scene_id, routes=routes, planner="fixture",
                              infeasibility={"proven": True, "kind": "opening_height",
                                             "detail": "slot height 0.100 m < upright envelope 0.180 m; wall spans the workspace"})
            rep = RolloutReport(rollout_id=rid, scene_id=scene.scene_id, spec_id=spec.spec_id, spec_version=spec.version,
                                condition=condition, status="planning_failed", plan_status="no_plan", plan_reason=plan.reason,
                                checker="fixture", notes=[FIXTURE_NOTE])
            e = self._register(Rollout(report=rep, visual=visual), None, plan, scene, spec, kind)
            e.stats = {}
            return e
        frames, stats = _play(scene, keys, scene.gripper_start.pos)
        plan = PlanResult(status="ok", spec_version=spec.version, scene_id=scene.scene_id, routes=routes, planner="fixture",
                          object_order=[o.id for o in scene.objects])
        rollout = Rollout(report=self._report(rid, scene, spec, condition, frames, stats, tilt_cup),
                          visual=visual, frame_hz=_HZ, frames=frames)
        e = self._register(rollout, {"frames": frames, "stats": stats}, plan, scene, spec, kind)
        e.stats = stats
        self._event("rollout", rollout_id=e.rollout_id, status=e.rollout.report.status, spec_version=spec.version,
                    scene_id=scene.scene_id)
        return e

    def _report(self, rid, scene, spec, condition, frames, stats, tilt_cup):
        enforced = {c.id: c for c in spec.enforced_constraints()}
        outcomes, violations = [], []
        for o in scene.objects:
            mt = stats["max_tilt"].get(o.id, 0.0)
            outcomes.append(ObjectOutcome(object_id=o.id, in_target=True, released=True, settled=True, final_tilt_deg=0.0,
                                          max_transport_tilt_deg=round(mt, 1), beads_total=o.beads,
                                          beads_in_container=o.beads - stats["spilled"].get(o.id, 0)))
        if "c_upright_transport" in enforced and stats["max_tilt"]["cup_0"] > DEFAULT_UPRIGHT_TOL_DEG:
            ts = [f.t for f in frames if f.phase == "transport:cup_0" and tilt_of(tuple(f.bodies["cup_0"].quat)) > 15]
            violations.append(ViolationInterval(constraint_id="c_upright_transport", object_id="cup_0",
                                                t_start=ts[0], t_end=ts[-1], peak=round(stats["max_tilt"]["cup_0"], 1),
                                                detail="cup tilt above 15° while carried"))
        status = "constraint_violated" if violations else "success"
        return RolloutReport(rollout_id=rid, scene_id=scene.scene_id, spec_id=spec.spec_id, spec_version=spec.version,
                             condition=condition, status=status, plan_status="ok",
                             route_labels=["through_slot:pitched_90" if tilt_cup else "around_wall_end"],
                             outcomes=outcomes, violations=violations, completion=True,
                             execution_time_s=frames[-1].t, physics_steps=int(frames[-1].t / 0.002),
                             checker="fixture", ground_truth_use=["fixture: none"], notes=[FIXTURE_NOTE])

    def recheck(self, rollout_id, spec_version=None, *, spec_version_id=None):
        e, spec, svid, own = self.recheck_target(rollout_id, spec_version, spec_version_id)
        if e.log is None:
            raise ValueError("rollout has no step log (planning failed)")
        rep = self._report(rollout_id, e.scene, spec, "", e.log["frames"], e.log["stats"],
                           "through_slot:pitched_90" in e.rollout.report.route_labels)
        return self._label_recheck(rep, e, rollout_id, svid, own)

    # evaluation ---------------------------------------------------------
    def paired_eval(self, spec_a: TaskSpec, spec_b: TaskSpec, seeds: list[int], progress=None) -> dict:
        pairs = []
        for i, seed in enumerate(seeds):
            scene = fixture_layout(seed)
            row = {"seed": seed, "scene_id": scene.scene_id, "scene": scene.model_dump(mode="json")}
            for cond, spec in (("original", spec_a), ("corrected", spec_b)):
                enforced = {c.id for c in spec.enforced_constraints()}
                has_slot = bool(scene.obstacles[0].slots)
                e = self._run(scene, spec, cond, tilt_cup=has_slot and "c_upright_transport" not in enforced)
                rep = e.rollout.report
                # Under the corrected rule, also re-check the original behaviour.
                row[cond] = rep.model_dump(mode="json")
                row[cond + "_rollout_id"] = rep.rollout_id
            orig_id = row["original_rollout_id"]
            row["original_under_corrected"] = self.recheck(orig_id, spec_b.version).model_dump(mode="json") \
                if self.rollouts[orig_id].log is not None else None
            pairs.append(row)
            if progress:
                progress(i + 1, len(seeds))
        return {"kind": "fixture", "spec_a": spec_a.version, "spec_b": spec_b.version, "seeds": seeds,
                "split": "eval", "pairs": pairs, "notes": [FIXTURE_NOTE]}



# ---------------------------------------------------------------------------
# Fixture stand-ins for the Meta-model stages (video tracking, video
# observations, speech to text). Used only by the fixture backend and tests;
# every output says "fixture". They never stand in for a model on the live
# backend.
# ---------------------------------------------------------------------------

FIXTURE_META_MODEL = "fixture (no model)"
_VID_W, _VID_H, _VID_FPS = 640, 360, 10.0
_VID_X, _VID_Y = (-0.45, 0.45), (-0.30, 0.30)


def _to_px(x: float, y: float) -> tuple[float, float]:
    """Table-plane metres -> top-view pixels (x to the right, y up the image)."""
    u = (x - _VID_X[0]) / (_VID_X[1] - _VID_X[0]) * _VID_W
    v = (1 - (y - _VID_Y[0]) / (_VID_Y[1] - _VID_Y[0])) * _VID_H
    return u, v


def _footprint(kind: str) -> float:
    return {"cup": 0.035, "block": 0.03, "marker": 0.05}.get(kind, 0.03)


class FixtureMeta:
    """Duck-typed like mirror.studio.meta_bridge, driven by a FixtureSession."""

    def __init__(self, session: "FixtureSession", workdir: Path):
        self.session = session
        self.workdir = Path(workdir)

    def available(self) -> dict:
        return {"perception": True, "observe": True, "voice": True, "bundled_video": _ffmpeg() is not None}

    def bundled_demo(self) -> Optional[dict]:
        if _ffmpeg() is None:
            return None
        path = self.workdir / "fixture_top.mp4"
        if not path.exists():
            self._render(path)
        return {"top": str(path), "side": None, "ground_truth": None, "fixture": True}

    def _render(self, path: Path) -> None:
        """A synthetic top-down video of the fixture demonstration (shapes on a table)."""
        import subprocess
        import tempfile
        from PIL import Image, ImageDraw
        s = self.session
        trace = s.demo or s.record_demo()
        scene = trace.scene
        kinds = {o.id: o.kind for o in scene.objects}
        colors = {"cup": (235, 235, 240), "block": (220, 120, 60), "marker": (60, 120, 220)}
        tmp = Path(tempfile.mkdtemp(prefix="fxvid-", dir=self.workdir))
        dur = trace.frames[-1].t
        n = int(dur * _VID_FPS) + 1
        for i in range(n):
            t = i / _VID_FPS
            fr = min(trace.frames, key=lambda f: abs(f.t - t))
            im = Image.new("RGB", (_VID_W, _VID_H), (58, 60, 66))
            d = ImageDraw.Draw(im)
            for tray in scene.trays:
                cx, cy = tray.pose.pos[0], tray.pose.pos[1]
                hx, hy = tray.inner_half
                a, b = _to_px(cx - hx, cy + hy), _to_px(cx + hx, cy - hy)
                d.rectangle([a, b], outline=(200, 200, 200), width=3)
            for ob in scene.obstacles:
                if ob.kind == "wall":
                    x, y = ob.pose.pos[0], ob.pose.pos[1]
                    a, b = _to_px(x - ob.thickness / 2, y + ob.length / 2), _to_px(x + ob.thickness / 2, y - ob.length / 2)
                    d.rectangle([a, b], fill=(120, 120, 128))
            for oid, pose in fr.objects.items():
                r = _footprint(kinds.get(oid, ""))
                a, b = _to_px(pose.pos[0] - r, pose.pos[1] + r), _to_px(pose.pos[0] + r, pose.pos[1] - r)
                (d.ellipse if kinds.get(oid) == "cup" else d.rectangle)([a, b], fill=colors.get(kinds.get(oid), (200, 200, 200)))
            d.text((10, 8), "FIXTURE VIDEO (synthetic)", fill=(255, 210, 0))
            im.save(tmp / f"f{i:05d}.png")
        subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-framerate", str(_VID_FPS), "-i", str(tmp / "f%05d.png"),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True, timeout=120,
                       stdin=subprocess.DEVNULL)
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    def perceive(self, top: str, side: Optional[str] = None, progress=None, ground_truth=None, demo_id=None,
                 footage=None) -> dict:
        s = self.session
        trace = s.demo or s.record_demo()
        kinds = {o.id: o.kind for o in trace.scene.objects}
        if progress:
            progress(0.5, "fixture tracking")
        objects: dict[str, list] = {oid: [] for oid in kinds}
        last = -1.0
        for fr in trace.frames:
            if fr.t - last < 1.0 / _VID_FPS - 1e-6:
                continue
            last = fr.t
            for oid, pose in fr.objects.items():
                r = _footprint(kinds[oid])
                (x0, y0), (x1, y1) = _to_px(pose.pos[0] - r, pose.pos[1] + r), _to_px(pose.pos[0] + r, pose.pos[1] - r)
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                rad = (x1 - x0) / 2
                poly = [[round(cx + rad * math.cos(a), 1), round(cy + rad * math.sin(a), 1)]
                        for a in [k * math.pi / 8 for k in range(16)]] if kinds[oid] == "cup" else \
                    [[round(x0, 1), round(y0, 1)], [round(x1, 1), round(y0, 1)], [round(x1, 1), round(y1, 1)], [round(x0, 1), round(y1, 1)]]
                objects[oid].append({"t": round(fr.t, 3), "box": [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)],
                                     "polygon": poly, "score": 1.0})
        vt = trace.model_copy(update={"source": "video_grounded", "source_note": FIXTURE_NOTE, "video_path": top})
        if "evidence" in type(vt).model_fields:
            try:   # never mistaken for video evidence (mirror.observability, when present)
                from ..observability import simulator_evidence
                vt = vt.model_copy(update={"evidence": simulator_evidence(trace, kind="interface_fixture")})
            except Exception:  # noqa: BLE001
                pass
        return {"trace": vt, "tracks": {"top": {"fps": _VID_FPS, "width": _VID_W, "height": _VID_H, "objects": objects}},
                "metrics": None, "model": FIXTURE_META_MODEL, "latency_s": 0.0, "source": "fixture", "calls": []}

    def observe(self, video: str, progress=None, model=None) -> dict:
        trace = self.session.demo or self.session.record_demo()
        events = [{"t": round(e.t, 2), "kind": e.type, "object_id": e.object_id, "text": ""} for e in trace.events]
        cup_t = next((e.t for e in trace.events if e.object_id.startswith("cup") and e.type == "grasp"), 1.0)
        cues = [{"t": round(cup_t + 0.5, 2), "t_end": round(cup_t + 3.0, 2), "kind": "care", "object_id": "cup_0",
                 "text": "fixture cue: the cup stays level while carried"}]
        obs = {"objects": [{"id": o.id, "kind": o.kind, "matters": True, "note": "fixture"} for o in trace.scene.objects],
               "events": events, "cues": cues, "uncertainties": ["fixture: is the cup's upright transport required?"],
               "summary": "Fixture observations for interface tests.", "model": FIXTURE_META_MODEL}
        return {"raw": obs, "observations": obs, "model": FIXTURE_META_MODEL, "latency_s": 0.0, "source": "fixture",
                "calls": []}

    def propose_with_observations(self, trace, client, observations):
        return self.session.interpret()

    def transcribe(self, wav: bytes) -> dict:
        return {"text": "This container must remain upright throughout transport, not just at the destination.",
                "raw": {"note": "fixture transcript: the audio is not read"}, "model": FIXTURE_META_MODEL,
                "latency_s": 0.0, "source": "fixture", "calls": []}


def _ffmpeg() -> Optional[str]:
    import shutil
    return shutil.which("ffmpeg")
