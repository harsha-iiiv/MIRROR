"""Export real D1 outputs as Remotion assets for the night film (no model calls, no fabrication).

Writes into OUT (Remotion public dir):
  phone.mp4            IMG_0815 rotated to portrait, 720x1280, H.264 (the person's own footage, untouched otherwise)
  masks.json           SAM 3.1 mask outlines per frame (540x960 image coords), simplified polygons, from the
                       recorded tracks (tracks_sam_540x960.json.gz); divider is one static mask (its video track was discarded)
  run_old.json / run_new.json   recorded MuJoCo rollouts: static geoms + per-frame body poses (30 Hz)
  data.json            timeline events, gate result, spec, report rule rows, correction records, labels, provenance

    python film/night/export_assets.py --capture DIR --phase2 DIR --corr DIR --take IMG_0815.MOV --out PUBLIC
"""
from __future__ import annotations
import argparse, gzip, hashlib, json, subprocess
from pathlib import Path
import numpy as np
import cv2

LABELS = {"mug": "mug", "red_box": "red box", "marker": "marker", "tray": "tray", "divider": "divider"}


def rle_decode(rle):
    h, w = rle["size"]
    counts = rle["counts"]
    flat = np.zeros(h * w, np.uint8)
    pos, val = 0, 0
    for c in counts:
        if val:
            flat[pos:pos + c] = 1
        pos += c
        val ^= 1
    return flat.reshape(h, w)


def outline(mask, eps=1.2):
    cs, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cs:
        return None
    c = max(cs, key=cv2.contourArea)
    if cv2.contourArea(c) < 12:
        return None
    p = cv2.approxPolyDP(c, eps, True).reshape(-1, 2)
    return p.astype(int).tolist()


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()[:16]


def export_masks(tr_path, out):
    tr = json.load(gzip.open(tr_path, "rt"))
    n = tr["n_frames"]
    top = tr["masks"]["top"]
    res = {"fps": tr["fps"], "n_frames": n, "size": [540, 960], "source": tr["source"], "objects": {}}
    for lab, frames in top.items():
        keys = sorted(int(k) for k in frames)
        if len(keys) == 1:  # static (divider)
            res["objects"][lab] = {"static": outline(rle_decode(frames[str(keys[0])]))}
            continue
        poly, cen, area = [], [], []
        for i in range(n):
            m = frames.get(str(i))
            if m is None:
                poly.append(None); cen.append(None); area.append(0); continue
            mk = rle_decode(m)
            poly.append(outline(mk))
            ys, xs = np.nonzero(mk)
            cen.append([round(float(xs.mean()), 1), round(float(ys.mean()), 1)] if xs.size else None)
            area.append(int(xs.size))
        res["objects"][lab] = {"poly": poly, "centroid": cen, "area": area}
    out.write_text(json.dumps(res, separators=(",", ":")))
    return res


def export_rollout(path, out, stride=1):
    ro = json.loads(Path(path).read_text())
    bodies = []
    for b in ro["visual"]["bodies"]:
        bodies.append({"name": b["name"], "static": b.get("static", False), "geoms": b["geoms"]})
    names = sorted({k for f in ro["frames"] for k in f["bodies"]})
    fr = ro["frames"][::stride]
    poses = {k: [] for k in names}
    for f in fr:
        for k in names:
            p = f["bodies"].get(k)
            poses[k].append(None if p is None else [round(v, 5) for v in p["pos"]] + [round(v, 5) for v in p["quat"]])
    rep = ro["report"]
    res = {"frame_hz": ro["frame_hz"] / stride, "n": len(fr), "t": [round(f["t"], 3) for f in fr],
           "phase": [f.get("phase", "") for f in fr], "gripper_width": [round(f.get("gripper_width", 0), 4) for f in fr],
           "bodies": bodies, "poses": poses, "rollout_id": rep["rollout_id"], "status": rep["status"],
           "routes": [r for r in rep["route_labels"] if not r.startswith("transit")],
           "beads": [[o["object_id"], o["beads_in_container"], o["beads_total"]] for o in rep["outcomes"] if o["beads_total"]]}
    out.write_text(json.dumps(res, separators=(",", ":")))
    return res


