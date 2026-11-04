"""Build the studio's "Your table" data from a real phone capture.

One folder per capture, shipped with the static site under ``data/table/<id>/``::

    table.json             everything the guided flow shows (see TABLE_FORMAT)
    media/take.mp4         the phone clip, H.264, at most 960 px tall, no audio
    media/evidence_*.jpg   the confirmation images
    media/twin_*.mp4       the robot-on-your-table overlay, when the twin step produced one
    rollouts/<id>.json     playback for the studio's 3D viewer (compact rollout format)

Nothing here calls a model. Corrections offered on the page are compiled by the
deterministic offline compiler and executed on this machine's simulator; each one
is labelled with how it was compiled. The capture gate is evaluated from the saved
session, never bypassed: when it refuses, the page says so and the task shown is
the one the person stated (labelled as such), not one MIRROR inferred.

Usage::

    # the D1 development capture (phase 1 + 2 outputs)
    python -m mirror.studio.real_table d1 --out data/table/D1

    # a pipeline output directory (python -m mirror.capture.real_run ... --out DIR)
    python -m mirror.studio.real_table pipeline DIR --out data/table/<id>
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

TABLE_FORMAT = "mirror-studio-table/1"
LOCK = os.environ.get("MIRROR_RENDER_LOCK", "")   # optional: serialise heavy encodes with other jobs (flock)

# Object colours on the page (also used for contours): restrained, distinguishable in both themes.
PALETTE = ["#d9622b", "#2f7fd1", "#2e9e6a", "#8a63d2", "#b08a2e", "#c2457a", "#5b8a99"]
# The capture's evidence images and overlay video colour these labels this way; the page matches them.
LABEL_COLOURS = {"mug": "#e5534b", "red_box": "#d99a1e", "marker": "#3c7ff0", "tray": "#3faf74", "divider": "#b25fd9"}

# Corrections the page offers when the source gives none: one per supported rule family
# that fits a kit-packing take. Compiled offline; the person still chooses.
DEFAULT_CANDIDATES = [
    {"id": "cup_clearance", "text": "Keep the mug at least 6 cm away from the divider while carrying it.",
     "label": "Keep the mug 6 cm clear of the divider",
     "why": "Adds a safety margin around the obstacle; order, goals and routes stay as you asked."},
    {"id": "block_upright", "text": "Keep the block upright while it is carried.",
     "label": "Keep the box upright while carried",
     "why": "A tall carton can tip in transit; this makes upright a rule while it is carried."},
    {"id": "no_table_contact", "text": "Nothing may touch the table outside the tray.",
     "label": "Nothing touches the table outside the tray",
     "why": "No dragging or setting things down on the way."},
]


def log(msg: str) -> None:
    print(f"[table] {msg}", flush=True)


# ------------------------------------------------------------------ media
def _run(cmd: list[str], timeout: int = 900) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {r.stderr[-800:]}")


def transcode(src: Path, dst: Path, *, height: int = 960, crf: int = 30, lock: bool = True) -> dict:
    """Browser-playable H.264 copy (rotation applied, no audio, fast start)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(src), "-an",
           "-vf", f"scale=-2:'min({height},ih)'", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst)]
    if lock and LOCK and Path(LOCK).exists():
        cmd = ["flock", LOCK] + cmd
    _run(cmd)
    return probe(dst)


def probe(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,codec_name,avg_frame_rate:format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    d = json.loads(r.stdout or "{}")
    s = (d.get("streams") or [{}])[0]
    num, _, den = (s.get("avg_frame_rate") or "0/1").partition("/")
    return {"width": s.get("width"), "height": s.get("height"), "codec": s.get("codec_name"),
            "fps": round(float(num) / float(den or 1), 3) if float(den or 1) else None,
            "duration_s": round(float((d.get("format") or {}).get("duration") or 0), 3)}


def small_jpeg(src: Path, dst: Path, width: int = 480) -> None:
    import cv2
    img = cv2.imread(str(src))
    if img is None:
        raise RuntimeError(f"cannot read {src}")
    h, w = img.shape[:2]
    if w > width:
        img = cv2.resize(img, (width, int(round(h * width / w))), interpolation=cv2.INTER_AREA)
    dst.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(dst), img, [cv2.IMWRITE_JPEG_QUALITY, 82])


# --------------------------------------------------------------- contours
def _poly(mask, w: int, h: int, eps_px: float = 2.5) -> Optional[list]:
    import cv2
    import numpy as np
    m = (mask > 0).astype(np.uint8)
    if not m.any():
        return None
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cs:
        return None
    c = max(cs, key=cv2.contourArea)
    if cv2.contourArea(c) < 12:
        return None
    c = cv2.approxPolyDP(c, eps_px, True).reshape(-1, 2)
    return [round(float(v), 3) for xy in c for v in (xy[0] / w, xy[1] / h)]


def contours_from_tracks(path: Path, *, camera: str = "top", hz: float = 10.0) -> dict:
    """Object outlines sampled at ``hz`` from a SAM track file, normalised to [0,1] image coordinates."""
    from mirror.perception.masks import rle_decode
    tr = json.load(gzip.open(path, "rt")) if str(path).endswith(".gz") else json.loads(Path(path).read_text())
    fps = float(tr["fps"])
    n = int(tr["n_frames"])
    step = max(1, int(round(fps / hz)))
    masks = tr["masks"][camera]
    out = {"fps": fps, "n_frames": n, "step": step, "source": tr.get("source"), "objects": {}}
    for label, frames in masks.items():
        keys = sorted(int(k) for k in frames)
        if not keys:
            continue
        static = len(keys) == 1 or label in (tr.get("static") or [])
        ts, polys = [], []
        want = [keys[0]] if static else list(range(0, n, step))
        have = set(keys)
        for i in want:
            if i not in have:
                ts.append(round(i / fps, 3)); polys.append(None)
                continue
            rle = frames[str(i)]
            hh, ww = rle["size"]
            ts.append(round(i / fps, 3)); polys.append(_poly(rle_decode(rle), ww, hh))
        out["objects"][label] = {"static": static, "t": ts, "poly": polys,
                                 "visible": round(sum(p is not None for p in polys) / max(1, len(polys)), 3)}
    return out


