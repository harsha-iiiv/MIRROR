"""Perception robustness: SAM 3.1 on original versus counterfactual frames,
scored against simulator ground truth.

For each ground-truth frame, each counterfactual (model edit or local
corruption), each prompt condition and each object class:
  * the edit's geometry verdict and its whole-frame correction
    (``geometry.check_geometry``: one similarity transform undoing the
    editor's drift, reported per edit);
  * SAM 3.1 on the corrected edited frame (primary) and, for the conditions in
    ``uncorrected_conditions``, on the uncorrected one (only resampled to the
    source size); SAM on the original frame as the reference;
  * IoU with the ground-truth visible mask and centroid error (px) of the
    top-confidence match; whether anything was found; the number of matches;
    the best IoU over all matches (diagnostic only: chosen with the ground truth).

Prompt conditions:
  * ``A`` (``CHOOSER``): phrases from ``mirror.interpret.sam_prompts.choose_prompts``
    (Muse names each object, SAM validates) run on each ORIGINAL frame and reused
    for every edit of that frame: the realistic pipeline;
  * ``B``: the fixed colour-free set ``COLOUR_FREE_PROMPTS``.

Edits whose geometry changed are kept in every table and counted separately;
their scores are measured against a ground truth that no longer fits.
"""
from __future__ import annotations

import datetime as _dt
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence, Union

import numpy as np

from .counterfactuals import EDIT_BUDGET, EDIT_OPTIONS, EDIT_MODEL, OUTPUT_FORMAT, make_counterfactuals, save_counterfactual
from .frames import GTFrame, encode_png
from .geometry import check_geometry, resample_to
from .instructions import EditInstruction, SAM_PROMPTS, instructions_sha256

SAM_MODEL = "sam-3.1"
CLASSES = ("cup", "block", "marker")
CHOOSER = "chooser"
CHOOSER_MODEL = "muse-spark-1.3"
# Condition B: colour-free shape names, proposed for the pre-registration.
# On development frames "cube" matched up to 5 objects in a top view and
# "small cube" matched the block first in both frames tried; "open box" and
# "small box" both find the cup but can rank another box-shaped thing (the
# tray, an obstacle) first. Kept as "open box": the condition tests whether
# dropping colour words helps, and this ambiguity is part of the answer.
COLOUR_FREE_PROMPTS: dict[str, str] = {"cup": "open box", "block": "small cube", "marker": "cylinder"}
DEFAULT_CONDITIONS: dict[str, Union[str, dict]] = {"A": CHOOSER, "B": COLOUR_FREE_PROMPTS}