def film_numbers(C, run_old, run_new, scene):
    """Numbers the film quotes, copied from the correction record and the recorded rollouts (nothing computed by hand)."""
    mc = next(c for c in C["candidates"] if c["id"] == "mug_clearance")

    def path(run, oid="cup_0"):
        idx = [i for i, ph in enumerate(run["phase"]) if ph in (f"lift:{oid}", f"transport:{oid}", f"place:{oid}")]
        return idx, [[run["poses"][oid][i][0], run["poses"][oid][i][1]] for i in idx]

    def nearest(run, t):
        return int(min(range(run["n"]), key=lambda i: abs(run["t"][i] - t)))

    io, po = path(run_old); inw, pn = path(run_new)
    mo, mn = C["old"]["measures"]["cup_0"], mc["new"]["measures"]["cup_0"]
    rule = next(r for r in mc["old_rechecked"]["rules"] if r["rule_id"] == "c_keep_clearance_transport_cup")
    return dict(
        correction_text=mc["text"], rule_id="c_keep_clearance_transport_cup", min_m=0.06,
        old_status=C["old"]["status"], old_rechecked_status=mc["old_rechecked"]["status"], new_status=mc["new"]["status"],
        old_detail=rule["detail"],
        old_clear_m=mo["min_transport_clearance_m"], old_clear_t=mo["at_t_s"], old_clear_frame=nearest(run_old, mo["at_t_s"]),
        new_clear_m=mn["min_transport_clearance_m"], new_clear_t=mn["at_t_s"], new_clear_frame=nearest(run_new, mn["at_t_s"]),
        old_path_frames=[io[0], io[-1]], new_path_frames=[inw[0], inw[-1]], old_path=po, new_path=pn,
        unchanged={oid: [C["old"]["measures"][oid]["min_transport_clearance_m"], mc["new"]["measures"][oid]["min_transport_clearance_m"]]
                   for oid in ("block_0", "marker_0")},
        tilt=[mo["max_transport_tilt_deg"], mn["max_transport_tilt_deg"]],
        beads_old=run_old["beads"], beads_new=run_new["beads"],
        exec_s=[C["old"]["execution_time_s"], mc["new"]["execution_time_s"]],
        rows=dict(old=C["old"]["rules"], rechecked=mc["old_rechecked"]["rules"], new=mc["new"]["rules"]),
        others=[dict(id=c["id"], text=c["text"], outcome=c["outcome"],
                     detail=(c.get("question") or c.get("reason") or
                             (c["old_rechecked"]["status"] + " / " + c["new"]["status"] if c["outcome"] == "apply" else "")),
                     routes_same=(c["new"]["routes"] == C["old"]["routes"]) if c["outcome"] == "apply" else None)
                for c in C["candidates"] if c["id"] != "mug_clearance"],
    )


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", required=True, help="D1 real-demo dir (timeline.json, tracks_sam_540x960.json.gz)")
    ap.add_argument("--phase2", required=True)
    ap.add_argument("--corr", required=True, help="make_correction.py output")
    ap.add_argument("--take", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--skip-video", action="store_true")
    ap.add_argument("--twin", default=None, help="twin track output dir (twin.json + overlay.mp4), optional")
    a = ap.parse_args(argv)
    cap, p2, corr, out = Path(a.capture), Path(a.phase2), Path(a.corr), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if not a.skip_video:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", a.take, "-an", "-vf",
                        "scale=720:1280:flags=lanczos,format=yuv420p", "-c:v", "libx264", "-crf", "18", "-preset", "medium",
                        "-g", "15", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                        str(out / "phone.mp4")], check=True, timeout=900, stdin=subprocess.DEVNULL)
    masks = export_masks(cap / "tracks_sam_540x960.json.gz", out / "masks.json")
    C = json.loads((corr / "corrections.json").read_text())
    ro = export_rollout(corr / "rollout_old.json", out / "run_old.json")
    rn = export_rollout(corr / "rollout_mug_clearance.json", out / "run_new.json")
    timeline = json.loads((cap / "timeline.json").read_text())
    p2s = json.loads((p2 / "phase2_summary.json").read_text())
    spec = json.loads((p2 / "spec_intent.json").read_text())
    scene = json.loads((p2 / "scene.json").read_text())
    data = dict(
        labels=C["labels"],
        timeline=timeline, gate=p2s["gate"], placement=p2s["placement"], divider_axis=p2s["divider_axis"],
        spec=spec, scene=scene, film=film_numbers(C, ro, rn, scene),
        provenance=dict(
            take="IMG_0815.MOV (owner's phone, one camera, 1920x1080 HEVC, 49.8 s) sha256 " + sha(a.take),
            masks="SAM 3.1 box-prompt tracking seeded by Muse Spark grounding at 12.5 s (recorded cassettes, $0.33 spent on 3 Nov); no model call tonight",
            run="MuJoCo rollouts re-run tonight by film/night/make_correction.py (reproduces the phase-2 report)",
            correction="offline rule interpreter (no model call)"),
    )
    if a.twin and (Path(a.twin) / "twin.json").exists():
        tj = json.loads((Path(a.twin) / "twin.json").read_text())
        reg = tj.get("registration", {})
        tt = reg.get("tray_table_frame", {})
        data["twin"] = dict(caption=tj.get("caption"), labels=tj.get("labels"), time_map=tj.get("time_map"),
                            tray_iou=reg.get("tray_iou"), tray_offset_cm=tt.get("centre_offset_cm"), tray_yaw_deg=tt.get("yaw_offset_deg"),
                            object_base_offset_px=reg.get("object_base_offset_px"), plate=tj.get("plate", {}).get("method"),
                            source=str(Path(a.twin) / "overlay.mp4"), created_unix=tj.get("created_unix"))
        subprocess.run(["cp", str(Path(a.twin) / "overlay.mp4"), str(out / "twin_overlay.mp4")], check=True, timeout=60)
    (out / "data.json").write_text(json.dumps(data, indent=1, default=str))
    print("ok", {k: len(v.get("poly", [])) for k, v in masks["objects"].items()})


if __name__ == "__main__":
    main()
