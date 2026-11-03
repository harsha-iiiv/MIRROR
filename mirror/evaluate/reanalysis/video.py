"""Video benchmark re-analysis (derived, offline, no model calls).

Inputs are the stored registration artifacts: rendered clips and simulator
ground truth under ``inputs/``, saved sam-3.1 masks under ``tracks/`` and the
per-seed rows under ``seeds/``. The registered estimates are rebuilt with the
same reconstruction the registration used (``bench_video.reconstruct``), and the
rebuilt row is checked against the stored row before anything else is
computed from it.

Extraction (slow, ~1 min per seed per variant) writes one compact per-seed
frame record; ``analyse`` (fast) turns those records into the summary.

Variants of the per-frame position estimate (all through the identical
reconstruction; only the kit-object masks differ):

* ``sam``: the registered sam-3.1 masks (the reported estimate).
* ``colour``: a classical colour-threshold segmentation of the same 5 fps
  clips sam-3.1 saw (largest connected component of the object's nominal
  colour, thresholds fixed from the scene colours, not fitted). Table, tray,
  gripper and other labels keep the sam-3.1 masks so camera calibration is
  identical; only cup, block and marker masks are replaced.
* ``oracle``: the simulator's rendered segmentation masks for the kit objects
  (an upper bound for the reconstruction geometry, not a baseline).

Baselines computed from these without new reconstruction: ``static`` (each
object stays at its first sam-3.1 detection) and ``hold_last`` (sam-3.1 with
missed frames filled by the last detection).
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Optional

import numpy as np

KIT_KINDS = ("cup", "block", "marker")
GATE_M = 0.05                 # the registration's 5 cm "found" gate (reported, never applied to the unconditional view)
MISS_PENALTY = "hold_last"    # stated penalty for a miss in the imputed view: error of the last detection (table centre if none yet)
TILT_UNOBSERVABLE_NOTE = ("cup tilt is estimated as the in-image component in the side view only; the component "
                          "along the side camera's viewing direction is unobservable, and frames without a side-view "
                          "cup silhouette have no tilt estimate (marked unobservable, never scored as zero)")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ------------------------------------------------------------- extraction ---

def _kit_labels(tracks) -> list[str]:
    labs = {l for c in tracks.masks for l in tracks.masks[c]}
    return sorted(l for l in labs if any(l.startswith(k) for k in KIT_KINDS))


def _swap_masks(tracks, new: dict):
    """A copy of ``tracks`` with the kit-object masks replaced by ``new``
    ({cam: {label: {i: rle}}}); every other label keeps its masks."""
    from mirror.perception.track import Tracks
    masks = {c: {l: dict(fr) for l, fr in d.items()} for c, d in tracks.masks.items()}
    for c, d in new.items():
        for l, fr in d.items():
            masks.setdefault(c, {})[l] = fr
    t = Tracks(fps=tracks.fps, n_frames=tracks.n_frames, size=dict(tracks.size), masks=masks,
               source=tracks.source + "+reanalysis", notes=list(tracks.notes), static=set(tracks.static))
    t.extra = dict(getattr(tracks, "extra", {}) or {})
    return t


def oracle_masks(gt: dict, tracks, labels: list[str]) -> dict:
    """Simulator segmentation masks for the kit labels at the tracks' frame times."""
    step = int(round(gt["fps"] / tracks.fps))
    out: dict = {}
    for i in range(tracks.n_frames):
        j = i * step
        if j >= len(gt["frames"]):
            break
        for c, d in gt["frames"][j]["masks"].items():
            for l in labels:
                if l in d:
                    rle = d[l]["rle"] if isinstance(d[l], dict) and "rle" in d[l] else d[l]
                    out.setdefault(c, {}).setdefault(l, {})[i] = rle
    return out


