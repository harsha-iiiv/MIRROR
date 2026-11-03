#!/usr/bin/env python
"""Export a self-contained static studio (no backend) for GitHub Pages.

It drives one studio session through the reference story, or loads an
existing snapshot, then writes the web frontend plus precomputed JSON:

    site/index.html, styles.css, js/*, config.js (static mode)
    site/data/session.json         snapshot shown by the studio
    site/data/rollouts/<id>.json   playback for every rollout
    site/data/provenance.json      how and when the data was produced

Usage:
    python scripts/export_static.py --out site                # live simulation (default)
    python scripts/export_static.py --out site --evaluation results/eval/final   # show the pre-registered results
    python scripts/export_static.py --out site --backend fixture --allow-fixture   # interface preview only

The exporter never generates evaluation-split layouts. Its own paired run (when
no --evaluation is given) uses the showcase development seeds 9000-9099;
pre-registered results are loaded from the files the evaluation package wrote.

Fixture data is key-framed placeholder motion; it is refused unless
--allow-fixture is given, and the exported studio labels it on every page.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mirror.studio.session_adapter import TOUR_DIR, tour_record  # noqa: E402
from mirror.studio.session_adapter import (REFERENCE_CORRECTION, SHOWCASE_SEEDS, SHOWCASE_SPLIT,  # noqa: E402
                                           StudioService, check_showcase)

WEB = ROOT / "web"


def log(msg: str) -> None:
    print(f"[export] {msg}", flush=True)


def run_story(svc: StudioService, *, seeds: list[int], clarify: bool, blocked: bool,
              video: str = "auto", voice_wav: Optional[Path] = None) -> list[dict]:
    steps = []

    def optional(j, what):
        """Steps after the core story are recorded even when they fail; the site shows what exists."""
        try:
            return job(j, what)
        except RuntimeError as exc:
            log(f"WARNING: {exc}")
            return None

    def job(j, what):
        t0 = time.time()
        svc.wait(j, timeout=3600)
        steps.append({"step": what, "status": j.status, "error": j.error, "seconds": round(time.time() - t0, 1)})
        log(f"{what}: {j.status}{' ' + j.error if j.error else ''} ({time.time() - t0:.0f} s)")
        if j.status != "done":
            raise RuntimeError(f"{what} failed: {j.error}")
        return j.result

    cap = svc.capabilities()
    use_video = video == "on" or (video == "auto" and cap.get("video") and cap.get("bundled_video"))
    if use_video:
        # The story starts from the bundled demonstration video: tracking, observations, proposal.
        job(svc.process_video("bundled"), "video demonstration (tracking, observations, proposal)")
    else:
        svc.load_demo()
        log("demonstration loaded")
        job(svc.interpret(), "interpret")
    # The human review: confirm every goal and the cup's final-upright rule, as in the evaluation.
    spec = svc.session.spec
    confirm = {g.id: "confirmed" for g in spec.goals if g.status.value != "confirmed"}
    for c in spec.constraints:
        if (c.type == "upright" and c.phase == "final" and c.applies_to.kind == "cup"
                and c.status.value != "confirmed"):
            confirm[c.id] = "confirmed"
    if confirm:
        svc.commit_decisions(confirm)
        steps.append({"step": "confirm " + ", ".join(confirm), "status": "done", "error": None, "seconds": 0})
        log(f"confirmed {len(confirm)} items -> v{svc.session.spec.version}")
    job(svc.run(condition="demonstration layout", tags=["run"]), "run on the demonstration layout")
    svc.set_scene_preset("shortcut")
    job(svc.run(condition="changed world", tags=["world"]), "run on the shortcut layout")
    text, tid = REFERENCE_CORRECTION, None
    if voice_wav is not None:
        tr = job(svc.transcribe(Path(voice_wav).read_bytes()), "spoken correction: transcription")
        text, tid = tr["text"], tr["id"]
        log(f"transcript: {text!r}")
    res = job(svc.correct(text, transcript_id=tid), "correction" + (" (spoken)" if tid else ""))
    if not res.get("changed"):
        raise RuntimeError(f"the reference correction did not change the specification: {res}")
    job(svc.run(condition="corrected", tags=["corrected"]), "corrected run on the shortcut layout")
    if clarify:
        optional(svc.clarify(), "clarifying question")
    if seeds:
        optional(svc.evaluate(seeds, split=SHOWCASE_SPLIT), f"showcase paired run on {len(seeds)} development layouts")
    if blocked:
        svc.set_scene_preset("blocked")
        optional(svc.run(condition="blocked layout", tags=["blocked"]), "blocked layout")
    svc.set_scene_preset("shortcut")
    return steps


def code_digest() -> str:
    h = hashlib.sha256()
    for p in sorted((ROOT / "mirror").rglob("*.py")):
        h.update(p.relative_to(ROOT).as_posix().encode())
        h.update(p.read_bytes())
    return h.hexdigest()


CALIBRATION = ROOT / "assets" / "calibration"


def copy_calibration(out: Path) -> list[str]:
    """Printable calibration targets at <site>/calibration/ (the capture guide links to them)."""
    if not CALIBRATION.exists():
        return []
    dst = out / "calibration"
    dst.mkdir(parents=True, exist_ok=True)
    names = []
    for p in sorted(CALIBRATION.iterdir()):
        if p.suffix in (".pdf", ".png", ".json") and p.is_file():
            shutil.copyfile(p, dst / p.name)
            names.append(p.name)
    return names


def copy_capture_sample(out: Path, sample: Path) -> None:
    """Ship a capture session as the wizard's review sample, only if it is labelled SYNTHETIC."""
    s = json.loads(Path(sample).read_text())
    if s.get("synthetic") is not True or s.get("evidence_kind") != "synthetic_test_render":
        raise SystemExit("--capture-sample must be a SYNTHETIC session (synthetic: true, evidence_kind synthetic_test_render)")
    dst = out / "data" / "capture"
    dst.mkdir(parents=True, exist_ok=True)
    s.setdefault("notes", []).append("SYNTHETIC test render shipped as a review sample: not real footage.")
    (dst / "synthetic_session.json").write_text(json.dumps(s, indent=1) + "\n")


