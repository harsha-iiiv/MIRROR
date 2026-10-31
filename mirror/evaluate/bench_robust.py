"""Perception robustness benchmark (pre-registered).

Frozen ground-truth frames (rendered before the freeze) x the plan track's
frozen instruction list: model edits by muse-image-1.0, sensor edits by local
deterministic code. sam-3.1 runs on every original and edited frame; masks are
scored against the simulator's ground-truth masks. Edits whose geometry
changed (mirror.robustness.geometry.check_geometry) are reported in their own
groups, never dropped.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import numpy as np


def iou(a: np.ndarray, b: np.ndarray) -> float:
    a, b = np.asarray(a, bool), np.asarray(b, bool)
    u = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / u) if u else 1.0


def centroid_px(mask: np.ndarray) -> Optional[tuple[float, float]]:
    ys, xs = np.nonzero(np.asarray(mask, bool))
    return (float(xs.mean()), float(ys.mean())) if xs.size else None


def score_masks(gt: dict, pred: dict, classes) -> dict:
    """``gt``/``pred``: class -> boolean mask (same shape); absent = not found."""
    out = {}
    for k in classes:
        g = gt.get(k)
        if g is None or not np.asarray(g).any():
            continue
        p = pred.get(k)
        if p is None or not np.asarray(p).any():
            out[k] = dict(found=False, iou=0.0, centroid_px=None)
            continue
        cg, cp = centroid_px(g), centroid_px(p)
        out[k] = dict(found=True, iou=iou(g, p), centroid_px=float(np.hypot(cg[0] - cp[0], cg[1] - cp[1])))
    return out


def sam_masks(client, png: bytes, prompts: dict, size: tuple[int, int], budget) -> dict:
    """One sam-3.1 call per class prompt; the highest-confidence object."""
    w, h = size
    out = {}
    for cls, phrase in prompts.items():
        budget.charge("sam-3.1")
        seg = client.sam_segment(png, phrase)
        objs = [o for f in seg.frames for o in f.objects]
        if objs:
            best = max(objs, key=lambda o: o.confidence)
            m = np.asarray(best.full_mask(seg.width, seg.height), bool)
            if (seg.width, seg.height) != (w, h):
                from PIL import Image
                m = np.asarray(Image.fromarray(m.astype(np.uint8) * 255).resize((w, h), Image.NEAREST)) > 0
            out[cls] = m
    return out


def run(design: dict, inputs: dict, *, ledger, budget, out_dir: Path, client=None,
        repo_root: Optional[Path] = None) -> dict:
    from mirror.robustness.frames import GTFrame, decode_png, encode_png
    from mirror.robustness.geometry import check_geometry
    from mirror.robustness.instructions import EditInstruction
    from mirror.robustness.local import apply_local
    if client is None:
        from mirror.meta import MetaClient
        client = MetaClient.from_env()
    repo_root = repo_root or Path(__file__).resolve().parents[2]
    prompts = design["instructions"]["sam_prompts"]
    classes = list(prompts)
    instructions = [EditInstruction(**d) for d in design["instructions"]["instructions"]]
    frames_dir = repo_root / Path(inputs["files"][0]["path"]).parent
    rows = []
    for fid in inputs["provenance"]["frames"]:
        g = GTFrame.load(frames_dir, fid)
        size = g.size
        try:
            base_pred = sam_masks(client, g.png(), prompts, size, budget)
            ledger.append(kind="sam", frame=fid, edit=None, requests=len(prompts))
            base = score_masks(g.masks, base_pred, classes)
        except Exception as exc:
            from .prereg import BudgetExceeded
            if isinstance(exc, BudgetExceeded):
                raise
            rows.append(dict(frame=fid, edit=None, error=f"{type(exc).__name__}: {exc}"[:300]))
            ledger.append(kind="error", frame=fid, error=rows[-1]["error"])
            continue
        for ins in instructions:
            row: dict[str, Any] = dict(frame=fid, edit=ins.id, family=ins.family, kind=ins.kind, original=base)
            try:
                if ins.kind == "model":
                    budget.charge("muse-image-1.0")
                    res = client.images_edit([g.png()], ins.prompt(), output_format="png")
                    edited = decode_png(res.images[0])
                    ledger.append(kind="edit", frame=fid, edit=ins.id, model=getattr(res, "model", None))
                else:
                    edited = apply_local(g.rgb, ins)
                rep, ed = check_geometry(g.rgb, edited, g.masks)
                row["image"] = _save_jpeg(ed, Path(out_dir) / "edits" / f"{fid}__{ins.id}.jpg", out_dir)
                pred = sam_masks(client, encode_png(ed), prompts, size, budget)
                ledger.append(kind="sam", frame=fid, edit=ins.id, requests=len(prompts))
                row.update(geometry=rep.verdict, changed_objects=rep.changed_objects(),
                           edited=score_masks(g.masks, pred, classes))
            except Exception as exc:
                from .prereg import BudgetExceeded
                if isinstance(exc, BudgetExceeded):
                    raise
                row["error"] = f"{type(exc).__name__}: {exc}"[:300]
                ledger.append(kind="error", frame=fid, edit=ins.id, error=row["error"])
            rows.append(row)
    from . import costs
    usage = costs.summarize_usage(list(getattr(client, "usage", []) or []))
    return dict(rows=rows, usage=usage, cost_usd=costs.total_cost(usage), summary=summarize(rows, design))


def _save_jpeg(rgb: np.ndarray, path: Path, out_dir) -> str:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.asarray(rgb, np.uint8)).save(path, format="JPEG", quality=88)
    return path.relative_to(Path(out_dir)).as_posix()


def _agg(sel: list[dict]) -> dict:
    keys = [(r, k) for r in sel for k in r["edited"] if k in r["original"]]
    d_iou = [r["edited"][k]["iou"] - r["original"][k]["iou"] for r, k in keys]
    found = [r["edited"][k]["found"] for r, k in keys]
    cpx = [r["edited"][k]["centroid_px"] for r, k in keys if r["edited"][k]["centroid_px"] is not None]
    return dict(edits=len(sel), objects=len(keys), delta_iou_median=float(np.median(d_iou)) if d_iou else None,
                found_rate=float(np.mean(found)) if found else None,
                centroid_px_median=float(np.median(cpx)) if cpx else None)


def summarize(rows: list[dict], design: dict) -> dict:
    done = [r for r in rows if "edited" in r]
    groups = {v: _agg([r for r in done if r["geometry"] == v]) for v in ("preserved", "changed", "reframed")}
    groups["all"] = _agg(done)
    first = {}
    for r in rows:
        if "original" in r and r["frame"] not in first:
            first[r["frame"]] = r["original"]
    orig = [o for v in first.values() for o in v.values()]
    per = {}
    for d in design["instructions"]["instructions"]:
        sel = [r for r in done if r["edit"] == d["id"]]
        per[d["id"]] = dict(family=d["family"], kind=d["kind"], n=len(sel),
                            geometry={v: sum(1 for r in sel if r["geometry"] == v) for v in ("preserved", "changed", "reframed")},
                            **{k: v for k, v in _agg([r for r in sel if r["geometry"] == "preserved"]).items()
                               if k in ("delta_iou_median", "found_rate")})
    th = design["thresholds"]
    g = groups["preserved"]
    checks = {
        f"median IoU change >= {th['delta_iou_median_min']} (geometry preserved)":
            g["delta_iou_median"] is not None and g["delta_iou_median"] >= th["delta_iou_median_min"],
        f"found rate >= {th['found_rate_min']} (geometry preserved)":
            g["found_rate"] is not None and g["found_rate"] >= th["found_rate_min"],
        f"median centroid error <= {th['centroid_px_median_max']} px (geometry preserved)":
            g["centroid_px_median"] is not None and g["centroid_px_median"] <= th["centroid_px_median_max"],
    }
    return dict(groups=groups, per_instruction=per, errors=sum(1 for r in rows if "error" in r),
                original_found_rate=float(np.mean([o["found"] for o in orig])) if orig else None,
                threshold_checks=checks)


def _r(x, nd=3):
    return "n/a" if x is None else f"{x:.{nd}f}"


def markdown(report: dict, design: dict) -> str:
    from . import costs
    res = report.get("result", {})
    L = [f"# {report.get('title', 'Perception robustness benchmark (pre-registered)')}", ""]
    if report.get("rehearsal"):
        L += ["> REHEARSAL on a development frame and a subset of instructions. Not an evaluation result.", ""]
    n_ins = len(design["instructions"]["instructions"])
    L += [f"Status: {report['status']}. Design `{report['design_sha256'][:16]}`, inputs `{report['inputs_sha256'][:16]}`, "
          f"code `{report['freeze_code_sha256'][:16]}`. {design['frames']['count']} frames x {n_ins} instructions.", ""]
    if "summary" not in res:
        return "\n".join(L + [f"Stopped: {res.get('stopped', '')}", ""])
    s = res["summary"]
    L += ["## What was measured", "",
          "Each ground-truth frame was edited once per instruction (by the image model, or by local code for camera "
          "noise and blur). SAM 3.1 segmented the cup, block and marker in the original and in each edited frame, and "
          "every mask was compared with the simulator's true mask. A geometry check decides whether an edit kept the "
          "objects where they were; edits that moved, resized or removed something are reported separately, not dropped.", "",
          "| geometry after the edit | edits | object masks scored | median IoU change | found rate | median centroid error |",
          "|---|---|---|---|---|---|"]
    for k, v in s["groups"].items():
        L.append(f"| {k} | {v['edits']} | {v['objects']} | {_r(v['delta_iou_median'])} | {_r(v['found_rate'])} | "
                 f"{_r(v['centroid_px_median'], 1)} px |")
    L += ["", f"Found rate on the unedited frames: {_r(s['original_found_rate'])}.", "",
          "| instruction | family | applied by | preserved / changed / reframed | median IoU change (preserved) |",
          "|---|---|---|---|---|"]
    L += [f"| {k} | {v['family']} | {'image model' if v['kind'] == 'model' else 'local code'} | {v['geometry']['preserved']} / "
          f"{v['geometry']['changed']} / {v['geometry']['reframed']} | {_r(v.get('delta_iou_median'))} |"
          for k, v in s["per_instruction"].items()]
    L += ["", "## Pre-stated checks", ""] + [f"- {k}: {'met' if v else 'not met'}" for k, v in s["threshold_checks"].items()]
    L += ["", "## Spend", "", "| model | network calls | replayed | median latency | cost |", "|---|---|---|---|---|"]
    for m, u in res.get("usage", {}).items():
        lat = "n/a" if u["latency_s_median"] is None else f"{u['latency_s_median']:.1f} s"
        L.append(f"| {m} | {u['network_calls']} | {u['replayed_calls']} | {lat} | ${u['cost_usd']:.3f} |")
    L += ["", f"Total ${res.get('cost_usd', 0):.3f} at list prices ({costs.PRICES_SOURCE}).", "",
          "## Every frame and instruction", "", "| frame | instruction | geometry | changed objects | IoU before -> after (per object) | edited image |",
          "|---|---|---|---|---|---|"]
    for r in res["rows"]:
        if r.get("edit") is None:
            L.append(f"| {r['frame']} | (original) | | | error: {r.get('error', '')[:120]} | |")
            continue
        if "error" in r:
            L.append(f"| {r['frame']} | {r['edit']} | error | | {r['error'][:120]} | |")
            continue
        ious = "; ".join(f"{k} {r['original'][k]['iou']:.2f} -> {v['iou']:.2f}" for k, v in r["edited"].items() if k in r["original"])
        L.append(f"| {r['frame']} | {r['edit']} | {r['geometry']} | {', '.join(r['changed_objects']) or '-'} | {ious} | "
                 f"{r.get('image', '')} |")
    L += ["", f"Errors: {s['errors']}. Budget lines: {json.dumps(report['budget'])}. Every attempt is in ledger.jsonl "
          f"({report['ledger_entries']} entries).", ""]
    return "\n".join(L)