def _colour_rule(kind: str, rgba) -> callable:
    """Fixed HSV rule from the object's nominal colour (OpenCV ranges)."""
    import cv2
    rgb = np.uint8([[[int(255 * rgba[0]), int(255 * rgba[1]), int(255 * rgba[2])]]])
    h, s, v = (int(x) for x in cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[0, 0])
    if s < 40:                                   # near-white (the cup): bright and unsaturated, plus its yellow beads
        def rule(hsv):
            white = (hsv[..., 1] < 40) & (hsv[..., 2] > 200)
            yellow = (np.abs(hsv[..., 0].astype(int) - 28) <= 7) & (hsv[..., 1] > 120) & (hsv[..., 2] > 150)
            return white | yellow
        return rule
    def rule(hsv):
        dh = np.abs(hsv[..., 0].astype(int) - h)
        dh = np.minimum(dh, 180 - dh)
        return (dh <= 10) & (hsv[..., 1] > max(80, s - 90)) & (hsv[..., 2] > 50)
    return rule


def colour_masks(root: Path, dv, scene: dict, tracks, labels: list[str], min_area: int = 30) -> dict:
    """Largest connected component of each kit object's colour per frame and camera."""
    import cv2
    from scipy import ndimage
    from mirror.evaluate.bench_video import sam_copy_name
    from mirror.perception.masks import rle_encode
    rgba = {o["id"]: o.get("rgba") for o in scene["objects"]}
    kind = {o["id"]: o["kind"] for o in scene["objects"]}
    rules = {l: _colour_rule(kind[l], rgba[l]) for l in labels if rgba.get(l)}
    out: dict = {}
    for c in dv.videos:
        cap = cv2.VideoCapture(str(Path(root) / sam_copy_name(c)))
        i = 0
        while i < tracks.n_frames:
            ok, bgr = cap.read()
            if not ok:
                break
            hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
            for l, rule in rules.items():
                m = rule(hsv)
                lab, n = ndimage.label(m)
                if n == 0:
                    continue
                areas = ndimage.sum(m, lab, index=np.arange(1, n + 1))
                k = int(np.argmax(areas))
                if areas[k] < min_area:
                    continue
                out.setdefault(c, {}).setdefault(l, {})[i] = rle_encode(lab == (k + 1))
            i += 1
        cap.release()
    return out


def _est_rows(est, dv) -> dict:
    """Per label: frame keys (ground-truth frame index), x, y, z, found, tilt."""
    kinds = est.scene_checks.get("kinds", {})
    rows = {}
    for lab, P in est.objects.items():
        if kinds.get(lab) not in KIT_KINDS:
            continue
        tilt = (est.tilt_deg or {}).get(lab)
        rows[lab] = dict(
            kind=kinds[lab],
            frame=[int(round(float(t) * dv.fps)) for t in est.t],
            found=[bool(f) and not math.isnan(float(P[i][0])) for i, f in enumerate(est.found[lab])],
            x=[None if math.isnan(float(p[0])) else round(float(p[0]), 5) for p in P],
            y=[None if math.isnan(float(p[1])) else round(float(p[1]), 5) for p in P],
            tilt_deg=None if tilt is None else [None if math.isnan(float(v)) else round(float(v), 4) for v in tilt])
    return rows


def _gt_rows(gt: dict) -> dict:
    kinds = {o["id"]: o["kind"] for o in gt["objects"]}
    out = {}
    for oid, k in kinds.items():
        if k not in KIT_KINDS:
            continue
        r = dict(kind=k, x=[], y=[], z=[], vis_top=[], vis_side=[], held=[], tilt_deg=[])
        for f in gt["frames"]:
            p = f["objects"].get(oid)
            r["x"].append(None if p is None else round(p["pos"][0], 5))
            r["y"].append(None if p is None else round(p["pos"][1], 5))
            r["z"].append(None if p is None else round(p["pos"][2], 5))
            r["vis_top"].append(oid in f["masks"].get("top", {}))
            r["vis_side"].append(oid in f["masks"].get("side", {}))
            r["held"].append(f.get("held") == oid)
            r["tilt_deg"].append(f.get("cup_tilt_deg", {}).get(oid))
        out[oid] = r
    return out