def write_site(svc: StudioService, out: Path, steps: list[dict], extra: dict) -> None:
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(WEB, out)
    copy_calibration(out)
    data = out / "data"
    (data / "rollouts").mkdir(parents=True)
    snap = svc.snapshot()
    snap["jobs"] = []
    snap["static"] = True
    snap["provenance"] = "recorded"     # the site replays this session; nothing is computed when it is viewed
    # The site replays recordings: calls that were live while recording are recorded responses here.
    for m in snap.get("models") or []:
        if m.get("source") == "live":
            m["source"], m["recorded_live"] = "recorded", True
    for tr in snap.get("transcripts") or []:
        if tr.get("source") == "live":
            tr["source"] = "recorded"
    media = data / "media"
    vd = snap.get("video_demo")
    if vd:
        vd["run"] = "recorded"           # the site replays one recorded run
        media.mkdir(exist_ok=True)
        for view, v in vd.get("views", {}).items():
            name = f"demo_{view}.mp4"
            shutil.copyfile(svc.video_file(v["id"]), media / name)
            v["file"] = name
    for tr in snap.get("transcripts") or []:
        src = svc.transcript_audio(tr["id"])
        if src:
            media.mkdir(exist_ok=True)
            shutil.copyfile(src, media / f"{tr['id']}.wav")
            tr["audio_file"] = f"{tr['id']}.wav"
    # Rollout files are cached as immutable; the page asks for them with ?v=<export id>.
    snap["export_id"] = hashlib.sha256(f"{time.time_ns()}:{len(snap['rollouts'])}".encode()).hexdigest()[:12]
    (data / "session.json").write_text(json.dumps(snap, separators=(",", ":")))
    # The recorded Challenge-mode tour (no live inference), labelled as a recording.
    tour = tour_record()
    if tour is not None:
        (data / "tour" / "rollouts").mkdir(parents=True)
        (data / "tour" / "record.json").write_text(json.dumps(tour, separators=(",", ":")))
        src = TOUR_DIR / tour["tour_version"] / "rollouts"
        for f in sorted(src.glob("*.json")):
            shutil.copyfile(f, data / "tour" / "rollouts" / f.name)
        snap["tour"] = {"file": "tour/record.json", "record_id": tour.get("record_id"),
                        "version": tour.get("tour_version"), "provenance": "recorded", "label": tour.get("label")}
        (data / "session.json").write_text(json.dumps(snap, separators=(",", ":")))
    ids = [r["id"] for r in snap["rollouts"]] + (["demo"] if snap.get("demo") else [])
    if (snap.get("demo") or {}).get("playback_id"):
        ids.append(snap["demo"]["playback_id"])         # the immutable id the alias points to
    svc.payload_digits = 3          # 1 mm and 0.001 quaternion steps: smaller files, same picture
    svc.payload_cache.clear()
    total = 0
    for rid in ids:
        payload = svc.rollout_payload(rid)
        text = json.dumps(payload, separators=(",", ":"))
        total += len(text)
        (data / "rollouts" / f"{rid}.json").write_text(text)
    prov = {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "backend": svc.mode,
        "llm_mode": os.environ.get("MIRROR_LLM_MODE", "live"),
        "llm_model": os.environ.get("MIRROR_LLM_MODEL", "muse-spark-1.3"),
        "interpreter": snap["specs"][0].get("interpreter") if snap["specs"] else None,
        "interpreter_fallbacks": snap.get("fallbacks", []),
        "code_sha256": code_digest(),
        "steps": steps,
        "rollouts": len(ids),
        **extra,
    }
    (data / "provenance.json").write_text(json.dumps(prov, indent=1))
    cfg = {"static": True, "base": "data/", "apiBase": extra.get("api_base") or "",
           "wsBase": extra.get("ws_base") or ""}
    if extra.get("repo_url"):
        cfg["repo"] = extra["repo_url"]
    (out / "config.js").write_text(config_js(cfg))
    write_hosting_files(out, cfg["apiBase"], cfg["wsBase"])
    (out / ".nojekyll").write_text("")
    log(f"wrote {out} ({len(ids)} rollouts, {total / 1e6:.1f} MB of playback data)")




