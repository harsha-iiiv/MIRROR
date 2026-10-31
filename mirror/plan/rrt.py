"""Sampling-based fallback: RRT-Connect over TCP position, yaw and the
allowed discrete orientation modes, followed by shortcut smoothing.

Used only when no structured route candidate is feasible (e.g. extra box
obstacles crowd a slot). It uses the same collision model and margins as
the structured search. Randomness is seeded from the query, so identical
inputs give identical results; the iteration budget, not wall time, bounds
the search (a wall-time cap exists only as a safety stop and is reported
when it triggers).
"""
from __future__ import annotations

import hashlib
import math
import time

import numpy as np

from .collision import check_poses, check_segment
from .routes import Candidate, Query, RouteSearch, Waypoint, _simplify, path_cost
from .se3 import gripper_rot, wrap


def _seed(q: Query) -> int:
    key = "|".join([q.seed_key, q.scene.scene_id, str(q.cfg.rrt_seed),
                    ",".join(m.name for m in q.modes),
                    ",".join(f"{v:.6f}" for v in list(q.start_pos) + list(q.goal_pos))])
    return int(hashlib.sha256(key.encode()).hexdigest()[:12], 16)


def rrt_candidate(q: Query) -> Candidate:
    cfg = q.cfg
    rs = RouteSearch(q)
    modes = list(q.modes) or []
    if not modes:
        return Candidate("rrt", "rrt", False, reason="no orientation mode allowed")
    rng = np.random.default_rng(_seed(q))
    ws = q.scene.workspace
    lo = np.array([ws.x_range[0], ws.y_range[0], max(ws.z_range[0], rs.z_low)])
    hi = np.array([ws.x_range[1], ws.y_range[1], ws.z_range[1]])
    pitches = [m.pitch for m in modes]
    up = 0 if 0.0 in pitches else None
    if up is None:
        return Candidate("rrt", "rrt", False, reason="upright mode not allowed for grasp and release")
    up = pitches.index(0.0)

    def R_of(c):
        return gripper_rot(c[3], pitches[int(c[4])])

    def state_ok(c) -> bool:
        return check_poses(q.world, q.env, c[None, :3], R_of(c)[None], cfg.margin, cfg.ground_margin) is None

    def edge_ok(a, b) -> bool:
        return check_segment(q.world, q.env, a[:3], R_of(a), b[:3], R_of(b), margin=cfg.margin,
                             ground_margin=cfg.ground_margin) is None

    s_up = np.array([*q.start_pos[:2], max(q.start_pos[2], rs.z_low), q.start_yaw, up], float)
    gy = rs._goal_yaw_near(q.start_yaw)
    g_up = np.array([*q.goal_pos[:2], max(q.goal_pos[2], rs.z_low), gy, up], float)
    fm, fg = cfg.fine_margin, cfg.fine_ground_margin
    if check_segment(q.world, q.env, q.start_pos, R_of(s_up), s_up[:3], R_of(s_up), margin=fm, ground_margin=fg):
        return Candidate("rrt", "rrt", False, reason="cannot lift from the start pose")
    if check_segment(q.world, q.env, g_up[:3], R_of(g_up), q.goal_pos, R_of(g_up), margin=fm, ground_margin=fg):
        return Candidate("rrt", "rrt", False, reason="cannot descend to the goal pose")

    def dist(nodes: np.ndarray, c) -> np.ndarray:
        dp = np.linalg.norm(nodes[:, :3] - c[:3], axis=1)
        dy = np.abs(np.mod(nodes[:, 3] - c[3] + math.pi / 2, math.pi) - math.pi / 2)
        dm = (nodes[:, 4] != c[4]) * 0.1
        return dp + 0.05 * dy + dm

    def steer(a, b):
        d = b[:3] - a[:3]
        n = float(np.linalg.norm(d))
        dy = wrap(b[3] - a[3])
        f = 1.0 if n <= cfg.rrt_step else cfg.rrt_step / n
        c = a.copy()
        c[:3] = a[:3] + f * d
        c[3] = a[3] + f * dy
        return c, f >= 1.0

    trees = [([s_up], [-1]), ([g_up], [-1])]
    t0 = time.perf_counter()
    timed_out = False
    connect = None
    for it in range(cfg.rrt_iterations):
        if time.perf_counter() - t0 > cfg.rrt_time_budget_s:
            timed_out = True
            break
        ta, tb = trees[it % 2], trees[(it + 1) % 2]
        if rng.random() < cfg.rrt_goal_bias:
            sample = tb[0][0].copy()
        else:
            sample = np.array([*rng.uniform(lo, hi), rng.uniform(-math.pi / 2, math.pi / 2),
                               rng.integers(len(modes))], float)
        nodes = np.array(ta[0])
        i_near = int(np.argmin(dist(nodes, sample)))
        near = ta[0][i_near]
        if sample[4] != near[4] and rng.random() < cfg.rrt_mode_switch_prob:
            new = near.copy()
            new[4] = sample[4]
        else:
            sample[4] = near[4]
            new, _ = steer(near, sample)
        if not state_ok(new) or not edge_ok(near, new):
            continue
        ta[0].append(new)
        ta[1].append(i_near)
        # connect the other tree greedily towards the new node (same mode only)
        nodes_b = np.array(tb[0])
        same = np.nonzero(nodes_b[:, 4] == new[4])[0]
        if len(same) == 0:
            continue
        j = int(same[np.argmin(dist(nodes_b[same], new))])
        cur = j
        while True:
            c_node = tb[0][cur]
            nxt, reached = steer(c_node, new)
            if not state_ok(nxt) or not edge_ok(c_node, nxt):
                break
            tb[0].append(nxt)
            tb[1].append(cur)
            cur = len(tb[0]) - 1
            if reached:
                connect = (it % 2, len(ta[0]) - 1, cur)
                break
        if connect:
            break
    if connect is None:
        why = "time budget reached" if timed_out else f"{cfg.rrt_iterations} iterations"
        return Candidate("rrt", "rrt", False,
                         reason=f"sampling-based search found no path ({why}); not a proof of infeasibility")
    a_idx, ia, ib = connect

    def trace(tree, i):
        out = []
        while i >= 0:
            out.append(tree[0][i])
            i = tree[1][i]
        return out

    i0, i1 = (ia, ib) if a_idx == 0 else (ib, ia)
    path = trace(trees[0], i0)[::-1] + trace(trees[1], i1)
    path = [np.array(p) for p in path]
    # shortcut smoothing within one mode
    for _ in range(cfg.rrt_smoothing_iters):
        if len(path) < 3:
            break
        i, j = sorted(rng.choice(len(path), 2, replace=False))
        if j - i < 2:
            continue
        if any(path[k][4] != path[i][4] for k in range(i, j + 1)):
            continue
        if edge_ok(path[i], path[j]):
            path = path[: i + 1] + path[j:]
    wps = [Waypoint(q.start_pos.copy(), R_of(s_up), "start")]
    for c in path:
        wps.append(Waypoint(c[:3].copy(), R_of(c), ""))
    rg = gripper_rot(q.goal_yaw + math.pi * round((g_up[3] - q.goal_yaw) / math.pi))
    wps.append(Waypoint(q.goal_pos.copy(), rg, ""))
    wps = _simplify(wps)
    used = []
    for c in path:
        name = modes[int(c[4])].name
        if not used or used[-1] != name:
            used.append(name)
    tilt = max(modes[int(c[4])].tilt_deg for c in path)
    return Candidate("rrt:" + ">".join(used), "rrt", True, cost=path_cost(wps, cfg.rotation_penalty),
                     waypoints=wps, max_tilt_deg=tilt, mode=max(used, key=lambda n: abs(next(m.pitch for m in modes if m.name == n))),
                     detail={"iterations": it + 1, "nodes": len(trees[0][0]) + len(trees[1][0])})
