"""Ingest a person's phone clips (no model calls).

    python -m mirror.capture.ingest init  DIR     # write DIR/capture.json to fill in
    python -m mirror.capture.ingest frames DIR    # first frame of each take clip, for drawing object boxes
    python -m mirror.capture.ingest run   DIR     # calibrate, sync, track, reconstruct; DIR/session.json + review.md
    python -m mirror.capture.ingest score DIR LABELS.json [--spec PROPOSED.json]
    python -m mirror.capture.ingest demo  DIR     # SYNTHETIC end-to-end example (rendered, not real footage)

``capture.json`` (paths relative to DIR):
    clips.take.top / clips.take.side      the demonstration (side optional: single-view mode)
    clips.lens.top / clips.lens.side      board waved in front of each phone (lens calibration)
    print_check                           measured_bar_mm and measured_square_mm from a ruler
    objects                               one mapping per tracked object (see mirror.capture.proxy)
    prompts                               boxes [x, y, w, h] drawn on a frame of each take clip
    tags                                  {"95": "mug"} if a printed tag is taped to an object
    intrinsics                            {camera: intrinsics.json} saved lens calibration (instead of clips.lens)
    board_search_s                        seconds searched for the flat board at the start (default 3)
    end_board_same_placement              false if the end-of-take board was laid down differently (default true)
    tracker                               "classical" (default), "manual" (baseline: clicked key frames only)
                                          or "model" (imported model tracks only)
    model_tracks                          {camera: {"path": tracks.json.gz, "origin": "recorded_replay"|"fresh_model_call",
                                          "labels": {"model label": "your label"}}}
    manual_keyframes                      {camera: {label: {frame: [u, v]}}} for the manual baseline
    identity_confirmations                [[camera, label, from_frame], ...] after review
    corrections                           event corrections after review
Nothing is uploaded and no model is called. Model tracks (for example SAM)
can be added as ``model_tracks`` {camera: tracks file} and are recorded with
their origin.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TEMPLATE = {
    "about": "MIRROR phone capture. Fill in every null; see docs/CAMERA_CAPTURE.md.",
    "clips": {"take": {"top": "take_top.mov", "side": "take_side.mov"},
              "lens": {"top": "lens_top.mov", "side": "lens_side.mov"}},
    "print_check": {"measured_bar_mm": None, "measured_square_mm": None},
    "objects": [
        {"track_label": "mug", "real_identity": None, "proxy": "cup", "real_contents": "dry rice",
         "real_dims": {"height": {"value_m": None, "source": "measured_ruler"}, "width": {"value_m": None, "source": "measured_ruler"}},
         "unsupported_features": ["handle"], "assumptions": [{"name": "mass_kg", "value": "unknown", "basis": "assumed"}],
         "confirmed_by_person": False},
        {"track_label": "box", "real_identity": None, "proxy": "block", "real_dims": {}, "confirmed_by_person": False},
        {"track_label": "pen", "real_identity": None, "proxy": "marker", "real_dims": {}, "confirmed_by_person": False},
        {"track_label": "tray", "real_identity": None, "proxy": "tray",
         "real_dims": {"width": {"value_m": None, "source": "measured_ruler"}, "depth": {"value_m": None, "source": "measured_ruler"}},
         "confirmed_by_person": False},
        {"track_label": "book", "real_identity": None, "proxy": "wall", "real_dims": {}, "confirmed_by_person": False},
    ],
    "prompts": [],
    "tags": {},
    "about_tray": "The tray is the destination and is required. Either confirm the 'tray' object above (a box on it in each "
                  "camera, inside width and depth measured), or set 'tray' to {\"center_xy\": [x, y], \"inner_half\": [w/2, d/2]} "
                  "in metres on the board (origin at its printed top-left corner).",
    "tray": None,
    "identity_confirmations": [],
    "corrections": [],
}


def _resolve(d: Path, p):
    return str((d / p).resolve()) if p else None


def cmd_init(d: Path) -> int:
    d.mkdir(parents=True, exist_ok=True)
    f = d / "capture.json"
    if f.exists():
        print(f"{f} exists; not overwriting")
        return 1
    f.write_text(json.dumps(TEMPLATE, indent=2) + "\n")
    print(f"wrote {f}. Put the clips next to it, measure the print, then run: frames, fill prompts, run.")
    return 0


def cmd_frames(d: Path) -> int:
    import cv2
    from mirror.capture import media as M
    cfg = json.loads((d / "capture.json").read_text())
    out = d / "review"
    out.mkdir(exist_ok=True)
    for cam, p in cfg["clips"]["take"].items():
        if not p:
            continue
        m = M.probe(_resolve(d, p))
        # first frame after the board is gone is the best place to draw boxes; write frame 0 and one at ~3 s
        for k in sorted({0, m.frame_at(m.pts_s[0] + 3.0)}):
            f = M.read_frame(m, k)
            g = f.copy()
            for x in range(0, m.width, 100):
                cv2.line(g, (x, 0), (x, m.height - 1), (255, 255, 255), 1)
                cv2.putText(g, str(x), (x + 2, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
            for y in range(0, m.height, 100):
                cv2.line(g, (0, y), (m.width - 1, y), (255, 255, 255), 1)
                cv2.putText(g, str(y), (2, y + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
            cv2.imwrite(str(out / f"{cam}_frame{k}.png"), g[..., ::-1])
        print(f"{cam}: {m.n_frames} frames, {m.width}x{m.height}, {'variable' if m.variable_frame_rate else 'constant'} frame rate; "
              f"boxes go in capture.json prompts as {{camera, label, frame, box: [x, y, w, h]}}")
    return 0


def _load_inputs(d: Path):
    from mirror.capture import calibrate as C, events as EV, proxy as PX, track as TR
    cfg = json.loads((d / "capture.json").read_text())
    pc = cfg.get("print_check") or {}
    if pc.get("measured_bar_mm") is None or pc.get("measured_square_mm") is None:
        raise SystemExit("measure the printed 50 mm bar and one square with a ruler and enter both in print_check")
    objs = []
    for o in cfg["objects"]:
        o = dict(o)
        o["real_identity"] = o.get("real_identity") or o["track_label"]
        o["real_dims"] = {k: v for k, v in (o.get("real_dims") or {}).items() if v and v.get("value_m") is not None}
        objs.append(PX.ObjectMapping(**o))
    return dict(
        take={c: _resolve(d, p) for c, p in cfg["clips"]["take"].items() if p and (d / p).exists()},
        lens={c: _resolve(d, p) for c, p in (cfg["clips"].get("lens") or {}).items() if p and (d / p).exists()},
        print_check=C.PrintCheck(**pc), prompts=[TR.Prompt(**p) for p in cfg.get("prompts", [])], mappings=objs,
        tags={int(k): v for k, v in (cfg.get("tags") or {}).items()} or None,
        tray=EV.TrayRegion(**cfg["tray"]) if cfg.get("tray") else None,
        identity_confirmations=[tuple(x) for x in cfg.get("identity_confirmations", [])],
        corrections=[EV.Correction(**c) for c in cfg.get("corrections", [])],
        tracker=cfg.get("tracker", "classical"),
        intrinsics={c: C.Intrinsics.model_validate_json((d / p).read_text())
                    for c, p in (cfg.get("intrinsics") or {}).items()} or None,
        board_search_s=float(cfg.get("board_search_s", 3.0)),
        end_board_same_placement=bool(cfg.get("end_board_same_placement", True)),
        model_tracks=_model_tracks(d, cfg),
        manual_keyframes={cam: {lab: {int(k): tuple(v) for k, v in kf.items()} for lab, kf in d2.items()}
                          for cam, d2 in (cfg.get("manual_keyframes") or {}).items()} or None)


def _model_tracks(d: Path, cfg: dict):
    """{camera: {"path": ..., "origin": "recorded_replay" | "fresh_model_call", "labels": {...}}} -> TrackSets."""
    spec = cfg.get("model_tracks") or {}
    if not spec:
        return None
    from mirror.capture import media as M, track as TR
    out = {}
    for cam, m in spec.items():
        clip = cfg["clips"]["take"].get(cam)
        meta = M.probe(_resolve(d, clip))
        out[cam] = TR.from_perception_tracks(meta, _resolve(d, m["path"]), camera=cam, track_camera=m.get("camera"),
                                             origin=m.get("origin", "recorded_replay"), labels=m.get("labels"))
    return out


def cmd_run(d: Path) -> int:
    from mirror.capture import session as SE
    kw = _load_inputs(d)
    if "top" not in kw["take"]:
        raise SystemExit("clips.take.top is missing")
    if not kw["prompts"]:
        raise SystemExit("no prompts: run 'frames', draw a box around each object, add them to capture.json")
    s = SE.build_session(**kw)
    s.save(d / "session.json")
    lines = SE.summary_lines(s)
    (d / "review.md").write_text("# Capture review\n\n" + "\n".join(f"- {x}" if not x.startswith("- ") else x for x in lines) + "\n")
    print("\n".join(lines))
    return 0


def cmd_score(d: Path, labels: Path, spec: Path | None) -> int:
    from mirror.capture import session as SE, validation as V
    s = SE.CaptureSession.load(d / "session.json")
    lab = V.ClipLabels.model_validate_json(Path(labels).read_text())
    row = V.score_clip(s, lab, proposed_spec=json.loads(spec.read_text()) if spec else None)
    (d / f"score_{lab.clip_id}.json").write_text(json.dumps(row, indent=1, default=float))
    print(json.dumps({k: row[k] for k in ("clip_id", "outcome", "gate_passed")}, indent=1))
    for c in row["checks"]:
        print(f"  {'PASS' if c['passed'] else ('n/a ' if c['passed'] is None else 'FAIL')} {c['name']} = {c['value']} (threshold {c['threshold']})")
    return 0


def cmd_demo(d: Path) -> int:
    """SYNTHETIC: render a take and lens clips, write capture.json, run, and score against render truth."""
    import numpy as np
    from mirror.capture import synthetic as S
    d.mkdir(parents=True, exist_ok=True)
    plan = S.TakePlan(tilt_deg=25.0, mug_tag=True)
    truth = S.render_take(d, plan)
    cams = S.default_cameras()
    for n, c in cams.items():
        S.render_lens_clip(d / f"lens_{n}.mp4", c, n_views=18, seed={"top": 1, "side": 2}[n])
    p0, _ = plan.mug_pose(0.0)
    cfg = json.loads(json.dumps(TEMPLATE))
    cfg["about"] = "SYNTHETIC demo: rendered clips, not real footage."
    cfg["clips"] = {"take": {"top": "synthetic_top.mp4", "side": "synthetic_side.mp4"},
                    "lens": {"top": "lens_top.mp4", "side": "lens_side.mp4"}}
    cfg["print_check"] = {"measured_bar_mm": 50.0, "measured_square_mm": 30.0, "measured_by": "synthetic_test"}
    cfg["objects"] = [{"track_label": "mug", "real_identity": "red mug (SYNTHETIC render)", "proxy": "cup", "real_contents": "dry rice",
                       "real_dims": {"height": {"value_m": 0.095, "source": "measured_ruler"}},
                       "unsupported_features": ["handle"], "confirmed_by_person": True}]
    cfg["prompts"] = [S.truth_prompt(truth, c, "mug", p0, (0.04, 0.04, 0.0475)).model_dump() for c in ("top", "side")]
    cfg["tags"] = {"95": "mug"}
    tray_xy = S.truth_table_xyz([*S.TRAY["center"], 0.0], truth)[:2]
    cfg["tray"] = {"center_xy": [float(tray_xy[0]), float(tray_xy[1])], "inner_half": list(S.TRAY["inner_half"]),
                   "source": "person_supplied", "footprint_source": "person_supplied"}   # render geometry (SYNTHETIC)
    cfg["identity_confirmations"] = [["top", "mug", 0], ["side", "mug", 0]]
    (d / "capture.json").write_text(json.dumps(cfg, indent=2) + "\n")
    from mirror.capture import session as SE
    kw = _load_inputs(d)
    s = SE.build_session(**kw, synthetic=True)
    s.save(d / "session.json")
    from mirror.capture import validation as V
    lab = synthetic_labels(truth, plan)
    row = V.score_clip(s, lab)
    (d / "score_synthetic.json").write_text(json.dumps(row, indent=1, default=float))
    lines = SE.summary_lines(s)
    (d / "review.md").write_text("# Capture review (SYNTHETIC demo)\n\n" + "\n".join(lines) + "\n")
    from mirror.capture import trace as TRC
    scene = TRC.to_scene(s)             # proxies at their start rest positions, tray with its footprint
    (d / "scene.json").write_text(scene.model_dump_json(indent=1) + "\n")
    print("\n".join(lines))
    for c in row["checks"]:
        print(f"  {'PASS' if c['passed'] else ('n/a ' if c['passed'] is None else 'FAIL')} {c['name']} = {c['value']} (threshold {c['threshold']})"
              "  [synthetic rehearsal, not evidence]")
    return 0


def synthetic_labels(truth: dict, plan, *, clip_id: str = "synthetic", condition: str = "synthetic", step: float = 0.5):
    """Labels from render ground truth (SYNTHETIC rehearsal only)."""
    import numpy as np
    from mirror.capture import synthetic as S, validation as V
    start = S.truth_table_xyz(plan.mug_pose(0.0)[0], truth)
    end = S.truth_table_xyz(plan.mug_pose(plan.duration)[0], truth)
    kf, tilt = {}, {}
    for t in np.arange(0.0, plan.duration - 0.2, step):
        p, R = plan.mug_pose(float(t))
        kf[round(float(t), 3)] = tuple(float(v) for v in S.truth_table_xyz(p, truth))
        tilt[round(float(t), 3)] = float(np.degrees(np.arccos(np.clip(R[2, 2], -1, 1))))
    return V.ClipLabels(clip_id=clip_id, split="synthetic_rehearsal", condition=condition, expected="reconstruct", labeller="render ground truth",
                        synthetic=True, rest_positions={"mug": {"start": (float(start[0]), float(start[1])), "end": (float(end[0]), float(end[1]))}},
                        keyframes={"mug": kf}, keyframe_reference="render_ground_truth", tilt={"mug": tilt},
                        tilt_reference="render_ground_truth",
                        events=[V.EventLabel(object="mug", type="pick", t=plan.pick), V.EventLabel(object="mug", type="place", t=plan.place)])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m mirror.capture.ingest", description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=["init", "frames", "run", "score", "demo"])
    ap.add_argument("dir", type=Path)
    ap.add_argument("labels", nargs="?", type=Path)
    ap.add_argument("--spec", type=Path)
    a = ap.parse_args(argv)
    if a.command == "init":
        return cmd_init(a.dir)
    if a.command == "frames":
        return cmd_frames(a.dir)
    if a.command == "run":
        return cmd_run(a.dir)
    if a.command == "score":
        if a.labels is None:
            ap.error("score needs LABELS.json")
        return cmd_score(a.dir, a.labels, a.spec)
    return cmd_demo(a.dir)


if __name__ == "__main__":
    sys.exit(main())