# ------------------------------------------------------------ simulation
def _quat_tilt(q) -> float:
    w, x, y, z = q
    zz = 1.0 - 2.0 * (x * x + y * y)
    return math.degrees(math.acos(max(-1.0, min(1.0, zz))))


def _rect_gap(px, py, cx, cy, yaw, hl, ht) -> float:
    dx, dy = px - cx, py - cy
    c, s = math.cos(-yaw), math.sin(-yaw)
    lx, ly = c * dx - s * dy, s * dx + c * dy
    ex, ey = max(abs(lx) - hl, 0.0), max(abs(ly) - ht, 0.0)
    return math.hypot(ex, ey)


def series(payload: dict, scene: dict, *, hz: float = 10.0) -> dict:
    """Derived per-run curves for the charts: container tilt and an approximate gap to the obstacle.

    The gap is the horizontal distance from the container's centre to the obstacle's box minus the
    proxy's radius, from recorded frames: a drawing aid. Verdicts come from the checker only."""
    t = payload["t"]
    if not t:
        return {}
    step = max(1, int(round(payload.get("frame_hz", 30) / hz)))
    cup = next((o for o in scene.get("objects", []) if o.get("kind") == "cup"), None)
    out: dict[str, Any] = {"t": [round(x, 2) for x in t[::step]]}
    if cup and cup["id"] in payload["bodies"]:
        b = payload["bodies"][cup["id"]][::step]
        out["tilt"] = [round(_quat_tilt(p[3:7]), 2) for p in b]
        obs = (scene.get("obstacles") or [])
        if obs:
            o = obs[0]
            w, x, y, z = o["pose"]["quat"]
            yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
            hl = (o.get("length") or 0.2) / 2.0
            ht = (o.get("thickness") or 0.02) / 2.0
            r = (cup.get("size", {}).get("outer_half") or [0.03])[0]
            # wall local y runs along the divider (see mirror/sim); length on y, thickness on x
            out["gap_cm"] = [round(100 * (_rect_gap(p[0], p[1], o["pose"]["pos"][0], o["pose"]["pos"][1], yaw, ht, hl) - r), 1)
                             for p in b]
        out["container"] = cup["id"]
    return out


def path2d(payload: dict, ids: list[str], *, hz: float = 5.0) -> dict:
    step = max(1, int(round(payload.get("frame_hz", 30) / hz)))
    return {i: [[round(p[0], 3), round(p[1], 3), round(p[2], 3)] for p in payload["bodies"][i][::step]]
            for i in ids if i in payload["bodies"]}


def _rule_rows(report: dict) -> list[dict]:
    return [{"rule_id": r["rule_id"], "kind": r.get("kind"), "type": r.get("type"), "verdict": r.get("verdict"),
             "enforced": r.get("enforced"), "objects": r.get("objects") or [], "detail": r.get("detail") or ""}
            for r in report.get("rule_results") or []]


def _summary(report: dict) -> dict:
    oc = report.get("outcomes") or []
    beads = [(o.get("beads_in_container"), o.get("beads_total")) for o in oc if (o.get("beads_total") or 0) > 0]
    return {"status": report.get("status"), "plan_status": report.get("plan_status"), "plan_reason": report.get("plan_reason"),
            "violations": len(report.get("violations") or []), "routes": report.get("route_labels") or [],
            "beads": {"kept": sum(b[0] or 0 for b in beads), "total": sum(b[1] or 0 for b in beads)} if beads else None,
            "max_transport_tilt_deg": max([o.get("max_transport_tilt_deg") or 0 for o in oc], default=None),
            "in_target": sum(1 for o in oc if o.get("in_target")), "objects": len(oc),
            "execution_time_s": report.get("execution_time_s"), "checker": report.get("checker"),
            "evidence_source": report.get("evidence_source")}


def spec_view(spec) -> dict:
    """The rules in plain words, in the order the page lists them."""
    d = spec.model_dump(mode="json")
    rows = []
    for g in d.get("goals", []):
        rows.append({"id": g["id"], "kind": "goal", "text": f"Put the {(g['object'] or {}).get('kind') or 'object'} {g['type'].replace('_', ' ')} the tray",
                     "status": g.get("status"), "source": (g.get("provenance") or {}).get("source")})
    for o in d.get("ordering", []):
        rows.append({"id": o["id"], "kind": "order", "text": f"{(o['before'] or {}).get('kind')} before {(o['after'] or {}).get('kind')}",
                     "status": o.get("status"), "source": (o.get("provenance") or {}).get("source")})
    for c in d.get("constraints", []):
        rows.append({"id": c["id"], "kind": "rule", "text": c.get("description") or c["type"], "type": c["type"], "phase": c.get("phase"),
                     "status": c.get("status"), "source": (c.get("provenance") or {}).get("source")})
    return {"spec_id": d.get("spec_id"), "version": d.get("version"), "summary": d.get("summary"),
            "interpreter": d.get("interpreter"), "rules": rows}


