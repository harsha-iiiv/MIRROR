"""Perception accuracy against simulator ground truth."""
from __future__ import annotations

import math

import numpy as np

from mirror.perception.reconstruct import Estimates


def perception_metrics(est: Estimates, gt: dict, *, object_ids=None, label_to_id=None,
                       event_tol_s: float = 2.0) -> dict:
    """Found rate, table-plane centroid error (cm), event timing error (s) and
    cup tilt error (deg) per object, plus their means.

    - found rate: fraction of frames where the object is estimated, among
      frames where any of its pixels are visible in some camera (ground truth).
    - centroid error: table-plane (x, y) distance between the estimate and the
      object's true centre, over frames where both exist.
    - event timing: each true grasp/release is matched to the nearest
      estimated event of the same type and object within ``event_tol_s``;
      unmatched true events count as misses, unmatched estimates as extra.
    - tilt: |estimated in-image tilt - true tilt| for cups, where estimated.
    """
    frames = gt["frames"]
    n = min(len(frames), len(est.t))
    label_to_id = label_to_id or {lab: lab for lab in est.objects}
    out = {"objects": {}, "events": {}}
    for lab, P in est.objects.items():
        oid = label_to_id.get(lab, lab)
        if object_ids and oid not in object_ids:
            continue
        vis = np.array([any(oid in f["masks"][c] for c in f["masks"]) for f in frames[:n]])
        truth = np.array([f["objects"][oid]["pos"] for f in frames[:n]])
        fnd = est.found[lab][:n]
        both = fnd & vis
        err = np.linalg.norm(P[:n][both, :2] - truth[both, :2], axis=1) * 100 if both.any() else np.array([])
        rec = {"found_rate": float(fnd[vis].mean()) if vis.any() else None,
               "visible_frames": int(vis.sum()),
               "centroid_err_cm_mean": float(err.mean()) if len(err) else None,
               "centroid_err_cm_median": float(np.median(err)) if len(err) else None,
               "centroid_err_cm_p90": float(np.percentile(err, 90)) if len(err) else None}
        if lab in est.tilt_deg:
            true_tilt = np.array([f["cup_tilt_deg"].get(oid, np.nan) for f in frames[:n]])
            e_t = est.tilt_deg[lab][:n]
            ok = ~np.isnan(e_t) & ~np.isnan(true_tilt)
            te = np.abs(e_t[ok] - true_tilt[ok])
            rec.update(tilt_err_deg_mean=float(te.mean()) if len(te) else None,
                       tilt_err_deg_p90=float(np.percentile(te, 90)) if len(te) else None,
                       tilt_frames=int(ok.sum()))
        out["objects"][oid] = rec
    id_of = {lab: label_to_id.get(lab, lab) for lab in est.objects}
    est_ev = [dict(e, object=id_of.get(e["object"], e["object"])) for e in est.events]
    used = set()
    errs, misses = [], []
    for te in gt["events"]:
        cands = [(abs(e["t"] - te["t"]), k) for k, e in enumerate(est_ev)
                 if k not in used and e["type"] == te["type"] and e["object"] == te["object_id"]]
        cands = [c for c in cands if c[0] <= event_tol_s]
        if cands:
            d, k = min(cands)
            used.add(k)
            errs.append({"type": te["type"], "object": te["object_id"], "true_t": te["t"],
                         "est_t": est_ev[k]["t"], "err_s": round(est_ev[k]["t"] - te["t"], 3)})
        else:
            misses.append(te)
    out["events"] = {"matched": errs, "missed": misses,
                     "extra": [e for k, e in enumerate(est_ev) if k not in used],
                     "abs_err_s_mean": float(np.mean([abs(e["err_s"]) for e in errs])) if errs else None,
                     "n_true": len(gt["events"]), "n_est": len(est_ev)}
    objs = [v for v in out["objects"].values()]
    def mean(key):
        vals = [v[key] for v in objs if v.get(key) is not None]
        return float(np.mean(vals)) if vals else None
    out["summary"] = {"found_rate": mean("found_rate"), "centroid_err_cm": mean("centroid_err_cm_mean"),
                      "event_abs_err_s": out["events"]["abs_err_s_mean"],
                      "events_matched": f"{len(errs)}/{len(gt['events'])}",
                      "tilt_err_deg": mean("tilt_err_deg_mean")}
    return out
