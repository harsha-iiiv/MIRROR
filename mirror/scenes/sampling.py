"""Randomised layouts for development and evaluation.

``sample_layout(seed, split)`` is deterministic in (seed, split). Development
and evaluation seeds are disjoint by construction: dev seeds are below
EVAL_SEED_BASE and eval seeds are at or above it, so evaluation layouts are
never seen during development.

What varies: wall position, which end of the wall is open (and how wide the
opening is), whether the wall has a slot and where (biased so that in many
layouts the slot is a shortcut for the cup and the open end is the long
way round), tray position, object
positions and yaw, and zero to two extra box obstacles. Every layout is
checked at sampling time for (1) a clear top-grasp envelope around every
object, (2) a clear tray, and (3) at least one route for the carried object
from the start region to the tray under the unconstrained task (around a
wall end, or through a slot with the object on its side). The route check
is a conservative 2-D occupancy search at carrying height, not a motion
plan.
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np

from mirror.contracts import ObstacleSpec, Pose, SceneState
from mirror.geometry import GRIPPER, TRAY, WORLD
from mirror.scenes.objects import make_block, make_box, make_cup, make_marker, make_tray, make_wall
from mirror.scenes.reference import default_gripper_start, default_workspace

EVAL_SEED_BASE = 10_000
SPLITS = ("dev", "eval", "blocked")

# Planar half extent of the gripper (palm) plus a carried object, for the
# route check, and the minimum free distance around an object for a grasp.
CARRY_RADIUS = 0.07
GRASP_CLEARANCE = 0.075
OBJECT_SPACING = 0.13
SLOT_PASS_MARGIN = GRIPPER.palm_half[1] + 0.01   # palm half length plus margin
_FAR = WORLD.workspace_y[1] + 0.10
_GRID = 0.01


def _check_seed(seed: int, split: str) -> None:
    if split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}, got {split!r}")
    if split == "dev" and seed >= EVAL_SEED_BASE:
        raise ValueError(f"dev seeds must be below {EVAL_SEED_BASE} (got {seed})")
    if split == "eval" and seed < EVAL_SEED_BASE:
        raise ValueError(f"eval seeds start at {EVAL_SEED_BASE} (got {seed})")


def _wall_extent(ob: ObstacleSpec) -> tuple[float, float, float]:
    """(x, y_min, y_max) of an axis-aligned wall in world coordinates."""
    x, y, _ = ob.pose.pos
    return x, y - ob.length / 2, y + ob.length / 2


def route_exists(scene: SceneState, start_xy, goal_xy) -> bool:
    """Conservative planar connectivity at carrying height.

    Walls block (inflated by CARRY_RADIUS) except where a slot is wide enough
    for the gripper to pass with the object on its side. Box obstacles are
    lower than the carrying height and do not block. The workspace boundary
    blocks.
    """
    ws = scene.workspace
    xs = np.arange(ws.x_range[0], ws.x_range[1] + 1e-9, _GRID)
    ys = np.arange(ws.y_range[0], ws.y_range[1] + 1e-9, _GRID)
    X, Y = np.meshgrid(xs, ys, indexing="ij")
    blocked = np.zeros(X.shape, dtype=bool)
    for ob in scene.obstacles:
        if ob.kind != "wall":
            continue
        wx, y0, y1 = _wall_extent(ob)
        band = (np.abs(X - wx) <= ob.thickness / 2 + CARRY_RADIUS) & (Y >= y0 - CARRY_RADIUS) & (Y <= y1 + CARRY_RADIUS)
        free = np.zeros_like(band)
        yc = ob.pose.pos[1]
        for s in ob.slots:
            lo, hi = yc + s.y_min + SLOT_PASS_MARGIN, yc + s.y_max - SLOT_PASS_MARGIN
            if hi > lo:
                free |= (Y >= lo) & (Y <= hi)
        blocked |= band & ~free
    def cell(p):
        return (int(round((p[0] - xs[0]) / _GRID)), int(round((p[1] - ys[0]) / _GRID)))
    s, g = cell(start_xy), cell(goal_xy)
    nx, ny = blocked.shape
    for c in (s, g):
        if not (0 <= c[0] < nx and 0 <= c[1] < ny) or blocked[c]:
            return False
    seen = np.zeros_like(blocked)
    q = deque([s])
    seen[s] = True
    while q:
        i, j = q.popleft()
        if (i, j) == g:
            return True
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            a, b = i + di, j + dj
            if 0 <= a < nx and 0 <= b < ny and not blocked[a, b] and not seen[a, b]:
                seen[a, b] = True
                q.append((a, b))
    return False


def _box_xy_dist(px, py, ob: ObstacleSpec) -> float:
    """Planar distance from a point to an axis-aligned (yaw-rotated) box footprint."""
    w, x, y, z = ob.pose.quat
    yaw = 2 * math.atan2(z, w)
    dx, dy = px - ob.pose.pos[0], py - ob.pose.pos[1]
    lx = math.cos(yaw) * dx + math.sin(yaw) * dy
    ly = -math.sin(yaw) * dx + math.cos(yaw) * dy
    ex = max(0.0, abs(lx) - ob.half[0])
    ey = max(0.0, abs(ly) - ob.half[1])
    return math.hypot(ex, ey)


def validate_layout(scene: SceneState) -> list[str]:
    """Problems that make a sampled layout unusable (empty list means valid)."""
    problems = []
    ws = scene.workspace
    tray = scene.trays[0]
    tx, ty = tray.pose.pos[:2]
    ohx = tray.inner_half[0] + tray.wall_thickness
    ohy = tray.inner_half[1] + tray.wall_thickness
    if not (ws.x_range[0] + 0.02 <= tx - ohx and tx + ohx <= ws.x_range[1] - 0.02
            and ws.y_range[0] + 0.02 <= ty - ohy and ty + ohy <= ws.y_range[1] - 0.02):
        problems.append("tray not fully inside the reachable workspace")
    boxes = [o for o in scene.obstacles if o.kind == "box"]
    walls = [o for o in scene.obstacles if o.kind == "wall"]
    for o in scene.objects:
        x, y = o.pose.pos[:2]
        if not (ws.x_range[0] + 0.03 <= x <= ws.x_range[1] - 0.03 and ws.y_range[0] + 0.03 <= y <= ws.y_range[1] - 0.03):
            problems.append(f"{o.id} outside workspace")
        for b in boxes:
            if _box_xy_dist(x, y, b) < GRASP_CLEARANCE:
                problems.append(f"{o.id} too close to {b.id} for a grasp")
        for w in walls:
            wx, y0, y1 = _wall_extent(w)
            if y0 - GRASP_CLEARANCE <= y <= y1 + GRASP_CLEARANCE and abs(x - wx) < w.thickness / 2 + GRASP_CLEARANCE + 0.03:
                problems.append(f"{o.id} too close to {w.id}")
        if abs(x - tx) < ohx + 0.04 and abs(y - ty) < ohy + 0.04:
            problems.append(f"{o.id} starts in or against the tray")
        if not route_exists(scene, (x, y), (tx, ty)):
            problems.append(f"no carrying route from {o.id} to the tray")
    for i, a in enumerate(scene.objects):
        for b in scene.objects[i + 1:]:
            if math.dist(a.pose.pos[:2], b.pose.pos[:2]) < OBJECT_SPACING:
                problems.append(f"{a.id} and {b.id} too close")
    for b in boxes:
        if _box_xy_dist(tx, ty, b) < max(ohx, ohy) + 0.03 or (abs(b.pose.pos[0] - tx) < ohx + b.half[0] + 0.04
                                                                and abs(b.pose.pos[1] - ty) < ohy + b.half[1] + 0.04):
            problems.append(f"{b.id} overlaps the tray")
        for w in walls:
            wx, y0, y1 = _wall_extent(w)
            if abs(b.pose.pos[0] - wx) < b.half[0] + w.thickness / 2 + 0.05 and y0 - 0.05 <= b.pose.pos[1] <= y1 + 0.05:
                problems.append(f"{b.id} against {w.id}")
    return problems


def sample_layout(seed: int, split: str) -> SceneState:
    """Deterministic random layout for ``split`` in {"dev", "eval", "blocked"}."""
    _check_seed(seed, split)
    rng = np.random.default_rng([seed, SPLITS.index(split), 0x6D6972])
    ws = default_workspace()
    for attempt in range(500):
        scene = _draw(rng, seed, split, ws)
        if not validate_layout(scene):
            note = scene.notes + f" (accepted after {attempt + 1} draw{'s' if attempt else ''})"
            return scene.model_copy(update={"notes": note})
    raise RuntimeError(f"could not sample a valid {split} layout for seed {seed}")


def _draw(rng: np.random.Generator, seed: int, split: str, ws) -> SceneState:
    u = rng.uniform
    wall_x = float(u(-0.04, 0.04))
    y_lo, y_hi = ws.y_range
    # Objects in the start region.
    objs = []
    kinds = [("cup_0", make_cup), ("block_0", make_block), ("marker_0", make_marker)]
    pts: list[tuple[float, float]] = []
    for oid, fn in kinds:
        for _ in range(200):
            x = float(u(ws.x_range[0] + 0.06, wall_x - 0.13))
            y = float(u(y_lo + 0.07, y_hi - 0.07))
            if all(math.dist((x, y), p) >= OBJECT_SPACING for p in pts):
                break
        pts.append((x, y))
        if oid.startswith("marker"):
            objs.append(fn(oid, x, y))
        else:
            objs.append(fn(oid, x, y, float(u(-math.pi / 4, math.pi / 4))))
    # Tray in the destination region.
    ohx = TRAY.inner_half[0] + TRAY.wall_thickness
    ohy = TRAY.inner_half[1] + TRAY.wall_thickness
    tx = float(u(wall_x + 0.04 + ohx, ws.x_range[1] - 0.03 - ohx))
    ty = float(u(y_lo + 0.03 + ohy, y_hi - 0.03 - ohy))
    tray = make_tray("tray_0", tx, ty)
    # Wall: open at one end unless blocked; optional slot.
    cup_y = pts[0][1]
    if split == "blocked":
        y0, y1 = -_FAR, _FAR
        has_slot = True
        side = 0
        gap = 0.0
    else:
        has_slot = bool(u() < 0.65)
        mid_y = 0.5 * (cup_y + ty)
        if has_slot and u() < 0.75:
            # Open end on the far side from the cup's line of travel, so that
            # going around is the long way and the slot is a real shortcut.
            side = -1 if mid_y > 0 else 1
        else:
            side = int(rng.choice([-1, 1]))
        gap = float(u(0.20, 0.30))
        if side > 0:
            y0, y1 = -_FAR, y_hi - gap
        else:
            y0, y1 = y_lo + gap, _FAR
    slots = []
    if has_slot:
        L = WORLD.slot_length
        lo_lim = max(y0, y_lo) + 0.02 + L / 2
        hi_lim = min(y1, y_hi) - 0.02 - L / 2
        if side > 0:
            hi_lim = min(hi_lim, y1 - 0.12 - L / 2)
        elif side < 0:
            lo_lim = max(lo_lim, y0 + 0.12 + L / 2)
        if hi_lim > lo_lim:
            if u() < 0.7:   # put the slot on the cup's direct line to the tray
                c = 0.5 * (cup_y + ty)
            else:
                c = float(u(lo_lim, hi_lim))
            c = min(max(c, lo_lim), hi_lim)
            slots.append((c - L / 2, c + L / 2))
    wall = make_wall("wall_0", wall_x, y0, y1, slots=slots)
    obstacles = [wall]
    n_boxes = 0 if split == "blocked" else int(rng.choice([0, 1, 2], p=[0.4, 0.4, 0.2]))
    for k in range(n_boxes):
        half = (float(u(0.03, 0.06)), float(u(0.03, 0.06)), float(u(0.03, 0.06)))
        region = rng.choice([-1, 1])
        if region < 0:
            bx = float(u(ws.x_range[0] + half[0], wall_x - 0.08 - half[0]))
        else:
            bx = float(u(wall_x + 0.08 + half[0], ws.x_range[1] - half[0]))
        by = float(u(y_lo + half[1], y_hi - half[1]))
        obstacles.append(make_box(f"box_{k}", bx, by, half, float(u(-math.pi / 2, math.pi / 2))))
    desc = []
    desc.append("wall spans the workspace" if split == "blocked" else
                f"wall open at its {'+' if side > 0 else '-'}y end ({gap:.2f} m)")
    desc.append(f"slot at y {slots[0][0]:+.2f}..{slots[0][1]:+.2f}" if slots else "no slot")
    desc.append(f"{n_boxes} extra box{'es' if n_boxes != 1 else ''}")
    return SceneState(scene_id=f"{split}_{seed}", seed=seed, split=split, objects=objs, trays=[tray],
                      obstacles=obstacles, workspace=ws, gripper_start=default_gripper_start(),
                      notes="Sampled layout: " + ", ".join(desc) + ".")
