"""Synthetic scenes, specifications and step logs for checker tests.

The logs are constructed directly (no simulator) so that each checker
behaviour can be tested against known ground truth.
"""
from __future__ import annotations

import math

import numpy as np

from mirror.contracts import (Constraint, Goal, ObjectBinding, ObjectSelector, ObjectSpec,
                              ObstacleSpec, Pose, Provenance, SceneState, SlotSpec, Status,
                              TaskSpec, TraySpec, Workspace)
from mirror.geometry import BLOCK, CUP, MARKER, TRAY, WORLD
from mirror.statelog import ContactRecord, StateLog

DT = 0.002
TRAY_Z = 0.004


def make_scene(scene_id="synthetic", full_wall=False, slot_height=(0.09, 0.19), boxes=()):
    length = 1.00 if full_wall else 0.60
    cy = 0.0 if full_wall else 0.10
    wall = ObstacleSpec(id="wall", kind="wall", pose=Pose(pos=(0.0, cy, 0.0)), length=length,
                        height=WORLD.wall_height, thickness=WORLD.wall_thickness,
                        slots=[SlotSpec(y_min=-0.10 - cy, y_max=0.10 - cy,
                                        z_min=slot_height[0], z_max=slot_height[1])])
    obstacles = [wall] + [ObstacleSpec(id=f"box{i}", kind="box", pose=Pose(pos=p), half=h)
                          for i, (p, h) in enumerate(boxes)]
    return SceneState(
        scene_id=scene_id, seed=0, split="custom",
        objects=[
            ObjectSpec(id="cup_0", kind="cup", size={"outer_half": CUP.outer_half, "wall": CUP.wall},
                       mass=CUP.mass, pose=Pose(pos=(-0.25, 0.0, CUP.outer_half[2])), beads=CUP.beads),
            ObjectSpec(id="block_0", kind="block", size={"half": BLOCK.half}, mass=BLOCK.mass,
                       pose=Pose(pos=(-0.25, 0.15, BLOCK.half[2]))),
            ObjectSpec(id="marker_0", kind="marker",
                       size={"radius": MARKER.radius, "half_length": MARKER.half_length},
                       mass=MARKER.mass, pose=Pose(pos=(-0.30, -0.15, MARKER.half_length + MARKER.radius))),
        ],
        trays=[TraySpec(id="tray_0", pose=Pose(pos=(0.25, 0.0, TRAY_Z)), inner_half=TRAY.inner_half)],
        obstacles=obstacles,
        workspace=Workspace(x_range=WORLD.workspace_x, y_range=WORLD.workspace_y,
                            z_range=WORLD.workspace_z),
        gripper_start=Pose(pos=(0.0, -0.3, 0.3)),
    )


def prov(src="demo_inference", v=1):
    return Provenance(source=src, version_introduced=v)


def make_spec(corrected=False, final_upright=True, marker_rule=False, extra=()):
    bindings = [ObjectBinding(role="container", kind="cup"),
                ObjectBinding(role="part", kind="block"),
                ObjectBinding(role="tool", kind="marker")]
    goals = [Goal(id=f"g_{k}", type="inside", object=ObjectSelector(kind=k), provenance=prov())
             for k in ("cup", "block", "marker")]
    cons = []
    if final_upright:
        cons.append(Constraint(id="c_upright_final", type="upright",
                               applies_to=ObjectSelector(role="container"), phase="final",
                               params={"max_tilt_deg": 15.0}, status=Status.confirmed,
                               provenance=prov()))
    if corrected:
        cons.append(Constraint(id="c_upright_transport", type="upright",
                               applies_to=ObjectSelector(role="container"), phase="transport",
                               params={"max_tilt_deg": 15.0}, status=Status.confirmed,
                               provenance=prov("user_correction", 2)))
    if marker_rule:
        cons.append(Constraint(id="c_marker", type="upright", applies_to=ObjectSelector(kind="marker"),
                               phase="transport", params={"max_tilt_deg": 15.0},
                               status=Status.proposed, provenance=prov()))
    cons += list(extra)
    return TaskSpec(spec_id="kit", version=2 if corrected else 1,
                    parent_version=1 if corrected else None, bindings=bindings, goals=goals,
                    constraints=cons)


