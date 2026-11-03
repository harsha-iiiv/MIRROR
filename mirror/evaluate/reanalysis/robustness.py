"""Robustness benchmark re-analysis with explicit denominators (derived, offline).

Reads results/eval_robustness/plan_report/report.json (the stored per-object
rows of the registered run) and recomputes every summary with stated
denominators. Nothing is re-scored and no model is called.

Why: the registered headline "median IoU change ~ 0 on geometry-kept objects"
is taken over every (edit, object) row, including objects that were never
prompted (no phrase chosen, IoU 0 before and 0 after) and objects prompted but
missed both before and after. Those rows contribute an IoU change of exactly
0 whatever the edit did. This module separates them.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

import numpy as np

KEPT = ("original", "preserved", "kept")          # as in mirror.robustness.evaluate
CHANGED = ("moved", "resized", "missing", "reframed")
CLASSES = ("cup", "block", "marker")
MATCH_IOU = 0.5          # a detection counts as the right object only at IoU >= 0.5 (stated here; the
                         # registered "found" means SAM returned any mask, whatever it overlapped)


def _r(x, nd=4):
    return None if x is None else round(float(x), nd)


def _median(v):
    v = [x for x in v if x is not None]
    return _r(np.median(v)) if v else None


def _mean(v):
    v = [x for x in v if x is not None]
    return _r(np.mean(v)) if v else None


def _rate(num, den):
    return dict(num=num, den=den, rate=_r(num / den) if den else None)


def pair_state(r: dict) -> str:
    """Detection state of one (edit, object) row against its original frame."""
    if r.get("prompt") is None:
        return "never_prompted"
    fo, fe = bool(r.get("found_original")), bool(r.get("found"))
    return {(True, True): "found_both", (True, False): "lost_after_edit",
            (False, True): "gained_after_edit", (False, False): "missed_both"}[(fo, fe)]


def iou_original(r: dict) -> float:
    return float(r["iou"]) - float(r.get("iou_delta") or 0.0)


def match_state(r: dict) -> str:
    """Correct-detection state (IoU >= MATCH_IOU) of one edit row against its original."""
    if r.get("prompt") is None:
        return "never_prompted"
    co, ce = iou_original(r) >= MATCH_IOU, float(r["iou"]) >= MATCH_IOU
    return {(True, True): "matched_both", (True, False): "lost_after_edit",
            (False, True): "gained_after_edit", (False, False): "unmatched_both"}[(co, ce)]


def group_stats(rows: list[dict]) -> dict:
    """Every denominator for one group of edit rows (version != original)."""
    kept = [r for r in rows if r["geometry"] in KEPT]
    prompted = [r for r in rows if r.get("prompt") is not None]
    kept_p = [r for r in kept if r.get("prompt") is not None]
    states = Counter(pair_state(r) for r in kept)
    zero_zero = [r for r in kept if r.get("iou_delta") == 0 and r["iou"] == 0]
    both = [r for r in kept_p if pair_state(r) == "found_both"]
    return dict(
        object_rows=len(rows),
        object_rows_prompted=len(prompted),
        object_rows_never_prompted=len(rows) - len(prompted),
        geometry={g: sum(1 for r in rows if r["geometry"] == g) for g in sorted({r["geometry"] for r in rows})},
        geometry_changed_rows=sum(1 for r in rows if r["geometry"] in CHANGED),
        geometry_kept_rows=len(kept),
        geometry_kept_rows_prompted=len(kept_p),
        kept_detection_states=dict(sorted(states.items())),
        kept_rows_with_iou_zero_before_and_after=len(zero_zero),
        registered_headline=dict(
            definition="median IoU change over every geometry-kept row (as registered)",
            kept_iou_delta_median=_median([r.get("iou_delta") for r in kept]),
            kept_found_rate=_rate(sum(bool(r["found"]) for r in kept), len(kept))),
        prompted_unconditional=dict(
            definition="geometry-kept rows whose object was prompted; misses count as IoU 0",
            iou_delta_median=_median([r.get("iou_delta") for r in kept_p]),
            iou_delta_mean=_mean([r.get("iou_delta") for r in kept_p]),
            iou_delta_p10=_r(np.quantile([r["iou_delta"] for r in kept_p], 0.1)) if kept_p else None,
            found_rate=_rate(sum(bool(r["found"]) for r in kept_p), len(kept_p)),
            found_rate_original=_rate(sum(bool(r["found_original"]) for r in kept_p), len(kept_p))),
        detection_conditional=dict(
            definition="geometry-kept rows found on both the original and the edited frame",
            n=len(both),
            iou_delta_median=_median([r.get("iou_delta") for r in both]),
            iou_delta_mean=_mean([r.get("iou_delta") for r in both]),
            centroid_err_px_median=_median([r.get("centroid_err_px") for r in both])),
        correct_detection=dict(
            definition=f"a row counts as detected only if SAM's top mask has IoU >= {MATCH_IOU} with the object",
            kept_states=dict(sorted(Counter(match_state(r) for r in kept).items())),
            kept_found_but_iou_zero=sum(1 for r in kept if r["found"] and float(r["iou"]) == 0.0),
            kept_matched_rate=_rate(sum(1 for r in kept if float(r["iou"]) >= MATCH_IOU), len(kept)),
            kept_matched_rate_prompted=_rate(sum(1 for r in kept_p if float(r["iou"]) >= MATCH_IOU), len(kept_p)),
            kept_matched_rate_original=_rate(sum(1 for r in kept if iou_original(r) >= MATCH_IOU), len(kept)),
            iou_delta_given_matched_original=dict(
                definition="rows matched on the original frame; an edit that loses the object counts its full IoU drop",
                n=sum(1 for r in kept if r.get("prompt") is not None and iou_original(r) >= MATCH_IOU),
                median=_median([r.get("iou_delta") for r in kept
                                if r.get("prompt") is not None and iou_original(r) >= MATCH_IOU]),
                mean=_mean([r.get("iou_delta") for r in kept
                            if r.get("prompt") is not None and iou_original(r) >= MATCH_IOU]))),
        all_objects_unconditional=dict(
            definition="every geometry-kept row incl. never-prompted objects; never-prompted counts as not found",
            found_rate=_rate(sum(bool(r["found"]) for r in kept), len(kept))),
        by_class={c: dict(
            kept_rows=sum(1 for r in kept if r["cls"] == c),
            kept_prompted=sum(1 for r in kept_p if r["cls"] == c),
            kept_found=sum(1 for r in kept if r["cls"] == c and r["found"]),
            kept_found_both=sum(1 for r in both if r["cls"] == c),
            prompted_iou_delta_median=_median([r.get("iou_delta") for r in kept_p if r["cls"] == c]),
            kept_matched=sum(1 for r in kept if r["cls"] == c and float(r["iou"]) >= MATCH_IOU),
            kept_found_but_iou_zero=sum(1 for r in kept if r["cls"] == c and r["found"] and float(r["iou"]) == 0.0),
            states=dict(sorted(Counter(pair_state(r) for r in kept if r["cls"] == c).items())))
            for c in CLASSES},
    )


def originals(rows: list[dict]) -> dict:
    """Unedited frames: who was prompted and found, per condition and class."""
    out = {}
    for cond in sorted({r["condition"] for r in rows}):
        o = [r for r in rows if r["condition"] == cond and r["version"] == "original" and r["correction"] == "corrected"]
        out[cond] = dict(
            object_rows=len(o),
            prompted=sum(1 for r in o if r.get("prompt") is not None),
            found=sum(1 for r in o if r["found"]),
            found_rate_all=_rate(sum(1 for r in o if r["found"]), len(o)),
            found_rate_prompted=_rate(sum(1 for r in o if r["found"]), sum(1 for r in o if r.get("prompt") is not None)),
            matched_rate_all=_rate(sum(1 for r in o if float(r["iou"]) >= MATCH_IOU), len(o)),
            by_class={c: dict(prompted=sum(1 for r in o if r["cls"] == c and r.get("prompt") is not None),
                              found=sum(1 for r in o if r["cls"] == c and r["found"]),
                              matched=sum(1 for r in o if r["cls"] == c and float(r["iou"]) >= MATCH_IOU),
                              n=sum(1 for r in o if r["cls"] == c),
                              iou_mean=_mean([r["iou"] for r in o if r["cls"] == c])) for c in CLASSES})
    return out


def cup_failures(rows: list[dict]) -> dict:
    """The cup (carried, with beads) per frame and condition on unedited frames,
    and how often it was found on any edited frame."""
    out = {}
    frames = sorted({r["frame_id"] for r in rows})
    for cond in sorted({r["condition"] for r in rows}):
        per = {}
        for f in frames:
            o = [r for r in rows if r["condition"] == cond and r["frame_id"] == f and r["cls"] == "cup"
                 and r["correction"] == "corrected"]
            orig = next((r for r in o if r["version"] == "original"), None)
            ed = [r for r in o if r["version"] != "original"]
            per[f] = dict(prompt=orig.get("prompt") if orig else None,
                          found_original=bool(orig and orig["found"]),
                          iou_original=_r(orig["iou"]) if orig else None,
                          iou_best_original=_r(orig.get("iou_best")) if orig else None,
                          gt_pixels=orig.get("gt_pixels") if orig else None,
                          centroid_err_px_original=orig.get("centroid_err_px") if orig else None,
                          edits=len(ed), found_on_edits=sum(1 for r in ed if r["found"]),
                          matched_on_edits=sum(1 for r in ed if float(r["iou"]) >= MATCH_IOU),
                          max_iou_on_edits=_r(max((float(r["iou"]) for r in ed), default=0.0)))
        out[cond] = dict(frames=per,
                         frames_cup_prompted=sum(1 for v in per.values() if v["prompt"]),
                         frames_cup_found_original=sum(1 for v in per.values() if v["found_original"]),
                         edited_rows=sum(v["edits"] for v in per.values()),
                         edited_rows_cup_found=sum(v["found_on_edits"] for v in per.values()),
                         edited_rows_cup_matched=sum(v["matched_on_edits"] for v in per.values()),
                         frames_cup_matched_original=sum(1 for v in per.values()
                                                         if (v["iou_original"] or 0) >= MATCH_IOU))
    return out


def edit_geometry(report: dict) -> dict:
    edits = report.get("edits", [])
    model = [e for e in edits if e.get("kind") == "model"]
    verdicts = Counter((e.get("geometry") or {}).get("verdict", e.get("status")) for e in model)
    return dict(model_edits=len(model), local_edits=sum(1 for e in edits if e.get("kind") == "local"),
                model_edit_status=dict(Counter(e.get("status") for e in model)),
                model_edit_geometry_verdicts=dict(sorted((str(k), v) for k, v in verdicts.items())))


def analyse(plan_report: Path, registered_report: Optional[Path] = None) -> dict:
    rep = json.loads(plan_report.read_text())
    rows = rep["rows"]
    groups = {}
    for key in sorted({(r["condition"], r["correction"]) for r in rows}):
        sel = [r for r in rows if (r["condition"], r["correction"]) == key and r["version"] != "original"]
        if sel:
            groups[f"{key[0]}/{key[1]}"] = group_stats(sel)
    # the stored headline must be reproduced from the stored rows before anything is said about it
    stored = rep.get("summary", {})
    repro = {}
    for g, s in groups.items():
        a = stored.get(g, {}).get("all_edits", {})
        repro[g] = dict(kept_iou_delta_median=(a.get("kept_iou_delta_median"),
                                               s["registered_headline"]["kept_iou_delta_median"]),
                        n_geometry_kept=(a.get("n_geometry_kept"), s["geometry_kept_rows"]),
                        kept_found_rate=(a.get("kept_found_rate"), s["registered_headline"]["kept_found_rate"]["rate"]))
    checks = None
    if registered_report and registered_report.exists():
        checks = json.loads(registered_report.read_text())["result"]["summary"]["threshold_checks"]
    return dict(version="reanalysis_v1/robustness", match_iou=MATCH_IOU, source=str(plan_report), rows=len(rows),
                frames=sorted({r["frame_id"] for r in rows}), groups=groups, originals=originals(rows),
                cup=cup_failures(rows), edits=edit_geometry(rep), registered_threshold_checks=checks,
                reproduces_stored_summary=repro)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--out", default="results/reanalysis_v1/robustness/report.json")
    a = ap.parse_args(argv)
    repo = Path(a.repo)
    rep = analyse(repo / "results/eval_robustness/plan_report/report.json", repo / "results/eval_robustness/report.json")
    out = repo / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=1, sort_keys=True) + "\n")
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