WEB_DIR = ROOT / "web"
NO_CACHE = "no-cache"
IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "public, max-age=0, must-revalidate"
CDN_SCRIPTS = ("https://cdn.jsdelivr.net", "https://esm.sh")


def inline_script_hashes(index_html: str) -> list[str]:
    """CSP hashes of the page's inline scripts (the import map)."""
    import base64
    import hashlib
    import re
    out = []
    for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", index_html, re.S):
        out.append("'sha256-" + base64.b64encode(hashlib.sha256(m.group(1).encode()).digest()).decode() + "'")
    return out


def header_rules(index_html: str, api_base: str = "", ws_base: str = "", generic: bool = False) -> list:
    """Response headers for the static site, as (path pattern, {header: value}) pairs."""
    if generic:
        connect = ["'self'", "https:", "wss:"]
    else:
        connect = ["'self'"]
        if api_base:
            connect += [api_base, ws_base or "ws" + api_base[4:]]
    live = generic or bool(api_base)
    media = ["'self'", "data:", "blob:"] + ([api_base] if api_base and not generic else (["https:"] if generic else []))
    csp = "; ".join([
        "default-src 'self'",
        "script-src 'self' " + " ".join(inline_script_hashes(index_html) + list(CDN_SCRIPTS)),
        "style-src 'self' 'unsafe-inline'",
        "img-src " + " ".join(media),
        "media-src " + " ".join(media),
        "font-src 'self'",
        "connect-src " + " ".join(connect),
        "worker-src 'self' blob:",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ])
    security = {
        "Content-Security-Policy": csp,
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        # the live studio records spoken corrections, so it needs the microphone (this origin only)
        "Permissions-Policy": "camera=(), microphone=" + ("(self)" if live else "()") + ", geolocation=(), payment=(), usb=()",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Strict-Transport-Security": "max-age=31536000",
    }
    return [
        ("/*", security),
        ("/", {"Cache-Control": NO_CACHE}),
        ("/index.html", {"Cache-Control": NO_CACHE}),
        ("/config.js", {"Cache-Control": NO_CACHE}),
        ("/data/session.json", {"Cache-Control": NO_CACHE}),
        ("/data/provenance.json", {"Cache-Control": NO_CACHE}),
        ("/data/rollouts/*", {"Cache-Control": IMMUTABLE}),
        ("/data/tour/record.json", {"Cache-Control": REVALIDATE}),
        ("/data/tour/rollouts/*", {"Cache-Control": IMMUTABLE}),
        ("/data/media/*", {"Cache-Control": "public, max-age=86400"}),
        ("/js/*", {"Cache-Control": REVALIDATE}),
        ("/styles.css", {"Cache-Control": REVALIDATE}),
    ]


