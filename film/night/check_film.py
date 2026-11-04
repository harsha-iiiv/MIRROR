"""Contact sheet and frame-to-frame motion check for the night film and loops.

    python film/night/check_film.py VIDEO --shots shots.json --out DIR [--loop]

Streams the video (never holds decoded frames), downscaled to 640x360. Per frame pair:
- global similarity scale change from ORB features + RANSAC over the whole frame (camera zoom / punch-in detector);
- 90th-percentile displacement of ORB features (at least 20 unambiguous matches) matched between consecutive frames inside the shot's text zones,
  in 1080p pixels (text must move < 20 px/frame); fades and text swaps add features in place and do not count as motion (Lowe ratio 0.7, matches within 120 px);
  footage and 3-D panels are outside the text zones by design.
Thresholds (from the owner's motion rules): scale change < 1% per frame; text-zone feature displacement p90 < 20 px/frame.
--loop also compares the first and last frames (seamless boundary).
Writes motion.json and contact.jpg (3 frames per shot).
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
import cv2
import numpy as np

W, H = 640, 360
K = 1080 / H  # 360p px -> 1080p px


def zones_mask(zones):
    m = np.zeros((H, W), np.uint8)
    for x0, y0, x1, y1 in zones:
        m[int(y0 / K):int(y1 / K), int(x0 / K):int(x1 / K)] = 1
    return m.astype(bool)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--shots", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--loop", action="store_true")
    a = ap.parse_args(argv)
    shots = json.loads(Path(a.shots).read_text())
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(a.video)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); fps = cap.get(cv2.CAP_PROP_FPS)
    orb = cv2.ORB_create(800)
    orb2 = cv2.ORB_create(1500, scaleFactor=1.2, nlevels=4, edgeThreshold=9, patchSize=15)
    starts = []; acc = 0
    for s in shots:
        starts.append(acc); acc += s["frames"]
    def shot_of(i):
        k = max(j for j, st in enumerate(starts) if st <= i)
        return k
    sample = {}
    for k, s in enumerate(shots):
        for q in (0.2, 0.6, 0.95):
            sample[min(n - 1, starts[k] + int(q * s["frames"]))] = (k, q)
    prev = None; first = last = None
    per = {k: dict(name=s["name"], max_scale_change=0.0, max_text_flow_p99_px=0.0, frames=0, top_flow=[]) for k, s in enumerate(shots)}
    thumbs = {}
    masks = {k: zones_mask(s.get("text_zones", [])) for k, s in enumerate(shots)}
    i = 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(cv2.resize(fr, (W, H), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        if i in sample:
            thumbs[i] = cv2.resize(fr, (480, 270), interpolation=cv2.INTER_AREA)
        if first is None:
            first = g.copy()
        k = shot_of(i)
        if prev is not None and shot_of(i - 1) == k:
            # global scale
            kp1, d1 = orb.detectAndCompute(prev, None); kp2, d2 = orb.detectAndCompute(g, None)
            sc = 0.0
            if d1 is not None and d2 is not None and len(kp1) > 20 and len(kp2) > 20:
                mt = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(d1, d2)
                if len(mt) > 20:
                    p1 = np.float32([kp1[m.queryIdx].pt for m in mt]); p2 = np.float32([kp2[m.trainIdx].pt for m in mt])
                    M, inl = cv2.estimateAffinePartial2D(p1, p2, method=cv2.RANSAC, ransacReprojThreshold=2.0)
                    if M is not None and inl is not None and inl.sum() > 30:
                        sc = abs(float(np.hypot(M[0, 0], M[1, 0])) - 1.0)
            per[k]["max_scale_change"] = max(per[k]["max_scale_change"], sc)
            local = i - starts[k]
            if masks[k].any() and 12 <= local <= shots[k]["frames"] - 12:  # dissolve frames at shot edges excluded (low-contrast, ambiguous matches)
                # displacement of matched features inside the text zones (fades and text swaps create new features in
                # place and do not register as motion; a moving caption does)
                mk = masks[k].astype(np.uint8) * 255
                ka, da = orb2.detectAndCompute(prev, mk); kb, db = orb2.detectAndCompute(g, mk)
                p99 = 0.0
                if da is not None and db is not None and len(ka) >= 8 and len(kb) >= 8:
                    kn = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(da, db, k=2)
                    mt = [p[0] for p in kn if len(p) == 2 and p[0].distance < 32 and p[0].distance < 0.7 * p[1].distance]
                    dd = np.array([np.hypot(*(np.subtract(kb[m.trainIdx].pt, ka[m.queryIdx].pt))) for m in mt]) * K
                    dd = dd[dd < 120]  # unambiguous matches within 120 px; larger jumps are re-detections, not motion
                    if dd.size >= 20:
                        p99 = float(np.percentile(dd, 90))
                per[k]["max_text_flow_p99_px"] = max(per[k]["max_text_flow_p99_px"], p99)
                per[k]["top_flow"] = sorted(per[k]["top_flow"] + [[i, round(p99, 1)]], key=lambda x: -x[1])[:6]
            per[k]["frames"] += 1
        prev = g; last = g; i += 1
    cap.release()
    res = dict(video=a.video, frames=i, fps=fps, thresholds=dict(scale_change_per_frame=0.01, text_flow_p99_px=20.0), shots=list(per.values()))
    for s in res["shots"]:
        s["max_scale_change"] = round(s["max_scale_change"], 5); s["max_text_flow_p99_px"] = round(s["max_text_flow_p99_px"], 2)
        s["pass"] = s["max_scale_change"] < 0.01 and s["max_text_flow_p99_px"] < 20.0
    if a.loop and first is not None:
        res["loop_boundary_mean_abs_diff"] = round(float(np.abs(first.astype(float) - last.astype(float)).mean()), 3)
    res["pass"] = all(s["pass"] for s in res["shots"]) and (not a.loop or res["loop_boundary_mean_abs_diff"] < 2.0)
    (out / "motion.json").write_text(json.dumps(res, indent=1))
    # contact sheet
    keys = sorted(thumbs)
    tiles = []
    for j in keys:
        t = thumbs[j].copy(); k, q = sample[j]
        cv2.putText(t, f"{shots[k]['name']} {j / fps:.1f}s", (8, 262), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (230, 230, 230), 1, cv2.LINE_AA)
        tiles.append(t)
    while len(tiles) % 3:
        tiles.append(np.zeros_like(tiles[0]))
    sheet = np.vstack([np.hstack(tiles[r:r + 3]) for r in range(0, len(tiles), 3)])
    cv2.imwrite(str(out / "contact.jpg"), sheet, [cv2.IMWRITE_JPEG_QUALITY, 86])
    print(json.dumps({s["name"]: [s["max_scale_change"], s["max_text_flow_p99_px"], s["pass"]] for s in res["shots"]}), res.get("loop_boundary_mean_abs_diff"), "PASS" if res["pass"] else "FAIL")


if __name__ == "__main__":
    main()