def extract_seed(demo_dir: Path, tracks_path: Path, seed_row: Optional[dict], variants=("sam", "colour", "oracle")) -> dict:
    from mirror.evaluate import bench_video as bv
    from mirror.perception.track import load_tracks
    from mirror.perception.video import load_demo_video
    manifest = demo_dir / "video.json"
    dv, gt = load_demo_video(manifest)
    scene = json.loads((demo_dir / "scene.json").read_text())
    sam = load_tracks(tracks_path)
    labels = _kit_labels(sam)
    rec = dict(demo_dir=str(demo_dir.name), fps=dv.fps, n_frames=dv.n_frames, kit_labels=labels,
               tracks_sha256=sha256_file(tracks_path), ground_truth=_gt_rows(gt),
               gt_events=[dict(type=e["type"], object=e["object_id"], t=e["t"]) for e in gt["events"]],
               estimates={}, reconstruction_notes={})
    for v in variants:
        if v == "sam":
            tr = _swap_masks(sam, {})
        elif v == "oracle":
            tr = _swap_masks(sam, oracle_masks(gt, sam, labels))
        elif v == "colour":
            tr = _swap_masks(sam, colour_masks(demo_dir, dv, scene, sam, labels))
        else:
            raise ValueError(v)
        try:
            est, _scene, _trace, _checks = bv.reconstruct(dv, tr)
        except Exception as exc:  # a variant that cannot be reconstructed is reported, not dropped
            rec["estimates"][v] = None
            rec["reconstruction_notes"][v] = f"failed: {type(exc).__name__}: {exc}"[:300]
            continue
        rec["estimates"][v] = _est_rows(est, dv)
        rec["reconstruction_notes"][v] = list(tr.notes)
        if v == "sam" and seed_row is not None and "tracking" in seed_row:
            kinds = est.scene_checks.get("kinds", {})
            fe = bv.frame_errors(bv.gt_frames(gt), bv.est_frames(est, kinds, gt_fps=dv.fps))
            stored = seed_row["tracking"]
            rec["reproduces_stored_row"] = dict(
                found_rate_all=fe["found_rate_all"] == stored["found_rate_all"],
                centroid_cm_all=fe["centroid_cm_all"] == stored["centroid_cm_all"],
                tilt_deg=fe["tilt_deg"] == stored["tilt_deg"])
    return rec


SOURCES = {
    "eval_video2": dict(results="results/eval_video2", tracks="tracks", seeds_dir="seeds", inputs="inputs",
                        reported=True, note="registration 2 (reported, 16 of 20 seeds run)"),
    "eval_video_attempt_1": dict(results="results/eval_video", tracks="attempt_1/tracks", seeds_dir=None,
                                 inputs="inputs", reported=False,
                                 note="registration 1, attempt 1 (failed run; tracking was valid but never summarised)"),
}


def demo_dir_for(root: Path, inputs: str, seed: int) -> Path:
    d = root / inputs / f"seed{seed}"
    subs = [p for p in d.iterdir() if p.is_dir()]
    if len(subs) != 1:
        raise FileNotFoundError(f"expected one demo directory under {d}")
    return subs[0]


def extract_all(repo: Path, out: Path, sources=tuple(SOURCES), variants=("sam", "colour", "oracle")) -> None:
    for name in sources:
        cfg = SOURCES[name]
        base = repo / cfg["results"]
        for tp in sorted((base / cfg["tracks"]).glob("*.json.gz")):
            seed = int(tp.name.split(".")[0])
            dst = out / name / f"{seed}.json.gz"
            if dst.exists():
                continue
            row = None
            if cfg["seeds_dir"]:
                rp = base / cfg["seeds_dir"] / f"{seed}.json"
                row = json.loads(rp.read_text()) if rp.exists() else None
            rec = extract_seed(demo_dir_for(base, cfg["inputs"], seed), tp, row, variants)
            rec.update(seed=seed, source=name)
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_suffix(".tmp")
            with gzip.open(tmp, "wt") as fh:
                json.dump(rec, fh, separators=(",", ":"))
            tmp.replace(dst)
            print(f"{name} {seed}: done", flush=True)


