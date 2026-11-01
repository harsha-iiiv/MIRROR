"""Video-to-spec benchmark (pre-registered).

Inputs: rendered demonstrations (top + side camera) with the simulator's
ground truth, made before the freeze. Measured per seed:

* tracking: segmentation tracks (sam-3.1 through ``mirror.perception``) ->
  reconstruction -> per-frame table-plane positions, found flags, in-image cup
  tilt and grasp/release events, scored against ground truth;
* observations: muse-wonder-1.0 and muse-spark-1.3 each watch the same clip
  once; their grasp/release events, relevant-object set and care cues are
  scored against ground truth; cost and latency from the client's records;
* specification: muse-spark-1.3 proposes from the video-derived trace (with
  the chosen model's observations) and from the ground-truth trace; the two
  proposal signatures are compared.

Ground truth is read only for scoring. No model grades a run.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import numpy as np

from . import costs

KIT = ("cup", "block", "marker")
RELEVANCE_KINDS = ("cup", "block", "marker", "tray")
FOUND_RADIUS_M = 0.05        # a detection counts as the object within 5 cm
EVENT_MATCH_S = 1.0          # tracking events: matched within 1 s
EVENT_F1_TOL_S = 0.5         # observation events: true positive within 0.5 s
OBS_MAE_TOL_S = 2.0          # observation events: timing error reported for matches within 2 s
UPRIGHT_TOL_DEG = 15.0


# ---------------------------------------------------------- ground truth ---

def _kinds(gt: dict) -> dict:
    return {o["id"]: o["kind"] for o in gt["objects"]}


def gt_frames(gt: dict) -> list[dict]:
    """Per frame and kit object: true table-plane (x, y), true cup tilt and
    whether any of its pixels are visible in some camera."""
    kinds = _kinds(gt)
    rows = []
    for f in gt["frames"]:
        for oid, kind in kinds.items():
            if kind not in KIT or oid not in f["objects"]:
                continue
            x, y = f["objects"][oid]["pos"][:2]
            rows.append(dict(frame=f["i"], object=oid, kind=kind, x=float(x), y=float(y),
                             tilt_deg=f.get("cup_tilt_deg", {}).get(oid),
                             visible=any(oid in f["masks"][c] for c in f["masks"])))
    return rows


def gt_events(gt: dict) -> list[dict]:
    kinds = _kinds(gt)
    return [dict(type=e["type"], object=kinds.get(e["object_id"], e["object_id"]), t=float(e["t"]))
            for e in gt["events"] if e["type"] in ("grasp", "release")]


def gt_relevant(gt: dict) -> set:
    """Objects that matter: every kit object that is moved, plus the tray."""
    kinds = _kinds(gt)
    moved = {kinds[e["object_id"]] for e in gt["events"] if e["type"] == "grasp" and e["object_id"] in kinds}
    return (moved & set(KIT)) | ({"tray"} if moved else set())


def gt_cup_kept_upright(gt: dict) -> bool:
    kinds = _kinds(gt)
    tilts = [f["cup_tilt_deg"][oid] for f in gt["frames"] for oid in f.get("cup_tilt_deg", {})
             if f.get("held") == oid and kinds.get(oid) == "cup"]
    return bool(tilts) and max(tilts) < UPRIGHT_TOL_DEG


def est_frames(est, kinds_by_label: dict, gt_fps: Optional[float] = None) -> list[dict]:
    """Estimates per sampled frame; with ``gt_fps`` the frame key is the
    ground-truth frame at the same time (tracking may run on fewer frames)."""
    rows = []
    for lab, P in est.objects.items():
        if kinds_by_label.get(lab) not in KIT:
            continue
        found = est.found[lab]
        tilt = (est.tilt_deg or {}).get(lab)
        for i in range(len(est.t)):
            key = int(round(float(est.t[i]) * gt_fps)) if gt_fps else i
            ok = bool(found[i]) and not np.isnan(P[i][0])
            t = None if tilt is None or np.isnan(tilt[i]) else float(tilt[i])
            rows.append(dict(frame=key, object=lab, found=ok, x=float(P[i][0]) if ok else None,
                             y=float(P[i][1]) if ok else None, tilt_deg=t))
    return rows


def est_events(est) -> list[dict]:
    kinds = est.scene_checks.get("kinds", {})
    return [dict(type=e["type"], object=kinds.get(e["object"], e["object"]), t=float(e["t"])) for e in est.events]


# ----------------------------------------------------------------- metrics ---

def frame_errors(gt: list[dict], est: list[dict]) -> dict:
    """Found rate per kind (estimate within 5 cm), centroid errors (cm) and
    cup tilt errors (deg) over frames where the object is visible."""
    idx = {(r["frame"], r["object"]): r for r in est}
    sampled = {r["frame"] for r in est}
    found, cerr, terr = {}, {}, []
    for g in gt:
        if not g.get("visible", True) or (sampled and g["frame"] not in sampled):
            continue
        k = g.get("kind", g["object"])
        e = idx.get((g["frame"], g["object"]))
        ok = bool(e and e.get("found", True) and e.get("x") is not None
                  and np.hypot(e["x"] - g["x"], e["y"] - g["y"]) <= FOUND_RADIUS_M)
        found.setdefault(k, []).append(ok)
        if ok:
            cerr.setdefault(k, []).append(100.0 * float(np.hypot(e["x"] - g["x"], e["y"] - g["y"])))
            if g.get("tilt_deg") is not None and e.get("tilt_deg") is not None:
                terr.append(abs(float(e["tilt_deg"]) - float(g["tilt_deg"])))
    allc = [v for vs in cerr.values() for v in vs]
    allf = [v for vs in found.values() for v in vs]
    return dict(found_rate={k: float(np.mean(v)) for k, v in found.items()},
                found_rate_all=float(np.mean(allf)) if allf else None,
                centroid_cm={k: _q(v) for k, v in cerr.items()}, centroid_cm_all=_q(allc),
                tilt_deg=_q(terr), n_visible=len(allf))


def _q(v: Iterable[float]) -> Optional[dict]:
    v = list(v)
    if not v:
        return None
    return dict(n=len(v), median=float(np.median(v)), p90=float(np.percentile(v, 90)), max=float(np.max(v)))


def match_events(gt: list[dict], est: list[dict], tol_s: float) -> dict:
    """One-to-one matching of events with the same type and object kind,
    closest in time first, within ``tol_s``."""
    pairs = []
    for i, g in enumerate(gt):
        for j, e in enumerate(est):
            if g["type"] == e.get("type") and g["object"] == e.get("object") and e.get("t") is not None:
                d = abs(float(e["t"]) - float(g["t"]))
                if d <= tol_s:
                    pairs.append((d, i, j))
    used_g, used_e, matched = set(), set(), []
    for d, i, j in sorted(pairs):
        if i not in used_g and j not in used_e:
            used_g.add(i)
            used_e.add(j)
            matched.append(d)
    tp = len(matched)
    prec = tp / len(est) if est else (1.0 if not gt else 0.0)
    rec = tp / len(gt) if gt else 1.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return dict(tp=tp, n_gt=len(gt), n_est=len(est), precision=prec, recall=rec, f1=f1, abs_err_s=matched)


def relevance_correct(gt_rel: set, observed: list[dict]) -> bool:
    said = {o["kind"] for o in observed if o.get("relevant") and o.get("kind") in RELEVANCE_KINDS}
    return said == set(gt_rel)


UPRIGHT_WORDS = ("upright", "level", "vertical", "tilt", "spill", "straight")


def care_cue_found(cues: list[dict], kind: str = "cup") -> bool:
    for c in cues:
        text = (c.get("text") or "").lower()
        if (c.get("object") == kind or kind in text) and any(w in text for w in UPRIGHT_WORDS):
            return True
    return False


def proposal_signature(spec) -> dict:
    """What a user accepting every proposed item would enforce: constraints
    not rejected (type, phase, selector, parameters) and active goals
    (selector, target). Identifiers and prose are ignored."""
    def sel(s):
        return json.dumps(s.model_dump(exclude_none=True), sort_keys=True)
    cons = sorted((c.type, c.phase, sel(c.applies_to), json.dumps(c.params, sort_keys=True))
                  for c in spec.constraints if getattr(c.status, "value", c.status) != "rejected")
    goals = sorted((sel(g.object), str(g.target)) for g in spec.active_goals())
    return dict(constraints=cons, goals=goals)


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _strip_numbers(v):
    if isinstance(v, dict):
        return {k: _strip_numbers(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_strip_numbers(x) for x in v]
    return "<number>" if _num(v) else v


def structure_signature(sig: dict) -> dict:
    """A proposal signature with every numeric parameter replaced by a
    placeholder: same items, phases, selectors and parameter names."""
    cons = sorted((t, ph, sel, json.dumps(_strip_numbers(json.loads(p)), sort_keys=True))
                  for t, ph, sel, p in sig["constraints"])
    return dict(constraints=cons, goals=sorted(sig["goals"]))


def numeric_differences(sig_a: dict, sig_b: dict) -> list[dict]:
    """Every numeric parameter that differs between items present in both
    signatures with the same type, phase, selector and parameter names
    (e.g. a cup tilt limit of 10 vs 15 degrees)."""
    def index(sig):
        out = {}
        for t, ph, sel, p in sig["constraints"]:
            params = json.loads(p)
            key = (t, ph, sel, json.dumps(_strip_numbers(params), sort_keys=True))
            out.setdefault(key, []).append(params)
        return out
    a, b = index(sig_a), index(sig_b)
    diffs = []
    for key in sorted(set(a) & set(b)):
        for pa, pb in zip(a[key], b[key]):
            for name in sorted(set(pa) | set(pb)):
                va, vb = pa.get(name), pb.get(name)
                if va != vb and (_num(va) or _num(vb)):
                    diffs.append(dict(type=key[0], phase=key[1], applies_to=json.loads(key[2]), param=name,
                                      video=va, ground_truth=vb))
    return diffs


def _get(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def observation_view(obs) -> dict:
    """Events (grasp/release, time = start of the reported span), objects,
    care cues and validator issues of a VideoObservations."""
    events = [dict(type=_get(e, "type"), object=_get(e, "object"), t=_get(e, "t_start", _get(e, "t")))
              for e in (_get(obs, "events") or []) if _get(e, "type") in ("grasp", "release")]
    objects = [dict(kind=_get(o, "kind"), relevant=bool(_get(o, "relevant"))) for o in (_get(obs, "objects") or [])]
    cues = [dict(object=_get(c, "object"), text=" ".join(str(x) for x in (_get(c, "cue"), _get(c, "evidence"),
                                                                            _get(c, "text")) if x))
            for c in (_get(obs, "care_cues") or _get(obs, "cues") or [])]
    return dict(events=events, objects=objects, cues=cues, issues=[str(i) for i in (_get(obs, "issues") or [])])


# --------------------------------------------------------------------- run ---

def oracle_tracker(dv, gt, root):
    """Ground-truth masks as tracks: rehearsal and upper bound only."""
    from mirror.perception.reconstruct import tracks_from_ground_truth
    return tracks_from_ground_truth(dv, gt)


SAM_FPS = 5.0


def sam_copy_name(cam: str) -> str:
    return f"{cam}_sam{int(SAM_FPS)}fps.mp4"


def make_sam_copies(dv, root: Path) -> list[Path]:
    """Frozen inputs for tracking: each camera video keeping every
    (fps / SAM_FPS)-th frame, starting with frame 0, re-encoded at SAM_FPS."""
    import imageio.v2 as imageio
    step = int(round(dv.fps / SAM_FPS))
    out = []
    for cam, rel in dv.videos.items():
        dst = Path(root) / sam_copy_name(cam)
        rd = imageio.get_reader(str(Path(root) / rel))
        w = imageio.get_writer(dst, fps=SAM_FPS, codec="libx264", quality=8, pixelformat="yuv420p",
                               macro_block_size=2, ffmpeg_params=["-threads", "1"])
        try:
            for i, frame in enumerate(rd):
                if i % step == 0:
                    w.append_data(frame)
        finally:
            w.close()
            rd.close()
        out.append(dst)
    return out


def sam_tracker(dv, gt, root, *, prompts=None, client=None):
    """sim's tracking on the frozen SAM_FPS copies of every camera."""
    import math
    from mirror.perception.track import _objects_from, track_videos
    videos = {c: Path(root) / sam_copy_name(c) for c in dv.videos}
    n = int(math.ceil(dv.n_frames / round(dv.fps / SAM_FPS)))
    return track_videos(videos, fps=SAM_FPS, n_frames=n, objects=_objects_from(prompts), client=client,
                        work=Path(root) / "perception_work")


