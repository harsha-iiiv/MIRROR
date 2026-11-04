"""Export real D1 outputs as Remotion assets for the capture film (no model calls, no fabrication).

Writes into OUT (Remotion public dir):
  phone.mp4            IMG_0815 rotated to portrait, 720x1280, H.264 (the person's own footage, untouched otherwise)
  masks.json           SAM 3.1 mask outlines per frame (540x960 image coords), simplified polygons, from the
                       recorded tracks (tracks_sam_540x960.json.gz); divider is one static mask (its video track was discarded)
  run_old.json / run_new.json   recorded MuJoCo rollouts: static geoms + per-frame body poses (30 Hz)
  data.json            timeline events, gate result, spec, report rule rows, correction records, labels, provenance

    python film/take/export_assets.py --capture DIR --phase2 DIR --corr DIR --take IMG_0815.MOV --out PUBLIC
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
    try:
        res["seed_t"] = json.loads(tr["notes"][0])["grounding"]["top"]["seed_t"]
    except Exception:
        pass
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


def primary_candidate(C):
    """The film's correction: the first applied keep_clearance correction whose old run re-judges as violated, else the first applied one."""
    ok = [c for c in C["candidates"] if c["outcome"] == "apply"]
    return next((c for c in ok if c["old_rechecked"]["status"] == "constraint_violated"
                 and any(x["op"] == "add_constraint" and (x.get("value") or {}).get("type") == "keep_clearance" for x in c["ops"])), None) \
        or next((c for c in ok if c["old_rechecked"]["status"] == "constraint_violated"), None) or ok[0]


def film_numbers(C, run_old, run_new, scene):
    """Numbers the film quotes, copied from the correction record and the recorded rollouts (nothing computed by hand)."""
    mc = primary_candidate(C)
    add = next(x for x in mc["ops"] if x["op"] == "add_constraint")["value"]
    sel = add["applies_to"]["kind"]
    obj = next((o for o in run_old["poses"] if o.startswith(sel + "_") and "/" not in o), "cup_0")

    def path(run, oid="cup_0"):
        idx = [i for i, ph in enumerate(run["phase"]) if ph in (f"lift:{oid}", f"transport:{oid}", f"place:{oid}")]
        return idx, [[run["poses"][oid][i][0], run["poses"][oid][i][1]] for i in idx]

    def nearest(run, t):
        return int(min(range(run["n"]), key=lambda i: abs(run["t"][i] - t)))

    io, po = path(run_old, obj); inw, pn = path(run_new, obj)
    mo, mn = C["old"]["measures"][obj], mc["new"]["measures"][obj]
    rule = next(r for r in mc["old_rechecked"]["rules"] if r["rule_id"] == add["id"])
    return dict(
        primary=mc["id"], applies_obj=obj, applies_kind=sel, rule_type=add["type"], phase=add["phase"],
        correction_text=mc["text"], rule_id=add["id"], min_m=(add.get("params") or {}).get("min_m", 0.06),
        old_status=C["old"]["status"], old_rechecked_status=mc["old_rechecked"]["status"], new_status=mc["new"]["status"],
        old_detail=rule["detail"],
        old_clear_m=mo["min_transport_clearance_m"], old_clear_t=mo["at_t_s"], old_clear_frame=nearest(run_old, mo["at_t_s"]),
        new_clear_m=mn["min_transport_clearance_m"], new_clear_t=mn["at_t_s"], new_clear_frame=nearest(run_new, mn["at_t_s"]),
        old_path_frames=[io[0], io[-1]], new_path_frames=[inw[0], inw[-1]], old_path=po, new_path=pn,
        unchanged={oid: [C["old"]["measures"][oid]["min_transport_clearance_m"], mc["new"]["measures"][oid]["min_transport_clearance_m"]]
                   for oid in C["old"]["measures"] if oid != obj},
        tilt=[mo["max_transport_tilt_deg"], mn["max_transport_tilt_deg"]],
        beads_old=run_old["beads"], beads_new=run_new["beads"],
        exec_s=[C["old"]["execution_time_s"], mc["new"]["execution_time_s"]],
        rows=dict(old=C["old"]["rules"], rechecked=mc["old_rechecked"]["rules"], new=mc["new"]["rules"]),
        others=[dict(id=c["id"], text=c["text"], outcome=c["outcome"],
                     detail=(c.get("question") or c.get("reason") or
                             (c["old_rechecked"]["status"] + " / " + c["new"]["status"] if c["outcome"] == "apply" else "")),
                     routes_same=(c["new"]["routes"] == C["old"]["routes"]) if c["outcome"] == "apply" else None)
                for c in C["candidates"] if c["id"] != mc["id"]],
    )