# --------------------------------------------------------------- analysis ---

def _q(v) -> Optional[dict]:
    v = [float(x) for x in v]
    if not v:
        return None
    a = np.asarray(v)
    return dict(n=len(v), median=round(float(np.median(a)), 4),
                p90=round(float(np.quantile(a, 0.9, method="inverted_cdf")), 4),
                p95=round(float(np.quantile(a, 0.95, method="inverted_cdf")), 4), max=round(float(a.max()), 4), mean=round(float(a.mean()), 4))


def _q_with_misses(errs: list, n_miss: int) -> dict:
    """Quantiles of the error with each miss counted as an infinite error:
    a quantile is reported as None ("undefined, misses") once misses reach it."""
    if n_miss == 0:
        q = _q(errs) or {}
        return dict(n=len(errs), misses=0, median=q.get("median"), p90=q.get("p90"), p95=q.get("p95"))
    a = np.sort(np.asarray(errs, float))
    n = len(a) + n_miss
    out = dict(n=n, misses=n_miss)
    for name, q in (("median", 0.5), ("p90", 0.9), ("p95", 0.95)):
        k = int(math.ceil(q * n)) - 1
        out[name] = round(float(a[k]), 4) if 0 <= k < len(a) else None
    return out


def frame_table(rec: dict, variant: str) -> list[dict]:
    """One row per (sampled frame, kit object) where the object is visible in
    some camera: true position, the variant's estimate (or a miss), and the
    nearest other kit object's true position (for identity errors)."""
    est = rec["estimates"].get(variant)
    if est is None:
        return []
    gt = rec["ground_truth"]
    rows = []
    for lab, e in est.items():
        g = gt.get(lab)
        if g is None:
            continue
        for j, fk in enumerate(e["frame"]):
            if fk >= len(g["x"]) or g["x"][fk] is None:
                continue
            vis = g["vis_top"][fk] or g["vis_side"][fk]
            if not vis:
                continue
            gx, gy = g["x"][fk], g["y"][fk]
            others = [(o, gg["x"][fk], gg["y"][fk]) for o, gg in gt.items() if o != lab and gg["x"][fk] is not None]
            r = dict(frame=fk, object=lab, kind=g["kind"], gx=gx, gy=gy, held=g["held"][fk],
                     vis_both=g["vis_top"][fk] and g["vis_side"][fk], found=e["found"][j],
                     ex=e["x"][j], ey=e["y"][j])
            if r["found"] and r["ex"] is not None:
                r["err_m"] = float(math.hypot(r["ex"] - gx, r["ey"] - gy))
                r["nearest_other_m"] = min((math.hypot(r["ex"] - ox, r["ey"] - oy) for _, ox, oy in others), default=None)
            rows.append(r)
    return rows


def _classify(r: dict) -> str:
    if "err_m" not in r:
        return "miss"
    if r["err_m"] <= GATE_M:
        return "hit"
    if r.get("nearest_other_m") is not None and r["nearest_other_m"] <= GATE_M:
        return "identity"           # within 5 cm of another kit object, more than 5 cm from its own
    return "outlier"


def _impute(rows: list[dict], how: str) -> list[float]:
    """Unconditional error per row with misses filled: ``hold_last`` (last
    detection of that object, table centre if none yet) or ``static`` (every
    frame at the object's first detection)."""
    by: dict = {}
    for r in sorted(rows, key=lambda r: (r["object"], r["frame"])):
        by.setdefault(r["object"], []).append(r)
    out = []
    for lab, rs in by.items():
        first = next(((r["ex"], r["ey"]) for r in rs if "err_m" in r), (0.0, 0.0))
        last = None
        for r in rs:
            if how == "static":
                px, py = first
            elif "err_m" in r:
                px, py = r["ex"], r["ey"]
                last = (px, py)
            else:
                px, py = last if last is not None else (0.0, 0.0)
            out.append(math.hypot(px - r["gx"], py - r["gy"]))
    return out


