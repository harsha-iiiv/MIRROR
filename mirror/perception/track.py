"""Video tracking with Meta models.

1. Grounding (muse-spark-1.3): on the first frame of each camera, Muse Spark
   reports a box for each named thing on the documented normalized 0-1000
   grid. A first call on the full frame locates the table; a second call on
   a near-square, upscaled crop around the table gives the boxes used below
   (on the full 16:9 frame the boxes of small objects were off by about 25
   pixels; on the crop they are within a few pixels). The second call also
   counts the small balls visible in the cup.
2. Tracking (sam-3.1): one video request per camera with box prompts (one
   object id per named thing) on frame 0; SAM 3.1 segments each object and
   follows it through the clip.

Concept (noun phrase) prompts alone were tried first on development frames:
on these renders SAM 3.1 found the blue cube, the table (top view) and the
wall (side view) but returned nothing for the cup, gripper and tray, hence
the grounding step. Both steps go through mirror.meta (cassettes, usage log).
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from mirror.perception.masks import rle_encode
from mirror.perception.reconstruct import Tracks

# label -> name given to the grounding model (plain visual descriptions)
DEFAULT_OBJECTS = (("cup_0", "the white cup"), ("block_0", "the blue cube"), ("marker_0", "the red cylinder"),
                   ("tray_0", "the dark grey tray"), ("wall_0", "the wall"), ("gripper", "the robot gripper"),
                   ("table", "the wooden table"))
GROUNDING_MODEL = "muse-spark-1.3"
STATIC_PREFIXES = ("table", "tray", "wall", "box")   # (table~inner, table~points: calibration hypotheses)   # scene parts that do not move: one image request
BOX_PAD_PX = 6
RESEED_GAP_S = 2.0          # an object missing this long is re-seeded (at the frame the gap reaches 2 s)
RESEED_MAX_PER_CAMERA = 2   # grounding attempts per camera per demonstration


def _schema(with_count: bool):
    box = {"type": "object", "properties": {k: {"type": "integer"} for k in ("x_min", "y_min", "x_max", "y_max")},
           "required": ["x_min", "y_min", "x_max", "y_max"], "additionalProperties": False}
    props = {"results": {"type": "array", "items": {"type": "object", "properties": {
        "object_name": {"type": "string"}, "bbox": box}, "required": ["object_name", "bbox"],
        "additionalProperties": False}}}
    req = ["results"]
    if with_count:
        props["small_yellow_balls_in_cup"] = {"type": "object", "properties": {
            "points": {"type": "array", "items": {"type": "object", "properties": {
                "x": {"type": "integer"}, "y": {"type": "integer"}}, "required": ["x", "y"],
                "additionalProperties": False}}, "count": {"type": "integer"}},
            "required": ["points", "count"], "additionalProperties": False}
        req.append("small_yellow_balls_in_cup")
    return {"type": "object", "properties": props, "required": req, "additionalProperties": False}


def _prompt(names, with_count):
    p = ("You are an object grounding expert. Provide the bounding box coordinates of the objects in the image: "
         + ", ".join(f'"{n}"' for n in names)
         + ". Ensure the objects accurately match the request and do not miss any objects. For each object, "
           'answer in the format {"object_name": "<name>", "bbox": {"x_min": <int>, "y_min": <int>, '
           '"x_max": <int>, "y_max": <int>}}. The coordinates should be in the 0-1000 range. If you cannot '
           "find an object, omit it from the results.")
    if with_count:
        p += (" Also point to every small yellow ball visible inside the white cup and count them "
              "(points in the same 0-1000 range); use count 0 if none are visible.")
    return p


def _match(results, names):
    out = {}
    for r in results:
        nm = r["object_name"].strip().lower()
        for n in names:
            if nm == n.lower() or nm.replace("the ", "") == n.lower().replace("the ", ""):
                out[n] = r["bbox"]
    return out


def ground_frame(img: np.ndarray, objects=DEFAULT_OBJECTS, *, client=None, work: Optional[Path] = None,
                 tag: str = "frame") -> dict:
    """Boxes (full-frame pixels, half-open) per label for one image, plus the
    visible ball count. Two Muse Spark calls: full frame (table), then crop."""
    import io
    from PIL import Image
    from mirror.meta import MetaClient, image_part, text_part
    c = client or MetaClient.from_env()
    H, W = img.shape[:2]
    names = [n for _, n in objects]
    label_of = {n: l for l, n in objects}
    work = Path(work or "/tmp")
    work.mkdir(parents=True, exist_ok=True)
    full = work / f"{tag}_full.png"
    Image.fromarray(img).save(full)
    r1 = c.responses(GROUNDING_MODEL, [text_part(_prompt(names, False)), image_part(str(full))],
                     schema=_schema(False), schema_name="grounding")
    b1 = _match(r1.json()["results"], names)
    table_name = next((n for l, n in objects if l == "table"), None)
    if table_name in b1:
        tb = b1[table_name]
        x0, y0, x1, y1 = tb["x_min"] * W / 1000, tb["y_min"] * H / 1000, tb["x_max"] * W / 1000, tb["y_max"] * H / 1000
    else:
        x0, y0, x1, y1 = 0, 0, W, H
    mx, my = 0.06 * (x1 - x0), 0.06 * (y1 - y0)
    x0, y0 = int(max(0, x0 - mx)), int(max(0, y0 - my))
    x1, y1 = int(min(W, x1 + mx)), int(min(H, y1 + my))
    # make the crop closer to square by growing the short side within the frame
    cw, ch = x1 - x0, y1 - y0
    if cw > ch:
        grow = min(cw - ch, H - ch)
        y0 = int(max(0, y0 - grow / 2)); y1 = int(min(H, y0 + ch + grow))
    else:
        grow = min(ch - cw, W - cw)
        x0 = int(max(0, x0 - grow / 2)); x1 = int(min(W, x0 + cw + grow))
    cw, ch = x1 - x0, y1 - y0
    s = min(2.5, max(1.0, 1200 / max(cw, ch)))
    crop = Image.fromarray(img).crop((x0, y0, x1, y1)).resize((int(cw * s), int(ch * s)), Image.LANCZOS)
    cp = work / f"{tag}_crop.png"
    crop.save(cp)
    r2 = c.responses(GROUNDING_MODEL, [text_part(_prompt(names, True)), image_part(str(cp))],
                     schema=_schema(True), schema_name="grounding")
    js = r2.json()
    b2 = _match(js["results"], names)
    boxes = {}
    for n, b in b2.items():
        boxes[label_of[n]] = [int(round(x0 + b["x_min"] * cw / 1000)), int(round(y0 + b["y_min"] * ch / 1000)),
                              int(round(x0 + b["x_max"] * cw / 1000)), int(round(y0 + b["y_max"] * ch / 1000))]
    balls = js.get("small_yellow_balls_in_cup", {})
    calls = [{"model": r1.model, "latency_s": r1.latency_s, "usage": r1.usage, "cassette": r1.from_cassette},
             {"model": r2.model, "latency_s": r2.latency_s, "usage": r2.usage, "cassette": r2.from_cassette}]
    # third call: one zoomed crop per small object in a single request; small
    # objects' boxes from the table crop were off by up to ~30 px
    small = [l for l in boxes if not l.startswith(STATIC_PREFIXES) and l != "gripper"]
    if small:
        refined, r3 = _refine_small(Image.fromarray(img), boxes, small, label_of_inv={l: n for l, n in objects},
                                    client=c, work=work, tag=tag)
        boxes.update(refined)
        calls.append({"model": r3.model, "latency_s": r3.latency_s, "usage": r3.usage, "cassette": r3.from_cassette})
    return {"boxes": boxes, "ball_count": int(balls.get("count", 0)), "crop": [x0, y0, x1, y1], "scale": s,
            "calls": calls}


def _refine_small(full, boxes, labels, *, label_of_inv, client, work, tag, factor: float = 3.0,
                  min_px: int = 96, zoom_to: int = 512):
    from PIL import Image
    from mirror.meta import image_part, text_part
    W, H = full.size
    parts, crops, lines = [], [], []
    for k, lab in enumerate(labels):
        x0, y0, x1, y1 = boxes[lab]
        cw, ch = max(min_px, (x1 - x0) * factor), max(min_px, (y1 - y0) * factor)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        X0, Y0 = int(max(0, cx - cw / 2)), int(max(0, cy - ch / 2))
        X1, Y1 = int(min(W, cx + cw / 2)), int(min(H, cy + ch / 2))
        sc = max(1.0, zoom_to / max(X1 - X0, Y1 - Y0))
        p = Path(work) / f"{tag}_zoom{k}.png"
        full.crop((X0, Y0, X1, Y1)).resize((int((X1 - X0) * sc), int((Y1 - Y0) * sc)), Image.LANCZOS).save(p)
        parts.append(image_part(str(p)))
        crops.append((lab, X0, Y0, X1, Y1))
        lines.append(f'image {k}: "{label_of_inv[lab]}"')
    schema = {"type": "object", "properties": {"boxes": {"type": "array", "items": {"type": "object", "properties": {
        "image_index": {"type": "integer"}, "found": {"type": "boolean"},
        "bbox": {"type": "object", "properties": {k: {"type": "integer"} for k in ("x_min", "y_min", "x_max", "y_max")},
                 "required": ["x_min", "y_min", "x_max", "y_max"], "additionalProperties": False}},
        "required": ["image_index", "found", "bbox"], "additionalProperties": False}}},
        "required": ["boxes"], "additionalProperties": False}
    prompt = (f"You are an object grounding expert. You are given {len(crops)} images. For each image, provide the "
              "bounding box of the named object in that image: " + "; ".join(lines)
              + ". Coordinates are in the 0-1000 range of that image, (0,0) top-left. Set found=false if the "
                "object is not visible in that image.")
    r = client.responses(GROUNDING_MODEL, [text_part(prompt)] + parts, schema=schema, schema_name="boxes")
    out = {}
    for bx in r.json().get("boxes", []):
        k = bx.get("image_index")
        if not isinstance(k, int) or not (0 <= k < len(crops)) or not bx.get("found"):
            continue
        lab, X0, Y0, X1, Y1 = crops[k]
        b = bx["bbox"]
        out[lab] = [int(round(X0 + b["x_min"] * (X1 - X0) / 1000)), int(round(Y0 + b["y_min"] * (Y1 - Y0) / 1000)),
                    int(round(X0 + b["x_max"] * (X1 - X0) / 1000)), int(round(Y0 + b["y_max"] * (Y1 - Y0) / 1000))]
    return out, r


def sam_track_boxes(video, boxes: dict, size, *, client=None, pad: int = BOX_PAD_PX, image: bool = False):
    """One SAM 3.1 request with box prompts on frame 0 of a video (or on one
    image). Returns (label -> frame -> RLE, SamResult)."""
    from meta_sam_parser import FramePrompt, PromptBox, PromptObject, PromptPoint, build_object_prompt
    from mirror.meta import MetaClient
    c = client or MetaClient.from_env()
    W, H = size
    labels = list(boxes)
    objs = []
    extra_ids = {}
    for k, lab in enumerate(labels, start=1):
        x0, y0, x1, y1 = boxes[lab]
        if lab == "table":
            # A large surface seen at a grazing angle, whose grounding box can be off: three
            # prompt hypotheses in the same request (box, inner box, interior points away from
            # other boxes); calibration keeps the mask that best fits a table slab.
            others = [b for l, b in boxes.items() if l != lab]
            cand = [(x0 + (x1 - x0) * fx, y0 + (y1 - y0) * fy) for fy in (0.45, 0.6, 0.75)
                    for fx in (0.15, 0.3, 0.5, 0.7, 0.85)]
            pts = [(int(px), int(py)) for px, py in cand
                   if not any(b[0] - 10 <= px <= b[2] + 10 and b[1] - 10 <= py <= b[3] + 10 for b in others)][:5]
            hyp = [("table", FramePrompt(frame=0, box=PromptBox(max(0, x0), max(0, y0), min(W, x1), min(H, y1))))]
            ix, iy = int(0.15 * (x1 - x0)), int(0.15 * (y1 - y0))
            hyp.append(("table~inner", FramePrompt(frame=0, box=PromptBox(x0 + ix, y0 + iy, x1 - ix, y1 - iy))))
            if pts:
                hyp.append(("table~points", FramePrompt(frame=0, points=[PromptPoint(x, y) for x, y in pts])))
            for name, fp in hyp:
                extra_ids[name] = 100 + len(extra_ids)
                objs.append(PromptObject(id=extra_ids[name], prompts=[fp]))
            continue
        bx = PromptBox(max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad))
        if bx.right > bx.left and bx.bottom > bx.top:
            objs.append(PromptObject(id=k, prompts=[FramePrompt(frame=0, box=bx)]))
    res = c.sam_segment(video, build_object_prompt(size=(W, H), objects=objs), video=not image)
    sw, sh = res.width or W, res.height or H
    by_id = {k: lab for k, lab in enumerate(labels, start=1) if lab != "table"}
    by_id.update({v: k for k, v in extra_ids.items()})
    out = {lab: {} for lab in by_id.values()}
    for f in res.frames:
        for o in f.objects:
            lab = by_id.get(o.object_id)
            if lab is None:
                continue
            m = o.full_mask(sw, sh).astype(bool)
            if (sw, sh) != (W, H):
                from PIL import Image
                m = np.asarray(Image.fromarray(m).resize((W, H), Image.NEAREST))
            out[lab][f.index] = rle_encode(m)
    return out, res


def track_videos(videos: dict, *, fps: float, n_frames: Optional[int] = None, objects=DEFAULT_OBJECTS,
                 client=None, work: Optional[Path] = None) -> Tracks:
    """Track the named things in each camera's video (camera -> mp4 path)."""
    import imageio.v2 as imageio
    from mirror.meta import MetaClient
    c = client or MetaClient.from_env()
    masks, info, size = {}, {"grounding": {}, "sam": {}}, {}
    for cam, vid in videos.items():
        rd = imageio.get_reader(str(vid))
        frame0 = rd.get_data(0)
        if n_frames is None:
            n_frames = rd.count_frames()
        H, W = frame0.shape[:2]
        size[cam] = (W, H)
        g = ground_frame(frame0, objects, client=c, work=Path(work or Path(vid).parent / "perception_work"), tag=cam)
        info["grounding"][cam] = g
        moving = {l: b for l, b in g["boxes"].items() if not l.startswith(STATIC_PREFIXES)}
        still = {l: b for l, b in g["boxes"].items() if l.startswith(STATIC_PREFIXES)}
        masks[cam] = {}
        if moving:
            tr, res = sam_track_boxes(str(vid), moving, (W, H), client=c)
            masks[cam].update(tr)
            info["sam"][cam] = {"latency_s": res.latency_s, "frames": len(res.frames), "objects": res.object_ids,
                                "cassette": res.from_cassette, "width": res.width, "height": res.height}
        if still:
            img = Path(work or Path(vid).parent / "perception_work") / f"{cam}_full.png"
            tr, res = sam_track_boxes(str(img), still, (W, H), client=c, image=True)
            masks[cam].update(tr)
            info["sam"][cam + "_static"] = {"latency_s": res.latency_s, "objects": res.object_ids,
                                            "cassette": res.from_cassette}
    info["reseed"] = reseed_lost(videos, masks, info, fps=fps, n_frames=int(n_frames), objects=objects, client=c,
                                 work=Path(work or Path(next(iter(videos.values()))).parent / "perception_work"))
    static = {l for d in masks.values() for l in d if l.startswith(STATIC_PREFIXES)}
    t = Tracks(fps=fps, n_frames=int(n_frames), size=size, masks=masks, static=static,
               source="muse-spark-1.3 grounding + sam-3.1 box-prompt tracking (static scene parts: frame 0)")
    t.notes.append(json.dumps(info, default=str))
    if "top" in info["grounding"]:
        t.extra = {"ball_count": info["grounding"]["top"]["ball_count"]}
    return t


