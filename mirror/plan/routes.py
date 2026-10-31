"""Route search for one gripper motion (a transport or an empty transit).

A query asks for a collision-free motion from a start pose S (e.g. the grasp
pose) to a goal pose G (e.g. the place pose). Candidates are enumerated by
family and labelled:

  direct                          straight lines between the S and G columns
  around:<wall>:+y | -y           past one end of a wall that separates S and G
  through_slot:<wall>:<k>:<mode>  through slot k of that wall in an orientation mode
  detour                          visibility graph around extra obstacles
  rrt:<modes>                     sampling-based fallback (see rrt.py)

Free-space motion happens upright at a set of horizontal levels on a graph of
xy nodes (S, G, slot approach points, inflated obstacle corners). Edges are
checked lazily with the exact swept OBB test. In free space every wall is
treated as solid (slots closed), so the only way through a slot is the slot
portal: rotate in front of the slot, pass straight through it, rotate back.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..contracts import ObstacleSpec, SceneState
from .collision import Box, Envelope, World, check_poses, check_segment, segment_samples
from .config import UPRIGHT, Mode, PlannerConfig
from .se3 import angle_between_mats, gripper_rot, rot_z, wrap

INF = float("inf")


@dataclass
class Waypoint:
    pos: np.ndarray
    R: np.ndarray
    tag: str = ""  # "lift", "transport", "place", "rotate", "slot"


@dataclass
class Candidate:
    label: str
    family: str
    feasible: bool
    cost: Optional[float] = None
    waypoints: list[Waypoint] = field(default_factory=list)
    reason: Optional[str] = None
    max_tilt_deg: float = 0.0
    mode: str = "upright"
    detail: dict = field(default_factory=dict)


@dataclass
class Query:
    world: World                # real static world (slots open), held object excluded
    env: Envelope
    start_pos: np.ndarray
    start_yaw: float
    goal_pos: np.ndarray
    goal_yaw: float
    modes: list[Mode]           # orientation modes allowed for this motion
    banned: dict                # mode name -> rejection reason (constraint id ...)
    scene: SceneState
    cfg: PlannerConfig
    start_fine: bool = True     # vertical moves at S use the fine margin
    goal_fine: bool = True
    seed_key: str = ""


# ------------------------------------------------------------ helpers ---


def wall_frame(ob: ObstacleSpec):
    from .se3 import quat_to_mat
    R = quat_to_mat(ob.pose.quat)
    return np.array(ob.pose.pos, float), R


def wall_yaw(ob: ObstacleSpec) -> float:
    _, R = wall_frame(ob)
    a = R[:, 1]
    return math.atan2(-a[0], a[1])


def closed_wall_box(ob: ObstacleSpec) -> Box:
    p, R = wall_frame(ob)
    H = float(ob.height)
    return Box(p + R @ np.array([0.0, 0.0, H / 2]), R,
               np.array([ob.thickness / 2, ob.length / 2, H / 2], float), f"obstacle:{ob.id}", "wall")


def end_blocker(ob: ObstacleSpec, end: str, far: float = 3.0) -> Box:
    """Virtual box closing the ``end`` ("+y" or "-y") of a wall out to ``far``."""
    p, R = wall_frame(ob)
    H, L = float(ob.height), float(ob.length)
    s = 1.0 if end == "+y" else -1.0
    yc = s * (L / 2 + far / 2)
    return Box(p + R @ np.array([0.0, yc, H / 2]), R,
               np.array([ob.thickness / 2, far / 2 + 0.01, H / 2], float),
               f"virtual:{ob.id}:{end}", "virtual")


def separates(ob: ObstacleSpec, a_xy, b_xy, reach: float = 0.0) -> bool:
    """Does the straight segment a->b cross the wall (its length extended by
    ``reach`` at both ends)?"""
    p, R = wall_frame(ob)
    n, al = R[:2, 0], R[:2, 1]
    da = float(n @ (np.asarray(a_xy) - p[:2]))
    db = float(n @ (np.asarray(b_xy) - p[:2]))
    t = ob.thickness / 2
    if not ((da < -t and db > t) or (da > t and db < -t)):
        return False
    f = da / (da - db)
    cross = np.asarray(a_xy) + f * (np.asarray(b_xy) - np.asarray(a_xy))
    yy = float(al @ (cross - p[:2]))
    return abs(yy) <= ob.length / 2 + reach


def side_of(ob: ObstacleSpec, xy) -> float:
    p, R = wall_frame(ob)
    return float(R[:2, 0] @ (np.asarray(xy) - p[:2]))


def seg_hits_box_2d(p0: np.ndarray, p1: np.ndarray, box: Box) -> np.ndarray:
    """Vectorised: do 2D segments p0[i]->p1[i] cross the box footprint?"""
    Rb = box.R[:2, :2]
    q0 = (p0 - box.c[:2]) @ Rb
    q1 = (p1 - box.c[:2]) @ Rb
    d = q1 - q0
    t0 = np.zeros(len(p0))
    t1 = np.ones(len(p0))
    ok = np.ones(len(p0), bool)
    for k in range(2):
        h = box.h[k]
        dk, qk = d[:, k], q0[:, k]
        par = np.abs(dk) < 1e-12
        ok &= ~(par & (np.abs(qk) > h))
        with np.errstate(divide="ignore", invalid="ignore"):
            ta = (-h - qk) / dk
            tb = (h - qk) / dk
        lo = np.where(par, -INF, np.minimum(ta, tb))
        hi = np.where(par, INF, np.maximum(ta, tb))
        t0 = np.maximum(t0, lo)
        t1 = np.minimum(t1, hi)
    return ok & (t0 <= t1)


def axis_bands(env: Envelope) -> list[tuple[float, float]]:
    """Height bands (relative to the TCP, upright) where the envelope is solid
    directly on the TCP's vertical axis: the palm, and the held object's
    floor (cup) or whole body (block, marker)."""
    bands = []
    for b in env.boxes:
        if b.name == "palm":
            bands.append((b.c[2] - b.h[2], b.c[2] + b.h[2]))
        elif b.group == "held":
            lo, hi = b.c[2] - b.h[2], b.c[2] + b.h[2]
            if "cup" in b.name:
                hi = lo + 0.003
            bands.append((lo, hi))
    return bands


# ------------------------------------------------------- free graph ---


class FreeGraph:
    """Upright free-space motion at fixed levels over a set of xy nodes."""

    def __init__(self, q: Query, world_closed: World, xy_nodes: list, levels: list[float],
                 S: np.ndarray, G: np.ndarray):
        self.q = q
        self.world = world_closed
        self.levels = levels
        self.xy = [np.asarray(p, float) for p in xy_nodes]
        self.S, self.G = np.asarray(S, float), np.asarray(G, float)
        nxy, nl = len(self.xy), len(levels)
        self.nxy, self.nl = nxy, nl
        # node index: level-major; then S and G
        self.pos = np.array([[p[0], p[1], z] for z in levels for p in self.xy] + [self.S, self.G])
        self.iS, self.iG = nxy * nl, nxy * nl + 1
        V = len(self.pos)
        self.V = V
        C = np.full((V, V), INF)
        bands = axis_bands(q.env)
        self.prune_hits: Counter = Counter()
        xy = np.array(self.xy)
        for li, z in enumerate(levels):
            base = li * nxy
            ii, jj = np.triu_indices(nxy, 1)
            d = np.linalg.norm(xy[ii] - xy[jj], axis=1)
            blocked = np.zeros(len(ii), bool)
            for b in world_closed.boxes:
                blo, bhi = b.z_range()
                if not any(blo < z + bb[1] and bhi > z + bb[0] for bb in bands):
                    continue
                hit = seg_hits_box_2d(xy[ii], xy[jj], b)
                newly = hit & ~blocked
                if newly.any():
                    self.prune_hits[b.name] += int(newly.sum())
                blocked |= hit
            dd = np.where(blocked, INF, d)
            C[base + ii, base + jj] = dd
            C[base + jj, base + ii] = dd
        # vertical edges between consecutive levels at every xy node
        for li in range(nl - 1):
            dz = abs(levels[li + 1] - levels[li])
            for k in range(nxy):
                a, b = li * nxy + k, (li + 1) * nxy + k
                C[a, b] = C[b, a] = dz
        # S and G columns
        self.s_col = self._column(self.S)
        self.g_col = self._column(self.G)
        for li in self.s_col:
            C[self.iS, li] = C[li, self.iS] = abs(self.pos[li][2] - self.S[2])
        for li in self.g_col:
            C[self.iG, li] = C[li, self.iG] = abs(self.pos[li][2] - self.G[2])
        self.C0 = C
        self.cache: dict = {}
        self.hits: Counter = Counter()

    def _column(self, p) -> list[int]:
        out = []
        for k, q in enumerate(self.xy):
            if np.linalg.norm(q - p[:2]) < 1e-9:
                out += [li * self.nxy + k for li in range(self.nl)]
        return out

    def node(self, xy_index: int, level_index: int) -> int:
        return level_index * self.nxy + xy_index

    # exact edge check -------------------------------------------------
    def edge_ok(self, i: int, j: int, yaw: float, goal_yaw: Optional[float]) -> bool:
        key = (min(i, j), max(i, j), round(yaw, 9), None if goal_yaw is None else round(goal_yaw, 9))
        if key in self.cache:
            return self.cache[key]
        cfg = self.q.cfg
        R = gripper_rot(yaw)
        fine = (self.iS in (i, j) and self.q.start_fine) or (self.iG in (i, j) and self.q.goal_fine)
        m = cfg.fine_margin if fine else cfg.margin
        gm = cfg.fine_ground_margin if fine else cfg.ground_margin
        p0, p1 = self.pos[i], self.pos[j]
        hit = None
        if self.iG in (i, j) and goal_yaw is not None and abs(wrap(goal_yaw - yaw)) > 1e-9:
            top = p0 if j == self.iG else p1
            Rg = gripper_rot(goal_yaw)
            hit = check_segment(self.world, self.q.env, top, R, top, Rg, margin=cfg.margin,
                                ground_margin=cfg.ground_margin)
            if hit is None:
                hit = check_segment(self.world, self.q.env, top, Rg, self.G, Rg, margin=m, ground_margin=gm)
        else:
            hit = check_segment(self.world, self.q.env, p0, R, p1, R, margin=m, ground_margin=gm)
        ok = hit is None
        if not ok:
            self.hits[hit.obstacle] += 1
        self.cache[key] = ok
        return ok

    def shortest(self, src: int, targets: list[int], yaw: float, goal_yaw: Optional[float] = None,
                 target_extra: Optional[dict] = None):
        """Lazy Dijkstra. Returns (cost, node path) or (None, None)."""
        C = self.C0.copy()
        lam = self.q.cfg.rotation_penalty
        if goal_yaw is not None:
            rot = abs(wrap(goal_yaw - yaw))
            # yaw symmetric gripper: caller passes the nearest equivalent goal yaw
            C[:, self.iG] = np.where(np.isfinite(C[:, self.iG]), C[:, self.iG] + lam * rot, INF)
        extra = np.zeros(self.V)
        if target_extra:
            for k, v in target_extra.items():
                extra[k] = v
        tset = set(targets)
        for _ in range(self.q.cfg.lazy_iterations):
            cost, path = _dijkstra(C, src, tset, extra)
            if path is None:
                return None, None
            bad = False
            for a, b in zip(path[:-1], path[1:]):
                if not self.edge_ok(a, b, yaw, goal_yaw if self.iG in (a, b) else None):
                    C[a, b] = C[b, a] = INF
                    bad = True
                    break
            if not bad:
                return cost, path
        return None, None

    def blockers(self) -> list[str]:
        c = self.hits + self.prune_hits
        return [k for k, _ in c.most_common(3)]


def _dijkstra(C: np.ndarray, src: int, targets: set, extra: np.ndarray):
    V = len(C)
    dist = np.full(V, INF)
    prev = np.full(V, -1, int)
    done = np.zeros(V, bool)
    dist[src] = 0.0
    while True:
        cand = np.where(done, INF, dist)
        u = int(np.argmin(cand))
        if not np.isfinite(cand[u]):
            return None, None
        if u in targets:
            # targets carry an extra terminal cost; pick the best settled target
            break
        done[u] = True
        nd = dist[u] + C[u]
        better = (nd < dist) & ~done
        dist[better] = nd[better]
        prev[better] = u
    # among targets, choose min dist+extra (dist is final for settled-or-better nodes);
    # run to completion over targets for correctness with extra costs
    best, bt = INF, -1
    # finish Dijkstra fully when extra costs differ
    if np.any(extra[list(targets)] != 0):
        while True:
            cand = np.where(done, INF, dist)
            u = int(np.argmin(cand))
            if not np.isfinite(cand[u]):
                break
            done[u] = True
            nd = dist[u] + C[u]
            better = (nd < dist) & ~done
            dist[better] = nd[better]
            prev[better] = u
    for t in targets:
        if dist[t] + extra[t] < best:
            best, bt = dist[t] + extra[t], t
    if bt < 0:
        return None, None
    path = [bt]
    while path[-1] != src:
        path.append(int(prev[path[-1]]))
    return float(best), path[::-1]


# -------------------------------------------------------- the search ---


class RouteSearch:
    def __init__(self, q: Query):
        self.q = q
        cfg = q.cfg
        self.walls = [ob for ob in q.scene.obstacles if ob.kind == "wall"]
        ws = q.scene.workspace
        self.ws = ws
        R0 = gripper_rot(0.0)
        lo, hi = q.env.vertical_extent(R0)
        self.lo, self.hi = lo, hi
        self.r_xy = q.env.horizontal_radius(R0)
        # closed world: walls without slots, plus everything else
        others = [b for b in q.world.boxes if b.group != "wall"]
        self.closed = World(others + [closed_wall_box(w) for w in self.walls], q.world.ground_z)
        tops = [b.z_range()[1] for b in q.world.boxes if b.group in ("object",) or b.group.startswith("tray")]
        tray_tops = [b.z_range()[1] for b in q.world.boxes if b.group.startswith("tray")]
        self.h_up = self._clamp_z(max(tops + [0.0]) - lo + 2 * cfg.margin)
        self.z_low = self._clamp_z(max(tray_tops + [0.0]) - lo + 2 * cfg.margin)

    def _clamp_z(self, z: float) -> float:
        return float(min(max(z, self.ws.z_range[0]), self.ws.z_range[1]))

    def in_ws_xy(self, p) -> bool:
        ws = self.ws
        return ws.x_range[0] <= p[0] <= ws.x_range[1] and ws.y_range[0] <= p[1] <= ws.y_range[1]

    def in_ws(self, p) -> bool:
        return self.in_ws_xy(p) and self.ws.z_range[0] - 1e-9 <= p[2] <= self.ws.z_range[1] + 1e-9

    def levels(self, extra: list[float]) -> list[float]:
        zs = sorted(set([self.z_low, self.h_up] + [self._clamp_z(z) for z in extra]))
        out: list[float] = []
        for z in zs:
            if not out or z - out[-1] > self.q.cfg.level_dedupe:
                out.append(z)
        return out

    def corners(self, world: World, levels: list[float]) -> list[np.ndarray]:
        r = self.r_xy + self.q.cfg.margin + 0.006
        zmin = min(levels) + self.lo
        pts: list[np.ndarray] = []
        for b in world.boxes:
            if b.z_range()[1] <= zmin:
                continue
            inf = Box(b.c, b.R, b.h + np.array([r, r, 0.0]), b.name, b.group)
            for c in inf.footprint_corners():
                if b.group == "virtual" and not self.in_ws_xy(c):
                    continue
                if self.in_ws_xy(c) and all(np.linalg.norm(c - p) > 0.01 for p in pts):
                    pts.append(c)
        return pts

    # ---------------------------------------------------------- legs
    def _path_waypoints(self, g: FreeGraph, path: list[int], yaw: float, goal_yaw=None) -> list[Waypoint]:
        out = []
        R = gripper_rot(yaw)
        for a, b in zip(path[:-1], path[1:]):
            if b == g.iG and goal_yaw is not None and abs(wrap(goal_yaw - yaw)) > 1e-9:
                out.append(Waypoint(g.pos[a].copy(), gripper_rot(goal_yaw), "rotate"))
                R = gripper_rot(goal_yaw)
            out.append(Waypoint(g.pos[b].copy(), R, ""))
        return out

    def free_candidate(self, label: str, family: str, world: World, xy_nodes, levels) -> Candidate:
        q = self.q
        g = FreeGraph(q, world, xy_nodes, levels, q.start_pos, q.goal_pos)
        gy = self._goal_yaw_near(q.start_yaw)
        cost, path = g.shortest(g.iS, [g.iG], q.start_yaw, gy)
        if path is None:
            bl = g.blockers()
            return Candidate(label, family, False, reason="no collision-free path within the workspace"
                             + (f" (blocked by {', '.join(bl)})" if bl else ""))
        wps = [Waypoint(q.start_pos.copy(), gripper_rot(q.start_yaw), "start")] + \
            self._path_waypoints(g, path, q.start_yaw, gy)
        return Candidate(label, family, True, cost=cost, waypoints=_simplify(wps))

    def _goal_yaw_near(self, yaw: float) -> float:
        # the gripper (and every supported grasp) is symmetric under a half turn
        gy = self.q.goal_yaw
        k = round((yaw - gy) / math.pi)
        return gy + k * math.pi

    # ------------------------------------------------------- families
    def run(self) -> list[Candidate]:
        q = self.q
        s_xy, g_xy = q.start_pos[:2], q.goal_pos[:2]
        reach = self.r_xy + q.cfg.margin
        sep = [w for w in self.walls if separates(w, s_xy, g_xy, reach)]
        cands: list[Candidate] = []
        base_levels = self.levels([])
        # direct
        cands.append(self.free_candidate("direct", "direct", self.closed, [s_xy, g_xy], base_levels))
        for w in sep:
            for end in ("+y", "-y"):
                other = "-y" if end == "+y" else "+y"
                world = self.closed.with_boxes([end_blocker(w, other)])
                nodes = [s_xy, g_xy] + self.corners(world, base_levels)
                cands.append(self.free_candidate(f"around:{w.id}:{end}", "around", world, nodes, base_levels))
            for k, slot in enumerate(w.slots):
                for mode in q.cfg.modes:
                    cands.append(self.slot_candidate(w, k, mode))
        if not sep:
            others = [c for c in cands if c.feasible]
            if not others:
                nodes = [s_xy, g_xy] + self.corners(self.closed, base_levels)
                cands.append(self.free_candidate("detour", "detour", self.closed, nodes, base_levels))
        return cands

    def slot_candidate(self, w: ObstacleSpec, k: int, mode: Mode) -> Candidate:
        q, cfg = self.q, self.q.cfg
        label = f"through_slot:{w.id}:{k}:{mode.name}"
        if mode.name in q.banned:
            return Candidate(label, "through_slot", False, reason=q.banned[mode.name],
                             max_tilt_deg=mode.tilt_deg, mode=mode.name)
        if all(m.name != mode.name for m in q.modes):
            return Candidate(label, "through_slot", False, reason="orientation mode not allowed",
                             max_tilt_deg=mode.tilt_deg, mode=mode.name)
        slot = w.slots[k]
        p_w, R_w = wall_frame(w)
        n, a = R_w[:, 0], R_w[:, 1]
        wyaw = wall_yaw(w)
        pyaw = wyaw + math.pi * round((q.start_yaw - wyaw) / math.pi)
        Rm = gripper_rot(pyaw, mode.pitch)
        lo, hi = q.env.extent_along(Rm, np.array([0.0, 0.0, 1.0]))
        alo, ahi = q.env.extent_along(Rm, a)
        height, width = hi - lo, ahi - alo
        open_h, open_w = slot.z_max - slot.z_min, slot.y_max - slot.y_min
        if height + 2 * cfg.margin > open_h:
            return Candidate(label, "through_slot", False, mode=mode.name, max_tilt_deg=mode.tilt_deg,
                             reason=(f"envelope height {height:.3f} m plus 2 x {cfg.margin:.3f} m margin "
                                     f"exceeds slot opening {open_h:.3f} m"),
                             detail={"envelope_height": height, "slot_height": open_h})
        if width + 2 * cfg.margin > open_w:
            return Candidate(label, "through_slot", False, mode=mode.name, max_tilt_deg=mode.tilt_deg,
                             reason=(f"envelope width {width:.3f} m plus margins exceeds slot length {open_w:.3f} m"))
        z_p = 0.5 * (slot.z_min + slot.z_max) - 0.5 * (lo + hi)
        y_mid = 0.5 * (slot.y_min + slot.y_max) - 0.5 * (alo + ahi)
        y_free = 0.5 * (open_w - width) - cfg.margin   # lateral freedom of the pass line
        offs = [0.0]
        j = 1
        while j * 0.01 <= y_free + 1e-9:
            offs += [j * 0.01, -j * 0.01]
            j += 1
        # rotation sweep extents along the wall normal
        Ru = gripper_rot(pyaw, 0.0)
        ts = np.linspace(0.0, 1.0, 19)
        from .se3 import slerp_mats
        sweep = slerp_mats(Ru, Rm, ts)
        nlo = min(q.env.extent_along(R, n)[0] for R in sweep)
        nhi = max(q.env.extent_along(R, n)[1] for R in sweep)
        t2 = w.thickness / 2
        s_side = 1.0 if side_of(w, q.start_pos[:2]) > 0 else -1.0
        d_neg = -t2 - cfg.margin - cfg.slot_extra_clearance - nhi
        d_pos = t2 + cfg.margin + cfg.slot_extra_clearance - nlo
        A_n, B_n = (d_neg, d_pos) if s_side < 0 else (d_pos, d_neg)
        best = None
        for off in offs:
            c = self._slot_through(w, k, mode, label, p_w, n, a, y_mid + off, z_p, A_n, B_n, pyaw, Ru, Rm,
                                   height, open_h)
            if c.feasible:
                return c
            if best is None:
                best = c
        return best

    def _slot_through(self, w, k, mode, label, p_w, n, a, y_p, z_p, A_n, B_n, pyaw, Ru, Rm, height, open_h):
        q, cfg = self.q, self.q.cfg
        base = p_w + a * y_p
        A = base + n * A_n
        B = base + n * B_n
        A[2] = B[2] = z_p
        if not (self.in_ws(A) and self.in_ws(B)):
            return Candidate(label, "through_slot", False, mode=mode.name, max_tilt_deg=mode.tilt_deg,
                             reason="slot approach points lie outside the workspace")
        levels = self.levels([z_p])
        if not any(abs(z - z_p) < 1e-9 for z in levels):
            levels = sorted([z for z in levels if abs(z - z_p) > cfg.level_dedupe / 2] + [z_p])
        li_p = levels.index(z_p)
        upper = [i for i in range(len(levels)) if levels[i] >= z_p - 1e-9]  # pass level first, then higher
        Rin = gripper_rot(q.start_yaw)

        def seg(pa, Ra, pb, Rb):
            return check_segment(q.world, q.env, pa, Ra, pb, Rb, margin=cfg.margin, ground_margin=cfg.ground_margin)

        # passage itself (pitched or not), in the real world with the slot open
        hit = seg(A, Rm, B, Rm)
        if hit is not None:
            return Candidate(label, "through_slot", False, mode=mode.name, max_tilt_deg=mode.tilt_deg,
                             reason=f"slot passage collides with {hit.obstacle}")
        # entry: rotate at A at the lowest level (>= pass level) where the rotation is free,
        # then move down to the pass height in the slot orientation
        entry, last_hit = None, None
        for li in upper:
            Ae = A.copy()
            Ae[2] = levels[li]
            h = seg(Ae, Rin, Ae, Ru) or seg(Ae, Ru, Ae, Rm) or seg(Ae, Rm, A, Rm)
            if h is None:
                entry = li
                break
            last_hit = h
        if entry is None:
            return Candidate(label, "through_slot", False, mode=mode.name, max_tilt_deg=mode.tilt_deg,
                             reason=f"no free space to turn in front of the slot (collides with {last_hit.obstacle})")
        exit_, last_hit = None, None
        for li in upper:
            Bx = B.copy()
            Bx[2] = levels[li]
            h = seg(B, Rm, Bx, Rm) or seg(Bx, Rm, Bx, Ru)
            if h is None:
                exit_ = li
                break
            last_hit = h
        if exit_ is None:
            return Candidate(label, "through_slot", False, mode=mode.name, max_tilt_deg=mode.tilt_deg,
                             reason=f"no free space to turn behind the slot (collides with {last_hit.obstacle})")
        Ae = A.copy(); Ae[2] = levels[entry]
        Bx = B.copy(); Bx[2] = levels[exit_]
        # leg 1: S -> A at the entry level (upright, start yaw)
        nodes1 = [q.start_pos[:2], A[:2]] + self.corners(self.closed, levels)
        q1 = Query(**{**q.__dict__, "goal_pos": Ae, "goal_fine": False})
        g1 = FreeGraph(q1, self.closed, nodes1, levels, q.start_pos, Ae)
        c1, p1 = g1.shortest(g1.iS, [g1.node(1, entry)], q.start_yaw)
        if p1 is None:
            bl = g1.blockers()
            return Candidate(label, "through_slot", False, mode=mode.name, max_tilt_deg=mode.tilt_deg,
                             reason="no path to the slot approach point" + (f" (blocked by {', '.join(bl)})" if bl else ""))
        rot = abs(wrap(pyaw - q.start_yaw)) + 2 * abs(mode.pitch)
        c_portal = (float(np.linalg.norm(B - A)) + abs(Ae[2] - A[2]) + abs(Bx[2] - B[2])
                    + cfg.rotation_penalty * rot)
        q2 = Query(**{**q.__dict__, "start_pos": Bx, "start_yaw": pyaw, "start_fine": False})
        nodes2 = [B[:2], q.goal_pos[:2]] + self.corners(self.closed, levels)
        g2 = FreeGraph(q2, self.closed, nodes2, levels, Bx, q.goal_pos)
        gy = q.goal_yaw + math.pi * round((pyaw - q.goal_yaw) / math.pi)
        c2, p2 = g2.shortest(g2.node(0, exit_), [g2.iG], pyaw, gy)
        if p2 is None:
            bl = g2.blockers()
            return Candidate(label, "through_slot", False, mode=mode.name, max_tilt_deg=mode.tilt_deg,
                             reason="no path from the slot to the goal" + (f" (blocked by {', '.join(bl)})" if bl else ""))
        wps = [Waypoint(q.start_pos.copy(), Rin, "start")]
        wps += self._path_waypoints(g1, p1, q.start_yaw)
        if abs(wrap(pyaw - q.start_yaw)) > 1e-9:
            wps.append(Waypoint(Ae.copy(), Ru, "rotate"))
        if mode.pitch != 0.0:
            wps.append(Waypoint(Ae.copy(), Rm, "rotate"))
        wps.append(Waypoint(A.copy(), Rm, "slot"))
        wps.append(Waypoint(B.copy(), Rm, "slot"))
        wps.append(Waypoint(Bx.copy(), Rm, "slot"))
        if mode.pitch != 0.0:
            wps.append(Waypoint(Bx.copy(), Ru, "rotate"))
        wps += self._path_waypoints(g2, p2, pyaw, gy)
        cost = c1 + c_portal + c2
        return Candidate(label, "through_slot", True, cost=cost, waypoints=_simplify(wps),
                         max_tilt_deg=mode.tilt_deg, mode=mode.name,
                         detail={"slot_pass_z": z_p, "envelope_height": height, "slot_height": open_h,
                                 "A": A.tolist(), "B": B.tolist(), "pass_offset_along_wall": float(y_p),
                                 "turn_in_z": float(Ae[2]),
                                 "turn_out_z": float(Bx[2])})


def _simplify(wps: list[Waypoint]) -> list[Waypoint]:
    """Drop duplicate waypoints and merge collinear translations with equal orientation."""
    out: list[Waypoint] = []
    for w in wps:
        if out and np.linalg.norm(w.pos - out[-1].pos) < 1e-9 and angle_between_mats(w.R, out[-1].R) < 1e-9:
            continue
        if len(out) >= 2:
            a, b = out[-2], out[-1]
            same_rot = angle_between_mats(a.R, b.R) < 1e-9 and angle_between_mats(b.R, w.R) < 1e-9
            d1, d2 = b.pos - a.pos, w.pos - b.pos
            n1, n2 = np.linalg.norm(d1), np.linalg.norm(d2)
            if same_rot and n1 > 1e-9 and n2 > 1e-9 and np.dot(d1, d2) / (n1 * n2) > 1 - 1e-9 \
                    and b.tag not in ("start",):
                out[-1] = Waypoint(w.pos.copy(), w.R, w.tag)
                continue
        out.append(w)
    return out


def path_cost(wps: list[Waypoint], lam: float) -> float:
    c = 0.0
    for a, b in zip(wps[:-1], wps[1:]):
        c += float(np.linalg.norm(b.pos - a.pos)) + lam * angle_between_mats(a.R, b.R)
    return c