def position_summary(rows: list[dict]) -> dict:
    cls = [_classify(r) for r in rows]
    n = len(rows)
    counts = {k: cls.count(k) for k in ("hit", "outlier", "identity", "miss")}
    present = [r["err_m"] * 100 for r in rows if "err_m" in r]
    gated = [r["err_m"] * 100 for r, c in zip(rows, cls) if c == "hit"]
    return dict(
        n_visible_object_frames=n,
        counts=counts,
        rates={k: round(v / n, 4) if n else None for k, v in counts.items()},
        error_cm_gated_hits_only=_q(gated),
        error_cm_all_estimates_no_gate=_q(present),
        error_cm_misses_as_infinite=_q_with_misses(present, counts["miss"]),
        error_cm_misses_hold_last=_q([e * 100 for e in _impute(rows, "hold_last")]),
        error_cm_static_first_detection=_q([e * 100 for e in _impute(rows, "static")]),
        fraction_within_5cm_unconditional=round(counts["hit"] / n, 4) if n else None,
        fraction_within_2cm_unconditional=round(sum(1 for e in present if e <= 2.0) / n, 4) if n else None,
    )


def tilt_summary(rec: dict) -> dict:
    """Ground-truth tilt range and the registered tilt measure against a
    constant-zero estimator, on exactly the frames the registration scored and
    on every side-visible frame."""
    out = {}
    gt = rec["ground_truth"]
    est = (rec["estimates"].get("sam") or {})
    for lab, g in gt.items():
        if g["kind"] != "cup":
            continue
        tilts = [t for t in g["tilt_deg"] if t is not None]
        held = [t for t, h in zip(g["tilt_deg"], g["held"]) if t is not None and h]
        e = est.get(lab)
        scored, zero_scored, side_vis, side_est = [], [], 0, 0
        if e and e.get("tilt_deg"):
            for j, fk in enumerate(e["frame"]):
                if fk >= len(g["x"]) or g["x"][fk] is None:
                    continue
                if not (g["vis_top"][fk] or g["vis_side"][fk]):
                    continue
                gtilt = g["tilt_deg"][fk]
                et = e["tilt_deg"][j]
                if g["vis_side"][fk]:
                    side_vis += 1
                    side_est += et is not None
                ok = e["found"][j] and e["x"][j] is not None and \
                    math.hypot(e["x"][j] - g["x"][fk], e["y"][j] - g["y"][fk]) <= GATE_M
                if ok and gtilt is not None and et is not None:
                    scored.append(abs(et - gtilt))
                    zero_scored.append(abs(0.0 - gtilt))
        zero_all = [abs(t) for t, vs in zip(g["tilt_deg"], g["vis_side"]) if t is not None and vs]
        out[lab] = dict(
            gt_tilt_deg_all_frames=_q(tilts), gt_tilt_deg_held_frames=_q(held),
            registered_measure_frames=len(scored),
            registered_tilt_error_deg=_q(scored),
            constant_zero_error_deg_same_frames=_q(zero_scored),
            constant_zero_error_deg_all_side_visible_frames=_q(zero_all),
            side_visible_sampled_frames=side_vis, side_visible_with_tilt_estimate=side_est,
            tilt_unobservable_fraction=round(1 - side_est / side_vis, 4) if side_vis else None)
    return out