def reconstruct(dv, tracks):
    from mirror.perception.reconstruct import estimate, scene_from_video, trace_from_estimates
    est = estimate(tracks, {c: ci.K for c, ci in dv.cameras.items()})
    scene, checks = scene_from_video(tracks, est, scene_id=dv.scene_id + "_from_video")
    trace = trace_from_estimates(tracks, est, scene, demo_id=dv.demo_id + "_from_video")
    return est, scene, trace, checks


def run(design: dict, inputs: dict, *, ledger, budget, out_dir: Path, meta_client=None, spark_client=None,
        tracker: Optional[Callable] = None, observe: Optional[Callable] = None, propose: Optional[Callable] = None,
        repo_root: Optional[Path] = None, tracker_label: str = "sam-3.1") -> dict:
    from mirror.contracts import DemoTrace
    from mirror.perception import load_demo_video
    if meta_client is None:
        from mirror.meta import MetaClient
        meta_client = MetaClient.from_env()
    if spark_client is None:
        from mirror.interpret import LLMClient
        spark_client = LLMClient.from_env()
    if observe is None:
        from mirror.interpret.video_observe import observe
    if propose is None:
        from mirror.interpret import propose_spec as propose
    if tracker is None:
        tracker = lambda dv, gt, root: sam_tracker(dv, gt, root, client=meta_client)  # noqa: E731  (frozen default objects)
    repo_root = repo_root or Path(__file__).resolve().parents[2]
    obs_cfg = dict(design.get("observation", {}))
    cam = obs_cfg.pop("camera", "top")
    manifests = {d["seed"]: repo_root / d["manifest"] for d in inputs["provenance"]["demos"]}
    rows, stopped = [], None
    cap = design.get("spend_cap_usd")
    est_seed = dict(design.get("spend_projection_per_seed_usd", {}))     # used until a seed has been measured
    per_seed: list[dict] = []

    def spent() -> float:
        return sum(costs.record_cost(r) for r in list(meta_client.usage) + list(getattr(spark_client, "usage", [])))

    for seed in design["seeds"]:
        row: dict[str, Any] = dict(seed=seed)
        skip_wonder = False
        if cap is not None:
            full = (np.mean([p["total"] for p in per_seed]) if per_seed else sum(est_seed.values()))
            wonder = (np.mean([p["wonder"] for p in per_seed]) if per_seed else est_seed.get("muse-wonder-1.0", 0.0))
            now = spent()
            if now + full > cap:
                if now + full - wonder <= cap:
                    skip_wonder = True
                    row["wonder_skipped"] = (f"spend cap: ${now:.2f} spent, a full seed is projected at ${full:.2f}; "
                                             f"muse-wonder-1.0 dropped for this seed, tracking kept")
                else:
                    stopped = (f"spend cap ${cap:.2f}: ${now:.2f} spent before seed {seed}; even without "
                               f"muse-wonder-1.0 a seed is projected at ${full - wonder:.2f}")
                    break
        seed_start, wonder_cost = spent(), 0.0
        try:
            dv, gt = load_demo_video(manifests[seed])
            root = manifests[seed].parent
            t0 = time.monotonic()
            n0 = len(getattr(meta_client, "usage", []))
            tracks = tracker(dv, gt, root)
            recs = getattr(meta_client, "usage", [])[n0:]
            budget.charge("sam-3.1", sum(1 for r in recs if str(r.get("model", "")).startswith("sam")))
            budget.charge("muse-spark-1.3:grounding", sum(1 for r in recs if str(r.get("model", "")).startswith("muse-spark")))
            row["tracking_cost_usd"] = round(sum(costs.record_cost(r) for r in recs), 4)
            notes = getattr(tracks, "notes", None) or []
            try:
                info = json.loads(notes[0]) if notes else {}
            except (TypeError, ValueError):
                info = {}
            row["reseeds"] = info.get("reseed", [])
            try:
                from mirror.perception.track import save_tracks
                (Path(out_dir) / "tracks").mkdir(parents=True, exist_ok=True)
                save_tracks(tracks, Path(out_dir) / "tracks" / f"{seed}.json.gz")
            except Exception as exc:  # the tracks file is a convenience record, not a measure
                row["tracks_saved"] = f"not saved: {type(exc).__name__}"
            est, scene_v, trace_v, checks = reconstruct(dv, tracks)
            row["tracking_latency_s"] = round(time.monotonic() - t0, 1)
            ledger.append(kind="track", seed=seed, tracker=tracker_label, requests=len(recs),
                          reseeds=len(row["reseeds"]), cost_usd=row["tracking_cost_usd"])
            kinds = est.scene_checks.get("kinds", {})
            row["tracking"] = frame_errors(gt_frames(gt), est_frames(est, kinds, gt_fps=dv.fps))
            row["tracking_frames"] = len(est.t)
            m = match_events(gt_events(gt), est_events(est), EVENT_MATCH_S)
            row["timing"] = m
            gt_rel, care_expected = gt_relevant(gt), gt_cup_kept_upright(gt)
            obs_by, row["observations"] = {}, {}
            for model, line in (("muse-wonder-1.0", "muse-wonder-1.0"), ("muse-spark-1.3", "muse-spark-1.3:video")):
                if model == "muse-wonder-1.0" and skip_wonder:
                    ledger.append(kind="observe_skipped", seed=seed, model=model, reason="spend cap")
                    continue
                budget.charge(line)
                n0 = len(meta_client.usage)
                t0 = time.monotonic()
                try:
                    o, err = observe(root / dv.videos[cam], model, client=meta_client, **obs_cfg), None
                except Exception as exc:  # reported, never retried here
                    o, err = None, f"{type(exc).__name__}: {exc}"[:300]
                lat = round(time.monotonic() - t0, 1)
                recs = meta_client.usage[n0:]
                if model == "muse-wonder-1.0":
                    wonder_cost = sum(costs.record_cost(r) for r in recs)
                obs_by[model] = o
                v = observation_view(o) if o is not None else dict(events=[], objects=[], cues=[], issues=[])
                me = match_events(gt_events(gt), v["events"], EVENT_F1_TOL_S)
                wide = match_events(gt_events(gt), v["events"], OBS_MAE_TOL_S)
                row["observations"][model] = dict(
                    error=err, latency_s=lat, cost_usd=round(sum(costs.record_cost(r) for r in recs), 4),
                    replayed=all(r.get("from_cassette") for r in recs) if recs else None,
                    event_f1=me["f1"], event_precision=me["precision"], event_recall=me["recall"],
                    event_abs_err_s=wide["abs_err_s"], events_matched_2s=wide["tp"], events_true=wide["n_gt"],
                    events_reported=len(v["events"]), relevance_correct=relevance_correct(gt_rel, v["objects"]) if o else False,
                    care_cue=care_cue_found(v["cues"]) if o else False, care_expected=care_expected,
                    validator_issues=v["issues"], usage=recs)
                ledger.append(kind="observe", seed=seed, model=model, error=err, event_f1=round(me["f1"], 3),
                              issues=len(v["issues"]))
            gt_trace = DemoTrace.model_validate_json((root / dv.trace).read_text())
            chosen = design.get("spec_observations_model", "muse-spark-1.3")
            spec_gt = propose(gt_trace, spark_client)
            spec_v = propose(trace_v, spark_client, observations=obs_by.get(chosen))
            sv, sg = proposal_signature(spec_v), proposal_signature(spec_gt)
            row["signature_equal"] = sv == sg
            row["structure_equal"] = structure_signature(sv) == structure_signature(sg)       # reported only
            row["numeric_differences"] = numeric_differences(sv, sg)
            row["signature_video"], row["signature_ground_truth"] = sv, sg
            ledger.append(kind="spec", seed=seed, signature_equal=row["signature_equal"])
        except Exception as exc:  # every attempt is reported
            from .prereg import BudgetExceeded
            if isinstance(exc, BudgetExceeded):
                raise
            row["error"] = f"{type(exc).__name__}: {exc}"[:400]
            ledger.append(kind="error", seed=seed, error=row["error"])
        row["cost_usd"] = round(spent() - seed_start, 4)
        if not skip_wonder and "error" not in row:
            per_seed.append(dict(total=row["cost_usd"], wonder=wonder_cost))
        rows.append(row)
    not_run = [s for s in design["seeds"] if s not in {r["seed"] for r in rows}]
    if stopped:
        ledger.append(kind="stopped", reason=stopped, not_run=not_run)
    usage = costs.summarize_usage(list(meta_client.usage) + list(getattr(spark_client, "usage", [])))
    return dict(rows=rows, tracker=tracker_label, usage=usage, cost_usd=costs.total_cost(usage),
                stopped=stopped, not_run=not_run, summary=summarize(rows, design))


