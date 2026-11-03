#!/usr/bin/env python
"""Write SHOTLIST_<cut>.md from timeline_<cut>.json (and motion_<cut>.json if present).

    python scripts/film/shotlist.py <film dir> <cut>
"""
import json
import sys
from pathlib import Path


def fmt_src(s):
    if not isinstance(s, dict):
        return str(s)
    if "scene" in s:
        bits = [s["source"], f"scene {s['scene']}"]
        if s.get("seed") is not None:
            bits.append(f"seed {s['seed']}")
        bits += [f"spec v{s['spec_version']}", f"planner {s['rotation_price']} m/rad", f"status {s['status']}"]
        if s.get("beads"):
            bits.append("beads " + ", ".join(f"{b[1]}/{b[2]}" for b in s["beads"]))
        if s.get("t"):
            bits.append(f"t {s['t'][0]}-{s['t'][1]} s at {s.get('speed', 1)}x")
        if s.get("freeze") is not None:
            bits.append(f"held at {s['freeze']} s")
        if s.get("interpreter"):
            bits.append(s["interpreter"])
        bits.append(f"commit {str(s.get('commit'))[:10]}")
        return "; ".join(bits)
    return "; ".join(f"{k}: {v}" for k, v in s.items())


def main():
    root, cut = Path(sys.argv[1]), sys.argv[2]
    tl = json.loads((root / f"timeline_{cut}.json").read_text())
    mo = root / f"motion_{cut}.json"
    motion = json.loads(mo.read_text()) if mo.exists() else None
    L = [f"# MIRROR {cut} cut: shot list", "",
         f"File `{tl['file']}`, {tl['duration']:.2f} s, {tl['fps']} fps. Built at {tl['build_commit'][:10]}. "
         f"Simulation sources: commit {tl['sources_commit'][:10]} on {tl['sources_platform']}; correction "
         f"demonstrations: commit {tl['demo_commit'][:10]} ({tl['demo_platform']['system']} {tl['demo_platform']['machine']}).",
         f"Specification pair: {tl['spec_provenance']['source']} (model {tl['spec_provenance']['requested_model']}, "
         f"offline interpreter agrees: {tl['spec_provenance']['offline_agrees']}).",
         f"Registered figures quoted from `{tl['registered']['report']}` ({tl['registered']['check']}).",
         f"Audio: {tl['audio']}.", "",
         "| # | shot | start | dur | source | camera |", "|---|---|---|---|---|---|"]
    for k, sh in enumerate(tl["shots"], 1):
        srcs = sh.get("src")
        srcs = srcs if isinstance(srcs, list) else [srcs]
        cam = ""
        if motion and sh["name"] in motion["camera"]:
            c = motion["camera"][sh["name"]]
            cam = "card" if c["kind"] == "card" else (
                f"{c['moves']} move; az {c['total']['azimuth']:+.1f}°, el {c['total']['elevation']:+.1f}°, "
                f"zoom {c['total']['distance']:+.3f} m")
        L.append(f"| {k} | {sh['name']} | {sh['start']:.2f} | {sh['duration']:.2f} | "
                 + "<br>".join(fmt_src(s) for s in srcs) + f" | {cam} |")
    if motion:
        L += ["", "## Motion check", "", "Flags: " + ("none" if not motion["flags"] else "; ".join(motion["flags"])), ""]
        L.append("| shot | pairs | max image scale change | max shift px |")
        L.append("|---|---|---|---|")
        for n, r in motion["pixels"].items():
            L.append(f"| {n} | {r['pairs']} | {r['max_scale_change']:.4f} | {r['max_shift_px']:.1f} |")
    (root / f"SHOTLIST_{cut}.md").write_text("\n".join(L) + "\n")
    print(root / f"SHOTLIST_{cut}.md")


if __name__ == "__main__":
    main()
