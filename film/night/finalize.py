"""Finish the night film: quiet synthesized pad + three soft cues muxed under the Remotion render, stills, checksums.

    python film/night/finalize.py --video film.mp4 --shots film/night/shots_film.json --out DIR

The pad is the v2 film's music_bed (scripts/film/build.py), copied here so this script needs no simulator imports;
no third-party audio, no voice. Cues: the gate refusal and the two verdicts of the re-judge shot (times derived from
the shot list and the scenes' own entrance frames).
"""
from __future__ import annotations
import argparse, hashlib, json, math, subprocess, wave
from pathlib import Path
import numpy as np

SR = 48000


def music_bed(seconds: float, seed: int = 7) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
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
    return out * 10 ** (-18 / 20)


def knock(freq: float, dur: float = 0.35) -> np.ndarray:
    """Soft wooden knock: two low partials with a fast exponential decay, under 1 kHz."""
    t = np.arange(int(dur * SR)) / SR
    env = np.exp(-t / 0.06) * np.clip(t / 0.004, 0, 1)
    return env * (0.8 * np.sin(2 * np.pi * freq * t) + 0.25 * np.sin(2 * np.pi * freq * 2.01 * t))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True); ap.add_argument("--shots", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--name", default="mirror_night_film_v3")
    a = ap.parse_args(argv)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    shots = json.loads(Path(a.shots).read_text())
    start = {}; acc = 0
    for s in shots:
        start[s["name"]] = acc; acc += s["frames"]
    dur = acc / 30
    bed = music_bed(dur)
    # cue frames: gate card enters at +14; re-judge verdicts at tOld+80 and tNew+80 (scenes2.tsx Rejudge defaults 20 / 150)
    cues = [(start["gate"] + 14, 196.0, -26), (start["rejudge"] + 100, 174.6, -24), (start["rejudge"] + 230, 220.0, -24)]
    for fr, f, db in cues:
        k = knock(f) * 10 ** (db / 20)
        i = int(fr / 30 * SR)
        bed[i:i + k.size] += k[: max(0, bed.size - i)]
    bed = np.clip(bed, -0.98, 0.98)
    st = np.stack([bed, np.roll(bed, int(0.011 * SR))], axis=1)
    wav = out / "bed.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes((st * 32767).astype("<i2").tobytes())
    final = out / f"{a.name}.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", a.video, "-i", str(wav), "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(final)], check=True, timeout=600,
                   stdin=subprocess.DEVNULL)
    stills = {}
    picks = {"watch": 0.62, "gate": 0.8, "run": 0.7, "twin": 0.7, "sentence": 0.9, "rejudge": 0.93, "ghosts": 0.6, "table": 0.9}
    (out / "stills").mkdir(exist_ok=True)
    for nm, q in picks.items():
        fr = start[nm] + int(q * next(s["frames"] for s in shots if s["name"] == nm))
        p = out / "stills" / f"{nm}.png"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", a.video, "-vf", f"select=eq(n\\,{fr})", "-frames:v", "1", str(p)],
                       check=True, timeout=300, stdin=subprocess.DEVNULL)
        stills[nm] = dict(file=f"stills/{nm}.png", frame=fr, t_s=round(fr / 30, 2))
    meta = dict(file=final.name, duration_s=dur, fps=30, size=[1920, 1080], audio="synthesized pad (v2 music_bed) + three soft knocks; no voice",
                cues_s=[round(c[0] / 30, 2) for c in cues], stills=stills,
                sha256=hashlib.sha256(final.read_bytes()).hexdigest())
    (out / f"{a.name}.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps(meta)[:400])


if __name__ == "__main__":
    main()
