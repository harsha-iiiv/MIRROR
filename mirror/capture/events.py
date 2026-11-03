"""Object-centric pick and place intervals for human-hand demonstrations.

A person's hand is not a robot gripper: there is no observed grasp or
release. What the footage can show is when an object starts to move away from
where it rested and when it comes to rest again (and where). These are
recorded as INFERRED carry intervals with explicit bounds:

* ``t_lo`` .. ``t_hi`` brackets the moment: the last frame the object was
  seen at rest and the first frame it was seen moving (or the reverse for a
  placement). Hand occlusion widens the bracket instead of being guessed.
* ``actor`` is ``human_hand`` and ``basis`` is ``object_motion``; they are
  never labelled as measured robot contact.
* A person can correct the interval, the object, or reject the event before
  any specification is proposed; corrections are kept with their before/after.
"""
from __future__ import annotations

import time
from typing import Literal, Optional, Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from mirror.capture.reconstruct import ObjectTrajectory

DEPART_M = 0.010             # moved this far from the rest position = moving
SETTLE_M = 0.005             # within this of the rest position = still at rest
MIN_CARRY_M = 0.03           # displacement between rests that counts as a carry
AMBIGUOUS_S = 0.5            # bracket wider than this needs a person's review

EVENT_LABEL = "inferred from object motion (human hand); not a robot contact event"


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TrayRegion(_M):
    center_xy: tuple[float, float]
    inner_half: tuple[float, float]
    yaw_deg: float = 0.0
    source: Literal["tracked_tray_at_rest", "person_supplied", "assumed_aligned_with_board"] = "tracked_tray_at_rest"
    # where inner_half (the inside footprint) came from; the simulated tray uses this footprint
    footprint_source: Literal["measured_ruler", "person_supplied", "assumed_proxy"] = "person_supplied"

    def contains(self, xy) -> bool:
        c, s = np.cos(np.radians(-self.yaw_deg)), np.sin(np.radians(-self.yaw_deg))
        d = np.asarray(xy[:2], float) - np.asarray(self.center_xy)
        x, y = c * d[0] - s * d[1], s * d[0] + c * d[1]
        return abs(x) <= self.inner_half[0] and abs(y) <= self.inner_half[1]


class HumanEvent(_M):
    id: str
    object: str
    type: Literal["pick", "place"]
    t: float
    t_lo: float
    t_hi: float
    status: Literal["inferred", "person_corrected", "rejected"] = "inferred"
    actor: Literal["human_hand"] = "human_hand"
    basis: Literal["object_motion"] = "object_motion"
    label: str = EVENT_LABEL
    position: Optional[tuple[float, float, float]] = None
    placed_in: Optional[Literal["tray", "table"]] = None
    ambiguous: bool = False
    occluded_frames: int = 0
    identity_uncertain: bool = False


class Correction(_M):
    target: str                  # event id, or "object:<label>"
    field: Literal["t", "t_lo", "t_hi", "reject", "object", "confirm"]
    before: Optional[str] = None
    after: Optional[str] = None
    by: Literal["person", "synthetic_test"] = "person"
    at: float = Field(default_factory=time.time)
    seconds_spent: Optional[float] = None      # for measuring correction effort


def _observed(tr: ObjectTrajectory, k: int) -> bool:
    return tr.pos[k] is not None and tr.pos_status[k] in ("measured", "inferred")


def _state(tr: ObjectTrajectory, k: int, anchor) -> Optional[str]:
    """'still' / 'moving' / None (unknown) at frame k relative to a rest position.
    Clean (measured) 3D positions decide by distance; otherwise only sustained
    2D motion counts as moving, and a resting estimate without 2D motion as still."""
    mv = tr.moving_2d[k] if k < len(tr.moving_2d) else None
    if _observed(tr, k) and tr.pos_status[k] == "measured":
        d = float(np.linalg.norm(np.asarray(tr.pos[k])[:2] - anchor[:2]))
        if d > DEPART_M:
            return "moving"
        if d <= SETTLE_M and mv is not True:
            return "still"
    if mv is True:
        return "moving"
    if _observed(tr, k) and tr.resting[k] and mv is not True:
        return "still"
    return None