def simulate(scene, spec, candidates: list[dict], rollout_dir: Path, *, client=None, base_only: bool = False,
             prefix: str = "") -> dict:
    """Run the task once, then each correction from the same base: compile offline, re-check the
    old run under the new rules, plan and run again. Returns the run + corrections blocks."""
    from mirror.interpret import LLMClient
    from mirror.interpret.types import NeedsClarification, CorrectionRejected  # noqa: F401
    from mirror.pipeline import Session
    from mirror.studio.session_adapter import compact_rollout
    rollout_dir.mkdir(parents=True, exist_ok=True)
    scene_d = scene.model_dump(mode="json")
    ids = [o["id"] for o in scene_d["objects"]]

    def save(entry, name, run_spec=None) -> dict:
        payload = compact_rollout(entry.rollout, max_hz=20.0,
                                  extra={"scene": scene_d, "spec": (run_spec or spec).model_dump(mode="json")})
        name = prefix + name
        payload["id"] = name
        (rollout_dir / f"{name}.json").write_text(json.dumps(payload, separators=(",", ":")))
        rep = entry.rollout.report.model_dump(mode="json")
        return {"id": name, "file": f"rollouts/{name}.json", "summary": _summary(rep), "rules": _rule_rows(rep),
                "series": series(payload, scene_d), "paths": path2d(payload, ids), "duration_s": payload["t"][-1] if payload["t"] else 0}

    t0 = time.time()
    client = client or LLMClient.offline_client()
    base_sess = Session(scene=scene, client=client)
    base_sess.specs = [spec]
    base = base_sess.run(condition="your layout, your task")
    run = save(base, "base")
    run["wall_s"] = round(time.time() - t0, 1)
    log(f"base run: {run['summary']['status']} in {run['wall_s']} s")
    out = []
    for cand in candidates:
        t1 = time.time()
        item = {k: cand.get(k) for k in ("id", "label", "why")}
        item["text"] = cand.get("text") or cand.get("compile_text")
        s = Session(scene=scene, client=client)
        s.specs = [spec]
        b = s.run(condition="before the correction")
        try:
            new_spec, record = s.correct(item["text"])
        except Exception as exc:  # NeedsClarification / CorrectionRejected are answers, not crashes
            kind = type(exc).__name__
            item["compiler"] = "offline rules (deterministic, no model call)" if getattr(client, "offline", True) else f"{getattr(client, 'model', 'model')} ({client.mode})"
            if kind not in ("NeedsClarification", "CorrectionRejected", "InterpretError"):
                raise
            item.update({"outcome": "question" if kind == "NeedsClarification" else "refused",
                         "message": str(exc), "error_type": kind})
            log(f"{item['id']}: {kind}")
            out.append(item)
            continue
        rec = record.model_dump(mode="json")
        item["diff"] = rec.get("diff") or []
        item["model"] = rec.get("model")
        item["compiler"] = ("offline rules (deterministic, no model call)" if (rec.get("model") or "").startswith("offline")
                            else f"{rec.get('model')} ({getattr(client, 'mode', 'live')})")
        item["version"] = new_spec.version
        if new_spec.version == spec.version:
            item.update({"outcome": "no_change", "message": "The rules already say this; nothing changed."})
            out.append(item)
            continue
        recheck = s.recheck(b.rollout_id).model_dump(mode="json")
        after = s.run(condition="after the correction")
        item["recheck"] = {"status": recheck.get("status"), "rules": _rule_rows(recheck),
                           "violations": [{k: v.get(k) for k in ("constraint_id", "object_id", "t_start", "t_end", "peak", "detail")}
                                          for v in recheck.get("violations") or []]}
        item["after"] = save(after, f"corr_{item['id']}", new_spec)
        # The old run judged under the new rules (for the viewer's ribbons and the re-check table).
        (rollout_dir / f"{prefix}recheck_{item['id']}.json").write_text(json.dumps(recheck, separators=(",", ":")))
        item["recheck_file"] = f"rollouts/{prefix}recheck_{item['id']}.json"
        added = {d.get("target_id") for d in item["diff"]}
        old_ok = all(r["verdict"] != "violated" for r in item["recheck"]["rules"] if r["rule_id"] in added)
        item["outcome"] = "already_held" if old_ok else "changed"
        if after.rollout.report.status == "planning_failed":
            item["outcome"] = "no_plan"          # the planner refuses: no motion keeps every rule; nothing was executed
        item["spec"] = spec_view(new_spec)
        item["wall_s"] = round(time.time() - t1, 1)
        log(f"{item['id']}: {item['outcome']} (old run {recheck.get('status')}, new run {item['after']['summary']['status']}) {item['wall_s']} s")
        out.append(item)
    return {"run": run, "corrections": out}


