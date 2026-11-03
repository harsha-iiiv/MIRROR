"""Execution evidence and the events ordering rules are judged on.

Two evidence sources are supported:

* ``dense_state_log``: the simulator's per-physics-step StateLog (object
  poses, the executor's finger-contact held label, contacts, beads).
* ``playback_frames``: the downsampled playback frames stored in a Rollout
  JSON (object poses at ``frame_hz``, bodies listed only when they move, no
  contacts, no held label). ``log_from_rollout_frames`` turns them into a
  StateLog marked with ``meta["evidence_source"] = "playback_frames"`` so the
  same checker can re-check historical rollouts. The held label is then derived
  from lift-off (an object above its resting height), and contact rules cannot
  be verified.

Events are derived from object motion and the held label only. The executor's
phase labels and the plan (declared object order, step notes) are never read,
so an ordering verdict cannot be produced by the planner asserting it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from mirror.contracts import Rollout, SceneState, TraySpec
from mirror.statelog import StateLog

from . import geom

LIFT_OFF_M = 0.005          # above resting height (playback held label)
MIN_CARRY_S = 0.05          # shorter held blips without lift-off are not grasps
PLACEMENT_SETTLE_S = 0.2    # rest window that completes a placement
PLAYBACK_SOURCE = "playback_frames"
DENSE_SOURCE = "dense_state_log"


def evidence_source(log: StateLog) -> str:
    return str(log.meta.get("evidence_source", DENSE_SOURCE))


def contacts_logged(log: StateLog) -> bool:
    return bool(log.meta.get("contacts_logged", evidence_source(log) == DENSE_SOURCE))


def resolution_s(log: StateLog) -> float:
    """Timing resolution of the evidence: one sample period."""
    if log.n >= 2:
        return float(max(log.dt, float(np.median(np.diff(log.t)))))
    return float(log.dt)


# ------------------------------------------------------------ events ---

@dataclass
class ObjectEvents:
    object_id: str
    present: bool                      # the object is in the evidence
    grasp_start: Optional[float] = None
    placement: str = "absent"          # "occurred" | "absent" | "unresolved"
    placement_complete: Optional[float] = None
    carried: list[tuple[float, float]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def time(self, event: str) -> tuple[str, Optional[float]]:
        """("occurred", t) | ("absent", None) | ("unresolved", None) | ("missing", None)."""
        if not self.present:
            return "missing", None
        if event == "grasp_start":
            return ("occurred", self.grasp_start) if self.grasp_start is not None else ("absent", None)
        if event == "placement_complete":
            return self.placement, self.placement_complete
        raise ValueError(f"unknown ordering event {event!r}")

    def summary(self) -> dict:
        return {"object_id": self.object_id, "present": self.present, "grasp_start": self.grasp_start,
                "placement": self.placement, "placement_complete": self.placement_complete,
                "carried": [[round(a, 4), round(b, 4)] for a, b in self.carried], "notes": list(self.notes)}


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    m = np.asarray(mask, bool)
    if not m.any():
        return []
    d = np.diff(np.concatenate([[0], m.astype(np.int8), [0]]))
    return list(zip(np.flatnonzero(d == 1).tolist(), (np.flatnonzero(d == -1) - 1).tolist()))


def object_events(log: StateLog, scene: SceneState, object_id: str, tray: Optional[TraySpec],
                  settle_speed: float = 0.01) -> ObjectEvents:
    """Grasp start and placement completion of one object, from the evidence."""
    from .checker import _speeds, _transport_mask   # same monitors as the rules use
    ev = ObjectEvents(object_id=object_id, present=object_id in log.object_ids and log.n > 0)
    if not ev.present:
        ev.notes.append(f"{object_id} is not in the evidence")
        return ev
    k = log.index(object_id)
    t = log.t
    carried = _transport_mask(log, k)
    z = log.obj_pos[:, k, 2]
    runs = []
    for s, e in _runs(carried):
        dur = float(t[e] - t[s]) + float(log.dt)
        lifted = bool(np.any(z[s:e + 1] > z[0] + LIFT_OFF_M))
        if dur < MIN_CARRY_S and not lifted:
            ev.notes.append(f"{object_id}: held for {dur:.3f} s at t={t[s]:.3f} s without lifting; "
                            "not counted as a grasp")
            continue
        runs.append((s, e))
    ev.carried = [(float(t[s]), float(t[e])) for s, e in runs]
    if runs:
        ev.grasp_start = float(t[runs[0][0]])
    if tray is None:
        ev.placement = "unresolved"
        ev.notes.append("no destination tray; placement cannot be established")
        return ev
    pos = log.obj_pos[:, k, :]
    inside = np.asarray(geom.inside_tray(tray, pos), bool)
    speed = _speeds(log)[:, k]
    w = max(1, int(round(PLACEMENT_SETTLE_S / resolution_s(log))))
    start = runs[-1][1] + 1 if runs else 0
    if not inside[-1]:
        ev.placement = "absent"
        ev.notes.append(f"{object_id} does not end inside {tray.id}")
        return ev
    calm = speed < settle_speed
    # first index after the last release from which the object is inside the
    # tray and calm for a full settle window
    cand = None
    for i in range(start, log.n - w + 1):
        if inside[i] and calm[i:i + w].all() and inside[i:i + w].all():
            cand = i
            break
    if cand is None:
        ev.placement = "unresolved"
        ev.notes.append(f"{object_id} ends inside {tray.id} but was not observed at rest for "
                        f"{PLACEMENT_SETTLE_S:g} s after its last release")
        return ev
    ev.placement = "occurred"
    ev.placement_complete = float(t[cand])
    if not runs:
        ev.notes.append(f"{object_id} rested in {tray.id} without being carried")
    return ev


# ------------------------------------------------- playback adapter ---

def log_from_rollout_frames(rollout: Rollout, scene: SceneState) -> StateLog:
    """Build an approximate StateLog from a Rollout's playback frames.

    Bodies absent from a frame keep their last listed pose (the playback
    convention). The held label is derived from lift-off: an object is held
    while its centre is more than LIFT_OFF_M above its resting height (its
    start height, plus the tray floor when it is over the tray). Contacts are
    not available. The result is marked as playback evidence."""
    frames = rollout.frames
    if not frames:
        raise ValueError("rollout has no playback frames")
    ids = [o.id for o in scene.objects]
    K, N = len(ids), len(frames)
    pos = np.zeros((N, K, 3))
    quat = np.zeros((N, K, 4))
    cur_p = {o.id: np.array(o.pose.pos, float) for o in scene.objects}
    cur_q = {o.id: np.array(o.pose.quat, float) for o in scene.objects}
    g_p = np.array(scene.gripper_start.pos, float)
    g_q = np.array(scene.gripper_start.quat, float)
    gp, gq, gw, t, phase = np.zeros((N, 3)), np.zeros((N, 4)), np.zeros(N), np.zeros(N), []
    bead_names: dict[str, list[str]] = {}
    for name in sorted({b for f in frames for b in f.bodies if "/bead" in b},
                       key=lambda s: (s.split("/")[0], int(s.rsplit("bead", 1)[1]) if s.rsplit("bead", 1)[1].isdigit() else 0)):
        bead_names.setdefault(name.split("/")[0], []).append(name)
    cur_b = {b: None for names in bead_names.values() for b in names}
    beads = {cid: np.zeros((N, len(names), 3)) for cid, names in bead_names.items()}
    seen = set()
    for i, f in enumerate(frames):
        t[i] = f.t
        gw[i] = f.gripper_width
        phase.append(f.phase)
        for name, p in f.bodies.items():
            if name in cur_p:
                cur_p[name] = np.array(p.pos, float)
                cur_q[name] = np.array(p.quat, float)
                seen.add(name)
            elif name == "gripper":
                g_p, g_q = np.array(p.pos, float), np.array(p.quat, float)
            elif name in cur_b:
                cur_b[name] = np.array(p.pos, float)
        for j, oid in enumerate(ids):
            pos[i, j], quat[i, j] = cur_p[oid], cur_q[oid]
        gp[i], gq[i] = g_p, g_q
        for cid, names in bead_names.items():
            for j, b in enumerate(names):
                if cur_b[b] is not None:
                    beads[cid][i, j] = cur_b[b]
                else:
                    beads[cid][i, j] = cur_p.get(cid, np.zeros(3))
    tray = scene.trays[0] if scene.trays else None
    held = np.full(N, -1, dtype=np.int32)
    lifted_any = np.zeros((N, K), bool)
    for j, o in enumerate(scene.objects):
        rest = float(o.pose.pos[2])
        support = np.zeros(N)
        if tray is not None:
            over = np.asarray(geom.inside_tray(tray, pos[:, j, :] * np.array([1, 1, 0]) +
                                               np.array([0, 0, tray.pose.pos[2] + 1e-6])), bool)
            support = np.where(over, float(tray.pose.pos[2]), 0.0)
        lifted_any[:, j] = pos[:, j, 2] > rest + support + LIFT_OFF_M
    for i in range(N):
        cand = np.flatnonzero(lifted_any[i])
        if cand.size:
            d = np.linalg.norm(pos[i, cand, :] - gp[i], axis=-1)
            held[i] = int(cand[int(np.argmin(d))])
    rep = rollout.report
    status = {"execution_failed": "execution_failed", "timeout": "timeout"}.get(rep.status, "completed")
    dt = 1.0 / float(rollout.frame_hz or 30.0)
    diffs = np.abs(np.diff(pos, axis=0))
    nz = diffs[diffs > 1e-9]
    quantum = float(round(np.min(nz), 6)) if nz.size else 0.0
    meta = {"status": status, "evidence_source": PLAYBACK_SOURCE, "contacts_logged": False,
            "position_quantum_m": quantum,
            "settle_note": "playback positions are rounded; motion of one rounding step per frame is "
                           "treated as at rest",
            "frame_hz": rollout.frame_hz, "rollout_id": rep.rollout_id,
            "held_label": "derived from lift-off (object centre above its resting height)",
            "bodies_never_listed": [o for o in ids if o not in seen]}
    return StateLog(dt=dt, t=t, object_ids=ids, obj_pos=pos, obj_quat=quat, gripper_pos=gp,
                    gripper_quat=gq, gripper_width=gw, held=held, phase=phase, beads=beads,
                    contacts=[], meta=meta)


def scene_from_rollout(rollout: Rollout, scene_id: Optional[str] = None) -> SceneState:
    """Reconstruct a SceneState from a Rollout's own visual scene and first frame.

    Used to re-check historical rollouts without regenerating their layouts.
    Static geometry (tray, obstacle boxes) comes from the recorded visual
    geoms; object poses from the first playback frame; object dimensions from
    the standard kit geometry (the only kit the task family uses). Wall
    segments become box obstacles, so clearance is measured to the same solid
    geometry. The scene is labelled as reconstructed in ``notes``."""
    from mirror.contracts import ObjectSpec, ObstacleSpec, Pose, TraySpec, Workspace
    from mirror.geometry import BLOCK, CUP, MARKER, WORLD
    if rollout.visual is None or not rollout.frames:
        raise ValueError("rollout has no visual scene or frames to reconstruct from")
    first = rollout.frames[0].bodies
    trays, obstacles, objects = [], [], []
    beads: dict[str, int] = {}
    for name in first:
        if "/bead" in name:
            beads[name.split("/")[0]] = beads.get(name.split("/")[0], 0) + 1
    for b in rollout.visual.bodies:
        if b.name.startswith("tray"):
            floor = next(g for g in b.geoms if (g.name or "").endswith("floor"))
            xp = next((g for g in b.geoms if (g.name or "").endswith("wall_xp")), None)
            yp = next((g for g in b.geoms if (g.name or "").endswith("wall_yp")), None)
            cx, cy = floor.pos[0], floor.pos[1]
            top = floor.pos[2] + floor.size[2]
            hx = (xp.pos[0] - xp.size[0] - cx) if xp else floor.size[0]
            hy = (yp.pos[1] - yp.size[1] - cy) if yp else floor.size[1]
            trays.append(TraySpec(id=b.name, pose=Pose(pos=(cx, cy, top), quat=floor.quat),
                                  inner_half=(round(hx, 6), round(hy, 6))))
        elif b.static and b.name != "world":
            for i, g in enumerate(b.geoms):
                if g.type == "box":
                    obstacles.append(ObstacleSpec(id=f"{b.name}" if len(b.geoms) == 1 else f"{b.name}#{i}",
                                                  kind="box", pose=Pose(pos=g.pos, quat=g.quat),
                                                  half=tuple(g.size[:3])))
    for b in rollout.visual.bodies:
        if b.static or b.name.startswith("gripper") or "/bead" in b.name or b.name not in first:
            continue
        kind = b.name.split("_")[0]
        p = first[b.name]
        if kind == "cup":
            objects.append(ObjectSpec(id=b.name, kind="cup", size={"outer_half": CUP.outer_half, "wall": CUP.wall},
                                      mass=CUP.mass, pose=p, beads=beads.get(b.name, 0)))
        elif kind == "block":
            objects.append(ObjectSpec(id=b.name, kind="block", size={"half": BLOCK.half}, mass=BLOCK.mass, pose=p))
        elif kind == "marker":
            objects.append(ObjectSpec(id=b.name, kind="marker",
                                      size={"radius": MARKER.radius, "half_length": MARKER.half_length},
                                      mass=MARKER.mass, pose=p))
    g = first.get("gripper")
    return SceneState(
        scene_id=scene_id or rollout.report.scene_id, split="custom", objects=objects, trays=trays,
        obstacles=obstacles,
        workspace=Workspace(x_range=WORLD.workspace_x, y_range=WORLD.workspace_y, z_range=WORLD.workspace_z),
        gripper_start=g if g is not None else Pose(pos=(0.0, 0.0, 0.3)),
        notes="reconstructed from a recorded rollout (visual geoms + first playback frame; standard kit geometry)")