def analyse(cache: Path) -> dict:
    recs = []
    for p in sorted(cache.glob("*/*.json.gz")):
        with gzip.open(p, "rt") as fh:
            recs.append(json.load(fh))
    report = dict(version="reanalysis_v1/video", gate_m=GATE_M, miss_penalty=MISS_PENALTY,
                  tilt_observability=TILT_UNOBSERVABLE_NOTE, sources={}, per_seed=[])
    for src in sorted({r["source"] for r in recs}):
        rs = [r for r in recs if r["source"] == src]
        block = dict(seeds=[r["seed"] for r in rs], note=SOURCES[src]["note"], variants={})
        for v in ("sam", "colour", "oracle"):
            rows = [x for r in rs for x in frame_table(r, v)]
            failed = [r["seed"] for r in rs if r["estimates"].get(v) is None]
            ps = position_summary(rows) if rows else None
            if ps:
                ps["by_kind"] = {k: position_summary([x for x in rows if x["kind"] == k]) for k in KIT_KINDS}
                ps["held_frames_only"] = position_summary([x for x in rows if x["held"]])
            block["variants"][v] = dict(position=ps, reconstruction_failed_seeds=failed)
        # registration-style aggregate (median of per-object medians), recomputed for every variant
        for v in ("sam", "colour", "oracle"):
            meds = []
            for r in rs:
                rows = frame_table(r, v)
                for lab in {x["object"] for x in rows}:
                    g = [x["err_m"] * 100 for x in rows if x["object"] == lab and _classify(x) == "hit"]
                    if g:
                        meds.append(float(np.median(g)))
            block["variants"][v]["registration_style_median_of_object_medians_cm"] = \
                round(float(np.median(meds)), 4) if meds else None
        tilts = [tilt_summary(r) for r in rs]
        cup = [t for ts in tilts for t in ts.values()]
        per_seed_med = [t["registered_tilt_error_deg"]["median"] for t in cup if t["registered_tilt_error_deg"]]
        per_seed_zero = [t["constant_zero_error_deg_same_frames"]["median"] for t in cup
                         if t["constant_zero_error_deg_same_frames"]]
        block["tilt"] = dict(
            gt_tilt_max_deg_over_all_seeds=max((t["gt_tilt_deg_all_frames"]["max"] for t in cup
                                                if t["gt_tilt_deg_all_frames"]), default=None),
            gt_tilt_max_deg_per_seed={str(r["seed"]): {lab: (t["gt_tilt_deg_all_frames"] or {}).get("max")
                                                       for lab, t in ts.items()} for r, ts in zip(rs, tilts)},
            registered_median_of_seed_medians_deg=round(float(np.median(per_seed_med)), 4) if per_seed_med else None,
            constant_zero_median_of_seed_medians_deg=round(float(np.median(per_seed_zero)), 4) if per_seed_zero else None,
            seeds_with_any_scored_tilt_frame=len(per_seed_med),
            scored_frames_total=sum(t["registered_measure_frames"] for t in cup),
            side_visible_sampled_frames_total=sum(t["side_visible_sampled_frames"] for t in cup),
            side_visible_with_tilt_estimate_total=sum(t["side_visible_with_tilt_estimate"] for t in cup))
        block["reproduces_stored_rows"] = {str(r["seed"]): r.get("reproduces_stored_row") for r in rs}
        report["sources"][src] = block
        for r, ts in zip(rs, tilts):
            report["per_seed"].append(dict(
                source=src, seed=r["seed"], tracks_sha256=r["tracks_sha256"],
                reproduces_stored_row=r.get("reproduces_stored_row"),
                position={v: (position_summary(frame_table(r, v)) if r["estimates"].get(v) else None)
                          for v in ("sam", "colour", "oracle")},
                tilt=ts))
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("extract", "analyse"))
    ap.add_argument("--repo", default=".")
    ap.add_argument("--cache", default="results/reanalysis_v1/video/frames")
    ap.add_argument("--out", default="results/reanalysis_v1/video/report.json")
    ap.add_argument("--sources", nargs="*", default=list(SOURCES))
    ap.add_argument("--variants", nargs="*", default=["sam", "colour", "oracle"])
    a = ap.parse_args(argv)
    repo = Path(a.repo)
    cache = repo / a.cache
    if a.cmd == "extract":
        extract_all(repo, cache, tuple(a.sources), tuple(a.variants))
    else:
        rep = analyse(cache)
        out = repo / a.out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, indent=1, sort_keys=True) + "\n")
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