def _rest_segments(tr: ObjectTrajectory):
    N = len(tr.t)
    rest = [bool(r) if r is not None else False for r in tr.resting]
    obs = [_observed(tr, k) for k in range(N)]
    raw = []
    k = 0
    while k < N:
        if obs[k] and rest[k]:
            j = k
            while j + 1 < N and obs[j + 1] and rest[j + 1]:
                j += 1
            raw.append([k, j])
            k = j + 1
        else:
            k += 1
    # merge segments with no motion evidence between them (a hand arriving shifts a centroid but is not a carry)
    segs = []
    for s in raw:
        if segs:
            a0, a1 = segs[-1]
            between = range(a1 + 1, s[0])
            moved = any(tr.moving_2d[q] for q in between if q < len(tr.moving_2d)) or \
                    any(_observed(tr, q) and tr.pos_status[q] == "measured" and tr.resting[q] is False for q in between)
            if not moved:
                segs[-1] = [a0, s[1]]
                continue
        segs.append(s)

    def anchor(a, b):
        clean = [tr.pos[q] for q in range(a, b + 1) if obs[q] and tr.pos_status[q] == "measured"]
        pts = clean or [tr.pos[q] for q in range(a, b + 1) if obs[q]]
        return np.median(np.array(pts), 0), bool(clean)

    return [(a, b, *anchor(a, b)) for a, b in segs]


def detect_events(tr: ObjectTrajectory, tray: Optional[TrayRegion] = None) -> list[HumanEvent]:
    """Pick/place brackets from rest segments and the motion any view saw."""
    t = np.asarray(tr.t)
    N = len(t)
    segs = _rest_segments(tr)
    events = []
    n = 0
    for (a0, a1, pa, ca), (b0, b1, pb, cb) in zip(segs, segs[1:]):
        if np.linalg.norm(pb[:2] - pa[:2]) < MIN_CARRY_M:
            continue
        lo = a1
        for q in range(a0, b0):
            s = _state(tr, q, pa)
            if s == "still":
                lo = q
            elif s == "moving":
                break
        hi = next((q for q in range(lo + 1, b0 + 1) if _state(tr, q, pa) == "moving"), b0)
        hi2 = b0
        for q in range(b1, lo, -1):
            s = _state(tr, q, pb)
            if s == "still":
                hi2 = q
            elif s == "moving":
                break
        lo2 = next((q for q in range(hi2 - 1, lo, -1) if _state(tr, q, pb) == "moving"), max(hi, a1))
        for typ, l, h, p, clean in (("pick", lo, hi, pa, ca), ("place", lo2, hi2, pb, cb)):
            occl = sum(1 for q in range(l + 1, h) if _state(tr, q, p) is None)
            idu = any(tr.identity_uncertain[q] for q in range(l, h + 1))
            width = float(t[h] - t[l])
            ev = HumanEvent(id=f"{tr.label}:{typ}:{n}", object=tr.label, type=typ, t=float(0.5 * (t[l] + t[h])),
                            t_lo=float(t[l]), t_hi=float(t[h]), position=tuple(float(v) for v in p),
                            ambiguous=width > AMBIGUOUS_S or idu, occluded_frames=occl, identity_uncertain=idu)
            if typ == "place":
                ev.placed_in = "tray" if (tray is not None and tray.contains(p)) else "table"
            events.append(ev)
        n += 1
    return events


def review_items(events: Sequence[HumanEvent], trajectories: dict[str, ObjectTrajectory]) -> list[dict]:
    items = []
    for e in events:
        if e.status == "rejected":
            continue
        if e.ambiguous and e.status != "person_corrected":
            why = "identity uncertain during the interval" if e.identity_uncertain else (
                f"the moment is only known within {e.t_hi - e.t_lo:.2f} s" + (f" ({e.occluded_frames} frames hidden)" if e.occluded_frames else ""))
            items.append({"code": "ambiguous_event_interval", "severity": "abstain", "object": e.object, "event": e.id,
                          "message": f"Check when the {e.object} was {'picked up' if e.type == 'pick' else 'put down'}: {why}."})
    for lab, tr in trajectories.items():
        if any(tr.identity_uncertain):
            items.append({"code": "identity_uncertain", "severity": "abstain", "object": lab, "event": None,
                          "message": f"The {lab} track was lost and found again; confirm it is the same object."})
        cov = tr.coverage(("measured", "inferred"))
        if cov < 0.8:
            items.append({"code": "low_coverage", "severity": "review", "object": lab, "event": None,
                          "message": f"The {lab} was located in {100 * cov:.0f}% of frames."})
    return items


def apply_corrections(events: list[HumanEvent], corrections: Sequence[Correction]) -> list[HumanEvent]:
    by_id = {e.id: e for e in events}
    for c in corrections:
        e = by_id.get(c.target)
        if e is None:
            continue
        if c.field == "reject":
            e.status = "rejected"
        elif c.field == "confirm":
            e.status = "person_corrected"
            e.ambiguous = False
        elif c.field in ("t", "t_lo", "t_hi"):
            setattr(e, c.field, float(c.after))
            if c.field == "t":
                e.t_lo = e.t_hi = float(c.after)
            e.status = "person_corrected"
            e.ambiguous = False
        elif c.field == "object":
            e.object = str(c.after)
            e.status = "person_corrected"
    return events