def norm_events(timeline):
    """Both timeline shapes (D1 phase 1 and mirror.capture.real_run) -> [{object, from_s, to_s, disp_px, in_tray, carry}]."""
    out = []
    for e in timeline.get("events", []):
        a = e.get("moved_from_s", e.get("from_s")); b = e.get("moved_to_s", e.get("to_s"))
        disp = e.get("image_displacement_px_540", e.get("displacement_px", 0.0))
        out.append(dict(object=e["object"], from_s=a, to_s=b, disp_px=disp,
                        in_tray=bool(e.get("ends_inside_tray_outline_2d", e.get("in_tray", False))), carry=disp >= 100))
    return out


def segments(events, n_frames, fps):
    """Source-time windows for the title and watch shots (11 s watch, 4 s title), from the carries themselves."""
    car = sorted([e for e in events if e["carry"]], key=lambda e: e["from_s"])
    dur = n_frames / fps
    if not car:
        return dict(title_src0=0.0, title_rate=1.0, watch_src0=0.0, watch_rate=round(max(1.0, dur / 11.0), 2))
    w0 = max(0.0, car[0]["from_s"] - 1.8); w1 = min(dur, car[-1]["to_s"] + 0.8)
    t0 = max(0.0, w0 - 10.5)
    return dict(title_src0=round(t0, 2), title_rate=round(max(0.5, (w0 - t0) / 4.0), 2), watch_src0=round(w0, 2),
                watch_rate=round(max(0.5, (w1 - w0) / 11.0), 2))


def object_names(session_path):
    """proxy kind / object id -> the person's object name, from the capture's confirmed mappings."""
    names = {"cup": "mug", "block": "box", "marker": "marker", "wall": "divider"}
    if session_path and Path(session_path).exists():
        s = json.loads(Path(session_path).read_text())
        for m in s.get("mappings", []):
            if m.get("proxy") and m.get("track_label"):
                names[m["proxy"]] = m["track_label"].replace("_", " ")
    return names


