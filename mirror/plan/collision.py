"""Geometric collision model used by the planner.

Everything is an oriented box (OBB). Static obstacles (wall pieces around
slots, box obstacles, tray walls and floor, objects that are not being moved)
are inflated by a safety margin; the moving body (gripper palm, two fingers
and the held object, dimensions from mirror/geometry.py) is tested against
them with the separating axis theorem, vectorised over many poses.

Swept checks sample a straight-line segment (linear in position, geodesic in
orientation) densely enough that no point of the moving body travels more
than ``step`` between consecutive samples. With ``step`` no larger than the
safety margin, a collision-free sampled segment keeps the body clear of the
un-inflated obstacles over the whole segment.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..geometry import GRIPPER
from .se3 import angle_between_mats, slerp_mats


@dataclass
class Box:
    """Oriented box: centre c (3,), rotation R (3,3) whose columns are the box
    axes in the parent frame, half extents h (3,)."""

    c: np.ndarray
    R: np.ndarray
    h: np.ndarray
    name: str = ""
    group: str = ""  # "wall", "box", "tray", "object", "gripper", "held"

    def inflated(self, m: float) -> "Box":
        return Box(self.c, self.R, self.h + m, self.name, self.group)

    def z_range(self) -> tuple[float, float]:
        ext = float(np.abs(self.R[2]) @ self.h)
        return float(self.c[2] - ext), float(self.c[2] + ext)

    def footprint_corners(self) -> np.ndarray:
        """Corners of the box's horizontal footprint (exact for boxes whose z
        axis is vertical). (4, 2)"""
        hx, hy = self.R[:2, 0] * self.h[0], self.R[:2, 1] * self.h[1]
        c = self.c[:2]
        return np.array([c + hx + hy, c - hx + hy, c - hx - hy, c + hx - hy])


def stack(boxes: list[Box]):
    if not boxes:
        return np.zeros((0, 3)), np.zeros((0, 3, 3)), np.zeros((0, 3))
    return (np.array([b.c for b in boxes], float), np.array([b.R for b in boxes], float),
            np.array([b.h for b in boxes], float))


def sat_overlap(ca, Ra, ha, cb, Rb, hb, eps: float = 1e-9) -> np.ndarray:
    """Pairwise OBB overlap test. a: (P,...) b: (Q,...). Returns bool (P, Q)."""
    P, Q = len(ca), len(cb)
    if P == 0 or Q == 0:
        return np.zeros((P, Q), bool)
    # R = A^T B, t = A^T (cb - ca)
    R = np.einsum("pji,qjk->pqik", Ra, Rb)  # (P,Q,3,3)
    d = cb[None, :, :] - ca[:, None, :]  # (P,Q,3)
    t = np.einsum("pji,pqj->pqi", Ra, d)  # (P,Q,3)
    AR = np.abs(R) + eps
    a = ha[:, None, :]  # (P,1,3)
    b = hb[None, :, :]  # (1,Q,3)
    sep = np.zeros((P, Q), bool)
    # A's axes
    ra = a
    rb = np.einsum("pqij,pqj->pqi", AR, np.broadcast_to(b, (P, Q, 3)))
    sep |= np.any(np.abs(t) > ra + rb, axis=2)
    # B's axes
    tb = np.einsum("pqji,pqj->pqi", R, t)
    ra2 = np.einsum("pqji,pqj->pqi", AR, np.broadcast_to(a, (P, Q, 3)))
    sep |= np.any(np.abs(tb) > ra2 + b, axis=2)
    # cross axes A_i x B_j
    aa = np.broadcast_to(a, (P, Q, 3))
    bb = np.broadcast_to(b, (P, Q, 3))
    for i in range(3):
        i1, i2 = (i + 1) % 3, (i + 2) % 3
        for j in range(3):
            j1, j2 = (j + 1) % 3, (j + 2) % 3
            lhs = np.abs(t[..., i2] * R[..., i1, j] - t[..., i1] * R[..., i2, j])
            r1 = aa[..., i1] * AR[..., i2, j] + aa[..., i2] * AR[..., i1, j]
            r2 = bb[..., j1] * AR[..., i, j2] + bb[..., j2] * AR[..., i, j1]
            sep |= lhs > r1 + r2
    return ~sep


# ------------------------------------------------------------- envelope ---


@dataclass
class HeldObject:
    """The grasped object as a box in the gripper (TCP) frame."""

    object_id: str
    c: np.ndarray  # centre in gripper frame
    R: np.ndarray  # orientation in gripper frame
    h: np.ndarray


@dataclass
class Envelope:
    """Moving body in the gripper frame: palm, two fingers, optional held object."""

    boxes: list[Box]
    opening: float
    held: Optional[HeldObject] = None

    @property
    def reach(self) -> float:
        """Largest distance from the TCP to any point of the envelope."""
        cached = self.__dict__.get("_reach")
        if cached is not None:
            return cached
        r = 0.0
        for b in self.boxes:
            for sx in (-1, 1):
                for sy in (-1, 1):
                    for sz in (-1, 1):
                        p = b.c + b.R @ (b.h * np.array([sx, sy, sz]))
                        r = max(r, float(np.linalg.norm(p)))
        self.__dict__["_reach"] = r
        return r

    def vertical_extent(self, R: np.ndarray) -> tuple[float, float]:
        """(lowest, highest) z of the envelope relative to the TCP for
        gripper orientation R."""
        lo, hi = math.inf, -math.inf
        for b in self.boxes:
            Rw = R @ b.R
            cz = float((R @ b.c)[2])
            ext = float(np.abs(Rw[2]) @ b.h)
            lo, hi = min(lo, cz - ext), max(hi, cz + ext)
        return lo, hi

    def extent_along(self, R: np.ndarray, axis: np.ndarray) -> tuple[float, float]:
        axis = np.asarray(axis, float)
        lo, hi = math.inf, -math.inf
        for b in self.boxes:
            Rw = R @ b.R
            c = float(axis @ (R @ b.c))
            ext = float(np.abs(axis @ Rw) @ b.h)
            lo, hi = min(lo, c - ext), max(hi, c + ext)
        return lo, hi

    def horizontal_radius(self, R: np.ndarray) -> float:
        r = 0.0
        for b in self.boxes:
            for sx in (-1, 1):
                for sy in (-1, 1):
                    for sz in (-1, 1):
                        p = R @ (b.c + b.R @ (b.h * np.array([sx, sy, sz])))
                        r = max(r, float(math.hypot(p[0], p[1])))
        return r


def gripper_boxes(opening: float) -> list[Box]:
    g = GRIPPER
    eye = np.eye(3)
    palm = Box(np.array([0.0, 0.0, g.palm_center_z]), eye, np.array(g.palm_half, float), "palm", "gripper")
    fy = opening / 2.0 + g.finger_half[1]
    f1 = Box(np.array([0.0, fy, g.finger_center_z]), eye, np.array(g.finger_half, float), "finger_l", "gripper")
    f2 = Box(np.array([0.0, -fy, g.finger_center_z]), eye, np.array(g.finger_half, float), "finger_r", "gripper")
    return [palm, f1, f2]


def make_envelope(opening: float, held: Optional[HeldObject] = None) -> Envelope:
    boxes = gripper_boxes(opening)
    if held is not None:
        boxes.append(Box(held.c, held.R, held.h, held.object_id, "held"))
    return Envelope(boxes, opening, held)


# ---------------------------------------------------------------- world ---


@dataclass
class World:
    """Static obstacles for one planning query."""

    boxes: list[Box] = field(default_factory=list)
    ground_z: float = 0.0  # table top

    def stacked(self, margin: float):
        c, R, h = stack(self.boxes)
        return c, R, h + margin

    def without(self, names: set[str]) -> "World":
        return World([b for b in self.boxes if b.name not in names], self.ground_z)

    def with_boxes(self, extra: list[Box]) -> "World":
        return World(list(self.boxes) + list(extra), self.ground_z)


@dataclass
class Hit:
    index: int  # first colliding sample
    obstacle: str  # obstacle name or "table"


def check_poses(world: World, env: Envelope, pos: np.ndarray, rots: np.ndarray,
                margin: float, ground_margin: float) -> Optional[Hit]:
    """Check a batch of gripper poses (S,3), (S,3,3). Returns the first hit.

    With a non-positive ``ground_margin`` (near-contact segments) the held
    object may rest on the table (it is grasped from, or released onto, a
    surface); the gripper itself must still clear the table by
    ``max(ground_margin, 0)``."""
    S = len(pos)
    eb = env.boxes
    E = len(eb)
    lc = np.array([b.c for b in eb])
    lR = np.array([b.R for b in eb])
    lh = np.array([b.h for b in eb])
    # world boxes of the envelope for every sample: (S*E, ...)
    wc = (np.einsum("sij,ej->sei", rots, lc) + pos[:, None, :]).reshape(S * E, 3)
    wR = np.einsum("sij,ejk->seik", rots, lR).reshape(S * E, 3, 3)
    wh = np.repeat(lh[None], S, axis=0).reshape(S * E, 3)
    first = None
    # ground: lowest point of each envelope box
    low = wc[:, 2] - np.einsum("nj,nj->n", np.abs(wR[:, 2, :]), wh)
    is_held = np.tile(np.array([b.group == "held" for b in eb]), S)
    gmarg = np.where(is_held & (ground_margin <= 0.0), ground_margin - 1e-6, max(ground_margin, 0.0))
    bad_g = np.nonzero(low < world.ground_z + gmarg)[0]
    if len(bad_g):
        first = Hit(int(bad_g[0] // E), "table")
    if world.boxes:
        oc, oR, oh = world.stacked(margin)
        # broad phase: obstacle AABBs against the AABB of all sampled envelope boxes
        oext = np.einsum("qij,qj->qi", np.abs(oR), oh)
        wext = np.einsum("nij,nj->ni", np.abs(wR), wh)
        lo, hi = (wc - wext).min(axis=0), (wc + wext).max(axis=0)
        keep = np.nonzero(np.all(oc + oext >= lo, axis=1) & np.all(oc - oext <= hi, axis=1))[0]
        if len(keep):
            ov = sat_overlap(wc, wR, wh, oc[keep], oR[keep], oh[keep])
            rows = np.nonzero(ov.any(axis=1))[0]
            if len(rows):
                s = int(rows[0] // E)
                if first is None or s < first.index:
                    j = int(keep[np.nonzero(ov[rows[0]])[0][0]])
                    first = Hit(s, world.boxes[j].name)
    return first


def segment_samples(p0, R0, p1, R1, reach: float, step: float):
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    ang = angle_between_mats(R0, R1)
    travel = float(np.linalg.norm(p1 - p0)) + reach * ang
    n = max(2, int(math.ceil(travel / max(step, 1e-4))) + 1)
    ts = np.linspace(0.0, 1.0, n)
    pos = p0[None] + ts[:, None] * (p1 - p0)[None]
    rots = slerp_mats(R0, R1, ts)
    return pos, rots


def check_segment(world: World, env: Envelope, p0, R0, p1, R1, *, margin: float,
                  ground_margin: float, step: Optional[float] = None) -> Optional[Hit]:
    step = margin if step is None else step
    pos, rots = segment_samples(p0, R0, p1, R1, env.reach, step)
    return check_poses(world, env, pos, rots, margin, ground_margin)
