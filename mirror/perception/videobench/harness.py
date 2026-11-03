"""Video benchmark v3 harness: one demonstration -> one row.

The same recorded tracks feed every condition: ``two_view`` (top + side) and
``top_only`` (the side camera dropped, no new model calls). Estimators are
scored unconditionally (mirror.perception.videobench.metrics). Proposals are
made ``repeats`` times on the ground-truth trace and on each video trace, so
differences between repeats of the same input (model variability) can be
separated from differences between the video and ground-truth inputs
(perception-induced). Each repeat uses its own cassette directory, so a
recorded run replays exactly and no repeat is served another's response.
"""
from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from mirror.perception.videobench import baselines, metrics


def _sig(spec) -> str:
    from mirror.evaluate.bench_video import proposal_signature
    return json.dumps(proposal_signature(spec), sort_keys=True)


def _nearest_index(t_est: np.ndarray, t: float) -> int:
    return int(np.clip(np.searchsorted(t_est, t), 0, len(t_est) - 1))


def _scores(dv, gt, tracks, condition: str, *, colour_masks: Optional[dict] = None) -> tuple[dict, object]:
    from mirror.observability import status_at
    from mirror.perception.reconstruct import estimate, scene_from_video, trace_from_estimates
    tr = copy.deepcopy(tracks)
    if condition == "top_only":
        tr.masks = {c: v for c, v in tr.masks.items() if c == "top"}
    K = {c: ci.K for c, ci in dv.cameras.items() if c in tr.masks}
    est = estimate(tr, K, intrinsics_source="rendered_camera_model")
    scene, _ = scene_from_video(tr, est, scene_id=dv.scene_id + "_from_video")
    trace = trace_from_estimates(tr, est, scene, demo_id=dv.demo_id + "_from_video")
    kinds = est.scene_checks["kinds"]
    ids = {lab: lab for lab in kinds}
    oids = [o["id"] for o in gt["objects"]]
    te = np.asarray(est.t)

    def mirror_xy(oid, t):
        lab = next((l for l in kinds if l == oid), None)
        if lab is None:
            return None
        k = _nearest_index(te, t)
        return None if not est.found[lab][k] else tuple(est.objects[lab][k][:2])
    static = baselines.static_positions(est)
    loc = {"mirror": metrics.localisation(gt, mirror_xy, oids),
           "static_positions": metrics.localisation(gt, lambda oid, t: None if static.get(oid) is None
                                                    else tuple(static[oid]), oids)}
    cup = next(o["id"] for o in gt["objects"] if o["kind"] == "cup")
    ev = trace.evidence.objects.get(cup)

    def mirror_tilt(t):
        k = _nearest_index(te, t)
        if ev is None or status_at(ev.tilt, k) not in ("measured", "interpolated"):
            return None
        v = est.tilt_deg.get(cup, np.full(len(te), np.nan))[k]
        if np.isnan(v):
            from mirror.interpret.geom import tilt_deg
            return float(tilt_deg(trace.frames[k].objects[cup].quat))
        return float(v)

    def pca_tilt(t):
        k = _nearest_index(te, t)
        return baselines.pca_axis_tilt(tr.get("side", cup, k)) if "side" in tr.masks else None
    tilt = {"mirror": metrics.tilt(gt, mirror_tilt, cup), "constant_zero": metrics.tilt(gt, baselines.constant_zero_tilt, cup),
            "pca_axis": metrics.tilt(gt, pca_tilt, cup)}
    if colour_masks is not None:
        ct = copy.deepcopy(tr)
        ct.masks = {c: dict(ct.masks.get(c, {}), **{l: m for l, m in colour_masks.get(c, {}).items()}) for c in ct.masks}
        ct.source = "colour threshold tracker (classical)"
        try:
            e2 = estimate(ct, K, intrinsics_source="rendered_camera_model", tilt_every=10 ** 6)
            t2 = np.asarray(e2.t)
            loc["colour_tracker"] = metrics.localisation(
                gt, lambda oid, t: (None if oid not in e2.found or not e2.found[oid][_nearest_index(t2, t)]
                                    else tuple(e2.objects[oid][_nearest_index(t2, t)][:2])), oids)
        except Exception as exc:          # a baseline that fails is reported, not dropped
            loc["colour_tracker"] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
    review = sorted({(r.code, r.severity) for r in trace.evidence.review})
    return dict(localisation=loc, tilt=tilt, review=[list(r) for r in review]), trace