def quat_pitch(deg):
    a = math.radians(deg) / 2
    return np.array([math.cos(a), 0.0, math.sin(a), 0.0])


class LogBuilder:
    """Append-only trajectory builder: objects stay put unless moved.

    Rows are stored as numpy chunks so building a full three-object rollout
    takes milliseconds."""

    def __init__(self, scene: SceneState):
        self.scene = scene
        self.ids = [o.id for o in scene.objects]
        self.pos = np.array([o.pose.pos for o in scene.objects], float)     # (K, 3)
        self.quat = np.array([o.pose.quat for o in scene.objects], float)   # (K, 4)
        self.chunks = []
        self.n = 0
        self.contacts: list[ContactRecord] = []
        cup = next(o for o in scene.objects if o.kind == "cup")
        self.cup_id = cup.id
        self.cup_k = self.ids.index(cup.id)
        rng = np.random.default_rng(0)
        hz = CUP.outer_half[2]
        self.bead_local = np.column_stack([rng.uniform(-0.015, 0.015, cup.beads),
                                           rng.uniform(-0.015, 0.015, cup.beads),
                                           rng.uniform(-hz + 0.012, -hz + 0.03, cup.beads)])
        self.spilled = False
        self.spill_pos = None

    def _beads(self, cup_pos, cup_quat):
        """cup_pos (M, 3), cup_quat (M, 4) -> (M, B, 3)."""
        from mirror.evaluate.geom import quat_rotate
        m, nb = cup_pos.shape[0], len(self.bead_local)
        q = np.repeat(cup_quat[:, None, :], nb, axis=1)
        v = np.broadcast_to(self.bead_local, (m, nb, 3))
        return cup_pos[:, None, :] + quat_rotate(q, v)

    def _append(self, pos, quat, held, phase, beads):
        m = pos.shape[0]
        self.chunks.append(dict(pos=pos, quat=quat, held=np.full(m, held), phase=[phase] * m, beads=beads))
        self.n += m

    def _static_beads(self, m):
        k = self.cup_k
        if self.spilled:
            return np.repeat(self.spill_pos[None], m, axis=0)
        return self._beads(np.repeat(self.pos[k][None], m, 0), np.repeat(self.quat[k][None], m, 0))

    def wait(self, seconds, held=-1, phase="settle", jitter=0.0):
        m = int(round(seconds / DT))
        if m == 0:
            return
        pos = np.repeat(self.pos[None], m, axis=0)
        if jitter and held < 0:
            sign = np.where(np.arange(m) % 2 == 0, 1.0, -1.0)
            pos = pos + (np.cumsum(sign)[:, None, None] * np.array([jitter, 0, 0]))
            self.pos = pos[-1].copy()
        quat = np.repeat(self.quat[None], m, axis=0)
        beads = self._static_beads(m) if not jitter else self._beads(pos[:, self.cup_k], quat[:, self.cup_k])
        self._append(pos, quat, held, phase, beads)

    def move(self, oid, waypoints, pitch_deg_fn=lambda s: 0.0, speed=0.25, spill_tilt=None,
             contact_other=None, contact_window=(0.4, 0.6)):
        k = self.ids.index(oid)
        pts = np.array([self.pos[k]] + [list(p) for p in waypoints], float)
        seglen = np.linalg.norm(np.diff(pts, axis=0), axis=1)
        total = float(seglen.sum())
        m = max(2, int(round(total / speed / DT)))
        cum = np.concatenate([[0], np.cumsum(seglen)])
        self.wait(0.1, held=k, phase=f"grasp:{oid}")
        s = (np.arange(m) + 1) / m
        d = s * total
        i = np.clip(np.searchsorted(cum, d, side="right") - 1, 0, len(seglen) - 1)
        f = np.where(seglen[i] > 0, (d - cum[i]) / np.where(seglen[i] > 0, seglen[i], 1), 0.0)
        path = pts[i] + f[:, None] * (pts[i + 1] - pts[i])
        tilt = np.array([pitch_deg_fn(x) for x in s])
        a = np.radians(tilt) / 2
        q = np.column_stack([np.cos(a), np.zeros(m), np.sin(a), np.zeros(m)])
        pos = np.repeat(self.pos[None], m, axis=0)
        quat = np.repeat(self.quat[None], m, axis=0)
        pos[:, k] = path
        quat[:, k] = q
        if self.spilled:
            beads = np.repeat(self.spill_pos[None], m, axis=0)
        else:
            beads = self._beads(pos[:, self.cup_k], quat[:, self.cup_k])
            if oid == self.cup_id and spill_tilt is not None and (tilt > spill_tilt).any():
                j = int(np.flatnonzero(tilt > spill_tilt)[0])
                self.spill_pos = beads[j] * np.array([1, 1, 0]) + np.array([0, 0, 0.0075])
                self.spilled = True
                beads[j:] = self.spill_pos
        if contact_other:
            for j in np.flatnonzero((s >= contact_window[0]) & (s <= contact_window[1])):
                self.contacts.append(ContactRecord(step=self.n + int(j), object_id=oid,
                                                   other=contact_other, depth=0.001))
        self._append(pos, quat, k, f"transport:{oid}", beads)
        self.pos = pos[-1].copy()
        self.quat = quat[-1].copy()
        self.wait(0.1, held=k, phase=f"release:{oid}")

    @property
    def rows(self):
        return range(self.n)

    def build(self, status="completed") -> StateLog:
        n = self.n
        cat = lambda key: np.concatenate([c[key] for c in self.chunks])
        return StateLog(
            dt=DT, t=np.arange(n) * DT, object_ids=list(self.ids),
            obj_pos=cat("pos"), obj_quat=cat("quat"),
            gripper_pos=np.zeros((n, 3)), gripper_quat=np.tile([1.0, 0, 0, 0], (n, 1)),
            gripper_width=np.zeros(n), held=cat("held"),
            phase=[p for c in self.chunks for p in c["phase"]],
            beads={self.cup_id: cat("beads")},
            contacts=list(self.contacts), meta={"status": status})