def iou(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a.astype(bool), b.astype(bool)
    u = int((a | b).sum())
    return float((a & b).sum()) / u if u else (1.0 if not a.any() and not b.any() else 0.0)


def centroid(m: np.ndarray) -> Optional[tuple[float, float]]:
    ys, xs = np.nonzero(m)
    return (float(xs.mean()), float(ys.mean())) if len(xs) else None


def score_sam(seg, gt_mask: np.ndarray) -> dict:
    """Scores of one SAM result against one ground-truth mask."""
    H, W = gt_mask.shape
    objs = [o for f in seg.frames for o in f.objects] if seg is not None else []
    out = {"found": bool(objs), "n_matches": len(objs), "iou": 0.0, "iou_best": 0.0,
           "centroid_err_px": None, "confidence": None}
    if not objs:
        return out
    masks = [o.full_mask(W, H).astype(bool) for o in objs]
    ious = [iou(m, gt_mask) for m in masks]
    top = max(range(len(objs)), key=lambda i: (objs[i].confidence or 0.0))
    out.update(iou=round(ious[top], 4), iou_best=round(max(ious), 4), confidence=objs[top].confidence)
    c_gt, c_sam = centroid(gt_mask), centroid(masks[top])
    if c_gt and c_sam:
        out["centroid_err_px"] = round(float(np.hypot(c_gt[0] - c_sam[0], c_gt[1] - c_sam[1])), 2)
    return out


NOT_PROMPTED = {"found": False, "n_matches": 0, "iou": 0.0, "iou_best": 0.0, "centroid_err_px": None,
                "confidence": None}


@dataclass
class _Sam:
    client: object
    calls: int = 0
    network: int = 0
    latencies: list = field(default_factory=list)
    memo: dict = field(default_factory=dict)

    def segment(self, png: bytes, prompt: str):
        import hashlib
        key = (hashlib.sha256(png).hexdigest(), prompt)
        if key in self.memo:                      # same image and phrase in two conditions: one call
            return self.memo[key]
        seg = self.client.sam_segment(png, prompt, video=False)
        self.calls += 1
        if not seg.from_cassette:
            self.network += 1
        self.latencies.append(float(seg.latency_s or 0.0))
        self.memo[key] = seg
        return seg


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(float(np.mean(xs)), 4) if xs else None


def _median(xs):
    xs = [x for x in xs if x is not None]
    return round(float(np.median(xs)), 4) if xs else None


def resolve_prompts(frame: GTFrame, spec: Union[str, dict], client, chooser: Optional[Callable] = None) -> tuple[dict, dict]:
    """(class -> phrase or None, provenance) for one frame and one condition."""
    if spec != CHOOSER:
        return {c: spec.get(c) for c in CLASSES}, {"source": "fixed"}
    if chooser is None:
        from mirror.interpret.sam_prompts import choose_prompts as chooser
    ps = chooser(frame.png(), CHOOSER_MODEL, client, kinds=CLASSES)
    # Only accepted phrases (SAM found exactly one object) are used, as the
    # pipeline would. If the chooser accepted two phrases for one kind (one of
    # them can uniquely match the wrong object), the first accepted wins:
    # attempt 1 before attempt 2, then the model's order. A kind without an
    # accepted phrase is not prompted and counts as not found.
    final = {}
    for ch in sorted(ps.choices, key=lambda c: getattr(c, "attempt", 1)):
        if ch.ok and ch.kind not in final:
            final[ch.kind] = ch.phrase
    return ({c: final.get(c) for c in CLASSES},
            {"source": "chooser", "model": ps.model, "attempts": ps.attempts, "latency_s": ps.latency_s,
             "choices": [ch.model_dump() for ch in ps.choices], "frame_sha256": ps.frame_sha256})


def evaluate_robustness(frames: Sequence[GTFrame], instructions: Sequence[EditInstruction], *,
                        client=None, conditions: Optional[dict] = None,
                        uncorrected_conditions: Sequence[str] = ("A",),
                        chooser: Optional[Callable] = None,
                        out_dir: Optional[Path | str] = None, usage_log: Optional[Path | str] = None,
                        edit_budget: int = EDIT_BUDGET, split: str = "dev") -> dict:
    """Run the whole study and return a JSON-serialisable report (also written
    to ``out_dir``/report.json and report.md when given)."""
    if client is None:
        from mirror.meta import MetaClient
        client = MetaClient.from_env()
    conditions = dict(conditions or DEFAULT_CONDITIONS)
    started = time.time()
    out = Path(out_dir) if out_dir else None
    sam = _Sam(client)
    rows: list[dict] = []
    edits: list[dict] = []
    prompts_used: dict = {}
    for fr in frames:
        png = fr.png()
        meta = dict(frame_id=fr.frame_id, camera=fr.meta.get("camera"), moment=fr.meta.get("moment"))
        cond_prompts = {}
        for cname, spec in conditions.items():
            p, prov = resolve_prompts(fr, spec, client, chooser)
            cond_prompts[cname] = p
            prompts_used.setdefault(cname, {})[fr.frame_id] = {"prompts": p, **prov}
        base = {}
        for cname, p in cond_prompts.items():
            for cls in CLASSES:
                s = score_sam(sam.segment(png, p[cls]), fr.masks[cls]) if p[cls] else dict(NOT_PROMPTED)
                base[(cname, cls)] = s
                for corr in ("corrected", "uncorrected"):
                    rows.append(dict(**meta, condition=cname, correction=corr, version="original",
                                     family="original", instruction_id="original", kind="original", cls=cls,
                                     prompt=p[cls], gt_pixels=int(fr.masks[cls].sum()), geometry="original", **s))
        cfs = make_counterfactuals(png, instructions, client, usage_log=usage_log, budget=edit_budget)
        for cf in cfs:
            rec = {"frame_id": fr.frame_id, **cf.summary()}
            if cf.status != "ok":
                edits.append(rec)
                continue
            rep, ed = check_geometry(fr.rgb, cf.image, fr.masks, CLASSES)
            raw = resample_to(cf.image, fr.size)
            rec["geometry"] = rep.to_dict()
            rec["correction"] = rep.registration
            if cf.kind == "local":
                rec["geometry_by_construction"] = "preserved"
            if out is not None:
                save_counterfactual(cf, out / "edits" / f"{fr.frame_id}__{cf.instruction_id}")
            images = {"corrected": encode_png(ed), "uncorrected": encode_png(raw)}
            for cname, p in cond_prompts.items():
                for corr, img in images.items():
                    if corr == "uncorrected" and cname not in uncorrected_conditions:
                        continue
                    for cls in CLASSES:
                        s = score_sam(sam.segment(img, p[cls]), fr.masks[cls]) if p[cls] else dict(NOT_PROMPTED)
                        g = rep.objects.get(cls)
                        geo = ("preserved" if cf.kind == "local" else
                               rep.verdict if rep.verdict == "reframed" else (g.verdict if g else "untested"))
                        rows.append(dict(**meta, condition=cname, correction=corr, version=cf.instruction_id,
                                         family=cf.family, instruction_id=cf.instruction_id, kind=cf.kind, cls=cls,
                                         prompt=p[cls], gt_pixels=int(fr.masks[cls].sum()), geometry=geo,
                                         iou_delta=round(s["iou"] - base[(cname, cls)]["iou"], 4),
                                         found_original=base[(cname, cls)]["found"], **s))
            edits.append(rec)
    report = {
        "title": "Perception robustness: SAM 3.1 on Muse Image counterfactuals",
        "split": split,
        "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "instructions_sha256": instructions_sha256(),
        "instructions": [i.id for i in instructions],
        "conditions": {k: (v if v == CHOOSER else dict(v)) for k, v in conditions.items()},
        "uncorrected_conditions": list(uncorrected_conditions),
        "prompts_used": prompts_used,
        "edit_request": {"model": EDIT_MODEL, "output_format": OUTPUT_FORMAT, **EDIT_OPTIONS,
                         "size": "source width x height"},
        "frames": [fr.meta for fr in frames],
        "edits": edits,
        "corrections": [{"frame_id": e["frame_id"], "instruction_id": e["instruction_id"], **e["correction"]}
                        for e in edits if e.get("correction")],
        "rows": rows,
        "summary": summarize(rows, edits),
        "sam": {"model": SAM_MODEL, "calls": sam.calls, "network_calls": sam.network,
                "latency_s_mean": _mean(sam.latencies),
                "latency_s_max": round(max(sam.latencies), 3) if sam.latencies else None},
        "wall_time_s": round(time.time() - started, 1),
        "notes": [
            "Ground truth: visible-pixel masks from a segmentation render of the same simulator frame.",
            "Edited frames are resampled to the source size. 'corrected' also undoes one whole-frame similarity "
            "transform per edit (the editor's drift; listed in 'corrections'); 'uncorrected' does not.",
            "Condition A uses the Muse-to-SAM prompt chooser on each original frame and reuses its phrases for "
            "that frame's edits; condition B uses fixed colour-free phrases.",
            "iou and centroid error use SAM's top-confidence match; iou_best (chosen with the ground truth) is "
            "diagnostic only.",
            "Local sensor corruptions preserve geometry by construction; the geometry check is still run.",
            "Edits whose geometry changed stay in every table; their scores use a ground truth that no longer fits.",
        ],
    }
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.json").write_text(json.dumps(report, indent=1, default=float))
        (out / "report.md").write_text(render_markdown(report))
    return report


KEPT = ("original", "preserved", "kept")
CHANGED = ("moved", "resized", "missing", "reframed")


def _stats(rs: list[dict]) -> dict:
    kept = [r for r in rs if r["geometry"] in KEPT]
    return {
        "n": len(rs),
        "found_rate": _mean([float(r["found"]) for r in rs]),
        "iou_mean": _mean([r["iou"] for r in rs]),
        "iou_delta_mean": _mean([r.get("iou_delta") for r in rs]),
        "centroid_err_px_mean": _mean([r["centroid_err_px"] for r in rs]),
        "n_geometry_kept": len(kept),
        "kept_found_rate": _mean([float(r["found"]) for r in kept]),
        "kept_iou_delta_median": _median([r.get("iou_delta") for r in kept]),
        "kept_centroid_err_px_median": _median([r["centroid_err_px"] for r in kept]),
        "n_geometry_weakened": sum(1 for r in rs if r["geometry"] == "weakened"),
        "n_geometry_changed": sum(1 for r in rs if r["geometry"] in CHANGED),
        "geometry": {g: sum(1 for r in rs if r["geometry"] == g) for g in sorted({r["geometry"] for r in rs})},
        "by_class": {c: {"iou_mean": _mean([r["iou"] for r in rs if r["cls"] == c]),
                         "found_rate": _mean([float(r["found"]) for r in rs if r["cls"] == c])}
                     for c in sorted({r["cls"] for r in rs})},
    }


def summarize(rows: list[dict], edits: list[dict]) -> dict:
    out: dict = {}
    for key in sorted({(r["condition"], r["correction"]) for r in rows}):
        sel = [r for r in rows if (r["condition"], r["correction"]) == key]
        if key[1] == "uncorrected" and not any(r["version"] != "original" for r in sel):
            continue
        fams = {}
        for fam in dict.fromkeys(r["family"] for r in sel):
            fams[fam] = _stats([r for r in sel if r["family"] == fam])
        fams["all_edits"] = _stats([r for r in sel if r["version"] != "original"])
        out[f"{key[0]}/{key[1]}"] = fams
    model = [e for e in edits if e.get("kind") == "model"]
    lat = [e["latency_s"] for e in model if e["status"] == "ok"]
    regs = [e["correction"] for e in model if e.get("correction")]
    out["_edits"] = {
        "model_edits": len(model),
        "ok": sum(e["status"] == "ok" for e in model),
        "refused": sum(e["status"] == "refused" for e in model),
        "errors": sum(e["status"] == "error" for e in model),
        "reframed": sum(1 for e in model if e.get("geometry", {}).get("verdict") == "reframed"),
        "changed": sum(1 for e in model if e.get("geometry", {}).get("verdict") == "changed"),
        "latency_s_mean": _mean(lat),
        "latency_s_min": round(min(lat), 2) if lat else None,
        "latency_s_max": round(max(lat), 2) if lat else None,
        "output_sizes": sorted({tuple(e["out_size"]) for e in model if e["status"] == "ok"}),
        "correction_shift_px_median": _median([float(np.hypot(r["dx"], r["dy"])) for r in regs]),
        "correction_shift_px_max": round(max(float(np.hypot(r["dx"], r["dy"])) for r in regs), 2) if regs else None,
        "correction_scales": sorted({r["scale"] for r in regs}),
    }
    return out


def render_markdown(rep: dict) -> str:
    s = rep["summary"]
    L = [f"# {rep['title']}", "",
         f"Split: {rep['split']}. Frames: {len(rep['frames'])}. Instructions: {len(rep['instructions'])} "
         f"(sha256 {rep['instructions_sha256'][:16]}).", "",
         "Prompt conditions: " + "; ".join(
             f"{k} = {'Muse-to-SAM chooser per original frame' if v == CHOOSER else json.dumps(v)}"
             for k, v in rep["conditions"].items()) + ".", ""]
    for cname, per in rep["prompts_used"].items():
        if any(v.get("source") == "chooser" for v in per.values()):
            L.append(f"Chooser phrases ({cname}): " + "; ".join(
                f"{fid}: " + ", ".join(f"{c} \"{p}\"" for c, p in v["prompts"].items()) for fid, v in per.items()))
    L.append("")
    for key, fams in s.items():
        if key.startswith("_"):
            continue
        L += [f"## {key}", "",
              "| family | rows | found | IoU | ΔIoU | centroid px | kept: found / median ΔIoU / median centroid px | geometry |",
              "|---|---|---|---|---|---|---|---|"]
        for name, f in fams.items():
            L.append(f"| {name} | {f['n']} | {f['found_rate']} | {f['iou_mean']} | {f['iou_delta_mean']} | "
                     f"{f['centroid_err_px_mean']} | {f['kept_found_rate']} / {f['kept_iou_delta_median']} / "
                     f"{f['kept_centroid_err_px_median']} | "
                     + ", ".join(f"{k} {v}" for k, v in f["geometry"].items()) + " |")
        L.append("")
    e = s["_edits"]
    L += [f"Model edits: {e['model_edits']} (ok {e['ok']}, refused {e['refused']}, errors {e['errors']}); "
          f"geometry changed in {e['changed']}, reframed in {e['reframed']}. "
          f"Latency per edit (as recorded): mean {e['latency_s_mean']} s (min {e['latency_s_min']}, max "
          f"{e['latency_s_max']}). Output sizes: {e['output_sizes']}. Whole-frame correction: median shift "
          f"{e['correction_shift_px_median']} px, max {e['correction_shift_px_max']} px, scales "
          f"{e['correction_scales']}.", "",
          "| frame | edit | dx | dy | scale | fit px |", "|---|---|---|---|---|---|"]
    for c in rep["corrections"]:
        L.append(f"| {c['frame_id']} | {c['instruction_id']} | {c.get('dx')} | {c.get('dy')} | {c.get('scale')} | "
                 f"{c.get('fit_px')} |")
    L += ["", f"SAM: {rep['sam']['calls']} calls ({rep['sam']['network_calls']} over the network), "
          f"mean latency {rep['sam']['latency_s_mean']} s.", "", "Notes:"]
    L += [f"- {n}" for n in rep["notes"]]
    refused = [x for x in rep["edits"] if x.get("status") in ("refused", "error")]
    if refused:
        L += ["", "Refusals and errors:"] + [f"- {x['frame_id']} / {x['instruction_id']}: {x['status']}: {x['error']}"
                                              for x in refused]
    return "\n".join(L) + "\n"