def recorded_item(r: dict, scene, spec, rollout_dir: Path) -> dict:
    """A correction the capture run compiled and executed itself (its own files, its own compiler label)."""
    from mirror.contracts import Rollout, TaskSpec
    from mirror.studio.session_adapter import compact_rollout
    scene_d = scene.model_dump(mode="json")
    ro = Rollout.model_validate_json(Path(r["rollout_path"]).read_text())
    new_spec = TaskSpec.model_validate_json(Path(r["spec_path"]).read_text())
    payload = compact_rollout(ro, max_hz=20.0, extra={"scene": scene_d, "spec": new_spec.model_dump(mode="json")})
    name = f"rec_{r['id']}"
    payload["id"] = name
    (rollout_dir / f"{name}.json").write_text(json.dumps(payload, separators=(",", ":")))
    rep = ro.report.model_dump(mode="json")
    recheck = json.loads(Path(r["recheck_path"]).read_text())
    old_ids = {c["id"] for c in spec.model_dump(mode="json").get("constraints", [])}
    added = [c for c in new_spec.model_dump(mode="json").get("constraints", []) if c["id"] not in old_ids]
    diff = [{"op": "add_constraint", "target_id": c["id"], "value": c} for c in added]
    ids = [o["id"] for o in scene_d["objects"]]
    after = {"id": name, "file": f"rollouts/{name}.json", "summary": _summary(rep), "rules": _rule_rows(rep),
             "series": series(payload, scene_d), "paths": path2d(payload, ids), "duration_s": payload["t"][-1] if payload["t"] else 0}
    item = {"id": r["id"], "text": r.get("text"), "label": r.get("label"), "why": "Compiled and run by the capture run itself.",
            "compiler": r.get("compiler") or "the capture run", "recorded": True, "rehearsal": r.get("rehearsal"),
            "diff": diff, "version": new_spec.version, "spec": spec_view(new_spec),
            "recheck": {"status": recheck.get("status"), "rules": _rule_rows(recheck),
                        "violations": [{k: v.get(k) for k in ("constraint_id", "object_id", "t_start", "t_end", "peak", "detail")}
                                       for v in recheck.get("violations") or []]},
            "after": after}
    # The full re-check report, so the 3D view can judge the old run's frames under the new rule.
    (rollout_dir / f"rec_recheck_{r['id']}.json").write_text(json.dumps(recheck, separators=(",", ":")))
    item["recheck_file"] = f"rollouts/rec_recheck_{r['id']}.json"
    added_ids = {d["target_id"] for d in diff}
    old_ok = all(x["verdict"] != "violated" for x in item["recheck"]["rules"] if x["rule_id"] in added_ids)
    item["outcome"] = "no_plan" if rep.get("status") == "planning_failed" else ("already_held" if old_ok else "changed")
    if r.get("before_after_path"):
        dst = rollout_dir.parent / "media" / f"corr_{r['id']}_before_after.mp4"
        info = probe(r["before_after_path"])
        if info.get("codec") == "h264":
            shutil.copyfile(r["before_after_path"], dst)
            item["video"] = {"file": f"media/{dst.name}", **info}
    return item


# ------------------------------------------------------------- plan view
def plan_view(sess, scene: dict, *, hz: float = 5.0, placement: Optional[dict] = None) -> Optional[dict]:
    """Top view in the simulator frame: where MIRROR saw your objects and hands move (from the capture,
    one camera, approximate), where the proxies start, and where the phone was.

    The capture's table frame is mapped onto the simulator frame with the placement the scene was built
    with, recovered from the tray (the one object both share): rotation from the session's placement yaw,
    translation so the capture's tray centre lands on the scene's tray."""
    import numpy as np
    if sess.reconstruction is None or sess.tray is None or not scene.get("trays"):
        return None
    if placement and placement.get("origin_xy_m") is not None:
        yaw = math.radians(float(placement.get("yaw_deg") or 0.0))
        c, s_ = math.cos(yaw), math.sin(yaw)
        ox, oy = placement["origin_xy_m"]
        origin_from = f"placement file ({placement.get('source') or 'given'})"
    else:
        yaw = math.radians(sess.placement.yaw_deg if sess.placement else 0.0)
        c, s_ = math.cos(yaw), math.sin(yaw)
        tc = sess.tray.center_xy
        st = scene["trays"][0]["pose"]["pos"]
        ox, oy = st[0] - (c * tc[0] - s_ * tc[1]), st[1] - (s_ * tc[0] + c * tc[1])
        origin_from = "tray centre (capture) onto the scene's tray; yaw from the session placement"

    def to_sim(p):
        return [round(ox + c * p[0] - s_ * p[1], 4), round(oy + s_ * p[0] + c * p[1], 4)]

    real = {}
    for label, tr in sess.reconstruction.objects.items():
        ts = list(getattr(tr, "t", None) or [])
        if not ts:
            continue
        step = max(1, int(round((len(ts) / max(ts[-1] - ts[0], 1e-6)) / hz)))
        pts = [[round(ts[k], 2)] + to_sim(tr.pos[k]) for k in range(0, len(ts), step) if tr.pos[k] is not None]
        rest = None
        idx = [k for k, (p, r) in enumerate(zip(tr.pos, getattr(tr, "resting", [True] * len(tr.pos)))) if p is not None and r]
        if idx:
            rest = to_sim(np.median(np.array([tr.pos[k] for k in idx[:5]]), 0))
        real[label] = {"path": pts, "rest": rest}
    cam = None
    pose = (sess.poses or {}).get("top")
    if pose is not None:
        R = np.asarray(pose.R, float); t = np.asarray(pose.t, float).reshape(3)
        C = -R.T @ t
        f = R.T @ np.array([0.0, 0.0, 1.0])
        cam = {"xy": to_sim(C), "height_m": round(float(C[2]), 3), "look_xy": [round(c * f[0] - s_ * f[1], 3), round(s_ * f[0] + c * f[1], 3)],
               "note": "phone position from the board pose (one view; calibration reuse flagged)"}
    return {"frame": "simulator (m)", "real": real, "camera": cam,
            "origin_from": origin_from,
            "note": "Your objects' paths come from one camera and the table-plane assumption: approximate, not measured."}


# ---------------------------------------------------------------- sources
@dataclass
class Source:
    """Where one capture's files are. Paths may be None when a step did not produce them."""
    capture_id: str
    title: str
    session: Path                     # CaptureSession json (after the person's answers, if any)
    take: Path                        # original task clip
    tracks: Optional[Path]            # SAM track file used for the session
    scene: Optional[Path]             # SceneState json in the simulator frame
    spec: Optional[Path]              # TaskSpec json to run
    spec_source: str                  # "inferred" | "person_stated_intent"
    evidence: dict = field(default_factory=dict)       # label -> image
    twin: dict = field(default_factory=dict)           # name -> mp4
    sim_video: Optional[Path] = None
    candidates: list = field(default_factory=lambda: list(DEFAULT_CANDIDATES))
    labels: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    spend: Optional[dict] = None
    calibration: dict = field(default_factory=dict)
    placement_note: str = ""
    placement_file: Optional[Path] = None   # json with origin_xy_m + yaw_deg (simulator placement of the table frame)
    recorded_corrections: list = field(default_factory=list)   # corrections the capture run itself compiled and ran
    questions: Optional[Path] = None         # questions.json the capture run asked the person
    answers: Optional[Path] = None           # the person's answers ({id: {answer, by, at, seconds_spent}})


