"""Unconditional measures for the video benchmark v3.

Every visible ground-truth (object, frame) counts: a missing estimate is a
miss, not a skipped frame. Localisation reports the full error distribution,
misses, outliers and identity errors separately. Tilt is scored over every
true carry frame of the cup; an estimator may abstain (None), which is never
scored as zero. "Conditional" numbers (over answered frames only) are always
reported next to their coverage and never on their own.
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np

OUTLIER_M = 0.05
UPRIGHT_TOL = 15.0


def _q(v) -> Optional[dict]:
    v = [float(x) for x in v]
    if not v:
        return None
    a = np.asarray(v)
    return dict(n=len(v), p50=round(float(np.percentile(a, 50)), 4), p90=round(float(np.percentile(a, 90)), 4),
                p99=round(float(np.percentile(a, 99)), 4), max=round(float(a.max()), 4), mean=round(float(a.mean()), 4))


def localisation(gt: dict, est_xy: Callable[[str, float], Optional[tuple]], object_ids: list[str]) -> dict:
    """``est_xy(object_id, t)`` -> (x, y) or None (no estimate). Over every GT
    frame where the object is visible in some camera."""
    out = {"per_object": {}, "all": None}
    allerr, n_vis, n_miss, n_out, n_id = [], 0, 0, 0, 0
    for oid in object_ids:
        errs, vis, miss, outl, ident = [], 0, 0, 0, 0
        for f in gt["frames"]:
            if oid not in f["objects"] or not any(oid in f["masks"].get(c, {}) for c in f["masks"]):
                continue
            vis += 1
            e = est_xy(oid, f["t"])
            if e is None:
                miss += 1
                continue
            true = np.asarray(f["objects"][oid]["pos"][:2])
            d = float(np.linalg.norm(np.asarray(e) - true))
            errs.append(d)
            if d > OUTLIER_M:
                outl += 1
                others = [np.linalg.norm(np.asarray(e) - np.asarray(f["objects"][o]["pos"][:2]))
                          for o in f["objects"] if o != oid]
                if others and min(others) < d and min(others) <= OUTLIER_M:
                    ident += 1
        out["per_object"][oid] = dict(visible=vis, missing=miss, outliers=outl, identity_errors=ident,
                                      error_m=_q(errs),
                                      within_2cm_of_visible=round(sum(1 for x in errs if x <= 0.02) / vis, 4) if vis else None)
        allerr += errs
        n_vis, n_miss, n_out, n_id = n_vis + vis, n_miss + miss, n_out + outl, n_id + ident
    out["all"] = dict(visible=n_vis, missing=n_miss, missing_rate=round(n_miss / n_vis, 4) if n_vis else None,
                      outliers=n_out, identity_errors=n_id, error_m_answered=_q(allerr),
                      within_1cm_of_visible=round(sum(1 for x in allerr if x <= 0.01) / n_vis, 4) if n_vis else None,
                      within_2cm_of_visible=round(sum(1 for x in allerr if x <= 0.02) / n_vis, 4) if n_vis else None,
                      within_5cm_of_visible=round(sum(1 for x in allerr if x <= 0.05) / n_vis, 4) if n_vis else None)
    return out


def tilt(gt: dict, est_tilt: Callable[[float], Optional[float]], cup_id: str) -> dict:
    """``est_tilt(t)`` -> degrees or None (abstain). Over every true carry frame of the cup."""
    rows = [(f["t"], f["cup_tilt_deg"].get(cup_id)) for f in gt["frames"]
            if f.get("held") == cup_id and f.get("cup_tilt_deg", {}).get(cup_id) is not None]
    n = len(rows)
    ans, errs, false_up, missed_tilt, truly_tilted = 0, [], 0, 0, 0
    est_vals, true_vals = [], []
    for t, true in rows:
        e = est_tilt(t)
        true_vals.append(true)
        tilted = true > UPRIGHT_TOL
        truly_tilted += int(tilted)
        if e is None:
            missed_tilt += int(tilted)
            continue
        ans += 1
        est_vals.append(e)
        errs.append(abs(e - true))
        if tilted and e <= UPRIGHT_TOL:
            false_up += 1
    true_max = max(true_vals) if true_vals else None
    # the specification-relevant decision: was the cup carried upright (max tilt <= tolerance)?
    if not rows:
        decision = "no_carry"
    elif ans < 0.8 * n:
        decision = "abstained"
    else:
        decision = "upright" if max(est_vals) <= UPRIGHT_TOL else "tilted"
    truth = None if true_max is None else ("upright" if true_max <= UPRIGHT_TOL else "tilted")
    return dict(carry_frames=n, answered=ans, coverage=round(ans / n, 4) if n else None,
                error_deg_answered=_q(errs),
                within_5deg_of_carry=round(sum(1 for x in errs if x <= 5) / n, 4) if n else None,
                truly_tilted_frames=truly_tilted, false_upright_frames=false_up, abstained_on_tilted_frames=missed_tilt,
                true_max_deg=round(true_max, 2) if true_max is not None else None,
                est_max_deg=round(max(est_vals), 2) if est_vals else None,
                upright_decision=decision, upright_truth=truth,
                upright_decision_correct=(None if decision in ("abstained", "no_carry") else decision == truth))
