"""From a phone capture session to the simulator's scene and a demonstration
trace that the rest of MIRROR (summary, interpretation, planner) consumes.

The scene uses simulation PROXIES placed where the real objects rested at the
start; the placement of the table frame in the simulator is the person's
choice (``WorkspacePlacement``). Nothing here is a measured physical model.

The trace is only built with observability evidence attached
(``mirror.observability``): positions and tilts that were not observed are
written as display placeholders and marked ``unobserved``; the gripper is
marked unobserved for the whole trace (a person's hand did the task); pick and
place events are inferred carry intervals, never measured robot contact.
"""
from __future__ import annotations

import typing
from typing import Optional

import numpy as np

from mirror.capture.events import EVENT_LABEL
from mirror.capture.session import CaptureSession

PROXY_KINDS = ("cup", "block", "marker")


def _to_sim(session: CaptureSession, p) -> np.ndarray:
    pl = session.placement
    c, s = np.cos(np.radians(pl.yaw_deg)), np.sin(np.radians(pl.yaw_deg))
    x, y = float(p[0]), float(p[1])
    return np.array([pl.origin_xy_m[0] + c * x - s * y, pl.origin_xy_m[1] + s * x + c * y, float(p[2]) if len(p) > 2 else 0.0])


def _start_rest(tr) -> Optional[np.ndarray]:
    idx = [k for k, (p, r) in enumerate(zip(tr.pos, tr.resting)) if p is not None and r]
    if not idx:
        idx = [k for k, p in enumerate(tr.pos) if p is not None]
    if not idx:
        return None
    first = idx[: max(1, min(len(idx), 5))]
    return np.median(np.array([tr.pos[k] for k in first]), 0)


def to_scene(session: CaptureSession):
    from mirror.contracts import SceneState
    from mirror.scenes.objects import make_block, make_cup, make_marker, make_tray, make_wall
    from mirror.scenes.reference import default_gripper_start, default_workspace
    if session.reconstruction is None:
        raise ValueError("no reconstruction")
    objs, trays, obstacles, notes = [], [], [], []
    counts = {}
    for m in session.mappings:
        if not m.confirmed_by_person or m.proxy == "ignore":
            continue
        tr = session.reconstruction.objects.get(m.track_label)
        p = _start_rest(tr) if tr is not None else None
        if m.proxy == "tray" and session.tray is not None:
            p = np.array([*session.tray.center_xy, 0.0])
        if p is None:
            notes.append(f"{m.track_label}: never located; left out of the scene")
            continue
        q = _to_sim(session, p)
        k = counts.get(m.proxy, 0)
        counts[m.proxy] = k + 1
        if m.proxy in PROXY_KINDS:
            objs.append({"cup": make_cup, "block": make_block, "marker": make_marker}[m.proxy](f"{m.proxy}_{k}", float(q[0]), float(q[1])))
        elif m.proxy == "tray":
            trays.append(make_tray(f"tray_{k}", float(q[0]), float(q[1]), session.placement.yaw_deg * np.pi / 180.0))
        elif m.proxy == "wall":
            w = m.real_dims.get("width")
            h = m.real_dims.get("height")
            half = (w.value_m if w else 0.15) / 2
            wall = make_wall(f"wall_{k}", float(q[0]), float(q[1] - half), float(q[1] + half))
            if h is not None:
                wall = wall.model_copy(update={"height": float(h.value_m)})
            obstacles.append(wall)
    return SceneState(scene_id=f"phone_{session.session_id}", split="custom", objects=objs, trays=trays, obstacles=obstacles,
                      workspace=default_workspace(), gripper_start=default_gripper_start(),
                      notes="scene of simulation proxies placed from a phone capture; not measured physical models. "
                            + " ".join(notes))