# The D1 development capture's folders come from the environment (they are local to the machine that made it):
# MIRROR_D1_ROOT (phase 1 + 2 working folder: D1/session.json, D1/IMG_0815.MOV, out/phase2/) and
# MIRROR_D1_STORE (the published copy: real-demo/, lens-IMG_0806/, twin/).
D1_ROOT = Path(os.environ.get("MIRROR_D1_ROOT", "phone_real"))
D1_CLOUD = Path(os.environ.get("MIRROR_D1_STORE", "capture/D1"))
D1_CANDIDATES = [
    {"id": "mug_clearance", "text": "Keep the mug at least 6 cm away from the divider while carrying it.",
     "label": "Keep the mug 6 cm clear of the divider",
     "why": "Adds a safety margin around the obstacle; does not change order, goals or routes you asked for."},
    {"id": "box_upright", "text": "Keep the block upright while it is carried.",
     "label": "Keep the red box upright while carried",
     "why": "Your carton stands 11 cm tall; this asks the robot not to tip it in transit."},
    {"id": "no_table_contact", "text": "Nothing may touch the table outside the tray.",
     "label": "Nothing touches the table outside the tray",
     "why": "No dragging or setting things down on the way; consistent with into-the-tray."},
]


def d1_source(root: Path = D1_ROOT, cloud: Path = D1_CLOUD, twin_dir: Optional[Path] = None) -> Source:
    rd = cloud / "real-demo"
    p2 = rd / "phase2"
    loc = root / "out" / "phase2"
    pick = lambda name: (loc / name) if (loc / name).exists() else (p2 / name)
    ev = {p.stem: p for p in sorted((rd / "evidence").glob("*.jpg"))} if (rd / "evidence").exists() else {}
    twin = {}
    for tdir in [p for p in (twin_dir, cloud / "twin") if p]:
        if tdir.exists() and not twin:
            for p in sorted(tdir.glob("*.mp4")):
                if not p.stem.startswith("arm_"):     # arm films come from a different run (the UR10e variant)
                    twin[p.stem] = p
    spend = None
    if (rd / "spend.json").exists():
        try:
            spend = json.loads((rd / "spend.json").read_text())
        except ValueError:
            spend = None
    intr = {}
    if (cloud / "lens-IMG_0806" / "intrinsics.json").exists():
        intr = {"lens_clip": "IMG_0806", "lens": "accepted (0.60 px, 65 views)",
                "pose": "board at rest 4.8 s: accepted (1.13 px, about 88 cm)", "focal_check": "+7.1% (limit 6%)"}
    return Source(
        capture_id="D1", title="Your table: development take D1",
        session=root / "D1" / "session.json", take=root / "D1" / "IMG_0815.MOV",
        tracks=root / "D1" / "tracks_top_sam_static_divider.json.gz",
        scene=pick("scene.json"), spec=pick("spec_intent.json"), spec_source="person_stated_intent",
        evidence=ev, twin=twin, sim_video=pick("sim_run.mp4"), candidates=D1_CANDIDATES,
        labels=["Real phone footage", "One camera (single view, reduced)", "Calibration reuse flagged",
                "Development clip D1, never scored"],
        notes=["Recorded 3 Nov 2026 on one iPhone 17 Pro; measurements by ruler.",
               "Simulated proxies stand in for your objects: a 4 cm cube for the 11 cm carton, a thicker marker; placement is approximate."],
        spend=spend, calibration=intr,
        placement_note="Layout centred on the simulator table by software (auto_layout_centred); positions approximate.",
        placement_file=pick("phase2_summary.json"))


def rehearsal_flag(c, where: str) -> bool:
    """A manifest correction's ``rehearsal`` flag. Missing, None or not a bool raises: a correction of unknown origin
    must never be shown as the person's own (it would lose its "rehearsal, not chosen by you" badge)."""
    v = c.get("rehearsal") if isinstance(c, dict) else None
    if not isinstance(v, bool):
        name = c.get("name") if isinstance(c, dict) else None
        raise ValueError(f"{where}: correction {name!r} has rehearsal={v!r}; expected true (rehearsed by the pipeline, "
                         "not chosen by the person) or false (the person's correction)")
    return v


