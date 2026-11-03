#!/usr/bin/env python
"""Build the demo films from recorded simulation sources.

    MUJOCO_GL=osmesa python scripts/film/build.py --sources <make_sources out> \
        --demos <demo_corrections out at 0.2445> --out <dir> [--cut social|judging|both] [--shots a,b]

Writes one MP4 per shot (shots/<cut>/<name>.mp4), a per-frame camera log
(camlog_<cut>.jsonl), the assembled film (<cut>.mp4, with a synthesized music
bed) and timeline_<cut>.json naming the source of every shot. Every number on
screen is read from the source summaries or from the frozen registration
report quoted in REGISTERED (checked against the report text when present).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from mirror.sim.render import _cup_beads_in as _cup_beads_in_  # noqa: E402
from scripts.film.compose import (BG, CHIP, FPS, GREEN, INK, MUTED, RED, RULE, Move, SimShot, Writer,  # noqa: E402
                                  chip, fade, font, sim_frames, text_block, wrap, write_camlog)

CAL = 0.2445
# Frozen registration objective_v1 (results/eval_objective_v1/attempt_1/report.md), blocked stratum.
REGISTERED = dict(report="results/eval_objective_v1/attempt_1/report.md", layouts_blocked=10, beads_lost_blocked=38,
                  new_layouts=120, quote_blocked="spills 38 beads per condition",
                  quote_never="0/80 primary and 0/40 shortcut")
STUDIO_URL = "mirror-robot-studio.netlify.app"

SIDE_CAM = dict(lookat="slot", distance=0.64, azimuth=-90.0, elevation=-14.0)   # along the wall plane


class Ctx:
    def __init__(self, sources: Path, demos: Path, out: Path):
        self.sources, self.demos, self.out = sources, demos, out
        self.summary = json.loads((sources / "summary.json").read_text())
        self.runs = {r["name"]: r for r in self.summary["runs"]}
        self.rechecks = {r["scene"]: r for r in self.summary["rechecks"]}
        self.demo = {c: json.loads((demos / c / "summary.json").read_text())
                     for c in ("ordering", "clearance", "ambiguous", "unsupported", "blocked_refusal")}
        self.demo_manifest = json.loads((demos / "manifest.json").read_text())
        self.camlog: list[dict] = []

    def src(self, name):
        return str(self.sources / f"{name}.json")

    def run(self, name):
        return self.runs[name]


def ease_text_alpha(img: Image.Image, layer: Image.Image, a: float) -> Image.Image:
    if a <= 0:
        return img
    if a < 1:
        layer = layer.copy()
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * a)))
    img.alpha_composite(layer)
    return img


def layer(size):
    return Image.new("RGBA", size, (0, 0, 0, 0))


def beads_text(info, total_first=None):
    if not info.beads:
        return None, INK
    k, n = info.beads
    return f"beads in cup  {k}/{n}", (RED if k < n else INK)


def certificate_numbers(detail: str):
    m = re.search(r"span at least ([0-9.]+) m vertically, but all of \S+ openings span only ([0-9.]+) m", detail)
    return (float(m.group(1)), float(m.group(2))) if m else (None, None)


_SPILL: dict = {}


def spill_time(source: str) -> float:
    """Seconds from the rollout's first frame to the first bead leaving the cup
    (read from the recorded frames)."""
    if source not in _SPILL:
        from mirror.sim.render import _Playback as PB
        from mirror.sim.render import _load as LD
        vis, frames, _ = LD(source)
        pb, t0 = PB(frames), frames[0].t
        k, n_steps = 0, int((frames[-1].t - t0) * 20)
        _SPILL[source] = None
        for k in range(n_steps):
            b = _cup_beads_in_(pb.at(t0 + k / 20), vis)
            if b and b[0] < b[1]:
                _SPILL[source] = k / 20
                break
    return _SPILL[source]


def check_registered():
    p = ROOT / REGISTERED["report"]
    if not p.exists():
        return "report not present in this checkout"
    t = p.read_text()
    for q in (REGISTERED["quote_blocked"], REGISTERED["quote_never"]):
        if q not in t:
            raise SystemExit(f"registered figure not found in {REGISTERED['report']}: {q!r}")
    return "checked"


# ------------------------------------------------------------------ layouts
class Vertical:
    """1080x1920. Text only between y 290 and 1240 (phone safe zone)."""
    W, H = 1080, 1920
    SIM = (0, 590, 1080, 1080)        # x, y, w, h
    HEAD_Y, SUB_Y = 300, 500
    kind = "vertical"

    def base(self):
        return Image.new("RGBA", (self.W, self.H), BG + (255,))

    def head(self, d, text, size=72, y=None, fill=INK):
        return text_block(d, (60, self.HEAD_Y if y is None else y), text, font("bold", size), fill=fill, max_w=960)

    def sub(self, d, text, fill=INK, size=42, y=None):
        return text_block(d, (60, self.SUB_Y if y is None else y), text, font("medium", size), fill=fill, max_w=960)

    def chips(self, d, items):
        y = self.SIM[1] + 16
        for t in items:
            _, y2 = chip(d, (24, y), t, font("medium", 28))
            y = y2 + 10


class Horizontal:
    """1920x1080: text column on the left, simulation on the right."""
    W, H = 1920, 1080
    COL = 520
    SIM = (520, 0, 1400, 1080)
    HEAD_Y, SUB_Y = 120, 330
    kind = "horizontal"

    def base(self):
        return Image.new("RGBA", (self.W, self.H), BG + (255,))

    def head(self, d, text, size=56, y=None, fill=INK):
        return text_block(d, (56, self.HEAD_Y if y is None else y), text, font("bold", size), fill=fill, max_w=420)

    def sub(self, d, text, fill=INK, size=30, y=None):
        return text_block(d, (56, self.SUB_Y if y is None else y), text, font("regular", size), fill=fill, max_w=420)

    def chips(self, d, items):
        y = 28
        for t in items:
            _, y2 = chip(d, (self.SIM[0] + 24, y), t, font("medium", 24))
            y = y2 + 8


# --------------------------------------------------------------- runners
from mirror.sim.render import phase_words  # noqa: E402


def A(color, a):
    return tuple(color[:3]) + (int(255 * max(0.0, min(1.0, a))),)


def run_sim(ctx, L, cut, name, shot, draw, record, tile=None):
    """One SimShot filling L.SIM (or ``tile`` = (x, y, w, h))."""
    x, y, w, h = tile or L.SIM
    wr = Writer(ctx.out / "shots" / cut / f"{name}.mp4", L.W, L.H)
    for img, info in sim_frames(shot, w, h):
        frame = L.base()
        frame.paste(Image.fromarray(img), (x, y))
        lay = layer((L.W, L.H))
        draw(ImageDraw.Draw(lay), info)
        frame.alpha_composite(lay)
        wr.add(frame)
        ctx.camlog.append(dict(cut=cut, shot=name, i=info.i, t_out=round(info.t_out, 4), t_sim=round(info.t_sim, 4),
                               cam=info.cam))
    wr.close()
    record.update(name=name, duration=shot.duration, kind="sim")
    return record


def run_grid(ctx, L, cut, name, shots, tiles, draw, record, duration):
    wr = Writer(ctx.out / "shots" / cut / f"{name}.mp4", L.W, L.H)
    gens = [sim_frames(s, t[2], t[3]) for s, t in zip(shots, tiles)]
    for items in zip(*gens):
        frame = L.base()
        for (img, _), t in zip(items, tiles):
            frame.paste(Image.fromarray(img), (t[0], t[1]))
        lay = layer((L.W, L.H))
        infos = [it[1] for it in items]
        draw(ImageDraw.Draw(lay), infos)
        frame.alpha_composite(lay)
        wr.add(frame)
        ctx.camlog.append(dict(cut=cut, shot=name, i=infos[0].i, t_out=round(infos[0].t_out, 4),
                               cam=infos[0].cam, tiles=[inf.cam for inf in infos]))
    wr.close()
    record.update(name=name, duration=duration, kind="grid")
    return record


def run_card(ctx, L, cut, name, duration, draw, record, background=None):
    wr = Writer(ctx.out / "shots" / cut / f"{name}.mp4", L.W, L.H)
    n = int(round(duration * FPS))
    from scripts.film import compose as _c
    if _c.MAX_FRAMES:
        n = min(n, _c.MAX_FRAMES)
    for i in range(n):
        if not _c.sampled(i, n):
            continue
        frame = L.base() if background is None else background.copy()
        lay = layer((L.W, L.H))
        draw(ImageDraw.Draw(lay), i, i / FPS)
        frame.alpha_composite(lay)
        wr.add(frame)
        ctx.camlog.append(dict(cut=cut, shot=name, i=i, t_out=round(i / FPS, 4), cam=None))
    wr.close()
    record.update(name=name, duration=duration, kind="card")
    return record


def still(ctx, source, t, cam, w, h):
    shot = SimShot(source, t_start=t, duration=1 / FPS, move=Move(dict(cam)), trail=False)
    for img, _ in sim_frames(shot, w, h):
        return Image.fromarray(img)


def src_rec(ctx, run_name, **kw):
    r = ctx.run(run_name)
    d = dict(source=f"{run_name}.json", scene=r["scene_id"], seed=r["seed"], spec_version=r["spec_version"],
             rotation_price=r["rotation_price"], status=r["status"], beads=r["beads"],
             rollout_sha256=r.get("rollout_sha256"), commit=ctx.summary["commit"])
    d.update(kw)
    return d


def demo_rec(ctx, case, which, **kw):
    s = ctx.demo[case]
    m = ctx.demo_manifest
    part = s[which]
    d = dict(source=f"demo_corrections {case}/rollout_{which}.json", scene=part["scene_id"],
             spec_version=part["spec_version"], rotation_price=m["planner_config"]["rotation_penalty"],
             status=part["status"], commit=m["commit"], interpreter=m["interpreter"])
    d.update(kw)
    return d


SIM_LABEL = "MuJoCo simulation · recorded run"
GOAL_WORDS = {"g_inside_cup": "Put the cup in the tray.", "g_inside_block": "Put the block in the tray.",
              "g_inside_marker": "Put the marker in the tray."}

# =================================================================== SOCIAL
def social_shots(ctx):
    L = Vertical()
    cut = "social"
    recs = []
    hook_src = "ref_blocked__original__p0.2445"
    t_spill = spill_time(ctx.src(hook_src))

    # S1 hook --------------------------------------------------------------
    def d1(d, info):
        L.chips(d, [SIM_LABEL + " · replay 0.75×", "reference blocked layout · rules as first understood"])
        L.head(d, "It finished the job.")
        a = fade(info.i, int(round((t_spill - 6.0) / 0.75 * FPS)) - 3)
        if a:
            text_block(d, (60, 390), "It also spilled half the beads.", font("bold", 72), fill=A(RED, a), max_w=960)
        bt, col = beads_text(info)
        if bt:
            text_block(d, (60, 520), bt, font("medium", 40), fill=col)
    shot = SimShot(ctx.src(hook_src), t_start=6.0, duration=4.5, speed=0.75,
                   move=Move(dict(SIDE_CAM, elevation=-12.0), dict(SIDE_CAM, elevation=-17.0), 0.0, 4.5))
    recs.append(run_sim(ctx, L, cut, "s1_hook", shot, d1, dict(src=src_rec(ctx, hook_src, t=[6.0, 9.375], speed=0.75))))

    # S2 correction --------------------------------------------------------
    prov = ctx.summary["spec_provenance"]
    sentence = prov["correction_text"]

    def d2(d, i, t):
        text_block(d, (60, 300), "ONE SENTENCE", font("medium", 30), fill=MUTED)
        text_block(d, (60, 350), "“" + sentence + "”", font("medium", 54), fill=INK, max_w=960, line_gap=1.2)
        a = fade(i, int(1.6 * FPS))
        if a:
            text_block(d, (60, 680), "BECOMES ONE SCOPED RULE", font("medium", 30), fill=A(MUTED, a))
            d.rounded_rectangle((60, 730, 1020, 900), radius=18, outline=A(INK, a), width=3)
            text_block(d, (90, 752), "cup · while carried", font("bold", 50), fill=A(INK, a))
            text_block(d, (90, 820), "stay within 15° of upright", font("medium", 42), fill=A(INK, a))
        a2 = fade(i, int(2.8 * FPS))
        if a2:
            text_block(d, (60, 930), "Block and marker: unchanged.", font("medium", 42), fill=A(MUTED, a2))
            text_block(d, (60, 1030), "Typed. Compiled by Muse Spark (recorded response, replayed); "
                       "the offline rules produce the same change.", font("regular", 32), fill=A(MUTED, a2), max_w=960)
    recs.append(run_card(ctx, L, cut, "s2_correction", 5.5, d2,
                         dict(src=dict(source="summary.json spec_provenance + spec_diff", correction=sentence,
                                       model=prov.get("requested_model"), from_cassette=True,
                                       diff=[x["id"] for x in ctx.summary["spec_diff"]]))))

    # S3 check 1: re-judge ------------------------------------------------
    rc = ctx.rechecks["ref_blocked"]
    up = next(r for r in rc["rules"] if r["rule_id"] == "c_upright_transport_cup")
    secs = re.search(r"for ([0-9.]+) s", up["detail"]).group(1)

    def d3(d, info):
        L.chips(d, [SIM_LABEL + " · same run as before", "re-judged under the corrected rule"])
        L.head(d, "Check 1. The old run, re-judged.", size=64)
        a = fade(info.i, int(3.6 * FPS))
        if a:
            text_block(d, (60, 455), "Upright while carried: VIOLATED", font("bold", 46), fill=A(RED, a))
            text_block(d, (60, 515), f"tilted past 15° for {float(secs):.1f} s · read from the recorded physics log",
                       font("regular", 32), fill=A(MUTED, a), max_w=960)
    shot = SimShot(ctx.src(hook_src), t_start=5.6, duration=6.0, speed=1.0, freeze_at=9.2,
                   move=Move(dict(SIDE_CAM)))
    recs.append(run_sim(ctx, L, cut, "s3_rejudge", shot, d3,
                        dict(src=src_rec(ctx, hook_src, t=[5.6, 9.2], speed=1.0, freeze=9.2,
                                         recheck=dict(status=rc["status"], rule=up["rule_id"], detail=up["detail"])))))

    # S4 check 2: new layouts --------------------------------------------
    seeds = [("mixed", 6200), ("mixed", 6201), ("shortcut", 6300), ("shortcut", 6301)]
    names = [f"dev_{f}_{s}__corrected__p{CAL}" for f, s in seeds]
    tiles = [(0, 590, 540, 540), (540, 590, 540, 540), (0, 1130, 540, 540), (540, 1130, 540, 540)]
    ov = dict(lookat=(0.0, -0.02, 0.06), distance=1.30, azimuth=-58.0, elevation=-38.0)
    shots = [SimShot(ctx.src(n), t_start=0.0, duration=6.5, speed=6.0, move=Move(dict(ov)), trail_objects=["cup_0"])
             for n in names]

    def d4(d, infos):
        L.head(d, "Check 2. New layouts, same rule.", size=64)
        text_block(d, (60, 455), "Planned and run on 4 development layouts:", font("medium", 38), fill=INK, max_w=960)
        text_block(d, (60, 505), "cup upright, all 8 beads kept.", font("medium", 38), fill=GREEN, max_w=960)
        for (f, s), t, inf in zip(seeds, tiles, infos):
            chip(d, (t[0] + 14, t[1] + 14), f"dev layout {s}", font("medium", 24))
        chip(d, (24, 1190), SIM_LABEL + " · 6× speed", font("medium", 24))
    recs.append(run_grid(ctx, L, cut, "s4_new_layouts", shots, tiles, d4,
                         dict(src=[src_rec(ctx, n, t=[0, 39], speed=6.0) for n in names]), 6.5))

    # S5 check 3: blocked refusal ----------------------------------------
    bsrc = "dev_blocked_6400__original__p0.2445"
    bref = ctx.run("dev_blocked_6400__corrected__p0.2445")
    need, slot = certificate_numbers(bref["planning_outcome"]["detail"])
    lost = 8 - ctx.run(bsrc)["beads"][0][1]

    def d5(d, info):
        L.chips(d, [SIM_LABEL, "development blocked layout 6400 · the slot is the only way through"])
        L.head(d, "Check 3. No upright route?", size=64)
        if info.t_out < 3.0:
            text_block(d, (60, 455), f"Rules as first understood: completes, loses {lost} of 8 beads.",
                       font("medium", 38), fill=RED, max_w=960)
        a = fade(info.i, int(3.0 * FPS))
        if a:
            d.rectangle((0, 590, 1080, 1250), fill=(22, 22, 26, int(205 * a)))
            text_block(d, (60, 455), "Corrected rule: it plans nothing.", font("medium", 38), fill=A(INK, a), max_w=960)
            text_block(d, (60, 660), "Refused, with a proof", font("bold", 64), fill=A((255, 255, 255), a), max_w=960)
            text_block(d, (60, 760), "Proven infeasible under these stated assumptions:", font("medium", 36),
                       fill=A((235, 235, 235), a), max_w=960)
            text_block(d, (60, 830), f"an upright cup in the gripper needs {need * 100:.1f} cm of height; "
                       f"the only opening is {slot * 100:.1f} cm.", font("regular", 36), fill=A((235, 235, 235), a),
                       max_w=960)
            text_block(d, (60, 1000), "A statement about the simulated task model, not a physical-safety guarantee.",
                       font("regular", 28), fill=A((190, 190, 196), a), max_w=960)
    ts5 = spill_time(ctx.src(bsrc))
    shot = SimShot(ctx.src(bsrc), t_start=ts5 - 1.5, duration=6.5, speed=0.9, freeze_at=ts5 + 1.2,
                   move=Move(dict(SIDE_CAM)))
    recs.append(run_sim(ctx, L, cut, "s5_refusal", shot, d5,
                        dict(src=[src_rec(ctx, bsrc, t=[round(ts5 - 1.5, 2), round(ts5 + 1.2, 2)], speed=0.9, first_bead_lost=ts5),
                                  dict(source="summary.json run dev_blocked_6400__corrected__p0.2445",
                                       planning_outcome=bref["planning_outcome"]["kind"],
                                       headline=bref["planning_outcome"]["headline"])])))

    # S6 end ---------------------------------------------------------------
    def d6(d, i, t):
        text_block(d, (60, 420), "Correct the rule,", font("bold", 88), fill=INK, max_w=960)
        text_block(d, (60, 525), "not every route.", font("bold", 88), fill=INK, max_w=960)
        a = fade(i, int(0.8 * FPS))
        if a:
            text_block(d, (60, 700), "MIRROR · task authoring for a simulated robot", font("medium", 40),
                       fill=A(INK, a), max_w=960)
            text_block(d, (60, 770), STUDIO_URL, font("medium", 40), fill=A(RED, a), max_w=960)
            text_block(d, (60, 900), "MuJoCo simulation. Reference scenes and development layouts only.",
                       font("regular", 30), fill=A(MUTED, a), max_w=960)
    recs.append(run_card(ctx, L, cut, "s6_end", 4.0, d6, dict(src=dict(source="end card", url=STUDIO_URL))))
    return recs


# ================================================================== JUDGING
def judging_shots(ctx):
    L = Horizontal()
    cut = "judging"
    recs = []
    hook_src = "ref_blocked__original__p0.2445"
    t_spill = spill_time(ctx.src(hook_src))
    X, MW = 56, 420

    def col(d, y, text, f, fill=INK, mw=MW):
        return text_block(d, (X, y), text, f, fill=fill, max_w=mw)

    # J1 hook -------------------------------------------------------------
    def j1(d, info):
        L.chips(d, [SIM_LABEL + " · replay 0.75×", "reference blocked layout · rules as first understood"])
        y = col(d, 140, "It finished the task.", font("bold", 56))
        a = fade(info.i, int(round((t_spill - 5.6) / 0.75 * FPS)) - 3)
        if a:
            col(d, y + 10, "It also spilled half the beads.", font("bold", 56), fill=A(RED, a))
        bt, c = beads_text(info)
        if bt:
            col(d, 520, bt, font("medium", 34), fill=c)
        if info.tilt is not None:
            col(d, 570, f"cup tilt  {info.tilt:3.0f}°", font("medium", 34), fill=RED if info.tilt > 15 else INK)
    shot = SimShot(ctx.src(hook_src), t_start=5.6, duration=7.0, speed=0.75,
                   move=Move(dict(SIDE_CAM, elevation=-12.0), dict(SIDE_CAM, elevation=-18.0), 0.0, 7.0))
    recs.append(run_sim(ctx, L, cut, "j01_hook", shot, j1, dict(src=src_rec(ctx, hook_src, t=[5.6, 10.85], speed=0.75))))

    # J2 what MIRROR is ----------------------------------------------------
    demo_old = str(ctx.demos / "ordering" / "rollout_old.json")

    def j2(d, info):
        L.chips(d, [SIM_LABEL + " · 6× speed", "reference demo scene · the task as MIRROR understood it"])
        col(d, 120, "MIRROR", font("bold", 72))
        col(d, 220, "Task authoring for a simulated robot.", font("medium", 34))
        col(d, 330, "Demonstrate a task once. MIRROR writes down the goals and rules it thinks you meant. "
                    "Correct one of them in a sentence. Then check what the correction did.", font("regular", 30))
        col(d, 640, "Goal here: cup, block and marker into the tray. The cup holds 8 beads.", font("regular", 28),
            fill=MUTED)
    ov = dict(lookat=(0.02, -0.02, 0.06), distance=1.18, azimuth=-58.0, elevation=-34.0)
    shot = SimShot(demo_old, t_start=0.0, duration=8.5, speed=6.0, move=Move(dict(ov)))
    recs.append(run_sim(ctx, L, cut, "j02_what", shot, j2, dict(src=demo_rec(ctx, "ordering", "old", speed=6.0))))

    # J3 the correction ---------------------------------------------------
    prov = ctx.summary["spec_provenance"]
    sentence = prov["correction_text"]
    orig = ctx.summary["specs"]["original"]
    corr = ctx.summary["specs"]["corrected"]

    def rows(spec):
        out = []
        for g in spec.get("goals", []):
            if g.get("status") in ("confirmed", "proposed"):
                out.append((g["id"], g.get("description") or g["id"], g.get("status")))
        for c in spec.get("constraints", []):
            out.append((c["id"], c.get("description") or c["id"], c.get("status")))
        return out
    before = {r[0]: r[2] for r in rows(orig)}

    def j3(d, i, t):
        col(d, 120, "One sentence becomes one scoped rule.", font("bold", 50))
        col(d, 380, "Typed correction. Compiled by Muse Spark (recorded response, replayed). "
                    "The offline rules produce the same change.", font("regular", 26), fill=MUTED)
        col(d, 560, "Block and marker keep their freedom.", font("medium", 30))
        x0 = 600
        text_block(d, (x0, 110), "“" + sentence + "”", font("medium", 40), fill=INK, max_w=1240)
        y = 250
        text_block(d, (x0, y), f"SPECIFICATION  v{orig['version']} TO v{corr['version']}", font("medium", 24), fill=MUTED)
        y += 44
        a = fade(i, int(1.5 * FPS))
        for rid, desc, st in rows(corr):
            changed = before.get(rid) != st
            if rid.startswith("g_"):
                kind = "goal"
            else:
                kind = "rule"
            if changed:
                d.rectangle((x0 - 16, y - 8, 1880, y + 52), fill=A((255, 236, 236), a))
            text_block(d, (x0, y), kind, font("medium", 24), fill=MUTED)
            text_block(d, (x0 + 90, y), GOAL_WORDS.get(rid, desc)[:80], font("regular", 28), fill=INK, max_w=900)
            stx = 1560
            if changed:
                text_block(d, (stx - 60, y), f"was {before.get(rid)}, now", font("regular", 26), fill=A(MUTED, a))
                text_block(d, (stx + 150, y), st, font("bold", 26), fill=A(RED, a))
            else:
                text_block(d, (stx, y), st, font("regular", 26), fill=MUTED)
            y += 62
            d.line((x0, y - 12, 1880, y - 12), fill=RULE + (255,), width=1)
    recs.append(run_card(ctx, L, cut, "j03_correction", 11.0, j3,
                         dict(src=dict(source="summary.json specs + spec_diff", correction=sentence,
                                       model=prov.get("requested_model"),
                                       cassettes=[r.get("cassette_file") for r in prov.get("requests", [])]))))

    # J4 check 1 ---------------------------------------------------------
    rc = ctx.rechecks["ref_blocked"]

    def label(rule):
        return {"g_inside_cup": "cup in the tray", "g_inside_block": "block in the tray",
                "g_inside_marker": "marker in the tray", "c_upright_transport_cup": "cup upright while carried",
                "c_upright_final_cup": "cup upright in the tray"}.get(rule["rule_id"], rule["rule_id"])

    def j4(d, info):
        L.chips(d, [SIM_LABEL + " · the same run as the opening", f"re-judged under specification v{ctx.summary['specs']['corrected']['version']}"])
        y = col(d, 110, "Check 1. Re-judge the old run.", font("bold", 50))
        col(d, y + 16, "The checker reads the recorded physics log of the run that already happened. "
                       "Nothing is simulated again.", font("regular", 28))
        a = fade(info.i, int(5.0 * FPS))
        if a:
            yy = 560
            for r in rc["rules"]:
                if not r["enforced"]:
                    continue
                v = r["verdict"]
                c = RED if v == "violated" else (GREEN if v == "satisfied" else MUTED)
                text_block(d, (X, yy), label(r), font("regular", 26), fill=A(INK, a))
                text_block(d, (X + 320, yy), v, font("bold", 24), fill=A(c, a))
                yy += 46
            up = next(r for r in rc["rules"] if r["rule_id"] == "c_upright_transport_cup")
            secs = float(re.search(r"for ([0-9.]+) s", up["detail"]).group(1))
            text_block(d, (X, yy + 14), f"Tilted past 15° for {secs:.1f} s while carried.", font("medium", 28),
                       fill=A(RED, a), max_w=MW)
    shot = SimShot(ctx.src(hook_src), t_start=5.4, duration=12.0, speed=0.6, freeze_at=10.4,
                   move=Move(dict(SIDE_CAM, distance=0.70)))
    recs.append(run_sim(ctx, L, cut, "j04_rejudge", shot, j4,
                        dict(src=src_rec(ctx, hook_src, t=[5.4, 10.4], speed=0.6, freeze=10.4,
                                         recheck=dict(status=rc["status"], rules=[(r["rule_id"], r["verdict"]) for r in rc["rules"] if r["enforced"]])))))

    # J5 check 2 ---------------------------------------------------------
    from scripts.film.make_sources import TRANSFER
    names = [f"dev_{f}_{s}__corrected__p{CAL}" for f, s in TRANSFER]
    tw, th = 466, 540
    tiles = [(520 + (k % 3) * (tw + 1), (k // 3) * th, tw, th) for k in range(6)]
    ov2 = dict(lookat=(0.0, -0.02, 0.06), distance=1.38, azimuth=-58.0, elevation=-40.0)
    shots = [SimShot(ctx.src(n), t_start=0.0, duration=11.0, speed=4.8, move=Move(dict(ov2)), trail_objects=["cup_0"])
             for n in names]
    ok = sum(ctx.run(n)["status"] == "success" for n in names)
    kept = sum(ctx.run(n)["beads"][0][1] == ctx.run(n)["beads"][0][2] for n in names)

    def j5(d, infos):
        y = col(d, 110, "Check 2. Plan and run it on new layouts.", font("bold", 50))
        col(d, y + 16, f"Six development layouts, never used to write the rules. Planner price {CAL} m/rad, "
                       "set from measured execution time.", font("regular", 28))
        y2 = col(d, 600, f"{ok}/6 completed with every rule satisfied.", font("medium", 30), fill=GREEN)
        col(d, y2 + 10, f"{kept}/6 kept all 8 beads.", font("medium", 30), fill=GREEN)
        for (f, s), t in zip(TRANSFER, tiles):
            chip(d, (t[0] + 12, t[1] + 12), f"dev {f} {s}", font("medium", 22))
        chip(d, (530, 1030), SIM_LABEL + " · 4.8× speed · cup path coloured by tilt", font("medium", 22))
    recs.append(run_grid(ctx, L, cut, "j05_new_layouts", shots, tiles, j5,
                         dict(src=[src_rec(ctx, n, t=[0, 52.8], speed=4.8) for n in names]), 11.0))

    # J6 honest comparison ------------------------------------------------
    a_src, b_src = "ref_shortcut__original__p0.05", f"ref_shortcut__original__p{CAL}"
    ov3 = dict(lookat=(0.0, -0.04, 0.08), distance=0.95, azimuth=-62.0, elevation=-30.0)
    shots = [SimShot(ctx.src(a_src), t_start=2.0, duration=13.0, speed=1.6, move=Move(dict(ov3)), trail_objects=["cup_0"]),
             SimShot(ctx.src(b_src), t_start=2.0, duration=13.0, speed=1.6, move=Move(dict(ov3)), trail_objects=["cup_0"])]
    tiles = [(520, 0, 1400, 538), (520, 542, 1400, 538)]
    ba, bb = ctx.run(a_src)["beads"][0], ctx.run(b_src)["beads"][0]

    def j6(d, infos):
        y = col(d, 110, "Would it have tilted anyway?", font("bold", 50))
        y = col(d, y + 16, "Only when tilting is priced as nearly free. Both runs use the rules as first "
                           "understood, on the reference shortcut layout.", font("regular", 28))
        col(d, y + 20, f"In a frozen registration at {CAL} m/rad, the uncorrected plan stayed upright on all "
                       f"{REGISTERED['new_layouts']} new layouts. There the correction changed nothing.",
            font("medium", 28))
        chip(d, (540, 20), "planner set to treat tilting as nearly free · 0.05 m/rad", font("medium", 24), bg=RED)
        chip(d, (540, 562), f"planner price from measured time · {CAL} m/rad", font("medium", 24))
        for inf, (_, ty, _, _) in zip(infos, tiles):
            if inf.beads:
                k, n = inf.beads
                chip(d, (1660, ty + 20), f"beads {k}/{n}", font("medium", 24), bg=RED if k < n else CHIP)
        chip(d, (540, 1030), SIM_LABEL + " · 1.6× speed", font("medium", 22))
    recs.append(run_grid(ctx, L, cut, "j06_honest", shots, tiles, j6,
                         dict(src=[src_rec(ctx, a_src, t=[2, 22.8], speed=1.6, label="0.05 m/rad, labelled"),
                                   src_rec(ctx, b_src, t=[2, 22.8], speed=1.6),
                                   dict(source=REGISTERED["report"], quote=REGISTERED["quote_never"])]), 13.0))

    # J7 check 3 ---------------------------------------------------------
    from scripts.film.make_sources import BLOCKED
    names = [f"dev_blocked_{s}__original__p{CAL}" for s in BLOCKED]
    tiles = [(520 + k * 467, 0, 466, 520) for k in range(3)]
    tsp = [spill_time(ctx.src(n)) for n in names]
    shots = [SimShot(ctx.src(n), t_start=t - 1.8, duration=14.0, speed=0.5, freeze_at=t + 2.6,
                     move=Move(dict(SIDE_CAM, distance=0.72)), trail_objects=["cup_0"]) for n, t in zip(names, tsp)]
    pref = ctx.run(f"ref_blocked__corrected__p{CAL}")["planning_outcome"]
    need, slot = certificate_numbers(pref["detail"])
    refused = sum(ctx.run(f"dev_blocked_{s}__corrected__p{CAL}")["planning_outcome"]["kind"] == "proven_infeasible"
                  for s in BLOCKED)
    lost = [8 - ctx.run(n)["beads"][0][1] for n in names]

    def j7(d, infos):
        y = col(d, 110, "Check 3. No compliant route: refuse, with a proof.", font("bold", 50))
        col(d, y + 16, f"Three development layouts where the slot is the only opening. Top: the rules as first "
                       f"understood complete the task and lose {', '.join(map(str, lost))} beads.",
            font("regular", 28))
        col(d, 700, f"Frozen registration, {REGISTERED['layouts_blocked']} blocked layouts: uncorrected completed "
                    f"{REGISTERED['layouts_blocked']}/{REGISTERED['layouts_blocked']} and lost "
                    f"{REGISTERED['beads_lost_blocked']} beads at every price; corrected planned nothing, each with a proof.",
            font("medium", 26))
        for s, t in zip(BLOCKED, tiles):
            chip(d, (t[0] + 12, t[1] + 12), f"dev blocked {s}", font("medium", 22))
        for inf, t in zip(infos, tiles):
            if inf.beads:
                k, n = inf.beads
                chip(d, (t[0] + 12, t[1] + 470), f"beads {k}/{n}", font("medium", 22), bg=RED if k < n else CHIP)
        a = fade(infos[0].i, int(4.0 * FPS))
        d.rectangle((520, 524, 1920, 1080), fill=(22, 22, 26, 255))
        x0 = 560
        text_block(d, (x0, 560), f"Corrected rule on the same layouts: plans nothing ({refused}/3).",
                   font("medium", 30), fill=(235, 235, 235, 255), max_w=1320)
        if a:
            text_block(d, (x0, 618), pref["headline"], font("bold", 40), fill=A((255, 255, 255), a), max_w=1320)
            text_block(d, (x0, 680), f"An upright cup in the gripper needs {need * 100:.1f} cm of height; every "
                       f"opening in the wall is {slot * 100:.1f} cm, and there is no way around or over it.",
                       font("regular", 28), fill=A((225, 225, 230), a), max_w=1320)
            yy = 770
            text_block(d, (x0, yy), "Assumptions", font("medium", 24), fill=A((170, 170, 178), a))
            yy += 36
            for s in pref["assumptions"][:3]:
                yy = text_block(d, (x0, yy), "· " + s, font("regular", 22), fill=A((200, 200, 206), a), max_w=1320)
            text_block(d, (x0, yy + 6), "Not covered: " + "; ".join(pref["not_covered"]) +
                       ". A statement about the simulated task model, not a physical-safety guarantee.",
                       font("regular", 22), fill=A((170, 170, 178), a), max_w=1320)
    recs.append(run_grid(ctx, L, cut, "j07_refusal", shots, tiles, j7,
                         dict(src=[src_rec(ctx, n, t=[round(t - 1.8, 2), round(t + 2.6, 2)], speed=0.5, first_bead_lost=t) for n, t in zip(names, tsp)] +
                              [dict(source=f"summary.json dev_blocked_*__corrected__p{CAL}", refused=refused),
                               dict(source=REGISTERED["report"], quote=REGISTERED["quote_blocked"])]), 14.0))

    # J8 beyond upright: ordering and clearance ---------------------------
    o = ctx.demo["ordering"]
    c = ctx.demo["clearance"]
    pr = ctx.demo_manifest["planner_config"]["rotation_penalty"]
    ovd = dict(lookat=(0.04, -0.02, 0.05), distance=1.05, azimuth=-58.0, elevation=-42.0)

    def tr_counts(s):
        n = len(s["transfer"])
        return (sum(x["old_under_new"]["status"] == "constraint_violated" for x in s["transfer"]),
                sum(x["new"]["status"] == "success" for x in s["transfer"]), n)
    ov_o, nw_o, n_o = tr_counts(o)
    tiles = [(520, 0, 1400, 538), (520, 542, 1400, 538)]
    shots = [SimShot(str(ctx.demos / "ordering" / f"rollout_{w}.json"), t_start=0.0, duration=9.0, speed=6.0,
                     move=Move(dict(ovd))) for w in ("old", "new")]

    def j8a(d, infos):
        y = col(d, 110, "Not only upright.", font("bold", 50))
        y = col(d, y + 16, "“" + o["turns"][-1]["text"] + "”", font("medium", 34))
        col(d, y + 16, f"A new ordering rule, opposite to the demonstration. The old run, re-judged: violated. "
                       f"On {n_o} new layouts the old order breaks the rule {ov_o}/{n_o}; the new plan satisfies it "
                       f"{nw_o}/{n_o}.", font("regular", 28))
        col(d, 760, f"Offline rule interpreter · planner {pr} m/rad", font("regular", 24), fill=MUTED)
        for inf, (lab, ty) in zip(infos, (("before", 0), ("after", 542))):
            chip(d, (540, ty + 20), f"{lab}: now {phase_words(inf.phase)}", font("medium", 26),
                 bg=RED if lab == "before" else CHIP)
        chip(d, (540, 1030), SIM_LABEL + " · reference demo scene · 6× speed", font("medium", 22))
    recs.append(run_grid(ctx, L, cut, "j08_ordering", shots, tiles, j8a,
                         dict(src=[demo_rec(ctx, "ordering", "old", speed=6.0), demo_rec(ctx, "ordering", "new", speed=6.0),
                                   dict(transfer=dict(old_violates=ov_o, new_succeeds=nw_o, n=n_o))]), 9.0))

    ov_c, nw_c, n_c = tr_counts(c)
    mo = c["old"]["measures"]["cup_0"]["min_transport_clearance_m"]
    mn = c["new"]["measures"]["cup_0"]["min_transport_clearance_m"]
    shots = [SimShot(str(ctx.demos / "clearance" / f"rollout_{w}.json"), t_start=0.0, duration=8.0, speed=2.4,
                     move=Move(dict(ovd, distance=0.9)), trail_objects=["cup_0"]) for w in ("old", "new")]

    def j8b(d, infos):
        y = col(d, 110, "A new constraint.", font("bold", 50))
        y = col(d, y + 16, "“" + c["turns"][-1]["text"] + "”", font("medium", 34))
        col(d, y + 16, f"The old run came within {mo * 100:.1f} cm of the wall: violated. The new plan keeps "
                       f"{mn * 100:.1f} cm. New layouts: old violates {ov_c}/{n_c}, new satisfies {nw_c}/{n_c}.",
            font("regular", 28))
        col(d, 760, f"Offline rule interpreter · planner {pr} m/rad", font("regular", 24), fill=MUTED)
        chip(d, (540, 20), f"before: closest {mo * 100:.1f} cm", font("medium", 26), bg=RED)
        chip(d, (540, 562), f"after: closest {mn * 100:.1f} cm", font("medium", 26))
        chip(d, (540, 1030), SIM_LABEL + " · reference demo scene · 2.4× speed", font("medium", 22))
    recs.append(run_grid(ctx, L, cut, "j09_clearance", shots, tiles, j8b,
                         dict(src=[demo_rec(ctx, "clearance", "old", speed=2.4), demo_rec(ctx, "clearance", "new", speed=2.4),
                                   dict(transfer=dict(old_violates=ov_c, new_succeeds=nw_c, n=n_c),
                                        clearance_m=dict(old=mo, new=mn))]), 8.0))

    # J10 a question, not a guess -----------------------------------------
    q = ctx.demo["ambiguous"]["turns"][-1]
    u = ctx.demo["unsupported"]["turns"][-1]

    def j10(d, i, t):
        col(d, 120, "Vague or unsupported? It asks.", font("bold", 50))
        col(d, 380, "Questions come from the offline rule interpreter; nothing is guessed silently.",
            font("regular", 26), fill=MUTED)
        x0 = 600
        y = text_block(d, (x0, 120), "“" + q["text"] + "”", font("medium", 40), fill=INK, max_w=1240)
        a = fade(i, int(1.0 * FPS))
        y = text_block(d, (x0, y + 16), "MIRROR: " + q["question"], font("regular", 32), fill=A(INK, a), max_w=1240)
        a2 = fade(i, int(3.5 * FPS))
        y = text_block(d, (x0, y + 60), "“" + u["text"] + "”", font("medium", 40), fill=A(INK, a2), max_w=1240)
        text_block(d, (x0, y + 16), "MIRROR: " + u["question"], font("regular", 32), fill=A(INK, a2), max_w=1240)
    recs.append(run_card(ctx, L, cut, "j10_questions", 9.0, j10,
                         dict(src=dict(source="demo_corrections ambiguous + unsupported summary.json",
                                       interpreter=ctx.demo_manifest["interpreter"]))))

    # J11 how it works ---------------------------------------------------
    steps = [("Muse Spark", "reads the demonstration, proposes rules, compiles corrections (recorded here)"),
             ("Specification", "versioned goals, rules, ordering; each change scoped to an object and phase"),
             ("Planner", "routes that satisfy the enforced rules, or a proof that none exists"),
             ("MuJoCo physics", "actuators and contacts execute the plan; beads are simulated"),
             ("Checker", "judges any recorded run against any specification version")]

    def j11(d, i, t):
        col(d, 120, "How it works", font("bold", 50))
        col(d, 220, "Language changes the specification. The planner and an independent checker do the rest.",
            font("regular", 28))
        x0, y0, bw, bh, gap = 600, 140, 1260, 132, 32
        for k, (h, s) in enumerate(steps):
            a = fade(i, int((0.4 + 0.5 * k) * FPS))
            y = y0 + k * (bh + gap)
            d.rounded_rectangle((x0, y, x0 + bw, y + bh), radius=14, outline=A(INK, a), width=2)
            text_block(d, (x0 + 30, y + 22), h, font("bold", 36), fill=A(INK, a))
            text_block(d, (x0 + 30, y + 74), s, font("regular", 26), fill=A(MUTED, a), max_w=bw - 60)
            if k < len(steps) - 1:
                cx = x0 + 60
                d.line((cx, y + bh + 4, cx, y + bh + gap - 4), fill=A(INK, a), width=2)
    recs.append(run_card(ctx, L, cut, "j11_how", 9.0, j11, dict(src=dict(source="architecture card"))))

    # J12 limits ---------------------------------------------------------
    lim = ["Simulation only (MuJoCo). One controller; timing is simulated.",
           "The rotation price is calibrated to execution time only; it does not price spill risk.",
           "With that price, the upright correction changed behaviour only where no upright route existed.",
           "The correction-language benchmark was not pre-registered; ordering and clearance runs here are "
           "development demonstrations, not an evaluation.",
           "Video perception: 4 of 6 pre-registered checks were not met.",
           "No usability study has been run yet; the kit is prepared."]

    def j12(d, i, t):
        col(d, 120, "Limits", font("bold", 50))
        col(d, 220, "Full tables, failures and frozen protocols are on the evidence page.", font("regular", 28))
        y = 120
        for k, s in enumerate(lim):
            y = text_block(d, (600, y), "· " + s, font("regular", 32), fill=INK, max_w=1250) + 22
    recs.append(run_card(ctx, L, cut, "j12_limits", 10.0, j12, dict(src=dict(source="docs/CLAIMS.md, results/eval_objective_v1, results/eval_video2"))))

    # J13 end ------------------------------------------------------------
    def j13(d, i, t):
        text_block(d, (600, 330), "Correct the rule,", font("bold", 96), fill=INK)
        text_block(d, (600, 450), "not every route.", font("bold", 96), fill=INK)
        a = fade(i, int(0.8 * FPS))
        text_block(d, (600, 640), "MIRROR · " + STUDIO_URL, font("medium", 40), fill=A(RED, a))
        text_block(d, (600, 700), "Source and evidence: github.com/harsha-iiiv/MIRROR", font("medium", 34), fill=A(INK, a))
        text_block(d, (600, 790), "MuJoCo simulation. Reference scenes and development layouts only. "
                   "Captions only; no narration.", font("regular", 26), fill=A(MUTED, a), max_w=1250)
    recs.append(run_card(ctx, L, cut, "j13_end", 6.0, j13, dict(src=dict(source="end card", url=STUDIO_URL))))
    return recs


# ==================================================================== audio
def music_bed(path: Path, seconds: float, sr: int = 48000, seed: int = 7):
    """A quiet synthesized pad (sine partials, slow attack, two chords), written
    by this script; no third-party audio."""
    t = np.arange(int(seconds * sr)) / sr
    chords = [[146.83, 220.0, 293.66, 369.99], [123.47, 185.0, 246.94, 293.66]]   # D, Bm (low register)
    seg = 8.0
    out = np.zeros_like(t)
    for k in range(int(math.ceil(seconds / seg)) + 1):
        c = chords[k % 2]
        start = k * seg
        env = np.clip((t - start + 1.5) / 3.0, 0, 1) * np.clip((start + seg + 1.5 - t) / 3.0, 0, 1)
        if not env.any():
            continue
        for j, f in enumerate(c):
            out += env * (0.22 / (1 + j * 0.6)) * np.sin(2 * np.pi * f * t + j) * (1 + 0.15 * np.sin(2 * np.pi * 0.07 * t + j))
    fade = np.clip(t / 1.0, 0, 1) * np.clip((seconds - t) / 1.5, 0, 1)
    out *= fade
    out /= max(1e-9, np.abs(out).max())
    out *= 10 ** (-18 / 20)          # peak -18 dBFS: a bed, not a soundtrack
    st = np.stack([out, np.roll(out, int(0.011 * sr))], axis=1)
    pcm = (st * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path


def assemble(ctx, cut, recs):
    shots_dir = ctx.out / "shots" / cut
    lst = ctx.out / f"concat_{cut}.txt"
    lst.write_text("".join(f"file '{(shots_dir / (r['name'] + '.mp4')).resolve()}'\n" for r in recs))
    dur = sum(r["duration"] for r in recs)
    bed = music_bed(ctx.out / f"bed_{cut}.wav", dur)
    final = ctx.out / f"mirror_{cut}.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(bed),
                    "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "slow", "-crf", "18",
                    "-pix_fmt", "yuv420p", "-profile:v", "high", "-r", str(FPS), "-c:a", "aac", "-b:a", "160k",
                    "-shortest", "-movflags", "+faststart", str(final)], check=True, timeout=3600,
                   stdin=subprocess.DEVNULL)
    t = 0.0
    for r in recs:
        r["start"] = round(t, 3)
        t += r["duration"]
    meta = dict(cut=cut, file=final.name, duration=round(dur, 3), fps=FPS, size=None, shots=recs,
                sources_commit=ctx.summary["commit"], sources_platform=ctx.summary["platform"],
                demo_commit=ctx.demo_manifest["commit"], demo_platform=ctx.demo_manifest["platform"],
                spec_provenance={k: ctx.summary["spec_provenance"].get(k) for k in
                                 ("source", "requested_model", "correction_text", "offline_agrees")},
                registered=dict(REGISTERED, check=check_registered()),
                audio="synthesized pad (music_bed in scripts/film/build.py); no voice, captions only",
                build_commit=_git("rev-parse", "HEAD"))
    (ctx.out / f"timeline_{cut}.json").write_text(json.dumps(meta, indent=1, default=str))
    return final


def _git(*a):
    try:
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # pragma: no cover
        return ""


def fetch(url: str, dest: Path):
    """Download and unpack a .tar.gz of sources into ``dest`` (for machines
    without the source directory)."""
    import tarfile
    import urllib.request
    dest.mkdir(parents=True, exist_ok=True)
    tgz = dest / "in.tar.gz"
    urllib.request.urlretrieve(url, tgz)
    with tarfile.open(tgz) as tf:
        tf.extractall(dest)
    tgz.unlink()
    return dest


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default=None)
    ap.add_argument("--demos", default=None)
    ap.add_argument("--fetch", default=None, help="URL of a .tar.gz holding sources/, demos/ and fonts/")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cut", default="both", choices=["social", "judging", "both"])
    ap.add_argument("--max-frames", type=int, default=0, help="smoke test: render at most N frames per shot")
    ap.add_argument("--sample", default="", help="review: comma list of shot fractions to render (e.g. 0.1,0.5,0.95)")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.fetch:
        base = fetch(a.fetch, out / "inputs")
        sources, demos = base / "sources", base / "demos"
        if (base / "fonts").exists():
            from scripts.film import compose
            compose.FONT_DIRS.insert(0, str(base / "fonts"))
    else:
        sources, demos = Path(a.sources), Path(a.demos)
    if a.max_frames:
        from scripts.film import compose
        compose.MAX_FRAMES = a.max_frames
    if a.sample:
        from scripts.film import compose
        compose.SAMPLE = [float(x) for x in a.sample.split(",")]
    ctx = Ctx(sources, demos, out)
    t0 = time.time()
    finals = []
    for cut, fn in (("social", social_shots), ("judging", judging_shots)):
        if a.cut not in (cut, "both"):
            continue
        ctx.camlog = []
        recs = fn(ctx)
        write_camlog(out / f"camlog_{cut}.jsonl", ctx.camlog)
        finals.append(assemble(ctx, cut, recs))
        print(f"{cut}: {finals[-1]} ({time.time() - t0:.0f} s)", flush=True)
    if a.fetch:
        import shutil
        shutil.rmtree(out / "inputs", ignore_errors=True)
    print("done", [str(f) for f in finals])


if __name__ == "__main__":
    main()