def netlify_toml(rules: list, generic: bool = False) -> str:
    lines = ["# Netlify configuration for the MIRROR studio static site.",
             "# The site is prebuilt by scripts/export_static.py; Netlify only serves it.",
             "# Rollout files are immutable per export (the page requests them with ?v=<export id>);",
             "# the page, its configuration and the session index are always revalidated."]
    if generic:
        lines.append("# This copy allows any https/wss API; the exporter writes a copy limited to the configured server.")
    lines += ["", "[build]", '  publish = "."', '  command = ""', ""]
    for path, headers in rules:
        lines.append("[[headers]]")
        lines.append(f'  for = "{path}"')
        lines.append("  [headers.values]")
        for k, v in headers.items():
            lines.append(f"    {k} = {json.dumps(v)}")
        lines.append("")
    return "\n".join(lines)


def netlify_headers_file(rules: list) -> str:
    out = ["# Netlify _headers (same rules as netlify.toml; used for drag-and-drop deploys)"]
    for path, headers in rules:
        out.append(path)
        out += [f"  {k}: {v}" for k, v in headers.items()]
    return "\n".join(out) + "\n"


def write_hosting_files(out: Path, api_base: str = "", ws_base: str = "") -> None:
    rules = header_rules((WEB_DIR / "index.html").read_text(), api_base, ws_base)
    (out / "netlify.toml").write_text(netlify_toml(rules))
    (out / "_headers").write_text(netlify_headers_file(rules))

CONFIG_PREFIX = "window.MIRROR_CONFIG = "
CONFIG_HEADER = ("// MIRROR studio configuration (written by scripts/export_static.py).\n"
                 "// apiBase: origin of a live studio server for the 'Live simulation' switch;\n"
                 "// leave empty to publish the recordings only.\n")


def config_js(cfg: dict) -> str:
    """The config.js file for a static site."""
    return CONFIG_HEADER + CONFIG_PREFIX + json.dumps(cfg, indent=1) + ";\n"


def parse_config_js(text: str) -> dict:
    """Read a config.js written by config_js (comments allowed before the assignment)."""
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//")).strip()
    if not body.startswith(CONFIG_PREFIX) or not body.endswith(";"):
        raise ValueError("config.js must be a single 'window.MIRROR_CONFIG = {...};' assignment")
    cfg = json.loads(body[len(CONFIG_PREFIX):-1])
    if not isinstance(cfg, dict):
        raise ValueError("MIRROR_CONFIG must be an object")
    return cfg


def check_origin(url: str, schemes: tuple) -> str:
    """'' or a bare origin (optionally with a path) such as https://studio.example.org."""
    from urllib.parse import urlparse
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    u = urlparse(url)
    if u.scheme not in schemes or not u.netloc or u.query or u.fragment or "@" in u.netloc:
        raise ValueError(f"not a valid {'/'.join(schemes)} origin: {url!r}")
    return url