def pipeline_source(d: Path, capture_id: Optional[str] = None, twin_override: Optional[Path] = None) -> Source:
    """A ``mirror.capture.real_run`` output directory (its manifest.json and page.json)."""
    d = Path(d)
    man = json.loads((d / "manifest.json").read_text()) if (d / "manifest.json").exists() else {}
    page = json.loads((d / (man.get("page") or "page.json")).read_text()) if (d / (man.get("page") or "page.json")).exists() else {}

    def f(rel: Optional[str], *fallbacks: str) -> Optional[Path]:
        for cand in ([rel] if rel else []) + list(fallbacks):
            p = Path(cand) if Path(cand).is_absolute() else d / cand
            if p.exists():
                return p
        return None

    session = f(man.get("session"), "session_answered.json", "session.json")
    take = f(man.get("take"), "take.mov", "take.mp4")
    if session is None or take is None:
        raise SystemExit(f"{d}: needs the session and the take clip (manifest keys 'session', 'take')")
    task = page.get("task") or {}
    gate_ok = bool(task.get("gate_passed"))
    spec = f(man.get("spec"), "spec.json", "proposal/spec.json")
    ss = str(man.get("spec_source") or task.get("spec_source") or "")
    spec_source = "inferred" if (gate_ok or (man.get("gate") or {}).get("passed")) and "stated" not in ss else "person_stated_intent"
    cands = [{"id": c["id"], "text": c.get("compile_text") or c.get("text") or c.get("label"), "label": c.get("label"),
              "why": c.get("why")} for c in page.get("correction_candidates") or []] or list(DEFAULT_CANDIDATES)
    twin_dir = Path(twin_override) if twin_override else f(man.get("twin_dir"), "twin")
    ev_dir = f(man.get("evidence_dir"), "evidence")
    sp = man.get("spend") or {}
    spend = sp.get("usd") if sp else page.get("spend_usd")
    recorded = []
    for c in man.get("corrections") or []:
        rehearsal = rehearsal_flag(c, str(d / "manifest.json"))     # missing or None is an error, never False
        paths = {k: f(c.get(k)) for k in ("rollout", "spec", "report", "recheck", "before_after", "video")}
        if paths["rollout"] and paths["spec"] and paths["recheck"]:
            recorded.append({"id": c.get("name"), "text": c.get("text") or c.get("label"), "label": c.get("label"),
                             "rehearsal": rehearsal, "compiler": c.get("compiler"), **{k + "_path": v for k, v in paths.items()}})
    return Source(capture_id=capture_id or man.get("capture_id") or d.parent.name if d.name == "out" else (capture_id or d.name),
                  title=man.get("title") or "Your table",
                  session=session, take=take, tracks=f(man.get("tracks"), "tracks_full.json.gz", "tracks_proxy.json.gz"),
                  scene=f(man.get("scene"), "scene/scene.json", "scene.json"), spec=spec, spec_source=spec_source,
                  evidence={p.stem: p for p in sorted(ev_dir.glob("*.jpg"))} if ev_dir else {},
                  twin={p.stem: p for p in sorted(twin_dir.glob("*.mp4")) if not p.stem.startswith("arm_")} if twin_dir and twin_dir.exists() else {},
                  sim_video=f(man.get("sim_video"), "run/sim_run.mp4"), candidates=cands, labels=man.get("labels") or page.get("labels") or [],
                  notes=(man.get("notes") or []) + ([page["headline"]] if page.get("headline") else []),
                  spend={"spent_usd": spend, "cap_usd": sp.get("cap_usd") or page.get("cap_usd"), "mode": sp.get("mode")} if spend is not None else None,
                  calibration=man.get("calibration") or page.get("calibration") or {},
                  placement_file=f(man.get("table_to_sim"), "table_to_sim.json"),
                  placement_note=man.get("placement_note") or (json.loads(f(man.get("table_to_sim"), "table_to_sim.json").read_text()).get("label")
                                                               if f(man.get("table_to_sim"), "table_to_sim.json") else ""),
                  recorded_corrections=recorded, questions=f(man.get("questions"), "questions.json"),
                  answers=f(man.get("answers"), "answers.json"))


# ------------------------------------------------------------------ build
def labels_for(sess, given: list[str]) -> list[str]:
    out = list(given)
    if not out:
        out.append("SYNTHETIC test render" if sess.synthetic else "Real phone footage")
        out.append("One camera (single view, reduced)" if str(sess.mode).endswith("single_view") or sess.mode == "single_view"
                   else "Two cameras")
        if any(r.code == "recalibration_required" and not r.resolved for r in sess.review):
            out.append("Calibration reuse flagged")
    return out