def summarize(rows: list[dict], design: dict) -> dict:
    ok = [r for r in rows if "error" not in r]
    found = [r["tracking"]["found_rate_all"] for r in ok if r["tracking"]["found_rate_all"] is not None]
    cm = [c["median"] for r in ok for c in r["tracking"]["centroid_cm"].values() if c]
    t_err = [e for r in ok for e in r["timing"]["abs_err_s"]]
    det = sum(r["timing"]["tp"] for r in ok) / max(sum(r["timing"]["n_gt"] for r in ok), 1)
    tilt = [r["tracking"]["tilt_deg"]["median"] for r in ok if r["tracking"]["tilt_deg"]]
    sig = sum(1 for r in ok if r.get("signature_equal"))
    struct = sum(1 for r in ok if r.get("structure_equal"))
    models = {}
    for m in ("muse-wonder-1.0", "muse-spark-1.3"):
        o = [r["observations"][m] for r in ok if m in r.get("observations", {})]
        good = [x for x in o if not x["error"]]
        models[m] = dict(n=len(o), errors=len(o) - len(good),
                         skipped=sum(1 for r in ok if m == "muse-wonder-1.0" and r.get("wonder_skipped")),
                         event_f1_mean=float(np.mean([x["event_f1"] for x in o])) if o else None,
                         relevance_correct=sum(x["relevance_correct"] for x in o),
                         care_cue_when_expected=sum(x["care_cue"] for x in o if x["care_expected"]),
                         care_expected=sum(x["care_expected"] for x in o),
                         validator_issues=sum(len(x["validator_issues"]) for x in o),
                         event_mae_s=float(np.mean([e for x in o for e in x.get("event_abs_err_s", [])]))
                         if any(x.get("event_abs_err_s") for x in o) else None,
                         events_matched_2s=sum(x.get("events_matched_2s", 0) for x in o),
                         events_true=sum(x.get("events_true", 0) for x in o),
                         latency_s_median=float(np.median([x["latency_s"] for x in good])) if good else None,
                         cost_usd=round(sum(x["cost_usd"] for x in o), 4))
    both = [r for r in ok if all(m in r.get("observations", {}) for m in ("muse-wonder-1.0", "muse-spark-1.3"))]
    paired = dict(seeds=len(both))
    for m in ("muse-wonder-1.0", "muse-spark-1.3"):
        paired[m] = dict(event_f1_mean=float(np.mean([r["observations"][m]["event_f1"] for r in both])) if both else None,
                         relevance_correct=sum(r["observations"][m]["relevance_correct"] for r in both))
    w, s = paired["muse-wonder-1.0"], paired["muse-spark-1.3"]
    rule = design["decision_rule"]
    adopt_wonder = (w["event_f1_mean"] is not None and s["event_f1_mean"] is not None
                    and w["event_f1_mean"] >= s["event_f1_mean"] + rule["min_f1_gain"]
                    and w["relevance_correct"] >= s["relevance_correct"])
    th = design["thresholds"]
    vals = dict(seeds=len(rows), completed=len(ok), found_rate=float(np.mean(found)) if found else None,
                centroid_cm_median=float(np.median(cm)) if cm else None,
                event_detection_rate=det, timing_abs_err_s_median=float(np.median(t_err)) if t_err else None,
                tilt_err_deg_median=float(np.median(tilt)) if tilt else None, signature_equal=sig,
                structure_equal_reported_only=struct,
                numeric_differences=sum(len(r.get("numeric_differences", [])) for r in ok))
    n = len(design["seeds"])
    checks = {
        f"found rate >= {th['found_rate_min']}": vals["found_rate"] is not None and vals["found_rate"] >= th["found_rate_min"],
        f"median centroid error <= {th['centroid_cm_median_max']} cm": vals["centroid_cm_median"] is not None and vals["centroid_cm_median"] <= th["centroid_cm_median_max"],
        f"grasp/release detected within {EVENT_MATCH_S:g} s >= {th['event_detection_min']}": det >= th["event_detection_min"],
        f"median timing error <= {th['timing_s_median_max']} s": vals["timing_abs_err_s_median"] is not None and vals["timing_abs_err_s_median"] <= th["timing_s_median_max"],
        f"median cup tilt error <= {th['tilt_deg_median_max']} deg": vals["tilt_err_deg_median"] is not None and vals["tilt_err_deg_median"] <= th["tilt_deg_median_max"],
        f"same proposal signature on >= {th['signature_equal_min']} of {n} seeds": sig >= th["signature_equal_min"],
    }
    return dict(values=vals, models=models, paired=paired, adopt_wonder=adopt_wonder, threshold_checks=checks)


