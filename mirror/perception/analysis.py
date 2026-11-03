"""Offline analysis of video reconstruction against simulator ground truth.

No model calls: recorded tracks (masks) are reconstructed again with the
current code, and the resulting trace is interpreted with the offline rules
(deterministic). Used for development analysis of perception changes; every
output states which tracks, which code and which interpreter produced it.

Per seed it reports
* tracking: found rate and centroid error (the frozen benchmark's measures),
* events: signed timing errors of grasp/release against ground truth,
* tilt: how much of each true carry has a tilt measurement, and how many
  carried frames the trace presents as upright without one ("unsupported
  upright"), and how many it presents as upright while truly tilted,
* specification: the offline proposal from the video trace against the
  offline proposal from the ground-truth trace (same interpreter), as the
  list of items a person would have to add or remove ("required corrections"),
  plus the review items the trace raises.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

import numpy as np

UPRIGHT_TOL = 15.0


def _signed_event_errors(gt_ev: list[dict], est_ev: list[dict], tol_s: float = 2.0) -> list[dict]:
    pairs = []
    for i, g in enumerate(gt_ev):
        for j, e in enumerate(est_ev):
            if g["type"] == e["type"] and g["object"] == e["object"]:
                d = float(e["t"]) - float(g["t"])
                if abs(d) <= tol_s:
                    pairs.append((abs(d), d, i, j))
    used_g, used_e, out = set(), set(), []
    for _, d, i, j in sorted(pairs):
        if i not in used_g and j not in used_e:
            used_g.add(i)
            used_e.add(j)
            out.append(dict(type=gt_ev[i]["type"], object=gt_ev[i]["object"], err_s=round(d, 3)))
    return out


def _sig_items(sig: dict) -> set:
    return {("constraint",) + tuple(c) for c in sig["constraints"]} | {("goal",) + tuple(g) for g in sig["goals"]}


def _readable(item) -> str:
    if item[0] == "goal":
        return f"goal inside: {json.loads(item[1]).get('kind')}"
    _, typ, phase, sel, params = item
    return f"{typ} {phase}: {json.loads(sel).get('kind')} {params}"


def analyse_seed(manifest: Path, tracks_path: Path, *, cameras: Optional[list] = None, tilt_every: int = 1) -> dict:
    from mirror.contracts import DemoTrace
    from mirror.evaluate.bench_video import (est_events, est_frames, frame_errors, gt_events, gt_frames,
                                             match_events, proposal_signature)
    from mirror.interpret import propose_spec, summarize_demo
    from mirror.perception import load_demo_video
    from mirror.perception.reconstruct import estimate, scene_from_video, trace_from_estimates
    from mirror.perception.track import load_tracks
    dv, gt = load_demo_video(manifest)
    root = Path(manifest).parent
    tr = load_tracks(tracks_path)
    if cameras:
        tr.masks = {c: v for c, v in tr.masks.items() if c in cameras}
    K = {c: ci.K for c, ci in dv.cameras.items() if c in tr.masks}
    est = estimate(tr, K, tilt_every=tilt_every)
    scene, _ = scene_from_video(tr, est, scene_id=dv.scene_id + "_from_video")
    trace = trace_from_estimates(tr, est, scene, demo_id=dv.demo_id + "_from_video")
    kinds = est.scene_checks.get("kinds", {})
    row: dict = dict(seed=dv.demo_id, cameras=sorted(tr.masks), frames=len(est.t))
    row["tracking"] = frame_errors(gt_frames(gt), est_frames(est, kinds, gt_fps=dv.fps))
    gev, eev = gt_events(gt), est_events(est)
    row["events"] = dict(within_1s=match_events(gev, eev, 1.0), signed=_signed_event_errors(gev, eev))
    # tilt honesty over true carry frames of the cup
    held_by_frame = {}
    for f in gt["frames"]:
        held_by_frame[f["i"]] = (f.get("held"), f.get("cup_tilt_deg", {}))
    cup_lab = next((l for l, k in kinds.items() if k == "cup"), None)
    tilt_rows = dict(carry_frames=0, measured=0, unsupported_upright=0, upright_but_truly_tilted=0,
                     truly_tilted_frames=0)
    if cup_lab is not None:
        from mirror.interpret.geom import tilt_deg as qtilt
        try:
            from mirror.observability import resolve_evidence, status_at
            ev = resolve_evidence(trace)
            tspans = ev.objects.get(cup_lab).tilt if ev.objects.get(cup_lab) else None
        except ImportError:                 # code without observability annotations
            tspans = None
        for k, (fr, ti) in enumerate(zip(trace.frames, est.t)):
            gi = int(round(float(ti) * dv.fps))
            held, tilts = held_by_frame.get(gi, (None, {}))
            if held != cup_lab or cup_lab not in fr.objects:
                continue
            true_t = tilts.get(cup_lab)
            tilt_rows["carry_frames"] += 1
            meas = not np.isnan(est.tilt_deg.get(cup_lab, np.full(len(est.t), np.nan))[k])
            tilt_rows["measured"] += int(meas)
            st = status_at(tspans, k) if tspans is not None else "measured"   # old traces present every value as data
            shown = qtilt(fr.objects[cup_lab].quat)
            presented_upright = st != "unobserved" and shown <= UPRIGHT_TOL
            if true_t is not None and true_t > UPRIGHT_TOL:
                tilt_rows["truly_tilted_frames"] += 1
                if presented_upright:
                    tilt_rows["upright_but_truly_tilted"] += 1
            if presented_upright and not meas and st not in ("interpolated",):
                tilt_rows["unsupported_upright"] += 1
    row["cup_carry_tilt"] = tilt_rows
    # specification (offline rules on both traces)
    gt_trace = DemoTrace.model_validate_json((root / dv.trace).read_text())
    sv, sg = proposal_signature(propose_spec(trace)), proposal_signature(propose_spec(gt_trace))
    iv, ig = _sig_items(sv), _sig_items(sg)
    row["spec_offline"] = dict(equal=iv == ig, missing=sorted(_readable(x) for x in ig - iv),
                               extra=sorted(_readable(x) for x in iv - ig),
                               required_corrections=len(ig ^ iv))
    summ = summarize_demo(trace)
    review = getattr(summ, "review", []) or []
    row["review"] = dict(abstain=sorted({r["code"] for r in review if r["severity"] == "abstain"}),
                         review=sorted({r["code"] for r in review if r["severity"] == "review"}))
    row["regularities"] = [r.statement for r in summ.regularities]
    row["evidence_kind"] = getattr(summ, "evidence_kind", None)
    return row


def aggregate(rows: list[dict]) -> dict:
    ok = [r for r in rows if "error" not in r]
    signed = [e for r in ok for e in r["events"]["signed"]]

    def q(v):
        v = list(v)
        return None if not v else dict(n=len(v), median=round(float(np.median(v)), 3),
                                       mean=round(float(np.mean(v)), 3),
                                       p90_abs=round(float(np.percentile(np.abs(v), 90)), 3))
    ct = [r["cup_carry_tilt"] for r in ok]
    return dict(
        seeds=len(rows), completed=len(ok),
        found_rate_all=q(r["tracking"]["found_rate_all"] for r in ok if r["tracking"]["found_rate_all"] is not None),
        events_f1_1s=q(r["events"]["within_1s"]["f1"] for r in ok),
        grasp_err_s=q(e["err_s"] for e in signed if e["type"] == "grasp"),
        release_err_s=q(e["err_s"] for e in signed if e["type"] == "release"),
        cup_carry_frames=sum(c["carry_frames"] for c in ct),
        cup_carry_tilt_measured=sum(c["measured"] for c in ct),
        unsupported_upright_frames=sum(c["unsupported_upright"] for c in ct),
        upright_but_truly_tilted_frames=sum(c["upright_but_truly_tilted"] for c in ct),
        truly_tilted_carry_frames=sum(c["truly_tilted_frames"] for c in ct),
        spec_equal=sum(1 for r in ok if r["spec_offline"]["equal"]),
        required_corrections_total=sum(r["spec_offline"]["required_corrections"] for r in ok),
        seeds_with_abstain=sum(1 for r in ok if r["review"]["abstain"]),
    )