def build(src: Source, out: Path, *, simulate_runs: bool = True, video: bool = True) -> dict:
    from mirror.capture.session import CaptureSession
    from mirror.capture import trace as T
    from mirror.contracts import SceneState, TaskSpec
    t_start = time.time()
    out = Path(out)
    media = out / "media"
    media.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}

    sess = CaptureSession.load(src.session)
    ok, blocking = sess.gate()
    try:
        T.propose_offline_from_capture(sess)
        proposal = "allowed"
    except Exception as exc:  # CaptureNotReviewed: the page shows the refusal verbatim
        proposal = "refused: " + str(exc)
    gate = {"passed": bool(ok), "blocking": [{"code": b.code, "message": b.message} for b in blocking],
            "proposal": proposal, "evidence_kind": sess.evidence_kind,
            "fix": {"recalibration_required": "Record the lens clip with the same phone, settings and position as the take (focus and exposure locked)."}}

    # ---- objects, confirmations, events
    corr_by_target = {}
    for c in sess.corrections:
        d = c.model_dump(mode="json")
        corr_by_target.setdefault(d.get("target"), []).append(d)
    objects = []
    for i, m in enumerate(sess.mappings):
        md = m.model_dump(mode="json")
        lab = md["track_label"]
        conf = [c for c in corr_by_target.get(f"object:{lab}", []) if c.get("by") == "person"]
        dims = {k: v.get("value_m") for k, v in (md.get("real_dims") or {}).items()}
        objects.append({"label": lab, "name": lab.replace("_", " "), "identity": md.get("real_identity"), "proxy": md.get("proxy"),
                        "dims_m": dims, "unsupported": md.get("unsupported_features") or [], "contents": md.get("real_contents"),
                        "confirmed_by_person": bool(md.get("confirmed_by_person")),
                        "confirmed": ({"answer": conf[-1].get("after"), "seconds_spent": conf[-1].get("seconds_spent"), "at": conf[-1].get("at")}
                                      if conf else None),
                        "fidelity": md.get("fidelity_statement"), "color": LABEL_COLOURS.get(lab, PALETTE[i % len(PALETTE)]), "evidence": None})
    events = []
    for e in sess.events:
        d = e.model_dump(mode="json")
        person = [c for c in corr_by_target.get(f"event:{d['id']}", []) if c.get("by") == "person"]
        events.append({"id": d["id"], "object": d["object"], "type": d["type"], "t": round(d["t"], 3),
                       "bracket": [round(d.get("t_lo") or d["t"], 3), round(d.get("t_hi") or d["t"], 3)],
                       "status": d.get("status"), "placed_in": d.get("placed_in"), "ambiguous": d.get("ambiguous"),
                       "actor": d.get("actor"), "person": person[-1] if person else None})
    events.sort(key=lambda x: x["t"])
    review = [{"code": r.code, "severity": r.severity, "message": r.message, "object": r.object, "event": r.event,
               "resolved": r.resolved, "resolution": r.resolution} for r in sess.review]

    # ---- the questions the capture run asked, with the person's answers (never MIRROR's)
    questions = None
    if src.questions and src.questions.exists():
        qj = json.loads(src.questions.read_text())
        ans = json.loads(src.answers.read_text()) if src.answers and src.answers.exists() else {}
        questions = []
        for q in qj.get("questions") or []:
            a = ans.get(q.get("id")) or {}
            person = a if a.get("by") == "person" else None
            questions.append({k: q.get(k) for k in ("id", "kind", "question", "options", "proposal", "resolves")} |
                             {"t_s": (q.get("evidence") or {}).get("t_s"),
                              "answer": person.get("answer") if person else None,
                              "seconds_spent": person.get("seconds_spent") if person else None})

    # ---- media
    t0 = time.time()
    take_info = {}
    if video:
        take_info = transcode(src.take, media / "take.mp4")
        log(f"take: {take_info}")
    elif (media / "take.mp4").exists():
        take_info = probe(media / "take.mp4")
    timings["video_s"] = round(time.time() - t0, 1)
    def evidence_for(label: str) -> Optional[Path]:
        keys = [k for k in src.evidence if k == label or k.startswith(label + "_")]
        keys.sort(key=lambda k: (k != label, "12.5" not in k and "seed" not in k, "start" in k or "end" in k, k))
        return src.evidence[keys[0]] if keys else None

    for o in objects:
        img = evidence_for(o["label"])
        if img and img.exists():
            name = f"evidence_{o['label']}.jpg"
            small_jpeg(img, media / name)
            o["evidence"] = f"media/{name}"
    twin = []
    for name, p in src.twin.items():
        dst = media / f"twin_{name}.mp4"
        info = probe(p)
        if info.get("codec") == "h264" and (info.get("height") or 9999) <= 1280:
            shutil.copyfile(p, dst)
        else:
            info = transcode(p, dst, height=720)
        item = {"name": name, "file": f"media/{dst.name}", **info}
        # A poster frame, so the player is not a black box before it plays (twin stills when present, else frame 40%).
        poster = media / f"twin_{name}.jpg"
        stills = sorted((p.parent / "stills").glob(f"{ {'twin_3panel': 'twin', 'before_after': 'before_after'}.get(name, name) }_*.png")) if (p.parent / "stills").exists() else []
        try:
            if stills:
                small_jpeg(stills[len(stills) // 2], poster, width=720)
            else:
                _run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-ss", str(round(0.4 * (info.get("duration_s") or 0), 2)), "-i", str(dst),
                      "-frames:v", "1", "-vf", "scale='min(720,iw)':-2", str(poster)], timeout=60)
            item["poster"] = f"media/{poster.name}"
        except Exception as exc:  # posters are optional
            log(f"poster for {name} skipped: {exc}")
        twin.append(item)
    twin_meta = None
    tj_path = next((p.parent / "twin.json" for p in src.twin.values() if (p.parent / "twin.json").exists()), None)
    if tj_path:
        tj = json.loads(tj_path.read_text())
        twin_meta = {k: tj.get(k) for k in ("caption", "labels", "registration", "honesty", "before_after")}
        td = tj.get("topdown") or {}
        img = tj_path.parent / (td.get("image") or "")
        if td.get("corners_sim_xy") and img.is_file():
            import cv2
            im = cv2.imread(str(img))
            cv2.imwrite(str(media / "twin_topdown.jpg"), im, [cv2.IMWRITE_JPEG_QUALITY, 84])
            twin_meta["topdown"] = {"file": "media/twin_topdown.jpg", "size": [int(im.shape[1]), int(im.shape[0])],
                                    "corners_sim_xy": td["corners_sim_xy"], "note": td.get("note")}
    sim_video = None
    if src.sim_video and src.sim_video.exists():
        shutil.copyfile(src.sim_video, media / "sim_run.mp4")
        sim_video = {"file": "media/sim_run.mp4", **probe(media / "sim_run.mp4")}

    # ---- contours
    t0 = time.time()
    contours = contours_from_tracks(src.tracks) if src.tracks and src.tracks.exists() else None
    timings["contours_s"] = round(time.time() - t0, 1)

    # ---- task + robot
    task, robot = None, None
    if src.spec and src.spec.exists() and src.scene and src.scene.exists():
        spec = TaskSpec.model_validate_json(src.spec.read_text())
        scene = SceneState.model_validate_json(src.scene.read_text())
        task = {"source": src.spec_source, **spec_view(spec)}
        if simulate_runs:
            t0 = time.time()
            rec_ids = {r["id"] for r in src.recorded_corrections}
            robot = simulate(scene, spec, [c for c in src.candidates if c.get("id") not in rec_ids], out / "rollouts")
            for r in src.recorded_corrections:
                try:
                    robot["corrections"].insert(0, recorded_item(r, scene, spec, out / "rollouts"))
                except Exception as exc:  # a malformed record is reported, not hidden
                    log(f"recorded correction {r.get('id')} skipped: {exc}")
            timings["simulate_s"] = round(time.time() - t0, 1)
            robot["scene"] = scene.model_dump(mode="json")
            robot["placement_note"] = src.placement_note or scene.notes
        try:
            pl = None
            if src.placement_file and src.placement_file.exists():
                pj = json.loads(src.placement_file.read_text())
                pl = pj.get("placement") if isinstance(pj.get("placement"), dict) and "origin_xy_m" in pj.get("placement", {}) else pj
            plan = plan_view(sess, scene.model_dump(mode="json"), placement=pl)
        except Exception as exc:  # the page works without it
            log(f"plan view skipped: {exc}")
            plan = None
        if robot is not None:
            robot["plan"] = plan
        # The live studio re-plans corrections from these two files.
        shutil.copyfile(src.scene, out / "scene.json")
        shutil.copyfile(src.spec, out / "spec.json")

    # The phone that filmed the table, for viewers that can draw it (viz3d's mirror.viz.capture_view, when installed).
    capture_view = None
    try:
        import importlib.util
        if importlib.util.find_spec("mirror.viz.capture_view") and src.placement_file:
            cv_dir = out / "capture"
            plate = next((p.parent / "plate.png" for p in src.twin.values() if (p.parent / "plate.png").exists()), None)
            frame = ["--image", str(plate)] if plate else ["--video", str(src.take), "--t", str(round(min(12.5, (take_info or {}).get("duration_s") or 12.5), 2))]
            cmd = [sys.executable, "-m", "mirror.viz.capture_view", "--session", str(src.session), "--placement", str(src.placement_file),
                   *frame, "--out", str(cv_dir)] + [x for lab in labels_for(sess, src.labels) for x in ("--label", lab)]
            _run(cmd, timeout=300)
            if (cv_dir / "capture_view.json").exists():
                capture_view = "capture/capture_view.json"
    except Exception as exc:  # optional: the page works without it
        log(f"capture view skipped: {exc}")

    table = {
        "format": TABLE_FORMAT, "id": src.capture_id, "capture_view": capture_view, "title": src.title,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "labels": labels_for(sess, src.labels), "notes": src.notes,
        "synthetic": bool(sess.synthetic), "evidence_kind": sess.evidence_kind, "session_id": sess.session_id,
        "video": {"file": "media/take.mp4", **take_info} if take_info else None,
        "calibration": src.calibration, "objects": objects, "events": events, "review": review, "questions": questions,
        "contours": "contours.json" if contours else None, "gate": gate, "task": task, "robot": robot, "twin": twin, "twin_meta": twin_meta, "sim_video": sim_video,
        "spend": src.spend, "model_calls_on_build": 0, "timings": timings,
        "honesty": ("Real footage from one phone. Object outlines come from SAM 3.1 masks (recorded); events come from object "
                    "motion in one view, not contact sensing. The robot runs in MuJoCo on simulated proxies placed from the "
                    "capture; positions are approximate. Corrections here are compiled by the offline rules and executed "
                    "on this machine; nothing on this page was computed while you view it.") if not sess.synthetic else
                   "SYNTHETIC test render: not real footage and not evidence about phones.",
    }
    table["timings"]["total_s"] = round(time.time() - t_start, 1)
    if contours:
        (out / "contours.json").write_text(json.dumps(contours, separators=(",", ":")))
    (out / "table.json").write_text(json.dumps(table, indent=1))
    log(f"wrote {out / 'table.json'} in {table['timings']['total_s']} s")
    return table