def _f(x, nd=2):
    return "n/a" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def markdown(report: dict, design: dict) -> str:
    res = report.get("result", {})
    L = [f"# {report.get('title', 'Video-to-spec benchmark (pre-registered)')}", ""]
    if report.get("rehearsal"):
        L += ["> REHEARSAL on development inputs. Not an evaluation result.", ""]
    L += [f"Status: {report['status']}. Design `{report['design_sha256'][:16]}`, inputs `{report['inputs_sha256'][:16]}`, "
          f"code `{report['freeze_code_sha256'][:16]}`. Seeds: {', '.join(map(str, design['seeds']))}. "
          f"Tracks from: {res.get('tracker', '?')}.", ""]
    if "summary" not in res:
        return "\n".join(L + [f"Stopped: {res.get('stopped', '')}", ""])
    s = res["summary"]
    v = s["values"]
    L += ["## What was measured", "",
          "Each rendered demonstration was tracked and reconstructed into object positions, grasp and release times "
          "and cup tilt, then compared with the simulator's ground truth frame by frame. Both video models watched "
          "the same clip once. Muse Spark then proposed a task specification from the video and from the "
          "ground-truth recording, and the two were compared.", "",
          "| measure | result |", "|---|---|",
          f"| seeds completed | {v['completed']} of {v['seeds']} |",
          f"| object found (within 5 cm, visible frames) | {_f(v['found_rate'], 3)} |",
          f"| median position error | {_f(v['centroid_cm_median'])} cm |",
          f"| grasp/release detected within 1 s | {_f(v['event_detection_rate'], 3)} |",
          f"| median timing error | {_f(v['timing_abs_err_s_median'])} s |",
          f"| median cup tilt error (in-image tilt vs true tilt) | {_f(v['tilt_err_deg_median'])} deg |",
          f"| same proposed specification as from ground truth (pass/fail measure) | {v['signature_equal']} of {v['seeds']} |",
          f"| same structure ignoring numeric limits (reported only) | {v.get('structure_equal_reported_only')} of {v['seeds']} |", "",
          "## Muse Wonder vs Muse Spark (video observations)", "",
          "| model | clips | errors | mean event F1 (0.5 s) | events within 2 s | mean timing error (within 2 s) "
          "| relevant objects right | care cue for the cup (expected) | validator issues | median latency | cost |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for m, x in s["models"].items():
        L.append(f"| {m} | {x['n']} | {x['errors']} | {_f(x['event_f1_mean'], 3)} | {x.get('events_matched_2s', 0)}/"
                 f"{x.get('events_true', 0)} | {_f(x.get('event_mae_s'))} s | {x['relevance_correct']} | "
                 f"{x['care_cue_when_expected']} ({x['care_expected']}) | {x['validator_issues']} | "
                 f"{_f(x['latency_s_median'], 1)} s | ${x['cost_usd']:.3f} |")
    if s["models"]["muse-wonder-1.0"].get("skipped"):
        L += ["", f"muse-wonder-1.0 was dropped on {s['models']['muse-wonder-1.0']['skipped']} seed(s) by the spend-cap rule "
              "(tracking coverage is never dropped); the comparison and the decision rule use the seeds where both ran."]
    pw, ps = s["paired"]["muse-wonder-1.0"], s["paired"]["muse-spark-1.3"]
    L += ["", f"On the {s['paired']['seeds']} seed(s) where both models ran: mean event F1 Wonder {_f(pw['event_f1_mean'], 3)}, "
          f"Spark {_f(ps['event_f1_mean'], 3)}; relevant objects right Wonder {pw['relevance_correct']}, Spark {ps['relevance_correct']}."]
    L += ["", f"Decision rule, stated in advance: {design['decision_rule']['text']}. Outcome: "
          f"{'use muse-wonder-1.0' if s['adopt_wonder'] else 'keep muse-spark-1.3'}.", "",
          "## Pre-stated checks", ""]
    L += [f"- {k}: {'met' if ok else 'not met'}" for k, ok in s["threshold_checks"].items()]
    L += ["", "## Every seed", "", "| seed | outcome | found | median cm | events matched | Wonder F1 | Spark F1 | same spec |",
          "|---|---|---|---|---|---|---|---|"]
    for r in res["rows"]:
        if "error" in r:
            L.append(f"| {r['seed']} | error: {r['error'][:120]} | | | | | | |")
            continue
        o = r["observations"]
        L.append(f"| {r['seed']} | completed | {_f(r['tracking']['found_rate_all'], 3)} | "
                 f"{_f((r['tracking']['centroid_cm_all'] or {}).get('median'))} | {r['timing']['tp']}/{r['timing']['n_gt']} | "
                 f"{_f(o['muse-wonder-1.0']['event_f1'])}{' (error)' if o['muse-wonder-1.0']['error'] else ''} | "
                 f"{_f(o['muse-spark-1.3']['event_f1'])}{' (error)' if o['muse-spark-1.3']['error'] else ''} | "
                 f"{'yes' if r['signature_equal'] else 'no'} |")
    if res.get("stopped"):
        L += ["", f"**Stopped early.** {res['stopped']}. Seeds not run: {', '.join(map(str, res.get('not_run', [])))}."]
    nd = [(r["seed"], d) for r in res["rows"] for d in r.get("numeric_differences", [])]
    L += ["", "## Every numeric difference between the two proposals (reported only)", ""]
    L += [f"- seed {sd}: {d['type']} ({d['phase']}) on {json.dumps(d['applies_to'])}: {d['param']} = {d['video']} from "
          f"video vs {d['ground_truth']} from the ground-truth trace" for sd, d in nd] or ["none"]
    L += ["", "## Spend", "", "| model | network calls | replayed | input tokens | output tokens | median latency | cost |",
          "|---|---|---|---|---|---|---|"]
    for m, u in res.get("usage", {}).items():
        L.append(f"| {m} | {u['network_calls']} | {u['replayed_calls']} | {u['input_tokens']} | {u['output_tokens']} | "
                 f"{_f(u['latency_s_median'], 1)} s | ${u['cost_usd']:.3f} |")
    L += ["", f"Total ${res.get('cost_usd', 0):.3f} at list prices ({costs.PRICES_SOURCE}). Budget lines: "
          f"{json.dumps(report['budget'])}. Every attempt is in ledger.jsonl ({report['ledger_entries']} entries).", ""]
    return "\n".join(L)
