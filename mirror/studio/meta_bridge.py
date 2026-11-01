"""Adapter between the studio and the Meta-model stages (perception, video
observations, speech to text) that live in other packages.

Imports are lazy and calls are introspective, so the studio keeps working
(with the feature marked unavailable) when a stage is not installed, and
small signature differences do not break it. Every result carries which
model produced it, how long it took, and whether it came from a live call or
a recording, for the studio's Models panel. Nothing here grades a run or
moves the robot.
"""
from __future__ import annotations

import base64
import contextlib
import json
import importlib
import inspect
import time
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_DIR = REPO_ROOT / "data" / "demos" / "video"


def _import(path: str):
    try:
        return importlib.import_module(path)
    except Exception:  # noqa: BLE001 - absent or broken stage: feature unavailable
        return None


def _fn(module: str, name: str) -> Optional[Callable]:
    m = _import(module)
    f = getattr(m, name, None) if m is not None else None
    return f if callable(f) else None


def _call(fn: Callable, *args, **kw):
    """Call ``fn`` passing only the keyword arguments it accepts."""
    try:
        sig = inspect.signature(fn)
        if not any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values()):
            kw = {k: v for k, v in kw.items() if k in sig.parameters}
    except (TypeError, ValueError):
        pass
    return fn(*args, **kw)