def build_copy(d, names, placement_label, gate_note=None):
    """Every sentence the film shows that depends on the take, computed from the records."""
    F = d["film"]; spec = d["spec"]; g = d["gate"]
    nm = lambda kind: names.get(kind, kind)
    oid = lambda o: nm(o.split("_")[0]) if o else o
    blocking = g.get("blocking_items") if isinstance(g.get("blocking"), int) or not g.get("blocking") else g.get("blocking")
    blocking = [b if isinstance(b, dict) else {"code": str(b), "message": ""} for b in (blocking or [])]
    passed = bool(g.get("passed"))
    msg = (blocking[0].get("message", "") if blocking else "")
    msg = msg.split(": ", 1)[-1].split(" (")[0]
    gate = dict(passed=passed,
                headline="Yes. The gate passed." if passed else "Not yet. The gate refused.",
                code=(blocking[0].get("code") if blocking else ("gate passed" if passed else "refused")), detail=msg, evidence_kind=g.get("evidence_kind", ""),
                note=gate_note or ("MIRROR may propose the task from this take; it is shown below for confirmation." if passed else
                      "One camera; the flag stays open until a lens clip with the same phone settings. No bypass exists, and none was used."),
                spec_intro=("the task MIRROR proposed from the take" if passed and spec.get("goals") and
                            (spec["goals"][0].get("provenance") or {}).get("source") != "manual" else "the one the person stated"),
                spec_tail=("" if passed else ", not one MIRROR inferred"))
    goals = [nm(x["object"]["kind"]) for x in spec.get("goals", []) if x.get("type") == "inside"]
    chain = []
    for o in spec.get("ordering", []):
        a, b = nm(o["before"]["kind"]), nm(o["after"]["kind"])
        if not chain:
            chain = [a, b]
        elif chain[-1] == a:
            chain.append(b)
    lines = []
    if goals:
        lines.append(["GOALS", (", ".join(goals[:-1]) + " and " + goals[-1] if len(goals) > 1 else goals[0]) + " end inside the tray",
                      " · ".join(x["id"] for x in spec["goals"])])
    if chain:
        lines.append(["ORDER", "  →  ".join(chain), " · ".join(o["id"] for o in spec["ordering"])])
    def ctext(c):
        k = (c.get("applies_to") or {}).get("kind"); p = c.get("params") or {}
        ph = {"transport": "while carried", "final": "once placed", "always": "always"}.get(c.get("phase"), c.get("phase") or "")
        if c["type"] == "upright":
            return f"{nm(k)} within {p.get('max_tilt_deg', 15):g}° of upright {ph}"
        if c["type"] == "forbidden_contact" and "obstacle" in (p.get("with") or []):
            return f"nothing touches the {nm('wall')}"
        if c["type"] == "keep_clearance":
            return f"{nm(k)} ≥ {p.get('min_m', 0) * 100:g} cm from the {nm('wall')} {ph}"
        t = (c.get("description") or c["type"]).rstrip(".")
        for kk, v in names.items():
            t = t.replace(f"the {v} ({kk})", f"the {v}").replace(f"{v} ({kk})", v)
        return t
    cons = list(spec.get("constraints", []))
    merged = []
    for c in cons:  # an object's upright rule while carried and once placed reads as one line
        twin_ = next((m for m in merged if m[0]["type"] == "upright" == c["type"] and m[0].get("applies_to") == c.get("applies_to")
                      and m[0].get("params") == c.get("params") and {m[0].get("phase"), c.get("phase")} == {"transport", "final"}), None)
        if twin_:
            twin_.append(c)
        else:
            merged.append([c])
    for grp in merged[:3]:
        if len(grp) == 2:
            t = ctext(grp[0]).replace(" while carried", "").replace(" once placed", "") + " while carried and once placed"
        else:
            t = ctext(grp[0])
        lines.append(["RULE", t, " · ".join(c["id"] for c in grp)])
    prov = (spec.get("goals") or [{}])[0].get("provenance", {}) or {}
    # rule table rows (every rule of the corrected spec)
    def rname(r):
        if r["kind"] == "goal":
            return f"{oid(r['objects'][0]) if r.get('objects') else 'object'} in the tray"
        if r["rule_id"] == F["rule_id"]:
            return f"NEW · {oid(F['applies_obj'])} ≥ {F['min_m'] * 100:g} cm from the {nm('wall')}"
        sc = next((c for c in spec.get("constraints", []) if c["id"] == r["rule_id"]), None)
        if sc and sc["type"] == "upright":
            return f"{nm(sc['applies_to'].get('kind'))} upright " + ("while carried" if sc.get("phase") == "transport" else "once placed" if sc.get("phase") == "final" else "always")
        if sc and sc["type"] == "forbidden_contact" and "obstacle" in ((sc.get("params") or {}).get("with") or []):
            return f"nothing touches the {nm('wall')}"
        if sc:
            t = (sc.get("description") or sc["type"]).rstrip(".")
            for k, v in names.items():
                t = t.replace(f"the {v} ({k})", f"the {v}").replace(f"{v} ({k})", v)
            return t[:46]
        so = next((o for o in spec.get("ordering", []) if o["id"] == r["rule_id"]), None)
        if so:
            return f"order {nm(so['before']['kind'])} → {nm(so['after']['kind'])}"
        return r["rule_id"]
    rank = lambda r: (0 if r["kind"] == "goal" else 1 if r["kind"] == "ordering" else 3 if r["rule_id"] == F["rule_id"] else 2)
    table = [[rname(r), r["rule_id"]] for r in sorted([r for r in F["rows"]["new"] if r.get("enforced", True)], key=rank)]
    say = []
    for o in F["others"]:
        if o["outcome"] == "apply":
            s = ("already satisfied by the old run · re-planned: same routes" if o["detail"].startswith("success") and o.get("routes_same")
                 else f"old run re-judged and re-planned: {o['detail']}")
        elif o["outcome"] == "clarify":
            s = "MIRROR asks a question instead of guessing"
        elif "cycle" in o["detail"]:
            s = "refused: it contradicts the order the person stated"
        else:
            s = "refused: " + o["detail"][:70]
        say.append([o["text"], s])
    return dict(
        names=names, gate=gate, spec_lines=lines,
        spec_prov=f"provenance: {prov.get('source', '?')}{' (stated by the person)' if prov.get('source') == 'manual' else ''} · status: confirmed · {spec['spec_id']} v{spec['version']}",
        rule_rows=[["type", F["rule_type"]], ["applies to", f"{F['applies_kind']} (the {oid(F['applies_obj'])})"], ["phase", f"{F['phase']} (while carried)" if F["phase"] == "transport" else F["phase"]],
                   ["min distance", f"{F['min_m']:g} m from obstacles"], ["source", "user_correction"]],
        table=table, others=say, placement_label=placement_label,
        obj=oid(F["applies_obj"]), obstacle=nm("wall"),
        unchanged=[[oid(k), v[1]] for k, v in F["unchanged"].items()],
        title_note="the printed board and sync card calibrate the phone's camera",
    )


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", default=None, help="D1 real-demo dir (timeline.json, tracks_sam_540x960.json.gz)")
    ap.add_argument("--phase2", default=None)
    ap.add_argument("--pipeline", default=None, help="a mirror.capture.real_run output dir (replaces --capture/--phase2/--take)")
    ap.add_argument("--session", default=None, help="capture session json (object names); default from the pipeline manifest")
    ap.add_argument("--gate-note", default=None, help="one sentence under the gate card (default: generic, from the gate result)")
    ap.add_argument("--corr", required=True, help="make_correction.py output")
    ap.add_argument("--take", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--skip-video", action="store_true")
    ap.add_argument("--twin", default=None, help="twin output dir (twin.json + overlay.mp4), optional")
    ap.add_argument("--spark", default=None, help="spark_compile.py output (one recorded Muse Spark compile), optional")
    a = ap.parse_args(argv)
    corr, out = Path(a.corr), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    labels = None
    if a.pipeline:
        pd = Path(a.pipeline); man = json.loads((pd / "manifest.json").read_text())
        page = json.loads((pd / man.get("page", "page.json")).read_text())
        take = man["take"]; tracks = pd / man.get("tracks_proxy", "tracks_proxy.json.gz"); timeline_p = pd / "timeline.json"
        gate = page.get("gate") or man.get("gate"); spec_p = pd / man.get("spec", "spec.json"); scene_p = pd / man.get("scene", "scene/scene.json")
        session_p = a.session or str(pd / man.get("session", "session_answered.json"))
        placement = json.loads((pd / man.get("table_to_sim", "table_to_sim.json")).read_text())
        placement_label = ("layout shifted by software to fit the robot's reach" if "shift" in (man.get("placement_note") or "")
                           else "layout centred by software" if placement.get("source") == "auto_layout_centred" else "layout in your table's frame")
        labels = man.get("labels")
        divider_axis = None
        prov = dict(take=f"{Path(take).name} sha256 {sha(take)}", masks=page.get("calibration", {}).get("lens", ""),
                    run="mirror.capture.real_run " + str(pd), correction="film/take/make_correction.py --pipeline")
    else:
        cap, p2 = Path(a.capture), Path(a.phase2)
        take = a.take; tracks = cap / "tracks_sam_540x960.json.gz"; timeline_p = cap / "timeline.json"
        p2s = json.loads((p2 / "phase2_summary.json").read_text())
        gate = p2s["gate"]; spec_p = p2 / "spec_intent.json"; scene_p = p2 / "scene.json"
        session_p = a.session
        placement = p2s["placement"]; divider_axis = p2s["divider_axis"]
        placement_label = "layout centred by software" if placement.get("source") == "auto_layout_centred" else "layout in your table's frame"
        prov = dict(
            take="IMG_0815.MOV (owner's phone, one camera, 1920x1080 HEVC, 49.8 s) sha256 " + sha(take),
            masks="SAM 3.1 box-prompt tracking seeded by Muse Spark grounding at 12.5 s (recorded cassettes, $0.33 spent on 3 Nov); no model call when building the film",
            run="MuJoCo rollouts re-run by film/take/make_correction.py (reproduces the phase-2 report)",
            correction="offline rule interpreter (no model call)")
    if not a.skip_video:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", take, "-an", "-vf",
                        "scale=720:1280:flags=lanczos,format=yuv420p", "-c:v", "libx264", "-crf", "18", "-preset", "medium",
                        "-g", "15", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                        str(out / "phone.mp4")], check=True, timeout=900, stdin=subprocess.DEVNULL)
    masks = export_masks(tracks, out / "masks.json")
    C = json.loads((corr / "corrections.json").read_text())
    ro = export_rollout(corr / "rollout_old.json", out / "run_old.json")
    prim = primary_candidate(C)
    rn = export_rollout(corr / f"rollout_{prim['id']}.json", out / "run_new.json")
    timeline = json.loads(timeline_p.read_text())
    spec = json.loads(spec_p.read_text())
    scene = json.loads(scene_p.read_text())
    events = norm_events(timeline)
    data = dict(
        labels=[l[:1].lower() + l[1:] for l in (labels or C["labels"])][:4],
        timeline=timeline, events=events, segments=segments(events, masks["n_frames"], masks["fps"]),
        gate=gate, placement=placement, divider_axis=divider_axis,
        spec=spec, scene=scene, film=film_numbers(C, ro, rn, scene), provenance=prov,
    )
    data["copy"] = build_copy(data, object_names(session_p), placement_label, a.gate_note)
    cp = data["copy"]; F = data["film"]
    minor = [e for e in events if not e["carry"]]
    cp["watch_notes"] = ([f"masks · SAM 3.1, box prompts from Muse Spark grounding at {masks.get('seed_t', 12.5):g} s",
                          "recorded on 3 Nov ($0.33) · replayed here, no model call"] if not a.pipeline else
                         ["masks · SAM 3.1 tracking, box prompts from Muse Spark grounding",
                          ("model outputs replayed from recordings · no new model call" if (man.get("spend") or {}).get("mode") == "replay"
                           else f"model spend for this take ${(man.get('spend') or {}).get('usd', 0):.2f}")])
    if minor:
        cp["watch_notes"].append(f"{len(minor)} small mask change{'s' if len(minor) > 1 else ''} ("
                                 + ", ".join(f"{e['object'].replace('_', ' ')} {e['from_s']:.1f} s" for e in minor[:3]) + ") kept for review, not hidden")
    chain = next((l[1] for l in cp["spec_lines"] if l[0] == "ORDER"), None)
    ordering_ok = all(r["verdict"] == "satisfied" for r in F["rows"]["old"] if r["kind"] == "ordering")
    cp["run_head"] = f"Checker: {F['old_status']}"
    cp["run_lines"] = ([f"order {chain.replace('  ', ' ')} {'✓' if ordering_ok else '✗'}"] if chain else []) + \
        [f"beads kept {b[1]} / {b[2]}" for b in F["beads_old"]] + \
        [f"max {cp['obj']} tilt carried {F['tilt'][0]}°", f"{sum(r['verdict'] == 'violated' for r in F['rows']['old'])} violations"]
    # top-view fit (screen x = sim y, screen y = sim x) and 3-D camera targets, from the layout itself
    pts = [p for p in F["old_path"] + F["new_path"]]
    for b in ro["bodies"]:
        if b["static"]:
            for g_ in b["geoms"]:
                if g_["name"] != "table" and g_["type"] == "box":
                    pts += [[g_["pos"][0] + sx * g_["size"][0], g_["pos"][1] + sy * g_["size"][1]] for sx in (-1, 1) for sy in (-1, 1)]
    P_ = np.array(pts); lo, hi = P_.min(0), P_.max(0)
    S = float(min((900 - 180) / max(1e-3, hi[0] - lo[0] + 0.12), (980 - 200) / max(1e-3, hi[1] - lo[1] + 0.12), 1250))
    cx, cy = (lo + hi) / 2
    cp["view"] = dict(S=round(S, 1), Ox=round(590 - cy * S, 1), Oy=round(540 - cx * S, 1))
    cp["center"] = [round(float(cx), 3), round(float(cy), 3)]
    fo = ro["poses"][F["applies_obj"]][F["old_clear_frame"]]
    cp["focus"] = [round(fo[0], 3), round(fo[1], 3)]
    car = [e for e in events if e["carry"]]
    end_s = min(masks["n_frames"] / masks["fps"] - 0.2, (car[-1]["to_s"] + 1.1) if car else masks["n_frames"] / masks["fps"] / 2)
    cp["end_still_s"] = round(end_s, 1)
    if (out / "phone.mp4").exists():
        for nm_, ss in (("phone_end.jpg", end_s), ("phone_start.jpg", data["segments"]["title_src0"])):
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{ss:.2f}", "-i", str(out / "phone.mp4"), "-frames:v", "1", "-q:v", "2",
                            str(out / nm_)], check=True, timeout=120, stdin=subprocess.DEVNULL)
    if a.twin and (Path(a.twin) / "twin.json").exists():
        tj = json.loads((Path(a.twin) / "twin.json").read_text())
        reg = tj.get("registration", {})
        tt = reg.get("tray_table_frame", {})
        data["twin"] = dict(caption=tj.get("caption"), labels=tj.get("labels"), time_map=tj.get("time_map"),
                            tray_iou=reg.get("tray_iou"), tray_offset_cm=tt.get("centre_offset_cm"), tray_yaw_deg=tt.get("yaw_offset_deg"),
                            object_base_offset_px=reg.get("object_base_offset_px"), plate=tj.get("plate", {}).get("method"),
                            source=str(Path(a.twin) / "overlay.mp4"), created_unix=tj.get("created_unix"))
        subprocess.run(["cp", str(Path(a.twin) / "overlay.mp4"), str(out / "twin_overlay.mp4")], check=True, timeout=60)
    if a.spark and Path(a.spark).exists():
        sp = json.loads(Path(a.spark).read_text())
        data["spark"] = dict(model=sp["model"], same_scoped_rule=sp["same_scoped_rule"], est_cost_usd=sp["est_cost_usd"],
                             live_calls=sp["live_calls"], rationale=(sp["ops_model"][0].get("rationale") if sp["ops_model"] else None),
                             scoped=sp["scoped_model"])
    (out / "data.json").write_text(json.dumps(data, indent=1, default=str))
    print("ok", {k: len(v.get("poly", [])) for k, v in masks["objects"].items()})


if __name__ == "__main__":
    main()