def _objects_from(prompts):
    if not prompts:
        return DEFAULT_OBJECTS
    if isinstance(prompts, dict):
        return tuple(prompts.items())
    return tuple(prompts)


def track(dv, root, *, prompts=None, objects=None, client=None, cams: Optional[Sequence[str]] = None,
          cache: Optional[Path] = None) -> Tracks:
    """Track the named things (``prompts``: label -> description, default
    DEFAULT_OBJECTS) in every camera video of a DemoVideo."""
    root = Path(root)
    cams = list(cams or dv.videos)
    t = track_videos({c: root / dv.videos[c] for c in cams}, fps=dv.fps, n_frames=dv.n_frames,
                     objects=objects or _objects_from(prompts), client=client, work=root / "perception_work")
    if cache:
        save_tracks(t, cache)
    return t


def save_tracks(t: Tracks, path) -> None:
    with gzip.open(path, "wt") as fh:
        json.dump({"fps": t.fps, "n_frames": t.n_frames, "size": t.size, "source": t.source, "notes": t.notes,
                   "extra": getattr(t, "extra", {}), "static": sorted(t.static),
                   "masks": {c: {l: {str(i): m for i, m in fr.items()} for l, fr in d.items()}
                             for c, d in t.masks.items()}}, fh, separators=(",", ":"))


