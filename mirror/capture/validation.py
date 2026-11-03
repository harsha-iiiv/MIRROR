"""Scoring for the real-footage validation (design v1, thresholds fixed before
any real footage exists: ``validation_design_v1.json``).

Labels are made by a person (see the design). ``score_clip`` compares one
capture session with its labels; ``aggregate`` reports every clip with its
denominators, including abstentions and rejections. Missing estimates are
counted as missing, never dropped: the unconditional error percentiles treat a
missing estimate as larger than every threshold.

Synthetic rehearsals produce labels from render ground truth; their reports
carry ``synthetic: true`` and never count toward any check.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal, Optional, Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from mirror.capture.session import CaptureSession

DESIGN_PATH = Path(__file__).with_name("validation_design_v1.json")


def load_design() -> dict:
    return json.loads(DESIGN_PATH.read_text())


def design_sha256() -> str:
    return hashlib.sha256(DESIGN_PATH.read_bytes()).hexdigest()


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EventLabel(_M):
    object: str
    type: Literal["pick", "place"]
    t: float                         # reference-camera clip time (s)


class ClipLabels(_M):
    clip_id: str
    split: Literal["development", "evaluation", "synthetic_rehearsal"]
    condition: str
    expected: str
    labeller: str
    synthetic: bool = False
    rest_positions: dict[str, dict[str, tuple[float, float]]] = Field(default_factory=dict)   # obj -> {"start": xy, "end": xy}
    keyframes: dict[str, dict[float, tuple[float, float, float]]] = Field(default_factory=dict)  # obj -> t -> xyz reference
    keyframe_reference: Literal["manual_triangulation", "render_ground_truth"] = "manual_triangulation"
    tilt: dict[str, dict[float, float]] = Field(default_factory=dict)       # obj -> t -> tilt deg (reference)
    tilt_reference: Literal["tag", "render_ground_truth", "none"] = "none"
    events: list[EventLabel] = Field(default_factory=list)
    intended_spec: Optional[dict] = None


def _pct(x, q):
    return None if not len(x) else float(np.percentile(x, q))


def _nearest(times: np.ndarray, t: float, tol: float) -> Optional[int]:
    if not len(times):
        return None
    k = int(np.argmin(np.abs(times - t)))
    return k if abs(times[k] - t) <= tol else None


def score_clip(s: CaptureSession, labels: ClipLabels, *, design: Optional[dict] = None,
               proposed_spec: Optional[dict] = None, retest_specs: Sequence[dict] = ()) -> dict:
    D = design or load_design()
    T = D["thresholds"]
    ok, blocking = s.gate()
    out = {"clip_id": labels.clip_id, "split": labels.split, "condition": labels.condition, "synthetic": labels.synthetic or s.synthetic,
           "design": {"name": D["name"], "version": D["version"], "sha256": design_sha256()},
           "session_id": s.session_id, "evidence_kind": s.evidence_kind, "mode": s.mode,
           "gate_passed": ok, "blocking": [b.code for b in blocking], "checks": []}

    def check(name, value, threshold, passed, note=""):
        out["checks"].append({"name": name, "value": value, "threshold": threshold, "passed": passed, "note": note})

    # expected rejection
    if labels.expected == "reject":
        rejected = any(b.code in ("recalibration_required", "calibration_invalid", "pose_invalid", "camera_moved") for b in blocking)
        check("invalid_calibration_rejected", rejected, True, bool(rejected))
        out["outcome"] = "rejected_as_expected" if rejected else "FAILED_to_reject"
        return out
    # calibration
    for cam, intr in s.intrinsics.items():
        if intr.rms_px is not None:
            check(f"calibration_intrinsics_rms_px[{cam}]", round(intr.rms_px, 3), T["calibration_intrinsics_rms_px"],
                  intr.rms_px <= T["calibration_intrinsics_rms_px"])
    for cam, p in s.poses.items():
        check(f"calibration_pose_rms_px[{cam}]", round(p.rms_px, 3), T["calibration_pose_rms_px"], p.rms_px <= T["calibration_pose_rms_px"])
    if s.reconstruction is None:
        out["outcome"] = "abstained_no_reconstruction"
        return out
    rec = s.reconstruction
    # rest positions (independent ruler reference)
    rest_err = []
    for obj, se in labels.rest_positions.items():
        tr = rec.objects.get(obj)
        for which, xy in se.items():
            if tr is None:
                rest_err.append(np.inf)
                continue
            idx = [k for k, (p, st) in enumerate(zip(tr.pos, tr.pos_status)) if p is not None and st == "measured" and tr.resting[k]]
            idx = idx[: max(1, len(idx) // 4)] if which == "start" else idx[-max(1, len(idx) // 4):]
            if not idx:
                rest_err.append(np.inf)
                continue
            est = np.median(np.array([tr.pos[k] for k in idx]), 0)[:2]
            rest_err.append(float(np.linalg.norm(est - np.asarray(xy))))
    if rest_err:
        r = np.array(rest_err) * 100
        out["rest_position_error_cm"] = {"n": len(r), "missing": int(np.isinf(r).sum()), "median": _pct(r, 50), "p90": _pct(r, 90),
                                         "max": float(r.max()), "values": [None if np.isinf(v) else round(float(v), 3) for v in r]}
        check("rest_position_error_median_cm", out["rest_position_error_cm"]["median"], T["rest_position_error_median_cm"],
              bool(out["rest_position_error_cm"]["median"] <= T["rest_position_error_median_cm"]))
        check("rest_position_error_p90_cm", out["rest_position_error_cm"]["p90"], T["rest_position_error_p90_cm"],
              bool(out["rest_position_error_cm"]["p90"] <= T["rest_position_error_p90_cm"]))
    # keyframes: unconditional distributions
    errs, cond, statuses, ident = [], [], [], 0
    for obj, kf in labels.keyframes.items():
        tr = rec.objects.get(obj)
        times = np.array(tr.t) if tr else np.array([])
        others = {o: rec.objects[o] for o in rec.objects if o != obj}
        for t, ref in kf.items():
            k = _nearest(times, float(t), 0.6 * (np.median(np.diff(times)) if len(times) > 1 else 0.1))
            if tr is None or k is None or tr.pos[k] is None:
                errs.append(np.inf); statuses.append("unobserved")
                continue
            e = float(np.linalg.norm(np.asarray(tr.pos[k]) - np.asarray(ref)))
            errs.append(e); cond.append(e); statuses.append(tr.pos_status[k])
            for o, otr in others.items():
                if otr.pos[k] is not None and np.linalg.norm(np.asarray(otr.pos[k]) - np.asarray(ref)) + 1e-9 < e and e > 0.05:
                    ident += 1
                    break
    if errs:
        e = np.array(errs) * 100
        n = len(e)
        cov = float(np.mean([s_ in ("measured", "inferred", "interpolated") for s_ in statuses]))
        meas = float(np.mean([s_ == "measured" for s_ in statuses]))
        outl = float(np.mean(e > T["outlier_cm"]))
        out["keyframe_position"] = {
            "reference": labels.keyframe_reference, "n": n, "missing": int(np.isinf(e).sum()),
            "status_counts": {k: statuses.count(k) for k in sorted(set(statuses))},
            "unconditional_cm": {"median": _pct(e, 50), "p75": _pct(e, 75), "p90": _pct(e, 90), "p95": _pct(e, 95),
                                 "note": "missing estimates count as larger than every threshold"},
            "conditional_found_only_cm": {"n": len(cond), "median": _pct(np.array(cond) * 100, 50), "p90": _pct(np.array(cond) * 100, 90),
                                          "note": "conditional on an estimate existing; not overall accuracy"},
            "outlier_rate": outl, "identity_errors": ident,
            "histogram_cm": np.histogram(np.clip(e, 0, 20), bins=[0, 0.5, 1, 2, 3, 5, 10, 20.0001])[0].tolist(),
        }
        check("keyframe_coverage", round(cov, 3), T["keyframe_coverage_min"], cov >= T["keyframe_coverage_min"])
        check("keyframe_measured", round(meas, 3), T["keyframe_measured_min"], meas >= T["keyframe_measured_min"])
        check("outlier_rate", round(outl, 3), T["outlier_rate_max"], outl <= T["outlier_rate_max"])
        check("identity_errors", ident, T["identity_errors_max"], ident <= T["identity_errors_max"])
    # tilt with the constant-zero baseline
    for obj, ref in labels.tilt.items():
        tr = rec.objects.get(obj)
        times = np.array(tr.t) if tr else np.array([])
        rows = []
        for t, true in ref.items():
            k = _nearest(times, float(t), 0.06)
            if k is None:
                continue
            rows.append((true, tr.tilt_deg[k], tr.tilt_status[k], tr.tilt_kind[k]))
        if not rows:
            continue
        true = np.array([r[0] for r in rows])
        obs = [r for r in rows if r[2] == "measured"]
        full = [r for r in obs if r[3] == "full"]
        lb = [r for r in obs if r[3] == "lower_bound"]
        res = {"reference": labels.tilt_reference, "n": len(rows), "observed": len(obs), "observability": len(obs) / len(rows),
               "true_max_deg": float(true.max()), "true_median_deg": float(np.median(true)),
               "constant_zero_median_abs_err_deg": float(np.median(np.abs(true))),
               "constant_zero_p90_abs_err_deg": float(np.percentile(np.abs(true), 90))}
        if full:
            fe = np.abs(np.array([r[1] - r[0] for r in full]))
            res["full_median_abs_err_deg"] = float(np.median(fe))
            res["full_p90_abs_err_deg"] = float(np.percentile(fe, 90))
            check(f"tilt_error_median_deg[{obj}]", round(res["full_median_abs_err_deg"], 2), T["tilt_error_median_deg"],
                  res["full_median_abs_err_deg"] <= T["tilt_error_median_deg"])
        if lb:
            valid = float(np.mean([r[1] <= r[0] + T["tilt_lower_bound_slack_deg"] for r in lb]))
            res["lower_bound_valid_rate"] = valid
            check(f"tilt_lower_bound_valid[{obj}]", round(valid, 3), T["tilt_lower_bound_valid_min"], valid >= T["tilt_lower_bound_valid_min"])
        res["note"] = ("tilt not observed is reported as unobserved (an abstention), never as upright; "
                       "the constant-zero baseline is shown for comparison")
        out.setdefault("tilt", {})[obj] = res
    # events
    if labels.events:
        rows = []
        for ev in labels.events:
            cands = [e for e in s.events if e.object == ev.object and e.type == ev.type and e.status != "rejected"]
            if not cands:
                rows.append({"label": ev.model_dump(), "found": False})
                continue
            e = min(cands, key=lambda x: abs(x.t - ev.t))
            rows.append({"label": ev.model_dump(), "found": True, "dt_s": e.t - ev.t, "bracket": [e.t_lo, e.t_hi],
                         "contains": e.t_lo - 1e-6 <= ev.t <= e.t_hi + 1e-6, "status": e.status})
        within = float(np.mean([r["found"] and abs(r["dt_s"]) <= T["event_timing_s"] for r in rows]))
        contains = float(np.mean([r["found"] and r["contains"] for r in rows]))
        out["events"] = {"n": len(rows), "missing": sum(not r["found"] for r in rows), "within_rate": within,
                         "bracket_contains_rate": contains, "rows": rows,
                         "note": "events are inferred from object motion (human hand), not robot contact"}
        check("event_within_rate", round(within, 3), T["event_within_min"], within >= T["event_within_min"])
        check("event_bracket_contains_rate", round(contains, 3), T["event_bracket_contains_min"], contains >= T["event_bracket_contains_min"])
    # correction effort
    out["correction_effort"] = {"corrections": len(s.corrections),
                                "seconds": sum(c.seconds_spent or 0 for c in s.corrections),
                                "blocking_items_before_review": len(blocking)}
    # end-to-end spec agreement
    if labels.intended_spec is not None:
        if proposed_spec is None:
            check("spec_agreement", None, "exact", None, "no specification proposed (gate closed or not run)")
        else:
            agree = _spec_core(proposed_spec) == _spec_core(labels.intended_spec)
            check("spec_agreement", agree, "exact", bool(agree))
    if retest_specs and proposed_spec is not None:
        same = all(_spec_core(r) == _spec_core(proposed_spec) for r in retest_specs)
        check("retest_agreement", same, "identical", bool(same))
    out["outcome"] = "scored"
    return out


def _spec_core(spec: dict) -> dict:
    """Goals and constraints only (ids, wording, versions and timestamps ignored)."""
    keep = {}
    for k in ("goals", "constraints", "ordering"):
        v = spec.get(k)
        if isinstance(v, list):
            keep[k] = sorted(json.dumps({kk: vv for kk, vv in (x.items() if isinstance(x, dict) else [])
                                         if kk not in ("id", "text", "rationale", "source", "regularity_ids", "question")},
                                        sort_keys=True) for x in v)
    return keep


def aggregate(rows: Sequence[dict]) -> dict:
    """Report over clips. Synthetic rows are listed separately and never counted."""
    real = [r for r in rows if not r.get("synthetic")]
    synth = [r for r in rows if r.get("synthetic")]
    def summ(rs):
        checks = [c for r in rs for c in r.get("checks", [])]
        return {"clips": len(rs), "by_outcome": {o: sum(r.get("outcome") == o for r in rs) for o in sorted({r.get("outcome") for r in rs})},
                "checks_passed": sum(c["passed"] is True for c in checks), "checks_failed": sum(c["passed"] is False for c in checks),
                "checks_not_evaluated": sum(c["passed"] is None for c in checks)}
    return {"design_sha256": design_sha256(), "evaluation": summ([r for r in real if r["split"] == "evaluation"]),
            "development": summ([r for r in real if r["split"] == "development"]),
            "synthetic_rehearsal_not_evidence": summ(synth),
            "status": "awaiting footage" if not real else "scored", "rows": list(rows)}