def _dump(x: Any) -> Any:
    if hasattr(x, "model_dump"):
        return x.model_dump(mode="json")
    if isinstance(x, dict):
        return {k: _dump(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_dump(v) for v in x]
    return x


def _get(d: Any, *names, default=None):
    for n in names:
        if isinstance(d, dict) and n in d and d[n] is not None:
            return d[n]
        if not isinstance(d, dict) and getattr(d, n, None) is not None:
            return getattr(d, n)
    return default


# ------------------------------------------------------------ call capture ---

@contextlib.contextmanager
def capture_calls():
    """Collect the Meta API calls made inside the block, when the shared client offers it."""
    meta = _import("mirror.meta")
    cap = None
    for name in ("capture_calls", "record_calls", "capture"):
        cap = getattr(meta, name, None) if meta is not None else None
        if callable(cap):
            break
        cap = None
    if cap is None:
        yield []
        return
    with cap() as calls:
        yield calls


def _norm_calls(calls) -> list[dict]:
    out = []
    for c in list(calls or []):
        c = _dump(c)
        if not isinstance(c, dict):
            continue
        out.append({"model": _get(c, "model"), "endpoint": _get(c, "endpoint", "path"),
                    "latency_s": _get(c, "latency_s", default=(_get(c, "latency_ms", default=0) or 0) / 1000.0),
                    "source": _source(_get(c, "source", "mode")), "units": _get(c, "units", "tokens")})
    return out


def _source(mode: Optional[str]) -> str:
    mode = (mode or "").lower()
    if mode in ("live", "record"):
        return "live"
    if mode in ("replay", "cassette", "recorded"):
        return "recorded"
    if mode == "offline":
        return "offline"
    return mode or "unknown"


def _summary(calls: list[dict], fallback_model: Optional[str], latency: float) -> dict:
    models = [c["model"] for c in calls if c.get("model")]
    sources = {c["source"] for c in calls}
    src = "live" if "live" in sources else "recorded" if "recorded" in sources else (sources.pop() if sources else "unknown")
    return {"model": models[0] if models else fallback_model, "latency_s": round(latency, 3), "source": src,
            "calls": calls}


# ------------------------------------------------------------- capability ---

def bundled_demo() -> Optional[dict]:
    """The rendered demonstration video shipped with the repository, if present."""
    f = _fn("mirror.perception", "bundled_demo")
    if f is not None:
        try:
            d = _dump(f())
            if d and Path(str(_get(d, "top", default=""))).exists():
                return {"top": str(_get(d, "top")), "side": _get(d, "side"), "ground_truth": _get(d, "ground_truth")}
        except Exception:  # noqa: BLE001
            pass
    man = BUNDLED_DIR / "reference" / "video.json"
    if man.exists():
        try:
            d = json.loads(man.read_text())
            vids = d.get("videos", {})
            top = man.parent / vids.get("top", "top.mp4")
            side = man.parent / vids["side"] if vids.get("side") else None
            if top.exists():
                return {"top": str(top), "side": str(side) if side and side.exists() else None,
                        "ground_truth": str(man), "duration_s": d.get("duration_s")}
        except (OSError, ValueError):
            pass
    top = BUNDLED_DIR / "reference_top.mp4"
    if top.exists():
        side = BUNDLED_DIR / "reference_side.mp4"
        return {"top": str(top), "side": str(side) if side.exists() else None, "ground_truth": None}
    return None


def available() -> dict:
    return {
        "perception": _fn("mirror.perception", "trace_from_video") is not None,
        "observe": _fn("mirror.interpret.video_observe", "observe") is not None,
        "voice": _fn("mirror.interpret.voice", "transcribe") is not None,
        "bundled_video": bundled_demo() is not None,
    }


# ---------------------------------------------------------------- stages ---

def perceive(top: str, side: Optional[str] = None, progress: Optional[Callable] = None,
             ground_truth: Optional[str] = None, **kw) -> dict:
    """Video(s) -> reconstructed DemoTrace + per-object tracks (normalised for the overlay).

    ``ground_truth``: a rendered demonstration's manifest; when given, the
    reconstruction is also measured against the simulator (metrics only)."""
    f = _fn("mirror.perception", "trace_from_video")
    if f is None:
        raise RuntimeError("video perception is not installed on this server")
    t0 = time.time()
    try:
        has_details = "return_details" in inspect.signature(f).parameters
    except (TypeError, ValueError):
        has_details = False
    with capture_calls() as calls:
        res = f(top, side, return_details=True) if has_details else f(top, side)
    lat = time.time() - t0
    est = scene_checks = tracks_obj = None
    if isinstance(res, tuple):
        trace, est, _scene, scene_checks, tracks_obj = (list(res) + [None] * 5)[:5]
    else:
        trace = _get(res, "trace", default=res) if not _is_trace(res) else res
        tracks_obj = _get(res, "tracks", default=None)
    calls = _norm_calls(calls) or _calls_from_tracks(tracks_obj)
    video_meta = probe_video(top)
    if tracks_obj is not None and hasattr(tracks_obj, "masks") and hasattr(tracks_obj, "get"):
        tracks = tracks_view(tracks_obj)
    else:
        tracks = normalize_tracks(tracks_obj, video_meta)
    metrics = None
    if ground_truth and est is not None:
        metrics = _metrics(est, ground_truth)
    sam = [c for c in calls if str(c.get("model", "")).startswith("sam")]
    src = "live" if any(c["source"] == "live" for c in calls) else "recorded" if calls else "unknown"
    return {"trace": trace, "tracks": tracks, "metrics": metrics, "checks": _dump(scene_checks),
            "model": sam[0]["model"] if sam else "sam-3.1", "latency_s": round(lat, 3), "source": src,
            "calls": calls, "source_note": getattr(tracks_obj, "source", None)}


def _calls_from_tracks(t) -> list[dict]:
    """Model calls recorded by perception in Tracks.notes (grounding + SAM), for the Models panel."""
    out = []
    for n in getattr(t, "notes", None) or []:
        try:
            d = json.loads(n) if isinstance(n, str) else n
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        for cam, g in (d.get("grounding") or {}).items():
            for c in g.get("calls", []):
                out.append({"model": c.get("model"), "endpoint": f"grounding ({cam})", "latency_s": c.get("latency_s"),
                            "source": "recorded" if c.get("cassette") else "live", "units": (c.get("usage") or {}).get("total_tokens")})
        for k, s in (d.get("sam") or {}).items():
            out.append({"model": "sam-3.1", "endpoint": f"segment {k.replace('_', ' ')}", "latency_s": s.get("latency_s"),
                        "source": "recorded" if s.get("cassette") else "live", "units": s.get("frames")})
        for rec in d.get("reseed") or []:                 # lost tracks re-seeded (grounding and/or SAM)
            where = f"re-seed {rec.get('object', '')} ({rec.get('camera', '')})"
            g = rec.get("grounding")
            if g:
                out.append({"model": "muse-spark-1.3", "endpoint": where, "latency_s": g.get("latency_s"),
                            "source": "recorded" if g.get("cassette") else "live",
                            "units": (g.get("usage") or {}).get("total_tokens")})
            s = rec.get("sam")
            if s:
                out.append({"model": "sam-3.1", "endpoint": where, "latency_s": s.get("latency_s"),
                            "source": "recorded" if s.get("cassette") else "live", "units": s.get("frames")})
    return out


def _metrics(est, manifest: str) -> Optional[dict]:
    try:
        from mirror.perception import load_demo_video, perception_metrics
        _dv, gt = load_demo_video(manifest)
        m = perception_metrics(est, gt)
        s = m.get("summary", {})
        ev = m.get("events", {})
        return {"against": "simulator ground truth of the rendered video (measurement only)",
                "found_rate": s.get("found_rate"), "centroid_err_cm": s.get("centroid_err_cm"),
                "events_matched": len(ev.get("matched", [])), "events_true": ev.get("n_true"),
                "event_err_s": ev.get("abs_err_s_mean"), "cup_tilt_err_deg": s.get("tilt_err_deg"),
                "objects": {k: {kk: (round(vv, 3) if isinstance(vv, float) else vv) for kk, vv in v.items()}
                            for k, v in (m.get("objects") or {}).items()}}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"[:200]}


def tracks_view(t, max_fps: float = 5.0, grid_w: int = 80) -> dict:
    """Perception Tracks (camera -> label -> frame -> mask) -> overlay frames."""
    out = {}
    static = set(getattr(t, "static", set()) or [])
    step = max(1, int(round(float(t.fps) / max_fps)))
    for cam, labels in t.masks.items():
        w, h = t.size.get(cam, (None, None))
        objs, stat = {}, []
        for lab in labels:
            if "~" in lab or lab == "table":
                continue                         # calibration hypotheses, not objects
            frames = []
            idxs = [0] if lab in static else sorted(i for i in labels[lab] if i % step == 0)
            for i in idxs:
                m = t.get(cam, lab, i)
                if m is None or not m.any():
                    continue
                ys, xs = np.nonzero(m)
                item = {"t": round(i / float(t.fps), 3),
                        "box": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]}
                g = _grid(m, {}, grid_w)
                if g:
                    item["grid"] = g
                frames.append(item)
            if frames:
                objs[lab] = frames
                if lab in static:
                    stat.append(lab)
        out[cam] = {"fps": float(t.fps), "width": w, "height": h, "objects": objs, "static": stat}
    return out