def load_tracks(path) -> Tracks:
    with gzip.open(path, "rt") as fh:
        d = json.load(fh)
    t = Tracks(fps=d["fps"], n_frames=d["n_frames"], size={k: tuple(v) for k, v in d["size"].items()},
               masks={c: {l: {int(i): m for i, m in fr.items()} for l, fr in dd.items()}
                      for c, dd in d["masks"].items()}, source=d["source"], notes=d.get("notes", []),
               static=set(d.get("static", [])))
    t.extra = d.get("extra", {})
    # provenance: these masks are a saved record, whatever the original calls were
    t.loaded_from = str(path)
    return t


def _gaps(frames: set, n: int, min_len: int):
    have = np.zeros(n, bool)
    have[[i for i in frames if 0 <= i < n]] = True
    out, i = [], 0
    while i < n:
        if not have[i]:
            j = i
            while j < n and not have[j]:
                j += 1
            if j - i >= min_len and i > 0:          # a gap after the object was seen
                out.append((i, j))
            i = j
        else:
            i += 1
    return out


def _subclip(video, start: int, out: Path, end: Optional[int] = None, reverse: bool = False) -> Path:
    """Frames start..end (inclusive) of a video, optionally in reverse order."""
    import subprocess
    sel = f"select=gte(n\\,{start})" if end is None else f"select=between(n\\,{start}\\,{end})"
    vf = sel + (",reverse" if reverse else "") + ",setpts=N/FRAME_RATE/TB"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vf", vf, "-vsync", "0",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], check=True, timeout=600,
                   stdin=subprocess.DEVNULL)
    return out