def run_demo(manifest: Path, tracks, *, propose: Optional[Callable] = None, repeats: int = 1,
             colour: bool = False, conditions=("two_view", "top_only")) -> dict:
    """``propose(trace, repeat_index) -> TaskSpec``; None uses the offline rules only."""
    from mirror.contracts import DemoTrace
    from mirror.interpret import propose_spec
    from mirror.perception import load_demo_video
    dv, gt = load_demo_video(manifest)
    root = Path(manifest).parent
    gt_trace = DemoTrace.model_validate_json((root / dv.trace).read_text())
    from mirror.perception.videobench.demos import true_cup_carry_tilt
    row = dict(demo_id=dv.demo_id, true_cup_tilt=true_cup_carry_tilt(gt_trace), conditions={})
    colour_masks = None
    if colour:
        boxes = {}
        f0 = gt["frames"][0]
        for c in dv.videos:
            boxes[c] = {o["id"]: f0["masks"][c][o["id"]]["bbox"] for o in gt["objects"] if o["id"] in f0["masks"][c]}
        colour_masks = {c: baselines.colour_tracker(root / dv.videos[c], boxes[c],
                                                    every=max(1, int(round(dv.fps / tracks.fps))))
                        for c in dv.videos}
        step = max(1, int(round(dv.fps / tracks.fps)))
        colour_masks = {c: {l: {i // step: m for i, m in fr.items()} for l, fr in d.items()} for c, d in colour_masks.items()}
    traces = {"ground_truth": gt_trace}
    for cond in conditions:
        sc, tr = _scores(dv, gt, tracks, cond, colour_masks=colour_masks)
        row["conditions"][cond] = sc
        traces[cond] = tr
    sigs = {}
    for name, t in traces.items():
        sigs[name] = {"offline": _sig(propose_spec(t))}
        if propose is not None:
            sigs[name]["model"] = [_sig(propose(t, k)) for k in range(repeats)]
    row["signatures"] = sigs
    row["spec"] = spec_comparison(sigs)
    return row


def spec_comparison(sigs: dict) -> dict:
    """Model variability (repeats of one input disagree) vs perception-induced
    difference (video input's modal signature differs from ground truth's)."""
    out = {"offline_equal": {c: sigs[c]["offline"] == sigs["ground_truth"]["offline"] for c in sigs if c != "ground_truth"}}
    if "model" in sigs["ground_truth"]:
        def modal(v):
            return Counter(v).most_common(1)[0][0]
        g = sigs["ground_truth"]["model"]
        out["ground_truth_repeats_distinct"] = len(set(g))
        for c in sigs:
            if c == "ground_truth":
                continue
            v = sigs[c]["model"]
            out[c] = dict(repeats_distinct=len(set(v)), modal_equal_to_ground_truth_modal=modal(v) == modal(g),
                          any_overlap_with_ground_truth=bool(set(v) & set(g)))
    return out


def spark_proposer(cassette_root: Path, *, mode: str = "replay", model: str = "muse-spark-1.3") -> Callable:
    """Repeat k of a proposal uses its own cassette directory (repeat0, repeat1, ...),
    so repeats are independent calls when recorded and replay exactly afterwards."""
    from mirror.interpret import propose_spec
    from mirror.interpret.llm import LLMClient

    def propose(trace, k: int):
        client = LLMClient(mode=mode, model=model, cassette_dir=Path(cassette_root) / f"repeat{k}")
        return propose_spec(trace, client)
    return propose