def write_config(site: Path, api_base: str, ws_base: str = "") -> dict:
    """Point an existing static site at a live server (or clear it) without re-exporting."""
    path = Path(site) / "config.js"
    cfg = parse_config_js(path.read_text())
    cfg["apiBase"] = check_origin(api_base, ("https", "http"))
    cfg["wsBase"] = check_origin(ws_base, ("wss", "ws"))
    path.write_text(config_js(cfg))
    write_hosting_files(Path(site), cfg["apiBase"], cfg["wsBase"])
    return cfg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="site", help="output folder (replaced)")
    ap.add_argument("--backend", choices=["live", "fixture"], default="live")
    ap.add_argument("--allow-fixture", action="store_true", help="permit exporting key-framed fixture data")
    ap.add_argument("--seeds", type=int, default=8,
                    help="showcase paired run on this many development layouts when no --evaluation is given (0 to skip)")
    ap.add_argument("--seed-base", type=int, default=SHOWCASE_SEEDS.start,
                    help=f"first showcase seed ({SHOWCASE_SEEDS.start}-{SHOWCASE_SEEDS.stop - 1})")
    ap.add_argument("--repo-url", default="https://github.com/harsha-iiiv/MIRROR")
    ap.add_argument("--video", choices=["auto", "on", "off"], default="auto",
                    help="start the story from the bundled demonstration video (auto: when available)")
    ap.add_argument("--voice-wav", type=Path, default=None,
                    help="speak the correction: a 16 kHz mono WAV, transcribed and then compiled")
    ap.add_argument("--api-base", default="", help="live studio server origin for the 'Live simulation' switch")
    ap.add_argument("--ws-base", default="", help="websocket origin, if different from --api-base")
    ap.add_argument("--write-config", metavar="SITE", default=None,
                    help="only rewrite SITE/config.js with --api-base/--ws-base, then exit")
    ap.add_argument("--capture-sample", type=Path, default=None,
                    help="a SYNTHETIC capture session.json (python -m mirror.capture.ingest demo DIR) shown as the review sample")
    ap.add_argument("--no-clarify", action="store_true")
    ap.add_argument("--no-blocked", action="store_true")
    ap.add_argument("--evaluation", default=None,
                    help="show a saved evaluation report (JSON file or its folder) instead of running one")
    args = ap.parse_args(argv)
    try:
        api_base = check_origin(args.api_base, ("https", "http"))
        ws_base = check_origin(args.ws_base, ("wss", "ws"))
    except ValueError as exc:
        ap.error(str(exc))
    if args.write_config:
        cfg = write_config(Path(args.write_config), api_base, ws_base)
        log(f"wrote {Path(args.write_config) / 'config.js'}: apiBase={cfg['apiBase'] or '(none)'}")
        return 0
    if args.backend == "fixture" and not args.allow_fixture:
        ap.error("fixture data is placeholder motion, not simulation; pass --allow-fixture to export it anyway")
    if os.environ.get("MIRROR_LLM_MODE") == "replay" and not os.environ.get("MIRROR_LLM_MODEL"):
        # Replay the recorded responses: request the model they were recorded with.
        recorded = sorted({(json.loads(p.read_text()).get("request") or {}).get("model")
                           for p in (ROOT / "data" / "cassettes").glob("*.json")
                           if json.loads(p.read_text()).get("name") == "propose_spec"} - {None})
        if recorded:
            os.environ["MIRROR_LLM_MODEL"] = recorded[0]
            log(f"replaying recorded responses of {recorded[0]}")
    svc = StudioService(backend=args.backend)
    if svc.mode == "fixture" and not args.allow_fixture:
        ap.error("simulation packages unavailable; refusing to export fixture data without --allow-fixture")
    seeds = list(range(args.seed_base, args.seed_base + args.seeds))
    if seeds and not args.evaluation:
        try:
            check_showcase(seeds, SHOWCASE_SPLIT)
        except RuntimeError as exc:
            ap.error(str(exc))
    t0 = time.time()
    steps = run_story(svc, seeds=[] if args.evaluation else seeds,
                      clarify=not args.no_clarify, blocked=not args.no_blocked,
                      video=args.video, voice_wav=args.voice_wav)
    if args.evaluation:
        svc.load_evaluation(args.evaluation)
        steps.append({"step": f"loaded evaluation report {Path(args.evaluation).name}", "status": "done",
                      "error": None, "seconds": 0})
        log(f"loaded evaluation report {args.evaluation}")
    write_site(svc, Path(args.out), steps, {"showcase_seeds": [] if args.evaluation else seeds,
                                            "evaluation": Path(args.evaluation).name if args.evaluation else None,
                                            "repo_url": args.repo_url, "api_base": api_base, "ws_base": ws_base,
                                            "wall_time_s": round(time.time() - t0, 1)})
    if args.capture_sample:
        copy_capture_sample(Path(args.out), args.capture_sample)
    failed = [s["step"] for s in steps if s["status"] != "done"]
    if failed:
        log("incomplete export, failed steps: " + "; ".join(failed))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
