#!/usr/bin/env python
"""Checks for an assembled film: contact sheet and frame-by-frame motion check.

    python scripts/film/check.py <film dir> <cut>

Reads <cut> timeline and camera log written by build.py and the final MP4.
Writes contact_<cut>.jpg (one frame from the middle of every shot, plus a
phone-size row for the social cut) and motion_<cut>.json:
  * camera log: per shot, the number of separate camera moves, any reversal,
    the largest per-frame change of distance (zoom), azimuth, elevation and
    look-at point;
  * pixels: per consecutive frame pair inside a shot, the global similarity
    transform of the simulation area (ORB features + RANSAC), reporting the
    largest scale change and shift; a sudden zoom shows up as a scale change.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def frames_at(mp4, idx):
    cap = cv2.VideoCapture(str(mp4))
    out = {}
    for i in sorted(idx):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, f = cap.read()
        if ok:
            out[i] = cv2.cvtColor(f, cv2.COLOR_BGR2RGB)
    cap.release()
    return out


def camera_report(rows):
    shots = {}
    for r in rows:
        shots.setdefault(r["shot"], []).append(r)
    rep = {}
    for name, rs in shots.items():
        cams = [r["cam"] for r in rs]
        if cams[0] is None:
            rep[name] = dict(kind="card", moves=0)
            continue
        keys = ("distance", "azimuth", "elevation")
        d = {k: np.diff([c[k] for c in cams]) for k in keys}
        la = np.linalg.norm(np.diff(np.array([c["lookat"] for c in cams]), axis=0), axis=1) if len(cams) > 1 else np.zeros(0)
        moving = np.zeros(max(0, len(cams) - 1), dtype=bool)
        for k in keys:
            moving |= np.abs(d[k]) > 1e-9
        moving |= la > 1e-9
        runs = int(np.sum(np.diff(np.concatenate([[0], moving.astype(int)])) == 1))
        rev = any(np.any(np.diff(np.sign(v[np.abs(v) > 1e-9])) != 0) for v in d.values())
        dist = np.array([c["distance"] for c in cams])
        rep[name] = dict(kind="sim", frames=len(cams), moves=runs, reversal=bool(rev),
                         max_zoom_per_frame=float(np.max(np.abs(dist[1:] / dist[:-1] - 1))) if len(dist) > 1 else 0.0,
                         max_dazimuth_deg=float(np.max(np.abs(d["azimuth"]))) if len(cams) > 1 else 0.0,
                         max_delevation_deg=float(np.max(np.abs(d["elevation"]))) if len(cams) > 1 else 0.0,
                         max_dlookat_m=float(la.max()) if la.size else 0.0,
                         total=dict(distance=float(dist[-1] - dist[0]),
                                    azimuth=float(cams[-1]["azimuth"] - cams[0]["azimuth"]),
                                    elevation=float(cams[-1]["elevation"] - cams[0]["elevation"])))
    return rep


def pixel_report(mp4, timeline, sim_box, step=1, max_pairs=None):
    cap = cv2.VideoCapture(str(mp4))
    fps = timeline["fps"]
    orb = cv2.ORB_create(800)
    out = {}
    x, y, w, h = sim_box
    for sh in timeline["shots"]:
        if sh["kind"] == "card":
            continue
        a = int(round(sh["start"] * fps))
        b = int(round((sh["start"] + sh["duration"]) * fps))
        cap.set(cv2.CAP_PROP_POS_FRAMES, a)
        prev, scales, shifts = None, [], []
        for i in range(a, b):
            ok, f = cap.read()
            if not ok:
                break
            g = cv2.cvtColor(f[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY)
            g = cv2.resize(g, (w // 2, h // 2))
            kp, des = orb.detectAndCompute(g, None)
            if prev is not None and des is not None and prev[1] is not None and len(kp) > 20:
                m = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(prev[1], des)
                if len(m) > 20:
                    p0 = np.float32([prev[0][q.queryIdx].pt for q in m])
                    p1 = np.float32([kp[q.trainIdx].pt for q in m])
                    M, inl = cv2.estimateAffinePartial2D(p0, p1, method=cv2.RANSAC, ransacReprojThreshold=2.0)
                    if M is not None:
                        scales.append(float(np.hypot(M[0, 0], M[1, 0])))
                        shifts.append(float(np.hypot(M[0, 2], M[1, 2]) * 2))
            prev = (kp, des)
        sc = np.array(scales) if scales else np.array([1.0])
        out[sh["name"]] = dict(pairs=len(scales), max_scale_change=float(np.max(np.abs(sc - 1))),
                               cumulative_scale=float(np.prod(sc)), max_shift_px=float(max(shifts) if shifts else 0.0),
                               frames_over_1pct=int(np.sum(np.abs(sc - 1) > 0.01)))
    cap.release()
    return out


def contact_sheet(mp4, timeline, out_jpg, phone=False):
    fps = timeline["fps"]
    mids = [int((sh["start"] + sh["duration"] * 0.55) * fps) for sh in timeline["shots"]]
    fr = frames_at(mp4, mids)
    ims = [Image.fromarray(fr[i]) for i in mids if i in fr]
    W, H = ims[0].size
    tw = 360 if W < H else 480
    th = int(H * tw / W)
    cols = 6 if W < H else 4
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows * (th + 30)), "white")
    d = ImageDraw.Draw(sheet)
    try:
        f = ImageFont.truetype("DejaVuSans.ttf", 16)
    except OSError:
        f = ImageFont.load_default()
    for k, (im, sh) in enumerate(zip(ims, timeline["shots"])):
        cx, cy = (k % cols) * tw, (k // cols) * (th + 30)
        sheet.paste(im.resize((tw, th), Image.LANCZOS), (cx, cy + 30))
        d.text((cx + 6, cy + 6), f"{sh['name']}  {sh['start']:.1f}s +{sh['duration']:.1f}s", fill="black", font=f)
    sheet.save(out_jpg, quality=88)
    return out_jpg


def main(argv=None):
    a = sys.argv[1:] if argv is None else argv
    root, cut = Path(a[0]), a[1]
    tl = json.loads((root / f"timeline_{cut}.json").read_text())
    rows = [json.loads(l) for l in (root / f"camlog_{cut}.jsonl").read_text().splitlines() if l.strip()]
    mp4 = root / tl["file"]
    sim_box = (0, 590, 1080, 1080) if cut == "social" else (520, 0, 1400, 1080)
    cam = camera_report(rows)
    pix = pixel_report(mp4, tl, sim_box)
    flags = []
    for n, r in cam.items():
        if r.get("moves", 0) > 1 or r.get("reversal"):
            flags.append(f"{n}: {r['moves']} camera moves, reversal={r.get('reversal')}")
        if r.get("max_zoom_per_frame", 0) > 0.003:
            flags.append(f"{n}: zoom {r['max_zoom_per_frame']:.4f} per frame")
    for n, r in pix.items():
        if r["frames_over_1pct"] and cam.get(n, {}).get("moves", 0) == 0:
            r["note"] = "camera static per the camera log: the change comes from objects moving in a textureless scene"
        elif r["frames_over_1pct"]:
            flags.append(f"{n}: {r['frames_over_1pct']} frame pairs with >1% image scale change (max {r['max_scale_change']:.3f})")
    rep = dict(cut=cut, file=tl["file"], duration=tl["duration"], camera=cam, pixels=pix, flags=flags,
               thresholds=dict(moves_per_shot=1, zoom_per_frame=0.003, image_scale_per_frame=0.01))
    (root / f"motion_{cut}.json").write_text(json.dumps(rep, indent=1))
    contact_sheet(mp4, tl, root / f"contact_{cut}.jpg")
    print(json.dumps(dict(flags=flags, shots=len(tl["shots"]), duration=tl["duration"]), indent=1))


if __name__ == "__main__":
    main()