def tray_spot(dx=0.0, dy=0.0, z=None):
    return (0.25 + dx, dy, z)


def pack_all(scene, *, cup_route="around", spill=True, marker_tilt=0.0, leave_cup_tilted=False,
             cup_contact=None, settle=0.6):
    """Pack cup, block and marker into the tray. cup_route: 'around' keeps the
    cup upright around the wall end; 'slot' pitches it 90 degrees through the slot."""
    b = LogBuilder(scene)
    b.wait(0.2)
    hz = CUP.outer_half[2]
    lift = 0.15
    if cup_route == "around":
        wps = [(-0.25, 0.0, lift), (-0.25, -0.30, lift), (0.15, -0.30, lift), (0.20, -0.04, lift),
               (0.20, -0.04, TRAY_Z + hz)]
        b.move("cup_0", wps, lambda s: 0.0, contact_other=cup_contact)
    else:
        def pitch(s):
            if leave_cup_tilted:
                return 90.0 if s > 0.1 else 900 * s
            return 90.0 if 0.2 < s < 0.8 else (450 * s if s <= 0.2 else 450 * (1 - s))
        wps = [(-0.25, 0.0, lift), (-0.10, 0.0, 0.14), (0.10, 0.0, 0.14), (0.20, -0.04, lift),
               (0.20, -0.04, TRAY_Z + (CUP.outer_half[0] if leave_cup_tilted else hz))]
        b.move("cup_0", wps, pitch, spill_tilt=60.0 if spill else None, contact_other=cup_contact)
    b.wait(0.2)
    b.move("block_0", [(-0.25, 0.15, lift), (-0.25, -0.30, lift), (0.15, -0.30, lift),
                       (0.30, 0.04, lift), (0.30, 0.04, TRAY_Z + BLOCK.half[2])])
    b.wait(0.2)
    mz = MARKER.half_length + MARKER.radius
    b.move("marker_0", [(-0.30, -0.15, 0.2), (0.0, 0.0, 0.14), (0.30, -0.05, 0.2),
                        (0.30, -0.05, TRAY_Z + mz)],
           lambda s: marker_tilt if 0.2 < s < 0.8 else 0.0)
    b.wait(settle)
    return b