def to_demo_trace(session: CaptureSession, *, demo_id: Optional[str] = None):
    """DemoTrace with evidence. Requires the observability annotations
    (``mirror.observability``) and a DemoTrace that carries them."""
    try:
        from mirror import observability as O
    except ImportError as e:   # pragma: no cover - depends on integration
        raise RuntimeError("phone traces need mirror.observability (evidence annotations); refusing to write a "
                           "trace whose unobserved values would read as measurements") from e
    from mirror.contracts import DemoTrace, Pose, TraceEvent, TraceFrame
    if "evidence" not in DemoTrace.model_fields:
        raise RuntimeError("DemoTrace has no evidence field; refusing to write an unannotated phone trace")
    scene = to_scene(session)
    rec = session.reconstruction
    label_to_id = {}
    counts = {}
    for m in session.mappings:
        if m.confirmed_by_person and m.proxy in PROXY_KINDS and m.track_label in rec.objects:
            k = counts.get(m.proxy, 0)
            counts[m.proxy] = k + 1
            label_to_id[m.track_label] = f"{m.proxy}_{k}"
    ref = next(iter(rec.objects.values()))
    times = ref.t
    n = len(times)
    start = {o.id: np.array(o.pose.pos) for o in scene.objects}
    frames = []
    last = dict(start)
    for i, t in enumerate(times):
        poses = {}
        for lab, oid in label_to_id.items():
            tr = rec.objects[lab]
            if tr.pos[i] is not None:
                last[oid] = _to_sim(session, tr.pos[i])
            tilt = tr.tilt_deg[i]
            if tilt is not None:
                h = np.radians(tilt) / 2
                quat = (float(np.cos(h)), float(np.sin(h)), 0.0, 0.0)   # magnitude only; axis not recorded
            else:
                quat = (1.0, 0.0, 0.0, 0.0)                              # placeholder, marked unobserved below
            poses[oid] = Pose(pos=tuple(float(v) for v in last[oid]), quat=quat)
        frames.append(TraceFrame(t=float(t - times[0]), gripper=scene.gripper_start, gripper_width=0.08, objects=poses))
    events = []
    for e in session.events:
        if e.status == "rejected" or e.object not in label_to_id:
            continue
        events.append(TraceEvent(t=float(e.t - times[0]), type="grasp" if e.type == "pick" else "release",
                                 object_id=label_to_id[e.object],
                                 detail={"inferred": True, "actor": e.actor, "basis": e.basis, "label": EVENT_LABEL,
                                         "t_lo": e.t_lo - times[0], "t_hi": e.t_hi - times[0], "status": e.status,
                                         "placed_in": e.placed_in}))
    objs = {}
    for lab, oid in label_to_id.items():
        tr = rec.objects[lab]
        ident = "ambiguous" if any(tr.identity_uncertain) else "confirmed_by_user"
        objs[oid] = O.ObjectEvidence(
            object_id=oid, label=lab, identity=ident, identity_note="mapped to this proxy by a person",
            position=O.spans_from_statuses(tr.pos_status, basis={"measured": "calibrated phone views",
                                                                  "inferred": "single view or partly hidden (see capture record)",
                                                                  "interpolated": "filled between observations",
                                                                  "unobserved": "not seen: placeholder value"}),
            tilt=O.spans_from_statuses(tr.tilt_status, basis={"measured": "printed tag (full) or side silhouette (lower bound); magnitude only",
                                                               "unobserved": "not observed: placeholder upright value is not evidence"}))
    intr_allowed = typing.get_args(O.IntrinsicsSource)
    calib = []
    for cam, intr in session.intrinsics.items():
        src = "user_supplied" if intr.source == "user_supplied" else ("charuco_board" if "charuco_board" in intr_allowed else "checkerboard")
        pose = session.poses.get(cam)
        calib.append(O.CalibrationRecord(camera=cam, intrinsics_source=src, extrinsics_source="calibration_board",
                                         table_size_source="not_used", residual_px=None if pose is None else pose.rms_px,
                                         valid=bool(intr.accepted and pose is not None and pose.accepted),
                                         assumptions=(["ChArUco board (checkerboard with markers)"] if src == "checkerboard" else []) +
                                         [f"metric scale from the person's measured square ({intr.square_mm} mm)"]))
    ok, blocking = session.gate()
    if session.synthetic:
        kind = "unverified_video_reconstruction"
    else:
        kind = "camera_video_reconstruction" if ok else "unverified_video_reconstruction"
    review = [O.ReviewItem(code=r.code, severity=r.severity, message=r.message, object_id=label_to_id.get(r.object or ""))
              for r in session.review if not r.resolved]
    if session.synthetic:
        review.append(O.ReviewItem(code="synthetic_capture", severity="abstain",
                                   message="SYNTHETIC test render, not real footage: never evidence."))
    models = [O.ModelArtifact(role="segmentation", model=mc.get("model") or "unknown", origin=mc.get("origin") or "unknown")
              for mc in session.model_calls]
    ev = O.TraceEvidence(kind=kind, cameras=list(session.poses), media={c.role: c.sha256 for c in session.clips},
                         calibration=calib, objects=objs,
                         gripper_position=O.uniform("unobserved", n, basis="human-hand demonstration: no gripper exists"),
                         gripper_width=O.uniform("unobserved", n, basis="human-hand demonstration: no gripper exists"),
                         events_basis="inferred from object motion (human hand); not robot contact",
                         models=models, review=review,
                         notes=[m.fidelity_statement() for m in session.mappings if m.confirmed_by_person])
    return DemoTrace(demo_id=demo_id or f"phone_{session.session_id}", scene=scene, source="video_grounded",
                     source_note="phone capture (human hand, calibrated cameras)" + (" SYNTHETIC test render" if session.synthetic else ""),
                     sample_hz=float(1.0 / np.median(np.diff(times))) if n > 1 else 1.0, frames=frames, events=events,
                     evidence=ev)
