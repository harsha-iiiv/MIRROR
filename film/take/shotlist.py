"""Shot list with the source of every shot and every number quoted on screen.

    python film/take/shotlist.py --data PUBLIC/data.json --shots film/take/shots_film.json --motion CHECK/motion.json --out SHOTLIST.md
"""
from __future__ import annotations
import argparse, json, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True); ap.add_argument("--shots", required=True)
    ap.add_argument("--motion", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    d = json.loads(Path(a.data).read_text()); F = d["film"]
    shots = json.loads(Path(a.shots).read_text()); mo = {s["name"]: s for s in json.loads(Path(a.motion).read_text())["shots"]}
    ev = [dict(object=e["object"], moved_from_s=e["from_s"], moved_to_s=e["to_s"]) for e in d.get("events", []) if e["carry"]]
    tw = d.get("twin") or {}
    sp = d.get("spark") or {}
    desc = {
        "title": (f"the take {d['segments']['title_src0']}-{d['segments']['watch_src0']} s at {d['segments']['title_rate']}x beside the title.", "phone footage (owner's take)"),
        "watch": (f"the take from {d['segments']['watch_src0']} s at {d['segments']['watch_rate']}x with SAM 3.1 outlines per frame; event bars grow on the take's clock: "
                  + "; ".join(f"{e['object']} {e['moved_from_s']:.2f}-{e['moved_to_s']:.2f} s" for e in ev) + ".",
                  "tracks_sam_540x960.json.gz (recorded 3 Nov), timeline.json"),
        "gate": (f"{d['copy']['gate']['headline']} {d['copy']['gate']['code']}: {d['copy']['gate']['detail']}; the task shown is "
                 f"{d['copy']['gate']['spec_intro']} ({d['spec']['spec_id']} v{d['spec']['version']}).", "gate result, spec json"),
        "run": (f"three.js drawing of the recorded MuJoCo rollout (recorded poses only) at 8x; {d['copy']['obj']} path ribbon; checker {F['old_status']}, "
                f"beads {F['beads_old'][0][1]}/{F['beads_old'][0][2]}, max carried tilt {F['tilt'][0]} deg.",
                "film/take/make_correction.py rollout_old.json (reproduces the phase-2 report)"),
        "twin": (f"Twin overlay (first 10 s at 2x; robot at {2 * (tw.get('time_map') or {}).get('sim_speed', 1)}x real time): "
                 f"tray IoU {tw.get('tray_iou')}, simulated tray {tw.get('tray_offset_cm')} cm / {abs(tw.get('tray_yaw_deg') or 0)} deg off.",
                 tw.get("source", "-")),
        "sentence": (f"\"{F['correction_text']}\" lands word by word, then the rule {F['rule_id']} (keep_clearance, cup, transport, 0.06 m). "
                     + (f"Muse Spark ({sp.get('model')}, 1 recorded call, ${sp.get('est_cost_usd')}) and the offline interpreter give the same rule; "
                        f"rationale quoted from the model." if sp else "offline interpreter."),
                     "corrections.json, spark.json (recorded cassette)"),
        "rejudge": (f"Top view: old path re-judged {F['old_rechecked_status']} (closest {F['old_clear_m'] * 100:.2f} cm at {F['old_clear_t']:.2f} s), "
                    f"new path {F['new_status']} (closest {F['new_clear_m'] * 100:.2f} cm at {F['new_clear_t']:.2f} s); 6 cm band; footprints at the "
                    f"closest frames; " + ", ".join(f"{u[0]} {u[1] * 100:.1f} cm" for u in d['copy']['unchanged']) + " unchanged.",
                    "corrections.json (checker detail: " + F["old_detail"] + ")"),
        "ghosts": ("Both recorded rollouts on one clock (old as a ghost, corrected solid), divider box grown by 6 cm drawn translucent.",
                   "rollout_old.json, rollout_mug_clearance.json"),
        "table": ("Every rule under v1 / old run re-judged under v2 / new run v2, plus three other sentences: "
                  + "; ".join(f"{o['id']}: {o['outcome']}" for o in F["others"]) + ".", "corrections.json"),
        "end": ("End card with the labels.", "-"),
    }
    t = 0.0
    lines = ["# MIRROR capture film v3: shot list", "",
             "Every robot image is a recorded MuJoCo rollout drawn from recorded poses; every number is copied from a record. "
             "Labels on screen: " + ", ".join(d["labels"]) + ".", "",
             f"Commit {subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()}.", "",
             "| # | Shot | Time | On screen | Source | Motion check (scale change, text px/frame) |", "|---|---|---|---|---|---|"]
    for k, s in enumerate(shots):
        dur = s["frames"] / 30; m = mo.get(s["name"], {})
        if s["name"] not in desc:
            continue
        lines.append(f"| {k + 1} | {s['name']} | {t:.1f}-{t + dur:.1f} s | {desc[s['name']][0]} | {desc[s['name']][1]} | "
                     f"{m.get('max_scale_change', '-')}, {m.get('max_text_flow_p99_px', '-')} {'pass' if m.get('pass') else 'FAIL'} |")
        t += dur
    lines += ["", "## Approximate (on screen)", "- " + "; ".join(d["labels"]),
              "- simulation proxies sized from ruler measurements (catalogue shapes, so they differ from the real objects); "
              "proxy colours neutralised in the 3-D view", "- layout centred on the simulator table by software",
              "- the correction sentence was chosen for the film from MIRROR's suggested corrections; the owner has not chosen one"]
    Path(a.out).write_text("\n".join(lines) + "\n")
    print(a.out)


if __name__ == "__main__":
    main()