def write_index(root: Path) -> list[dict]:
    """data/table/index.json: the tables shipped with a site (newest build first)."""
    rows = []
    for p in sorted(Path(root).glob("*/table.json")):
        t = json.loads(p.read_text())
        rows.append({"id": t["id"], "title": t.get("title"), "labels": t.get("labels", []), "built_at": t.get("built_at"),
                     "featured": bool(t.get("featured")), "path": f"{p.parent.name}/table.json"})
    rows.sort(key=lambda r: (r["featured"], r.get("built_at") or ""), reverse=True)   # the page opens the first
    (Path(root) / "index.json").write_text(json.dumps({"tables": rows}, indent=1))
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("d1", help="the D1 development capture")
    a.add_argument("--root", type=Path, default=D1_ROOT)
    a.add_argument("--cloud", type=Path, default=D1_CLOUD)
    a.add_argument("--twin", type=Path, default=None, help="folder with the twin films (overlay.mp4, ... , twin.json)")
    b = sub.add_parser("pipeline", help="a mirror.capture.real_run output directory")
    b.add_argument("dir", type=Path)
    b.add_argument("--id", default=None)
    b.add_argument("--twin", type=Path, default=None, help="folder with the twin films rendered from this run (overrides the manifest's twin_dir)")
    for p in (a, b):
        p.add_argument("--out", type=Path, required=True)
        p.add_argument("--no-sim", action="store_true", help="skip the simulator runs")
        p.add_argument("--no-video", action="store_true", help="keep an existing media/take.mp4")
        p.add_argument("--site", type=Path, default=None, help="also add the result to this exported static site")
        p.add_argument("--title", default=None, help="page title for this capture")
        p.add_argument("--feature", action="store_true", help="open this capture first on the page")
    args = ap.parse_args(argv)
    src = d1_source(args.root, args.cloud, args.twin) if args.cmd == "d1" else pipeline_source(args.dir, args.id, args.twin)
    if args.title:
        src.title = args.title
    t = build(src, args.out, simulate_runs=not args.no_sim, video=not args.no_video)
    if args.feature:
        t["featured"] = True
        (Path(args.out) / "table.json").write_text(json.dumps(t, indent=1))
    write_index(Path(args.out).parent)
    if args.site:
        import importlib.util
        spec = importlib.util.spec_from_file_location("export_static", Path(__file__).resolve().parents[2] / "scripts" / "export_static.py")
        ex = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ex)
        log(f"added {ex.copy_tables(args.site, [args.out])} to {args.site}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