def reseed_lost(videos: dict, masks: dict, info: dict, *, fps: float, n_frames: int, objects, client, work: Path):
    """Re-seed objects that SAM lost for at least RESEED_GAP_S.

    A carried object is usually lost while the gripper hides it, so the seed
    is taken where the object is visible again and SAM tracks backwards over
    the gap (the clip is reversed):
      * the gap ends inside the video (SAM found the object again): the seed
        box is SAM's own box at the first frame after the gap, no grounding call;
      * the gap runs to the end: one Muse Spark grounding call on the last
        frame (table crop, that object only) gives the box, if the object is found.
    One SAM 3.1 video request per re-seed; at most RESEED_MAX_PER_CAMERA per
    camera, gaps ranked by (other camera also missing the object, length).
    New masks fill only frames where the object was missing, and only if the
    seed mask has a plausible size (0.3x to 3x its earlier median area)."""
    import imageio.v2 as imageio
    from PIL import Image
    from mirror.meta import image_part, text_part
    from mirror.perception.masks import mask_bbox, rle_area, rle_decode
    name_of = {l: n for l, n in objects}
    min_len = int(round(RESEED_GAP_S * fps))
    records = []
    for cam, vid in videos.items():
        cands = []
        for lab, fr in masks.get(cam, {}).items():
            if lab.startswith(STATIC_PREFIXES) or lab not in name_of:
                continue
            for a, b in _gaps(set(fr), n_frames, min_len):
                others = [masks[c].get(lab, {}) for c in masks if c != cam]
                mid = (a + b) // 2
                other_missing = all(mid not in o for o in others)
                cands.append(((0 if other_missing else 1), -(b - a), lab, a, b))
        cands.sort()
        for _, _, lab, a, b in cands[:RESEED_MAX_PER_CAMERA]:
            rec = {"camera": cam, "object": lab, "gap": [a, b], "found": False, "merged": 0}
            reader = imageio.get_reader(str(vid))
            if b < n_frames:                      # object seen again at b: SAM's own box there
                seed = b
                box = mask_bbox(rle_decode(masks[cam][lab][b]))
                rec.update(seed_frame=seed, seed_source="sam (reappearance)")
            else:                                 # lost to the end: ground the last frame
                seed = n_frames - 1
                frame = reader.get_data(seed)
                H, W = frame.shape[:2]
                x0, y0, x1, y1 = info["grounding"][cam].get("crop", [0, 0, W, H])
                cw, ch = x1 - x0, y1 - y0
                sc = min(2.5, max(1.0, 1200 / max(cw, ch)))
                p = Path(work) / f"{cam}_reseed_{lab}_{seed}.png"
                Image.fromarray(frame).crop((x0, y0, x1, y1)).resize((int(cw * sc), int(ch * sc)),
                                                                      Image.LANCZOS).save(p)
                r = client.responses(GROUNDING_MODEL, [text_part(_prompt([name_of[lab]], False)), image_part(str(p))],
                                     schema=_schema(False), schema_name="grounding")
                rec.update(seed_frame=seed, seed_source="muse-spark grounding (last frame)",
                           grounding={"latency_s": r.latency_s, "cassette": r.from_cassette, "usage": r.usage})
                hit = _match(r.json().get("results", []), [name_of[lab]]).get(name_of[lab])
                box = None if not hit else [int(round(x0 + hit["x_min"] * cw / 1000)),
                                            int(round(y0 + hit["y_min"] * ch / 1000)),
                                            int(round(x0 + hit["x_max"] * cw / 1000)),
                                            int(round(y0 + hit["y_max"] * ch / 1000))]
            if not box:
                records.append(rec)
                continue
            rec["found"], rec["box"] = True, box
            H, W = reader.get_data(0).shape[:2]
            clip = _subclip(vid, a, Path(work) / f"{cam}_reseed_{lab}_{a}_{seed}.mp4", end=seed, reverse=True)
            tr, res = sam_track_boxes(str(clip), {lab: box}, (W, H), client=client)
            rec["sam"] = {"latency_s": res.latency_s, "frames": len(res.frames), "cassette": res.from_cassette}
            new = tr.get(lab, {})
            before = [rle_area(m) for i, m in masks[cam][lab].items() if i < a]
            ref = float(np.median(before)) if before else None
            first = new.get(0)
            ok = first is not None and (ref is None or 0.3 * ref <= rle_area(first) <= 3.0 * ref)
            rec["plausible"] = bool(ok)
            if ok:
                for k, m in new.items():
                    i = seed - k
                    if a <= i < n_frames and i not in masks[cam][lab]:
                        masks[cam][lab][i] = m
                        rec["merged"] += 1
            records.append(rec)
    return records