def probe_video(path: str) -> dict:
    """fps, width, height of a video file (ffprobe; empty if unavailable)."""
    import json as _json
    import shutil
    import subprocess
    exe = shutil.which("ffprobe")
    if not exe:
        return {}
    try:
        r = subprocess.run([exe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate",
                            "-of", "json", str(path)], capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
        st = (_json.loads(r.stdout or "{}").get("streams") or [{}])[0]
        num, _, den = (st.get("r_frame_rate") or "0/1").partition("/")
        fps = float(num) / float(den) if float(den or 0) else None
        return {k: v for k, v in {"fps": fps, "width": st.get("width"), "height": st.get("height")}.items() if v}
    except Exception:  # noqa: BLE001
        return {}


def _is_trace(x) -> bool:
    return type(x).__name__ == "DemoTrace"


def video_model() -> str:
    """Model used for video understanding (MIRROR_VIDEO_MODEL; Muse Spark unless set)."""
    import os
    return os.environ.get("MIRROR_VIDEO_MODEL") or "muse-spark-1.3"


def _result_source(obj: Any, calls: list[dict]) -> Optional[str]:
    """live | recorded, from the captured calls or the result's own provenance."""
    if calls:
        return None
    fc = _get(obj, "from_cassette", default=None)
    if fc is None:
        return None
    return "recorded" if fc else "live"


def observe(video: str, progress: Optional[Callable] = None, model: Optional[str] = None) -> dict:
    f = _fn("mirror.interpret.video_observe", "observe")
    if f is None:
        raise RuntimeError("video observations are not installed on this server")
    model = model or video_model()
    t0 = time.time()
    with capture_calls() as calls:
        obs = _call(f, video, model, progress=progress)
    lat = time.time() - t0
    calls = _norm_calls(calls)
    out = {"raw": obs, "observations": normalize_observations(obs),
           **_summary(calls, _get(obs, "model", default=model), _get(obs, "latency_s", default=lat) or lat)}
    out["source"] = _result_source(obs, calls) or out["source"]
    return out


def propose_with_observations(trace, client, observations) -> Any:
    from ..interpret import propose_spec
    try:
        params = inspect.signature(propose_spec).parameters
    except (TypeError, ValueError):
        params = {}
    if "observations" in params and observations is not None:
        return propose_spec(trace, client, observations=observations)
    return propose_spec(trace, client)


def transcribe(wav: bytes) -> dict:
    f = _fn("mirror.interpret.voice", "transcribe")
    if f is None:
        raise RuntimeError("speech to text is not installed on this server")
    t0 = time.time()
    with capture_calls() as calls:
        tr = f(wav)
    lat = time.time() - t0
    calls = _norm_calls(calls)
    text = tr if isinstance(tr, str) else _get(tr, "text", "transcript", default="")
    out = {"text": str(text).strip(), "raw": _dump(tr),
           **_summary(calls, _get(tr, "model", default="muse-voice-transcribe-1.0"),
                      _get(tr, "latency_s", default=lat) or lat)}
    out["source"] = _result_source(tr, calls) or out["source"]
    return out


# --------------------------------------------------------- normalisation ---

def normalize_observations(obs: Any) -> dict:
    """VideoObservations (any close variant) -> what the timeline shows."""
    d = _dump(obs) or {}
    if not isinstance(d, dict):
        return {"objects": [], "events": [], "cues": [], "uncertainties": [], "summary": str(d)}

    def num(x):
        try:
            return round(float(x), 2)
        except (TypeError, ValueError):
            return None
    objects = [{"id": _get(o, "object_id", "id", "name", "kind"), "kind": _get(o, "kind", "type"),
                "matters": _get(o, "matters", "relevant", "important", default=None),
                "note": "; ".join(x for x in (_get(o, "description", default=""), _get(o, "note", "reason", "role", default=""))
                                  if x)}
               for o in (_get(d, "objects", default=[]) or []) if isinstance(o, dict)]
    events, cues = [], []
    for e in (_get(d, "events", default=[]) or []):
        if not isinstance(e, dict):
            continue
        kind = str(_get(e, "kind", "type", "event", default="")).lower()
        item = {"t": num(_get(e, "t", "time", "t_s", "timestamp", "t_start", "start")), "kind": kind,
                "object_id": _get(e, "object_id", "object", "target"),
                "text": _get(e, "text", "description", "note", "cue", default=""),
                "evidence": _get(e, "evidence", default="")}
        if kind in ("grasp", "release", "pick", "place"):
            item["kind"] = {"pick": "grasp", "place": "release"}.get(kind, kind)
            events.append(item)
        else:
            item["t_end"] = num(_get(e, "t_end", "end", default=None))
            cues.append(item)
    for c in (_get(d, "cues", "care_cues", "care", default=[]) or []):
        if not isinstance(c, dict):
            continue
        cues.append({"t": num(_get(c, "t", "t_start", "start", "time")), "t_end": num(_get(c, "t_end", "end")),
                     "kind": "care", "object_id": _get(c, "object_id", "object"),
                     "text": _get(c, "text", "cue", "description", default=""),
                     "evidence": _get(c, "evidence", default="")})
    unc = []
    for u in (_get(d, "uncertainties", "questions", default=[]) or []):
        if isinstance(u, str):
            unc.append(u)
        else:
            q = _get(u, "question", "text", "description", default=str(u))
            about = _get(u, "about", default="")
            unc.append(f"{q} ({about})" if about and about.lower() not in q.lower() else q)
    events.sort(key=lambda e: (e["t"] is None, e["t"] or 0))
    cues.sort(key=lambda e: (e["t"] is None, e["t"] or 0))
    return {"objects": objects, "events": events, "cues": cues, "uncertainties": unc,
            "summary": _get(d, "summary", "description", default=""), "model": _get(d, "model", default=None),
            "issues": list(_get(d, "issues", default=[]) or [])}


def normalize_tracks(raw: Any, video_meta: Optional[dict] = None, max_fps: float = 10.0, grid_w: int = 96) -> dict:
    """Tracks -> {view: {fps, width, height, objects: {id: [{t, box, polygon?|grid?, score}]}}}.

    Masks given as polygons are kept (rounded); binary masks or COCO RLE become a
    coarse cell grid (``grid_w`` columns) so the browser can draw them cheaply."""
    if raw is None:
        return {}
    if _is_sam(raw):
        raw = {"top": raw}
    if isinstance(raw, dict) and raw and all(_is_sam(v) or isinstance(v, dict) for v in raw.values()):
        conv = {}
        vfps = (video_meta or {}).get("fps")
        for k, v in raw.items():
            if _is_sam(v):
                conv[k] = _sam_view(v, max_fps, fps=vfps)
            elif isinstance(v, dict) and v and all(_is_sam(x) for x in v.values()):
                conv[k] = _merge_sam_views(v, max_fps, fps=vfps)       # {label: SamResult} for one view
            else:
                conv[k] = v
        if any(_is_sam(v) for v in raw.values()) or any(isinstance(v, dict) and v and all(_is_sam(x) for x in v.values()) for v in raw.values()):
            if not any(k in conv for k in ("top", "side")):
                conv = {"top": _merge_sam_views({k: v for k, v in raw.items() if _is_sam(v)}, max_fps, fps=vfps)}
            raw = conv
    raw = _dump(raw)
    if not isinstance(raw, dict):
        return {}
    views = raw if any(k in raw for k in ("top", "side")) else {"top": raw}
    out = {}
    for view, tr in views.items():
        tr = _dump(tr) or {}
        meta = {**(video_meta or {}), **{k: tr.get(k) for k in ("fps", "width", "height") if isinstance(tr, dict) and tr.get(k)}}
        objs = _get(tr, "objects", "tracks", default=tr) if isinstance(tr, dict) else {}
        if isinstance(objs, list):     # [{object_id, frames:[...]}]
            objs = {_get(o, "object_id", "id", "name"): _get(o, "frames", "track", default=[]) for o in objs}
        res = {}
        for oid, frames in (objs or {}).items():
            if oid in ("fps", "width", "height") or not isinstance(frames, list):
                continue
            keep, last_t = [], None
            for fr in frames:
                fr = _dump(fr)
                if not isinstance(fr, dict):
                    continue
                t = _get(fr, "t", "time", "t_s")
                if t is None and _get(fr, "frame") is not None and meta.get("fps"):
                    t = float(fr["frame"]) / float(meta["fps"])
                if t is None:
                    continue
                t = float(t)
                if last_t is not None and t - last_t < 1.0 / max_fps - 1e-6:
                    continue
                last_t = t
                item = {"t": round(t, 3)}
                box = _get(fr, "box", "bbox", "xyxy")
                if box is not None:
                    item["box"] = [round(float(v), 1) for v in box][:4]
                poly = _get(fr, "polygon", "contour", "mask_polygon")
                if isinstance(fr.get("grid"), dict) and fr["grid"].get("runs") is not None:
                    item["grid"] = fr["grid"]
                elif poly:
                    item["polygon"] = [[round(float(x), 1), round(float(y), 1)] for x, y in poly][:400]
                else:
                    m = _get(fr, "mask", "rle", "segmentation")
                    g = _grid(m, meta, grid_w) if m is not None else None
                    if g:
                        item["grid"] = g
                sc = _get(fr, "score", "confidence")
                if sc is not None:
                    item["score"] = round(float(sc), 3)
                keep.append(item)
            res[str(oid)] = keep
        out[view] = {"fps": meta.get("fps"), "width": meta.get("width"), "height": meta.get("height"), "objects": res}
    return out


def _is_sam(x) -> bool:
    return hasattr(x, "frames") and hasattr(x, "width") and hasattr(x, "height") and not isinstance(x, dict)


def _sam_frames(sam, label: str, fps: Optional[float], max_fps: float, grid_w: int = 96) -> list:
    out, last = [], None
    w, h = int(sam.width), int(sam.height)
    for fr in sam.frames:
        idx = getattr(fr, "index", 0)
        t = (idx / fps) if fps else float(getattr(fr, "t", idx))
        if last is not None and t - last < 1.0 / max_fps - 1e-6:
            continue
        objs = list(getattr(fr, "objects", []) or [])
        if not objs:
            continue
        last = t
        o = max(objs, key=lambda x: getattr(x, "confidence", 0) or 0)
        bx = o.box
        box = [float(getattr(bx, "left", bx[0] if isinstance(bx, (list, tuple)) else 0)),
               float(getattr(bx, "top", bx[1] if isinstance(bx, (list, tuple)) else 0)),
               float(getattr(bx, "right", bx[2] if isinstance(bx, (list, tuple)) else 0)),
               float(getattr(bx, "bottom", bx[3] if isinstance(bx, (list, tuple)) else 0))]
        item = {"t": round(t, 3), "box": [round(v, 1) for v in box]}
        try:
            full = o.full_mask(w, h) if getattr(o, "mask", None) is not None else None
        except Exception:  # noqa: BLE001
            full = None
        g = _grid(full, {"width": w, "height": h}, grid_w) if full is not None else None
        if g:
            item["grid"] = g
        if getattr(o, "confidence", None) is not None:
            item["score"] = round(float(o.confidence), 3)
        out.append(item)
    return out


def _sam_view(sam, max_fps: float, label: str = "object", fps: Optional[float] = None) -> dict:
    fps = getattr(sam, "fps", None) or fps
    return {"fps": fps, "width": sam.width, "height": sam.height,
            "objects": {label: _sam_frames(sam, label, fps, max_fps)}}


def _merge_sam_views(by_label: dict, max_fps: float, fps: Optional[float] = None) -> dict:
    first = next(iter(by_label.values()))
    fps = getattr(first, "fps", None) or fps
    return {"fps": fps, "width": first.width, "height": first.height,
            "objects": {str(k): _sam_frames(v, str(k), fps, max_fps) for k, v in by_label.items()}}


def _decode_mask(m: Any):
    import numpy as np
    if isinstance(m, dict) and "counts" in m and "size" in m:
        h, w = m["size"]
        counts = m["counts"]
        if isinstance(counts, str):
            return None          # compressed COCO strings need pycocotools; boxes are used instead
        flat = np.zeros(h * w, dtype=bool)
        pos, val = 0, False
        for c in counts:
            if val:
                flat[pos:pos + c] = True
            pos += c
            val = not val
        return flat.reshape((h, w))       # row-major, starting with a 0-run (mirror.perception.masks)
    if isinstance(m, str):
        try:
            import io
            from PIL import Image
            return np.array(Image.open(io.BytesIO(base64.b64decode(m))).convert("L")) > 127
        except Exception:  # noqa: BLE001
            return None
    try:
        a = np.asarray(m)
        return a.astype(bool) if a.ndim == 2 else None
    except Exception:  # noqa: BLE001
        return None


def _grid(m: Any, meta: dict, grid_w: int) -> Optional[dict]:
    import numpy as np
    a = _decode_mask(m)
    if a is None or not a.any():
        return None
    h, w = a.shape
    gw = min(grid_w, w)
    gh = max(1, round(h * gw / w))
    ys = (np.arange(gh) * h / gh).astype(int)
    xs = (np.arange(gw) * w / gw).astype(int)
    small = a[np.ix_(ys, xs)]
    runs = []
    for r in range(gh):
        row = small[r]
        if not row.any():
            continue
        d = np.diff(np.concatenate([[0], row.astype(int), [0]]))
        for x0, x1 in zip(np.where(d == 1)[0], np.where(d == -1)[0]):
            runs.append([int(r), int(x0), int(x1)])
    return {"w": gw, "h": gh, "runs": runs}
