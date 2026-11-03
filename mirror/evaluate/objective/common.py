"""Shared helpers: the specification pair, route features and phase timing."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Optional

import numpy as np

from mirror.contracts import PlanResult, Pose, SceneState, TaskSpec

REPO_ROOT = Path(__file__).resolve().parents[3]
MUSE_CASSETTES = REPO_ROOT / "data" / "cassettes_muse"
MUSE_MODEL = "muse-spark-1.3"
SWEEP = (0.05, 0.10, 0.20, 0.40, 1.0)
CUP = "cup_0"


SWEEP_KEYS = {0.05: "0.05", 0.10: "0.10", 0.20: "0.20", 0.40: "0.40", 1.0: "1.0"}


def lam_key(lam: float) -> str:
    """Stable text key for a rotation price: the audit's names for the sweep
    values ("0.05", "0.10", ...), four significant digits otherwise."""
    for v, k in SWEEP_KEYS.items():
        if abs(float(lam) - v) < 1e-12:
            return k
    return f"{float(lam):.4g}"


def spec_pair() -> tuple[TaskSpec, TaskSpec, dict]:
    """The Muse registration's pair (replayed recordings, offline agreement checked)."""
    from mirror.evaluate.specs import evaluation_specs
    return evaluation_specs(source="replay", model=MUSE_MODEL, cassette_dir=MUSE_CASSETTES)


def config_with(lam: Optional[float] = None, **kw):
    from mirror.plan.config import DEFAULT_CONFIG
    if lam is not None:
        kw["rotation_penalty"] = float(lam)
    return replace(DEFAULT_CONFIG, **kw)


def scene_sha(scene: SceneState) -> str:
    return hashlib.sha256(scene.model_dump_json().encode()).hexdigest()


def plan_sha(plan: PlanResult) -> str:
    """Hash of what the executor receives (steps only)."""
    data = json.dumps([s.model_dump(mode="json") for s in plan.steps], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()


def transport_note(plan: PlanResult, obj: str = CUP) -> dict:
    """Route label, mode and planned tilt of ``obj``'s transport step."""
    for s in plan.steps:
        if s.kind == "transport" and s.object_id == obj:
            parts = dict(p.strip().split("=", 1) for p in s.note.split(";") if "=" in p)
            return dict(route=parts.get("route", ""), mode=parts.get("mode", ""),
                        max_tilt_deg=float(parts.get("max_tilt_deg", "nan")))
    return dict(route="", mode="", max_tilt_deg=float("nan"))


def tilted(note: dict) -> bool:
    return bool(note["mode"]) and note["mode"] != "upright"


def _qangle(q0, q1) -> float:
    d = abs(float(np.dot(np.asarray(q0, float), np.asarray(q1, float))))
    return 2.0 * math.acos(min(1.0, d))


def path_features(poses: list[Pose]) -> dict:
    """Translation length (m), rotation (rad) and segment count of a pose
    sequence; rotation is the geodesic angle between consecutive gripper
    orientations, as in the planner's route cost."""
    L = R = 0.0
    n = 0
    for p0, p1 in zip(poses[:-1], poses[1:]):
        L += float(np.linalg.norm(np.asarray(p1.pos) - np.asarray(p0.pos)))
        R += _qangle(p0.quat, p1.quat)
        n += 1
    return dict(length_m=L, rotation_rad=R, segments=n)


def route_segments(plan: PlanResult, scene: SceneState) -> list[dict]:
    """Per object: the transit (move step, empty gripper) and the transport
    candidate as the planner costs it (lift + transport + place, starting at
    the grasp pose), with their pose sequences' features."""
    out = []
    prev: Optional[Pose] = scene.gripper_start
    steps = plan.steps
    for i, s in enumerate(steps):
        if s.kind == "move":
            f = path_features([prev] + list(s.waypoints))
            out.append(dict(kind="transit", object_id=s.object_id, label=s.note.replace("route=", ""),
                            phases=[f"move:{s.object_id}"], speed=s.max_speed, **f))
        if s.kind == "lift":
            grasp = steps[i - 1].waypoints[-1]
            seq = [grasp]
            for k in (i, i + 1, i + 2):
                seq += list(steps[k].waypoints)
            note = transport_note(plan, s.object_id)
            f = path_features(seq)
            out.append(dict(kind="transport", object_id=s.object_id, label=note["route"], mode=note["mode"],
                            phases=[f"lift:{s.object_id}", f"transport:{s.object_id}", f"place:{s.object_id}"],
                            **f))
        if s.waypoints:
            prev = s.waypoints[-1]
    return out


def phase_durations(log) -> dict:
    """Seconds spent in each executor phase label."""
    ph = np.asarray(log.phase)
    labels, counts = np.unique(ph, return_counts=True)
    return {str(k): float(c) * float(log.dt) for k, c in zip(labels, counts)}
