"""One command from your phone clips to a checked robot run (real footage).

    python -m mirror.capture.real_run --lens LENS.MOV --take TASK.MOV \
        --measurements m.json --out DIR [--mode record|replay|offline] [--cap-usd X] \
        [--answers answers.json] [--seed-t S] [--intrinsics I.json]

What it does, unattended and resumable (each stage writes its outputs and a
marker in DIR/real_run.json; a second invocation continues where the first
stopped):

  watch    probe the clips, calibrate the lens (ChArUco, every 15th frame),
           find the board-at-rest windows and the sync-card events, pick a
           clean seed frame, ground the objects on it (Muse Spark), track them
           through the take (SAM 3.1, forward and backward, re-seeding lost
           objects; static obstacles segmented once), build the capture session,
           and write the review as a machine-readable questions file.
  answers  (only once DIR/answers.json or --answers exists, answers written by
           the person) apply the confirmations through the capture API and
           rebuild the session.
  task     gate (CaptureSession.gate(), never bypassed). Gate passed: propose a
           task from the capture (Muse Spark; offline rules in offline mode) and
           wait for the person's review of the proposed rules. Gate refused: the
           refusal is reported; if the person stated the task, that stated task
           is used and labelled as such (never as MIRROR's inference).
  robot    scene in the real table frame (board origin; software-shifted with a
           label only if the layout is out of the robot's reach), plan, execute,
           render; then the person's correction (or a labelled rehearsal),
           replan, re-check the first run under the corrected rule, render the
           before/after.

Model calls go through MIRROR's Meta client and LLM client: record mode saves
every response as a cassette (and charges the spend ledger, with a hard cap
checked before each call); replay mode serves cassettes only and installs a
transport that fails any network request; offline mode uses no model at all.
"""
from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

VERSION = 1
STAGES = ("probe", "lens", "scan", "seed", "ground", "track", "session", "report",
          "answers", "task", "scene", "run", "render", "correct", "publish")
WATCH_STAGES = STAGES[:8]
LABELS_REAL = ["Real phone footage", "One camera (single view, reduced)"]
STOP_FRACTION = 0.95                    # stop before any call that could cross 95 % of the cap
LOCK = os.environ.get("MIRROR_RENDER_LOCK", "")
PROXY_CRF = 18                          # proxy video for SAM (D1 recording settings)


class StageStop(Exception):
    """The run stops here and waits for something (answers, review)."""


class SpendCapReached(RuntimeError):
    pass


class LiveCallInReplay(RuntimeError):
    pass


# ------------------------------------------------------------------ helpers
def _now_ms() -> int:
    return int(time.time() * 1000)


def _sha(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _jdump(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=_json_default))
    tmp.replace(path)
    return path


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json")
    if isinstance(o, Path):
        return str(o)
    raise TypeError(type(o).__name__)


def _run(cmd: list, timeout: int = 1800):
    return subprocess.run(cmd, check=True, timeout=timeout, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


@contextlib.contextmanager
def heavy_lock():
    """Shared CPU: hold the night render lock (if configured) for heavy work."""
    if not LOCK:
        yield
        return
    import fcntl
    with open(LOCK, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


# ------------------------------------------------------------- spend ledger
class Ledger:
    """Append-only spend ledger with a hard cap checked BEFORE each call."""

    def __init__(self, path: Path, cap_usd: float, prior_usd: float = 0.0):
        self.path, self.cap, self.prior = Path(path), float(cap_usd), float(prior_usd)

    def spent(self) -> float:
        if not self.path.exists():
            return self.prior
        return self.prior + sum(json.loads(l).get("est_cost_usd", 0.0)
                                for l in self.path.read_text().splitlines() if l.strip())

    def guard(self, worst_usd: float, what: str) -> None:
        s = self.spent()
        if s + worst_usd > STOP_FRACTION * self.cap:
            raise SpendCapReached(f"stopped before {what}: spent ${s:.4f} + worst case ${worst_usd:.4f} would cross "
                                  f"{int(STOP_FRACTION * 100)}% of the ${self.cap:.2f} cap")

    def charge(self, rec: dict) -> float:
        from mirror.evaluate.costs import record_cost
        if rec.get("from_cassette"):
            return 0.0
        c = float(record_cost(rec))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a") as f:
            f.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "model": rec.get("model"),
                                "call": rec.get("call") or rec.get("name"), "est_cost_usd": round(c, 6),
                                "cassette_key": (rec.get("key") or "")[:24],
                                "usage": {k: v for k, v in (rec.get("usage") or {}).items() if not isinstance(v, dict)}})
                    + "\n")
        return c


def _no_network_transport():
    import httpx

    def handler(request):
        raise LiveCallInReplay(f"replay mode: refused a live request to {request.url.host}{request.url.path}")
    return httpx.MockTransport(handler)


# worst-case cost estimates used by the guard (USD)
WORST_GROUNDING_CALL = 0.04
def _worst_sam_video(n_frames: int) -> float:
    return max(n_frames, 50) * 0.0002 * 1.2
WORST_SAM_IMAGE = 0.0025 * 2
WORST_LLM_CALL = 0.08


class GuardedMeta:
    """MetaClient wrapper: spend guard before each call, ledger after, and a
    record of every responses() result (the grounding passes are read back)."""

    def __init__(self, client, ledger: Ledger, n_frames_hint: int = 1500):
        self.c, self.ledger, self.results, self.n_hint = client, ledger, [], n_frames_hint
        orig = client._log

        def _log(name, endpoint, model, usage, latency, *, from_cassette, key):
            orig(name, endpoint, model, usage, latency, from_cassette=from_cassette, key=key)
            ledger.charge({"model": model, "usage": usage, "call": name, "from_cassette": from_cassette, "key": key})
        client._log = _log

    @property
    def mode(self):
        return self.c.mode

    @property
    def usage(self):
        return self.c.usage

    def responses(self, *a, **k):
        if self.c.mode in ("record", "live"):
            self.ledger.guard(WORST_GROUNDING_CALL, "a Muse Spark call")
        r = self.c.responses(*a, **k)
        self.results.append(r)
        return r

    def sam_segment(self, media, prompt, *, video=None, **k):
        if self.c.mode in ("record", "live"):
            n = self.n_hint
            if video or str(media).lower().endswith((".mp4", ".mov")):
                try:
                    from mirror.capture import media as M
                    n = M.probe(media).n_frames
                except Exception:
                    pass
                self.ledger.guard(_worst_sam_video(n), f"a SAM 3.1 video request ({n} frames)")
            else:
                self.ledger.guard(WORST_SAM_IMAGE, "a SAM 3.1 image request")
        return self.c.sam_segment(media, prompt, video=video, **k)

    def upload_file(self, *a, **k):
        return self.c.upload_file(*a, **k)

    def __getattr__(self, name):
        return getattr(self.c, name)


def install_fast_sam_decoder():
    """Decode each SAM 3.1 one_bit mask once (official raster decoder, parallel)
    instead of decode + canonical re-encode check (about 0.3 s per mask: most of
    an hour for a 50 s clip). Same pixels; see tests/capture/test_real_run.py."""
    from mirror.meta import sam as S
    if getattr(S, "_fast_installed", False):
        return
    S._orig_parse = S.parse_sam_output
    S.parse_sam_output = _fast_parse
    S._fast_installed = True


_REC = re.compile(r"(\d+)<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)(?:;c=([0-9.]+))?\|>"
                  r"<\|mask;x=(\d+);y=(\d+);(?:c=[0-9.]+;)?data=(\d+),(\d+),([^|]*)\|>")


def _dec(args):
    h, w, payload = args
    from meta_sam_parser._mask_codec import _decode_raster
    return np.frombuffer(_decode_raster(payload, w, h), dtype=np.uint8).reshape(h, w)


def _fast_parse(text, *, prompt="", media="image", decode_masks=True, workers=3):
    from concurrent.futures import ProcessPoolExecutor
    from mirror.meta import sam as S
    items, frames, width, height = [], {}, None, None
    for line in text.splitlines():
        m = re.match(r"<(\d+)f>", line)
        if not m:
            continue
        fi = int(m.group(1))
        for r in _REC.finditer(line[m.end():]):
            oid, x1, y1, x2, y2, w, h = (int(v) for v in r.groups()[:7])
            mx, my, mh, mw = int(r.group(9)), int(r.group(10)), int(r.group(11)), int(r.group(12))
            if mx or my:
                return S._orig_parse(text, prompt=prompt, media=media, decode_masks=decode_masks)
            width, height = w, h
            o = S.SamObject(oid, fi, S.SamBox(x1, y1, x2 + 1, y2 + 1), float(r.group(8)) if r.group(8) else None)
            frames.setdefault(fi, []).append(o)
            items.append((o, (mh, mw, r.group(13))))
    if not items:
        return S._orig_parse(text, prompt=prompt, media=media, decode_masks=decode_masks)
    if decode_masks:
        if len(items) < 64:
            for o, a in items:
                o.mask = _dec(a)
        else:
            with ProcessPoolExecutor(workers) as ex:
                for (o, _), arr in zip(items, ex.map(_dec, [a for _, a in items], chunksize=16)):
                    o.mask = arr
    out = [S.SamFrame(i, sorted(frames[i], key=lambda o: o.object_id)) for i in sorted(frames)]
    return S.SamResult(prompt=prompt, media=media, width=width, height=height, frames=out, raw_text=text,
                       diagnostics=["decoded with real_run fast parser (official raster decoder, no canonical re-encode check)"])


# ------------------------------------------------------------- measurements
DIM_KEYS = ("width", "depth", "height")


def load_measurements(path) -> dict:
    """The person's measurements and object list (see docs/REAL_RUN.md):

    {"print_check": {"measured_bar_mm": 50, "measured_square_mm": 30, "measured_by": "person"},
     "table_name": "the dark wooden table",
     "objects": [{"label": "mug", "name": "the white patterned mug", "proxy": "cup",
                  "real_identity": "...", "real_contents": "dry rice",
                  "dims": {"height": 0.12, "width": {"value_m": 0.08, "note": "outside diameter"}},
                  "unsupported_features": ["handle"]}, ...]}

    ``name`` is what the grounding model is asked to find. Object order is the
    order of the model prompts."""
    m = json.loads(Path(path).read_text())
    if not m.get("objects"):
        raise SystemExit("measurements: no objects")
    pc = m.get("print_check") or {}
    if pc.get("measured_bar_mm") is None or pc.get("measured_square_mm") is None:
        raise SystemExit("measurements: print_check needs measured_bar_mm and measured_square_mm (ruler on the printed sheet)")
    labels = [o["label"] for o in m["objects"]]
    if len(set(labels)) != len(labels):
        raise SystemExit("measurements: object labels must be unique")
    for o in m["objects"]:
        if o.get("proxy") not in ("cup", "block", "marker", "tray", "wall", "ignore"):
            raise SystemExit(f"measurements: {o.get('label')}: proxy must be cup, block, marker, tray, wall or ignore")
        if not o.get("name"):
            raise SystemExit(f"measurements: {o['label']}: 'name' (what the grounding model looks for) is required")
    return m


def _dims(o: dict) -> dict:
    out = {}
    for k, v in (o.get("dims") or {}).items():
        if v is None:
            continue
        if isinstance(v, (int, float)):
            out[k] = {"value_m": float(v), "source": "measured_ruler"}
        else:
            out[k] = {"value_m": float(v["value_m"]), "source": v.get("source", "measured_ruler"), "note": v.get("note", "")}
    return out


def mappings_from(meas: dict, confirmed: set = frozenset(), rejected: set = frozenset()):
    from mirror.capture import proxy as PX
    out = []
    for o in meas["objects"]:
        lab = o["label"]
        out.append(PX.ObjectMapping(
            track_label=lab, real_identity=o.get("real_identity") or o["name"].removeprefix("the "),
            proxy="ignore" if lab in rejected else o["proxy"], real_dims=_dims(o),
            unsupported_features=list(o.get("unsupported_features") or []),
            assumptions=[PX.Assumption(**a) for a in (o.get("assumptions") or [])],
            real_contents=o.get("real_contents"), confirmed_by_person=lab in confirmed, notes=o.get("notes", "")))
    return out


def _movers(meas) -> list[str]:
    return [o["label"] for o in meas["objects"] if o["proxy"] in ("cup", "block", "marker")]


def _walls(meas) -> list[str]:
    return [o["label"] for o in meas["objects"] if o["proxy"] == "wall"]


def _nice(meas, lab) -> str:
    o = next(x for x in meas["objects"] if x["label"] == lab)
    return o.get("short") or lab.replace("_", " ")


# --------------------------------------------------------------------- run
class RealRun:
    def __init__(self, out: Path, *, take: Optional[Path] = None, lens: Optional[Path] = None,
                 measurements: Optional[Path] = None, mode: str = "replay", cap_usd: float = 10.0,
                 prior_usd: float = 0.0, cassettes: Optional[Path] = None, intrinsics: Optional[Path] = None,
                 seed_t: Optional[float] = None, answers: Optional[Path] = None, rehearse_correction: Optional[str] = None,
                 render: bool = True, label_extra: Optional[list] = None, dev_clip: Optional[str] = None,
                 media_cache: Optional[Path] = None, log=print):
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.state_path = self.out / "real_run.json"
        st = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        inp = st.get("inputs", {})
        self.take = Path(take or inp.get("take") or "")
        self.lens = Path(lens) if lens else (Path(inp["lens"]) if inp.get("lens") else None)
        self.intrinsics_in = Path(intrinsics) if intrinsics else (Path(inp["intrinsics"]) if inp.get("intrinsics") else None)
        meas_p = Path(measurements or inp.get("measurements") or "")
        if not str(self.take) or not self.take.exists():
            raise SystemExit(f"take clip not found: {self.take}")
        if not meas_p.exists():
            raise SystemExit(f"measurements file not found: {meas_p}")
        if self.lens is None and self.intrinsics_in is None:
            raise SystemExit("give --lens LENS.MOV (or --intrinsics for an already accepted lens calibration)")
        self.meas_path = meas_p
        self.meas = load_measurements(meas_p)
        self.mode = mode
        if mode not in ("record", "replay", "offline", "live"):
            raise SystemExit("mode must be record, replay or offline")
        self.cassettes = Path(cassettes or inp.get("cassettes") or (self.out / "cassettes"))
        self.ledger = Ledger(self.out / "spend.jsonl", cap_usd, prior_usd)
        self.seed_t = seed_t if seed_t is not None else inp.get("seed_t")
        self.answers_path = Path(answers) if answers else self.out / "answers.json"
        self.rehearse = rehearse_correction
        self.render_on = render
        self.dev_clip = dev_clip if dev_clip is not None else inp.get("dev_clip")
        self.label_extra = list(label_extra or inp.get("label_extra") or [])
        self.media_cache = Path(media_cache) if media_cache else None
        self.log = log
        self.state = st or {"version": VERSION, "stages": {}, "created_at": _now_ms()}
        self.state["inputs"] = {"take": str(self.take), "lens": str(self.lens) if self.lens else None,
                                "intrinsics": str(self.intrinsics_in) if self.intrinsics_in else None,
                                "measurements": str(meas_p), "cassettes": str(self.cassettes), "seed_t": self.seed_t,
                                "dev_clip": self.dev_clip, "label_extra": self.label_extra}
        self.state["mode"] = mode
        self.state["cap_usd"] = cap_usd
        self._meta = None
        self._llm = None
        self._save()

    # ---------------------------------------------------------- state
    def _save(self):
        self.state["updated_at"] = _now_ms()
        self.state["spend_usd"] = round(self.ledger.spent(), 6)
        _jdump(self.state_path, self.state)

    def done(self, stage: str) -> bool:
        return self.state["stages"].get(stage, {}).get("status") == "done"

    def info(self, stage: str) -> dict:
        return self.state["stages"].get(stage, {})

    def mark(self, stage: str, t0: float, **info):
        self.state["stages"][stage] = {"status": "done", "wall_s": round(time.time() - t0, 2), "at": _now_ms(), **info}
        self._save()
        self.log(f"[{stage}] done in {time.time() - t0:.1f} s")

    def p(self, *parts) -> Path:
        q = self.out.joinpath(*parts)
        q.parent.mkdir(parents=True, exist_ok=True)
        return q

    def labels(self) -> list[str]:
        out = list(LABELS_REAL)
        s = self._session_quick()
        if s is not None and any(r.code == "recalibration_required" and not r.resolved for r in s.review):
            out.append("Calibration reuse flagged")
        if self.dev_clip:
            out.append(f"Development clip {self.dev_clip}, never scored")
        out += self.label_extra
        if self.mode == "replay":
            out.append("Model outputs replayed from recordings (no new model calls)")
        return out

    def _session_quick(self):
        from mirror.capture.session import CaptureSession
        for name in ("session_answered.json", "session.json"):
            f = self.out / name
            if f.exists():
                return CaptureSession.load(f)
        return None


    def clip(self, path) -> "Any":
        """ClipMeta, probed once per file (probing hashes the file and reads every timestamp)."""
        from mirror.capture import media as M
        path = Path(path)
        st = path.stat()
        key = hashlib.sha1(f"{path.resolve()}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:16]
        if not hasattr(self, "_clips"):
            self._clips = {}
        if key in self._clips:
            return self._clips[key]
        f = self.out / "work" / "probe_cache" / f"{key}.json"
        if f.exists():
            m = M.ClipMeta.model_validate_json(f.read_text())
        else:
            m = M.probe(path)
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(m.model_dump_json())
        self._clips[key] = m
        return m

    # ---------------------------------------------------------- clients
    def meta(self):
        if self._meta is None:
            from mirror.meta import MetaClient
            if self.mode == "offline":
                raise SystemExit("offline mode cannot ground or track objects: use --mode record (live Meta models, "
                                 "recorded) or --mode replay with the recorded cassettes")
            kw = dict(mode="replay" if self.mode == "replay" else "record", cassette_dir=self.cassettes,
                      usage_log=self.out / "meta_usage.jsonl")
            if self.mode == "replay":
                kw.update(api_key=None, transport=_no_network_transport())
            else:
                key = os.environ.get("MODEL_API_KEY")
                if not key:
                    raise SystemExit("record mode needs MODEL_API_KEY in the environment (never written to files)")
                kw["api_key"] = key
            n = self.info("probe").get("take", {}).get("n_frames", 1500)
            self._meta = GuardedMeta(MetaClient(**kw), self.ledger, n)
        return self._meta

    def llm(self):
        """LLM client for interpretation/corrections, or None in offline mode."""
        if self._llm is None:
            from mirror.interpret.llm import LLMClient
            if self.mode == "offline":
                self._llm = LLMClient.offline_client()
            else:
                kw = dict(mode="replay" if self.mode == "replay" else "record", cassette_dir=self.cassettes / "llm",
                          usage_log_path=self.out / "llm_usage.jsonl")
                if self.mode == "replay":
                    kw.update(api_key=None, transport=_no_network_transport())
                else:
                    kw["api_key"] = os.environ.get("MODEL_API_KEY")
                c = LLMClient(**kw)
                led = self.ledger
                orig = c.complete_json

                def complete_json(*a, **k):
                    if c.mode in ("record", "live"):
                        led.guard(WORST_LLM_CALL, "a Muse Spark interpretation call")
                    n0 = len(c.usage)
                    try:
                        return orig(*a, **k)
                    finally:
                        for rec in c.usage[n0:]:
                            led.charge({**rec, "model": rec.get("model") or c.model})
                c.complete_json = complete_json
                self._llm = c
        return self._llm

    # ----------------------------------------------------------- driver
    def run(self, until: Optional[str] = None) -> dict:
        t_all = time.time()
        try:
            for st in STAGES:
                if self.done(st):
                    continue
                getattr(self, "stage_" + st)()
                if until and st == until:
                    break
            self.state["status"] = "complete"
        except StageStop as e:
            self.state["status"] = "waiting"
            self.state["waiting_for"] = str(e)
            self.log(f"[waiting] {e}")
        except SpendCapReached as e:
            self.state["status"] = "stopped_spend_cap"
            self.state["waiting_for"] = str(e)
            self.log(f"[stopped] {e}")
        self.state["last_invocation_wall_s"] = round(time.time() - t_all, 1)
        self._save()
        return self.state


    # ============================================================ WATCH
    def stage_probe(self):
        from mirror.capture import media as M
        t0 = time.time()
        tm = self.clip(self.take)
        out = {"take": {"path": str(self.take), "sha256": tm.sha256, "n_frames": tm.n_frames, "duration_s": tm.duration_s,
                        "fps": tm.nominal_fps, "size": [tm.width, tm.height], "variable_frame_rate": tm.variable_frame_rate,
                        "fingerprint": tm.fingerprint.model_dump()}}
        notes = []
        if self.lens is not None:
            lm = self.clip(self.lens)
            out["lens"] = {"path": str(self.lens), "sha256": lm.sha256, "n_frames": lm.n_frames, "duration_s": lm.duration_s,
                           "fps": lm.nominal_fps, "size": [lm.width, lm.height], "fingerprint": lm.fingerprint.model_dump()}
            diff = lm.fingerprint.differences(tm.fingerprint)
            out["fingerprint_differences"] = diff
            if diff:
                notes.append("lens clip and take differ in recorded camera settings: " + "; ".join(diff))
        if tm.duration_s and tm.duration_s > 120:
            notes.append(f"take is {tm.duration_s:.0f} s long; MIRROR's budget assumes about a minute")
        _jdump(self.p("probe.json"), out)
        self.mark("probe", t0, notes=notes, **out)

    def stage_lens(self):
        from mirror.capture import calibrate as C, media as M
        from mirror.capture.target import DEFAULT_TARGET
        t0 = time.time()
        pc = C.PrintCheck(**self.meas["print_check"])
        dst = self.p("lens", "intrinsics.json")
        if self.intrinsics_in is not None:
            intr = C.Intrinsics.model_validate_json(self.intrinsics_in.read_text())
            dst.write_text(intr.model_dump_json(indent=1))
            how = f"reused accepted calibration {self.intrinsics_in.name}"
            n_samples = None
        else:
            lm = self.clip(self.lens)
            dets = [C.detect(g, DEFAULT_TARGET, frame_index=i, pts_s=t) for i, t, g in M.iter_frames(lm, step=15, gray=True)]
            intr = C.calibrate_intrinsics(dets, DEFAULT_TARGET, pc, fingerprint=lm.fingerprint)
            dst.write_text(intr.model_dump_json(indent=1))
            how = f"calibrated from {self.lens.name} (every 15th frame, {len(dets)} samples)"
            n_samples = len(dets)
        checks = [c.model_dump() for c in intr.checks]
        self.mark("lens", t0, accepted=bool(intr.accepted), rms_px=intr.rms_px, n_views=intr.n_views, how=how,
                  samples=n_samples, failed=[c["name"] for c in checks if c.get("passed") is False],
                  path="lens/intrinsics.json")

    def stage_scan(self):
        """Board-at-rest windows and sync-card events (one decoding pass, half resolution)."""
        import cv2
        from mirror.capture import calibrate as C, media as M, sync as Y
        from mirror.capture.target import DEFAULT_TARGET as SPEC
        from mirror.capture.session import BOARD_STEADY_PX
        t0 = time.time()
        m = self.clip(self.take)
        det = cv2.aruco.ArucoDetector(SPEC.dictionary_obj(), cv2.aruco.DetectorParameters())
        ts, fs, vis, boards = [], [], [], []
        BSTEP, SC = 2, 0.5
        with heavy_lock():
            for i, t, g in M.iter_frames(m, gray=True):
                h = cv2.resize(g, None, fx=SC, fy=SC, interpolation=cv2.INTER_AREA)
                _, ids, _ = det.detectMarkers(h)
                ts.append(t); fs.append(i)
                vis.append(ids is not None and SPEC.sync_marker_id in ids.ravel())
                if i % BSTEP == 0:
                    d = C.detect(h, SPEC, frame_index=i, pts_s=t)
                    boards.append((i, t, {int(k): (p[0] / SC, p[1] / SC) for k, p in zip(d.corner_ids, d.corners_px)}))
        sync = [e.model_dump() for e in Y.card_events(m, SPEC, states=(ts, fs, vis))]
        # board windows: visible (>= 6 corners) and at rest (median corner motion < BOARD_STEADY_PX per frame)
        rows = []
        for k, (i, t, cs) in enumerate(boards):
            mot = None
            if k and len(cs) >= 6:
                j, _, prev = boards[k - 1]
                sh_ = [np.hypot(cs[c][0] - prev[c][0], cs[c][1] - prev[c][1]) for c in cs if c in prev]
                if len(sh_) >= 6:
                    mot = float(np.median(sh_)) / max(1, i - j)
            rows.append({"frame": i, "t": t, "n": len(cs), "motion_px_per_frame": mot})
        def runs(pred, min_s):
            out, cur = [], []
            for r in rows:
                if pred(r):
                    cur.append(r)
                else:
                    if cur:
                        out.append(cur)
                    cur = []
            if cur:
                out.append(cur)
            return [{"t0": c[0]["t"], "t1": c[-1]["t"], "f0": c[0]["frame"], "f1": c[-1]["frame"],
                     "max_corners": max(x["n"] for x in c)} for c in out if c[-1]["t"] - c[0]["t"] >= min_s]
        visible = runs(lambda r: r["n"] >= 6, 0.2)
        rest = runs(lambda r: r["n"] >= 6 and r["motion_px_per_frame"] is not None
                    and r["motion_px_per_frame"] < BOARD_STEADY_PX, 0.3)
        pts0 = m.pts_s[0]
        first_rest = rest[0] if rest else None
        board_search_s = max(3.0, round(first_rest["t1"] - pts0 + 1.5, 2)) if first_rest else 3.0
        # was the board laid down differently at the end? (orientation of the printed grid in the image)
        same_placement, why = True, "one board window or no board at the end: end-of-take drift check applies as usual"
        if len(rest) >= 2 and rest[-1]["t0"] - first_rest["t1"] > 5.0:
            def angle(f):
                cs = next(c for i, _, c in boards if i == f)
                ids = sorted(cs)
                a, b = cs[ids[0]], cs[ids[-1]]
                return ids[0], ids[-1], math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
            fa = (first_rest["f0"] + first_rest["f1"]) // 2 // BSTEP * BSTEP
            fb = (rest[-1]["f0"] + rest[-1]["f1"]) // 2 // BSTEP * BSTEP
            try:
                ia, ja, aa = angle(fa); ib, jb, ab = angle(fb)
                dd = abs((ab - aa + 180) % 360 - 180)
                if (ia, ja) == (ib, jb) and dd > 45:
                    same_placement = False
                    why = (f"the board at {rest[-1]['t0']:.1f}-{rest[-1]['t1']:.1f} s lies rotated {dd:.0f} deg relative to "
                           f"{first_rest['t0']:.1f}-{first_rest['t1']:.1f} s: laid down differently, so its pose difference is "
                           "not camera motion (the image-border shift check still guards against a moved phone)")
                else:
                    why = f"end board orientation within {dd:.0f} deg of the start: drift check applies"
            except StopIteration:
                pass
        out = {"sync_events": sync, "board_visible": visible, "board_rest": rest, "board_search_s": board_search_s,
               "end_board_same_placement": same_placement, "end_board_reason": why,
               "method": f"aruco sync marker every frame and ChArUco board every {BSTEP}nd frame at {SC}x resolution"}
        _jdump(self.p("scan.json"), {**out, "board_samples": rows})
        self.mark("scan", t0, **out)

    def stage_seed(self):
        """A clean frame to ground on: after the setup (board removed, sync card
        shown) and before anything moves; least image motion in that window."""
        from mirror.capture import media as M
        t0 = time.time()
        m = self.clip(self.take)
        sc = self.info("scan")
        pts0 = m.pts_s[0]
        dur = m.pts_s[-1] - pts0
        setup = [b["t1"] for b in sc.get("board_rest", [])[:1]] + [b["t1"] for b in sc.get("board_visible", [])
                                                                     if b["t0"] - pts0 < 0.5 * dur]
        setup += [e["t"] for e in sc.get("sync_events", []) if e["t"] - pts0 < 0.6 * dur]
        setup_end = max(setup) if setup else pts0 + 1.0
        w, h = 120, int(round(120 * m.height / m.width / 2) * 2)
        proc = subprocess.run(["ffmpeg", "-v", "error", "-i", str(self.take), "-map", "0:v:0", "-fps_mode", "passthrough",
                               "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=600, check=True)
        a = np.frombuffer(proc.stdout, np.uint8)
        n = len(a) // (w * h)
        v = a[: n * w * h].reshape(n, h, w).astype(np.float32)
        motion = np.zeros(n)
        motion[1:] = np.abs(np.diff(v, axis=0)).mean(axis=(1, 2))
        win = [k for k in range(n) if k < len(m.pts_s) and setup_end + 0.5 <= m.pts_s[k] <= setup_end + 2.5]
        if not win:
            win = list(range(min(n, len(m.pts_s))))
        score = [motion[max(0, k - 3): k + 4].sum() for k in win]
        auto = win[int(np.argmin(score))]
        how = f"auto: least image motion {setup_end + 0.5:.2f}-{setup_end + 2.5:.2f} s (after the setup ended at {setup_end:.2f} s)"
        frame = auto
        if self.seed_t is not None:
            frame = m.frame_at(float(self.seed_t))
            how = f"given (--seed-t {self.seed_t}); automatic choice would be frame {auto} ({m.pts_s[auto]:.2f} s)"
        _jdump(self.p("seed.json"), {"frame": frame, "t": m.pts_s[frame], "auto_frame": auto, "auto_t": m.pts_s[auto],
                                     "setup_end_s": setup_end, "how": how})
        self.mark("seed", t0, frame=frame, t=m.pts_s[frame], auto_frame=auto, auto_t=m.pts_s[auto], how=how)

    def _objects(self):
        objs = [(o["label"], o["name"]) for o in self.meas["objects"] if o["proxy"] != "ignore"]
        return tuple(objs + [("table", self.meas.get("table_name") or "the table")])

    def stage_ground(self):
        from mirror.capture import media as M
        PT = __import__('importlib').import_module('mirror.perception.track')
        t0 = time.time()
        m = self.clip(self.take)
        seed = self.info("seed")["frame"]
        full = M.read_frame(m, seed)
        c = self.meta()
        n0 = len(c.results)
        g = PT.ground_frame(full, self._objects(), client=c, work=self.p("work", "grounding", "x").parent, tag="top_seed")
        res = c.results[n0:]
        x0, y0, x1, y1 = g["crop"]
        cw, ch = x1 - x0, y1 - y0
        name_of = {n: l for l, n in self._objects()}
        pass2 = {}
        if len(res) >= 2:
            for r in res[1].json().get("results", []):
                lab = name_of.get(r["object_name"].strip()) or name_of.get(r["object_name"].strip().lower())
                if lab is None:
                    for n, l in name_of.items():
                        if r["object_name"].strip().lower().replace("the ", "") == n.lower().replace("the ", ""):
                            lab = l
                if lab:
                    b = r["bbox"]
                    pass2[lab] = [int(round(x0 + b["x_min"] * cw / 1000)), int(round(y0 + b["y_min"] * ch / 1000)),
                                  int(round(x0 + b["x_max"] * cw / 1000)), int(round(y0 + b["y_max"] * ch / 1000))]
        want = [l for l, _ in self._objects() if l != "table"]
        missing = [l for l in want if l not in g["boxes"]]
        # overlay of the boxes for the person
        import cv2
        img = full.copy()
        for l, b in g["boxes"].items():
            if l == "table":
                continue
            cv2.rectangle(img, (b[0], b[1]), (b[2], b[3]), (255, 120, 40), 4)
            cv2.putText(img, l, (b[0] + 4, b[1] + 28), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 120, 40), 2)
        small = cv2.resize(img, (img.shape[1] // 2, img.shape[0] // 2))
        cv2.imwrite(str(self.p("evidence", "grounding_boxes.jpg")), small[..., ::-1], [cv2.IMWRITE_JPEG_QUALITY, 85])
        calls = g["calls"]
        out = {"seed_frame": seed, "boxes_full": g["boxes"], "boxes_pass2_full": pass2, "crop": g["crop"],
               "missing": missing, "calls": calls, "from_cassette": all(x.get("cassette") for x in calls)}
        _jdump(self.p("grounding.json"), out)
        if missing:
            self.state.setdefault("problems", []).append(f"grounding did not find: {', '.join(missing)}")
        self.mark("ground", t0, missing=missing, n_calls=len(calls), from_cassette=out["from_cassette"])


    def _media(self, name: str, make) -> Path:
        """Derived media in DIR/work, made once (or taken from --media-cache if a file of the same name exists there)."""
        dst = self.p("work", name)
        if dst.exists() and dst.stat().st_size > 0:
            return dst
        if self.media_cache is not None and (self.media_cache / name).exists():
            shutil.copyfile(self.media_cache / name, dst)
            self.state.setdefault("media_from_cache", []).append(name)
            return dst
        make(dst)
        return dst

    def stage_track(self):
        import imageio.v2 as iio
        from PIL import Image
        from mirror.capture import media as M
        PT = __import__('importlib').import_module('mirror.perception.track')
        from mirror.perception.masks import rle_decode, rle_encode
        from mirror.perception.reconstruct import Tracks
        install_fast_sam_decoder()
        t0 = time.time()
        m = self.clip(self.take)
        N, W, H = m.n_frames, m.width, m.height
        g = json.loads(self.p("grounding.json").read_text())
        seed = g["seed_frame"]
        k = 2 if W >= 1000 else 1
        pw, ph = W // k // 2 * 2, H // k // 2 * 2
        s = W / pw

        def mk_proxy(dst):
            with heavy_lock():
                _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(self.take), "-map", "0:v:0", "-vf", f"scale={pw}:{ph}",
                      "-an", "-c:v", "libx264", "-crf", str(PROXY_CRF), "-pix_fmt", "yuv420p", str(dst)])
        proxy = self._media(f"proxy_{pw}x{ph}.mp4", mk_proxy)
        pm = self.clip(proxy)
        if pm.n_frames != N:
            raise RuntimeError(f"proxy has {pm.n_frames} frames, take has {N}: frame indices would not match")

        def mk_sub(start, end, reverse):
            def f(dst):
                with heavy_lock():
                    PT._subclip(proxy, start, dst, end=end, reverse=reverse)
            return f
        fwd = self._media(f"fwd_{seed}.mp4", mk_sub(seed, N - 1, False))
        bwd = self._media(f"bwd_{seed}.mp4", mk_sub(0, seed, True))
        for clip, n in ((fwd, N - seed), (bwd, seed + 1)):
            if self.clip(clip).n_frames != n:
                raise RuntimeError(f"{clip.name}: expected {n} frames")
        objs = self._objects()
        want = [l for l, _ in objs if l != "table"]
        walls = [l for l in _walls(self.meas) if l in want]
        boxes = {l: [int(round(v / s)) for v in g["boxes_full"][l]] for l in want if l in g["boxes_full"]}
        c = self.meta()
        masks = {lab: {} for lab in boxes}
        sam_info = {}
        for nm, clip, n, mapf in (("forward", fwd, N - seed, lambda q: seed + q), ("backward", bwd, seed + 1, lambda q: seed - q)):
            ts = time.time()
            tr, res = PT.sam_track_boxes(str(clip), boxes, (pw, ph), client=c)
            sam_info[nm] = {"frames": len(res.frames), "objects": res.object_ids, "latency_s": res.latency_s,
                            "cassette": res.from_cassette, "wall_s": round(time.time() - ts, 1)}
            for lab, fr in tr.items():
                for q, mm in fr.items():
                    i = mapf(q)
                    if 0 <= i < N and (i not in masks[lab] or nm == "forward"):
                        masks[lab][i] = mm
            self.log(f"[track] SAM {nm}: {len(res.frames)} frames, cassette={res.from_cassette}")
        info = {"grounding": {"top": {"crop": [int(round(v / s)) for v in g["crop"]], "boxes_full": g["boxes_full"],
                                      "calls": g["calls"], "seed_frame": seed, "seed_t": m.pts_s[seed]}}, "sam": sam_info}
        discarded = {}
        for w in walls:
            discarded[w] = {"frames": len(masks.pop(w, {})),
                            "reason": "static obstacle: its video track is discarded (a hand passing in front of it pulls the "
                                      "track along); one SAM image request on the seed frame segments it, held for every frame"}
        info["discarded"] = discarded
        allm = {"top": masks}
        info["reseed"] = PT.reseed_lost({"top": proxy}, allm, info, fps=float(m.nominal_fps or 30), n_frames=N,
                                        objects=objs, client=c, work=self.p("work", "reseed", "x").parent)
        # static obstacles: one SAM image request on the forward clip's first frame (the seed frame)
        if walls:
            rd = iio.get_reader(str(fwd)); f0 = rd.get_data(0); rd.close()
            sp = self.p("work", "seed_proxy.png")
            Image.fromarray(f0).save(sp)
            for w in walls:
                src = g.get("boxes_pass2_full", {}).get(w) or g["boxes_full"].get(w)
                if not src:
                    continue
                b = [int(round(v / s)) for v in src]
                trd, resd = PT.sam_track_boxes(str(sp), {w: b}, (pw, ph), client=c, image=True)
                info["sam"][f"{w}_static"] = {"box_proxy": b, "objects": resd.object_ids, "cassette": resd.from_cassette,
                                              "frames": len(resd.frames),
                                              "box_source": "grounding pass 2 (table crop)" if w in g.get("boxes_pass2_full", {}) else "grounding pass 3"}
                if trd.get(w):
                    masks[w] = {seed: next(iter(trd[w].values()))}
        src_txt = (f"muse-spark-1.3 grounding (seed frame {seed}) + sam-3.1 box-prompt tracking forward and backward, "
                   f"{pw}x{ph}")
        t = Tracks(fps=float(m.nominal_fps or 30), n_frames=N, size={"top": (pw, ph)}, masks={"top": masks}, static=set(),
                   source=src_txt)
        t.notes.append(json.dumps(info, default=_json_default))
        PT.save_tracks(t, self.p("tracks_proxy.json.gz"))
        # full-resolution copy for the capture (nearest upscale; static obstacles held for every frame)
        up = {}
        for lab, fr in masks.items():
            up[lab] = {}
            items = fr.items() if lab not in walls else [(i, next(iter(fr.values()))) for i in range(N)]
            cache = {}
            for i, mm in items:
                key = id(mm)
                if key not in cache:
                    a = rle_decode(mm)
                    a = np.repeat(np.repeat(a, k, 0), k, 1) if (pw * k, ph * k) == (W, H) else \
                        np.asarray(Image.fromarray(a.astype(np.uint8)).resize((W, H), Image.NEAREST)).astype(bool)
                    cache = {key: rle_encode(a)}
                up[lab][i] = cache[key]
        t2 = Tracks(fps=t.fps, n_frames=N, size={"top": (W, H)}, masks={"top": up},
                    source=t.source + f"; masks upscaled x{k} (nearest) to {W}x{H}; static obstacles held for every frame")
        t2.notes = list(t.notes)
        PT.save_tracks(t2, self.p("tracks_full.json.gz"))
        all_cas = all(v.get("cassette") for v in sam_info.values()) and all(
            v.get("cassette") for kk, v in info["sam"].items() if kk.endswith("_static")) and \
            all(r.get("sam", {}).get("cassette", True) for r in info["reseed"])
        cov = {lab: round(len(fr) / N, 3) if lab not in walls else 1.0 for lab, fr in masks.items()}
        self.mark("track", t0, coverage=cov, proxy=[pw, ph], reseed=len(info["reseed"]),
                  origin="recorded_replay" if self.mode == "replay" or all_cas else "fresh_model_call",
                  sam={k2: {kk: v[kk] for kk in ("frames", "cassette") if kk in v} for k2, v in info["sam"].items()})

    # ----------------------------------------------------------- session
    def _build_session(self, confirmed=frozenset(), rejected=frozenset(), corrections=(), extra_notes=()):
        from mirror.capture import calibrate as C, events as EV, session as SE, track as TR, media as M
        m = self.clip(self.take)
        sc = self.info("scan")
        intr = C.Intrinsics.model_validate_json(self.p("lens", "intrinsics.json").read_text())
        origin = self.info("track").get("origin", "recorded_replay")
        ts = TR.from_perception_tracks(m, self.p("tracks_full.json.gz"), camera="top", origin=origin)
        s = SE.build_session(take={"top": str(self.take)}, intrinsics={"top": intr},
                             print_check=C.PrintCheck(**self.meas["print_check"]), prompts=[],
                             mappings=mappings_from(self.meas, set(confirmed), set(rejected)), tracker="model",
                             model_tracks={"top": ts}, corrections=list(corrections),
                             board_search_s=float(sc.get("board_search_s", 3.0)),
                             end_board_same_placement=bool(sc.get("end_board_same_placement", True)))
        s.notes.append("built by mirror.capture.real_run (board search and end-board placement found automatically: "
                       + sc.get("end_board_reason", "") + ")")
        s.notes += list(extra_notes)
        return s

    def stage_session(self):
        from mirror.capture import session as SE
        t0 = time.time()
        s = self._build_session()
        s.save(self.p("session.json"))
        lines = SE.summary_lines(s)
        self.p("review.md").write_text("# Capture review\n\n" + "\n".join(x if x.startswith("- ") else f"- {x}" for x in lines) + "\n")
        ok, blocking = s.gate()
        self.mark("session", t0, session_id=s.session_id, gate_passed=ok, blocking=[b.code for b in blocking],
                  mode=s.mode, pose={c: {"accepted": p.accepted, "rms_px": p.rms_px, "distance_m": p.distance_m,
                                          "frame": getattr(p, "frame_index", None)} for c, p in s.poses.items()})


    # ----------------------------------------------------------- report
    def _proxy_tracks(self):
        PT = __import__('importlib').import_module('mirror.perception.track')
        return PT.load_tracks(self.p("tracks_proxy.json.gz"))

    def _timeline(self):
        """2D image-space motion of the masks (proxy resolution): moves, jitter, visibility."""
        import cv2
        from mirror.perception.masks import rle_decode
        t = self._proxy_tracks()
        masks = t.masks["top"]
        pw, ph = t.size["top"]
        m = self.clip(self.take)
        pts = np.asarray(m.pts_s)
        N = len(pts)
        walls = set(_walls(self.meas))
        trays = [o["label"] for o in self.meas["objects"] if o["proxy"] == "tray"]
        labels = [o["label"] for o in self.meas["objects"] if o["label"] in masks]
        seed = self.info("seed")["frame"]
        cent = {l: np.full((N, 2), np.nan) for l in labels}
        area = {l: np.zeros(N) for l in labels}
        for l in labels:
            for i, mm in masks[l].items():
                a = rle_decode(mm)
                ys, xs = np.nonzero(a)
                if len(xs):
                    cent[l][i] = (xs.mean(), ys.mean()); area[l][i] = len(xs)
        tray_ref = None
        if trays and trays[0] in masks and masks[trays[0]]:
            tk = seed if seed in masks[trays[0]] else min(masks[trays[0]])
            tm = rle_decode(masks[trays[0]][tk]).astype(np.uint8)
            cnts, _ = cv2.findContours(tm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if cnts:
                hull = cv2.convexHull(max(cnts, key=cv2.contourArea))
                tray_ref = np.zeros_like(tm); cv2.fillPoly(tray_ref, [hull], 1); tray_ref = tray_ref.astype(bool)
        pix_s = 60.0 * pw / 1080
        min_disp = 15.0 * pw / 540
        events, jitter, speed = [], [], {}
        for l in labels:
            c = cent[l]
            ok = ~np.isnan(c[:, 0])
            sp = np.full(N, np.nan)
            idx = np.flatnonzero(ok)
            for a, b in zip(idx, idx[1:]):
                if b - a <= 3:
                    sp[b] = np.hypot(*(c[b] - c[a])) / max(pts[b] - pts[a], 1e-6)
            sm = np.array([np.nanmedian(sp[max(0, i - 2):i + 3]) if np.any(~np.isnan(sp[max(0, i - 2):i + 3])) else np.nan
                           for i in range(N)])
            speed[l] = sm
            if l in walls or l in trays:
                continue
            moving = np.nan_to_num(sm) > pix_s
            segs, i = [], 0
            while i < N:
                if moving[i]:
                    j = i
                    while j < N and moving[j]:
                        j += 1
                    segs.append([i, j - 1]); i = j
                else:
                    i += 1
            merged = []
            for sgm in segs:
                if merged and pts[sgm[0]] - pts[merged[-1][1]] < 0.4:
                    merged[-1][1] = sgm[1]
                else:
                    merged.append(sgm)
            for a, b in merged:
                pre = next((q for q in range(a - 1, -1, -1) if ok[q]), None)
                post = next((q for q in range(b + 1, N) if ok[q]), None)
                sxy = c[pre] if pre is not None else None
                exy = c[post] if post is not None else None
                in_tray = None
                if tray_ref is not None and exy is not None:
                    x, y = int(round(exy[0])), int(round(exy[1]))
                    in_tray = bool(0 <= y < tray_ref.shape[0] and 0 <= x < tray_ref.shape[1] and tray_ref[y, x])
                disp = float(np.hypot(*(exy - sxy))) if sxy is not None and exy is not None else None
                rec = {"object": l, "from_s": round(float(pts[a]), 2), "to_s": round(float(pts[b]), 2),
                       "frames": [int(a), int(b)], "in_tray": in_tray, "displacement_px": round(disp, 1) if disp else None,
                       "frames_hidden_during": int(np.sum(~ok[a:b + 1]))}
                (events if (disp or 0) >= min_disp else jitter).append(rec)
        events.sort(key=lambda e: e["from_s"])
        vis = {l: round(float(np.mean(area[l] > 0)), 3) for l in labels}
        return {"what": "2D image-space motion of the SAM masks (one camera). Not 3D positions, not contact events.",
                "proxy_size": [pw, ph], "threshold_px_s": pix_s, "min_net_displacement_px": min_disp,
                "events": events, "jitter_not_moves": jitter, "visible_fraction": vis,
                "tray_outline": "convex hull of the tray mask on the seed frame"}, {"cent": cent, "area": area, "speed": speed,
                                                                                     "labels": labels, "pts": pts, "pix_s": pix_s}

    def _chart(self, tl, series):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        pts, labels, area, speed = series["pts"], series["labels"], series["area"], series["speed"]
        sc = self.info("scan")
        cols = plt.cm.tab10(np.linspace(0, 1, 10))
        fig, axes = plt.subplots(2, 1, figsize=(11, 5.2), sharex=True, gridspec_kw={"height_ratios": [1.1, 1]})
        ax = axes[0]
        for r, l in enumerate(labels[::-1]):
            col = cols[labels.index(l) % 10]
            seen = area[l] > 0
            if seen.sum() <= 1:
                ax.text(pts[0] + 0.5, r, "segmented once (static obstacle)", va="center", fontsize=8, color="0.35")
                continue
            ax.fill_between(pts, r - 0.32, r + 0.32, where=seen, color=col, alpha=0.3, step="mid", linewidth=0)
            for e in tl["events"]:
                if e["object"] == l:
                    ax.add_patch(plt.Rectangle((e["from_s"], r - 0.32), e["to_s"] - e["from_s"], 0.64, color=col, alpha=0.95))
        ax.set_yticks(range(len(labels))); ax.set_yticklabels([_nice(self.meas, l) for l in labels[::-1]])
        for b in sc.get("board_visible", []):
            ax.axvspan(b["t0"], b["t1"], color="0.5", alpha=0.12)
            ax.text((b["t0"] + b["t1"]) / 2, len(labels) - 0.45, "board", ha="center", fontsize=8, color="0.35")
        for e in sc.get("sync_events", []):
            ax.axvline(e["t"], color="0.4", lw=0.8, ls=":")
        ax.set_title("What MIRROR saw (light = mask present, solid = moving in the image)", fontsize=10, loc="left")
        ax2 = axes[1]
        walls = set(_walls(self.meas)) | {o["label"] for o in self.meas["objects"] if o["proxy"] == "tray"}
        for l in labels:
            if l in walls:
                continue
            ax2.plot(pts, speed[l], color=cols[labels.index(l) % 10], lw=1.2, label=_nice(self.meas, l))
        ax2.axhline(series["pix_s"], color="0.4", ls="--", lw=0.8)
        ax2.set_ylabel("image speed (px/s)"); ax2.set_xlabel("time in clip (s)"); ax2.legend(fontsize=8, ncol=3, loc="upper right")
        ax2.set_ylim(0, max(4 * series["pix_s"], 10))
        fig.text(0.01, 0.005, " · ".join(self.labels()) + ". 2D image motion only.", fontsize=7, color="0.35")
        fig.tight_layout(rect=(0, 0.03, 1, 1))
        fig.savefig(self.p("timeline.png"), dpi=110)
        plt.close(fig)

    def _overlay(self, tl):
        """Masks and labels over the real footage (proxy resolution, H.264, browser-playable)."""
        import cv2
        from mirror.perception.masks import rle_decode
        t = self._proxy_tracks()
        masks = t.masks["top"]
        pw, ph = t.size["top"]
        proxy = next(self.out.glob("work/proxy_*.mp4"))
        pts = self.clip(self.take).pts_s
        walls = set(_walls(self.meas))
        pal = [(255, 90, 90), (255, 200, 40), (60, 140, 255), (90, 230, 150), (230, 120, 255), (120, 220, 230)]
        labels = [o["label"] for o in self.meas["objects"] if o["label"] in masks]
        COL = {l: pal[k % len(pal)] for k, l in enumerate(labels)}
        static = {l: rle_decode(next(iter(masks[l].values()))) for l in labels if len(masks[l]) == 1}
        moving = {l: set() for l in labels}
        for e in tl["events"]:
            moving[e["object"]].update(range(e["frames"][0], e["frames"][1] + 1))
        out = self.p("overlay.mp4")
        font = cv2.FONT_HERSHEY_SIMPLEX
        head = " | ".join(x for x in self.labels() if not x.startswith("Model outputs"))[:96]
        dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(proxy), "-f", "rawvideo", "-pix_fmt", "rgb24",
                                "-fps_mode", "passthrough", "-"], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
        enc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{pw}x{ph}",
                                "-r", f"{float(self.clip(self.take).nominal_fps or 30):.6f}", "-i", "-", "-c:v", "libx264",
                                "-pix_fmt", "yuv420p", "-crf", "23", "-movflags", "+faststart", str(out)],
                               stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
        i = 0
        with heavy_lock():
            while True:
                buf = dec.stdout.read(pw * ph * 3)
                if len(buf) < pw * ph * 3:
                    break
                f = np.frombuffer(buf, np.uint8).reshape(ph, pw, 3).copy()
                over = f.copy()
                for l in labels:
                    a = static.get(l)
                    if a is None:
                        mm = masks[l].get(i)
                        if mm is None:
                            continue
                        a = rle_decode(mm)
                    over[a] = COL[l]
                    cnts, _ = cv2.findContours(a.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    cv2.drawContours(f, cnts, -1, COL[l], 2)
                    if cnts:
                        x, y, w_, h_ = cv2.boundingRect(max(cnts, key=cv2.contourArea))
                        txt = _nice(self.meas, l) + (" (moving)" if i in moving[l] else "") + (" (static)" if l in walls else "")
                        (tw, th), _ = cv2.getTextSize(txt, font, 0.45, 1)
                        cv2.rectangle(f, (x, max(0, y - th - 8)), (x + tw + 6, y), (20, 20, 20), -1)
                        cv2.putText(f, txt, (x + 3, y - 4), font, 0.45, COL[l], 1, cv2.LINE_AA)
                f = cv2.addWeighted(over, 0.35, f, 0.65, 0)
                cv2.rectangle(f, (0, 0), (pw, 44), (15, 15, 15), -1)
                cv2.putText(f, head, (8, 17), font, 0.38, (235, 235, 235), 1, cv2.LINE_AA)
                cv2.putText(f, f"masks: SAM 3.1, seeded by Muse Spark   t = {pts[min(i, len(pts) - 1)]:5.2f} s", (8, 36), font,
                            0.42, (190, 190, 190), 1, cv2.LINE_AA)
                enc.stdin.write(f.tobytes())
                i += 1
        enc.stdin.close(); enc.wait(timeout=600); dec.stdout.close(); dec.wait(timeout=60)
        return out

    def _evidence(self, tl):
        """Crops of each object at the seed frame and a strip for each ambiguous pick."""
        import cv2
        import imageio.v2 as iio
        from PIL import Image
        from mirror.perception.masks import rle_decode
        t = self._proxy_tracks()
        masks = t.masks["top"]
        pw, ph = t.size["top"]
        proxy = next(self.out.glob("work/proxy_*.mp4"))
        rd = iio.get_reader(str(proxy))
        seed = self.info("seed")["frame"]
        f0 = rd.get_data(seed)
        out = {}
        for l in [o["label"] for o in self.meas["objects"] if o["label"] in masks]:
            mm = masks[l].get(seed) or next(iter(masks[l].values()))
            a = rle_decode(mm).astype(np.uint8)
            if not a.any():
                continue
            g = f0.copy()
            ov = g.copy(); ov[a > 0] = (255, 120, 40); g = cv2.addWeighted(ov, 0.35, g, 0.65, 0)
            cnts, _ = cv2.findContours(a, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(g, cnts, -1, (255, 120, 40), 2)
            ys, xs = np.nonzero(a)
            pad = int(60 * pw / 540)
            X0, Y0, X1, Y1 = max(0, xs.min() - pad), max(0, ys.min() - pad), min(pw, xs.max() + pad), min(ph, ys.max() + pad)
            pth = self.p("evidence", f"{l}_seed.jpg")
            Image.fromarray(g[Y0:Y1, X0:X1]).save(pth, quality=88)
            out[l] = str(pth.relative_to(self.out))
        fps = float(self.clip(self.take).nominal_fps or 30)
        for l, (short, main) in self._pick_ambiguities(tl).items():
            tiles = []
            for tt in (short["from_s"], (short["from_s"] + main["from_s"]) / 2, main["from_s"], main["from_s"] + 0.6):
                k = min(int(round(tt * fps)), self.clip(self.take).n_frames - 1)
                g = rd.get_data(k).copy()
                mm = masks[l].get(k)
                if mm is not None:
                    a = rle_decode(mm).astype(np.uint8)
                    cnts, _ = cv2.findContours(a, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    cv2.drawContours(g, cnts, -1, (255, 120, 40), 2)
                cv2.putText(g, f"{tt:.1f} s", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
                tiles.append(Image.fromarray(g))
            w = tiles[0].width // 2
            strip = Image.new("RGB", (w * len(tiles), tiles[0].height // 2))
            for q, im in enumerate(tiles):
                strip.paste(im.resize((w, im.height // 2)), (w * q, 0))
            pth = self.p("evidence", f"{l}_pickup.jpg")
            strip.save(pth, quality=86)
            out[f"{l}_pickup"] = str(pth.relative_to(self.out))
        last = self.clip(self.take).n_frames - 1
        end_t = max([e["to_s"] for e in tl["events"]] + [0]) + 1.5
        k = min(last, int(round(end_t * fps)))
        pth = self.p("evidence", "end.jpg")
        Image.fromarray(rd.get_data(k)).save(pth, quality=86)
        out["end"] = str(pth.relative_to(self.out))
        out["end_t"] = round(k / fps, 2)
        rd.close()
        return out

    @staticmethod
    def _pick_ambiguities(tl) -> dict:
        """object -> (short early move, main move into the tray) when an object
        shows a short move just before the move that carries it to the tray."""
        out = {}
        by = {}
        for e in tl["events"]:
            by.setdefault(e["object"], []).append(e)
        for l, evs in by.items():
            main = next((e for e in evs if e["in_tray"]), None)
            if main is None:
                continue
            pre = [e for e in evs if e["to_s"] <= main["from_s"] and main["from_s"] - e["to_s"] < 3.0
                   and not e["in_tray"] and e["to_s"] - e["from_s"] < 0.6]
            if pre:
                out[l] = (pre[-1], main)
        return out

    def _questions(self, s, tl, ev) -> list[dict]:
        meas = self.meas
        seed_t = round(self.info("seed")["t"], 2)
        q = []
        for o in meas["objects"]:
            if o["proxy"] == "ignore":
                continue
            l, short = o["label"], _nice(meas, o["label"])
            d = _dims(o)
            dims = ", ".join(f"{k} {v['value_m'] * 100:g} cm" for k, v in d.items())
            from mirror.capture.proxy import SUPPORTED
            prox = {"cup": "the simulator's cup proxy (open box with simulated beads)", "block": "the block proxy (a 4 cm cube)",
                    "marker": "the marker proxy", "tray": "the destination tray (centre from the footage, your inside size)",
                    "wall": "a static wall sized from your measurements"}[o["proxy"]]
            q.append({"id": l, "kind": "object", "question": f"Is the outlined object your {o.get('real_identity') or short}"
                      + (f" ({dims})" if dims else "") + "?",
                      "options": [f"Yes, that's the {short}" + (" (destination)" if o["proxy"] == "tray" else ""), "No / wrong object"],
                      "proposal": f"Yes: track it as the {short}; in simulation it becomes {prox}. Proxy fidelity is not physical accuracy.",
                      "evidence": {"image": ev.get(l), "t_s": seed_t},
                      "resolves": f"mapping_unconfirmed: {l}" + ("; tray_not_mapped" if o["proxy"] == "tray" else "")})
        for l, (short, main) in self._pick_ambiguities(tl).items():
            nm = _nice(meas, l)
            q.append({"id": f"{l}_pickup", "kind": "event", "object": l,
                      "question": f"When did you start lifting the {nm}: about {main['from_s']:.1f} s, or already at {short['from_s']:.1f} s?",
                      "options": [f"{main['from_s']:.1f} s (MIRROR's proposal)", f"{short['from_s']:.1f} s", "Not sure"],
                      "proposal": f"{main['from_s']:.1f} s. At {short['from_s']:.1f} s the {nm}'s outline changed briefly (often a hand "
                                  f"arriving); the carry runs {main['from_s']:.1f} to {main['to_s']:.1f} s.",
                      "evidence": {"image": ev.get(f"{l}_pickup"), "t_s": short["from_s"]},
                      "resolves": "event bracket (applied as a correction only if you answer)"})
        if any(r.code == "recalibration_required" and not r.resolved for r in s.review):
            msg = next(r.message for r in s.review if r.code == "recalibration_required")
            q.append({"id": "camera_settings", "kind": "calibration",
                      "question": "Between the lens clip and the task video, did you change zoom, switch lens (0.5x / 1x / 2x / 3x), "
                                  "change the video mode, or tap to refocus?",
                      "options": ["No change, same 1x lens and mode", "Something changed", "Not sure", "I can film a new lens clip"],
                      "proposal": "The calibration flag stays either way (" + msg + "); only a new lens clip that passes clears it.",
                      "evidence": {"image": None, "t_s": (s.poses.get("top").pts_s if s.poses.get("top") else None)},
                      "resolves": "recorded only; recalibration_required stays open unless a new lens clip passes"})
        into = [e for e in tl["events"] if e["in_tray"]]
        order, seen = [], set()
        for e in sorted(into, key=lambda e: e["to_s"]):
            if e["object"] not in seen:
                order.append(e["object"]); seen.add(e["object"])
        cups = [o["label"] for o in meas["objects"] if o["proxy"] == "cup"]
        walls = _walls(meas)
        names = [_nice(meas, l) for l in order] or [_nice(meas, l) for l in _movers(meas)]
        prop = ("Put the " + ", ".join(names[:-1]) + (" and " if len(names) > 1 else "") + names[-1] + " into the tray"
                + (f", going around the {_nice(meas, walls[0])}" if walls else "")
                + (f", keeping the {_nice(meas, cups[0])} upright" if cups else "") + ".") if names else "Put the objects into the tray."
        if len(order) > 1:
            prop += " The order (" + ", ".join(names) + ") looked incidental."
        q.append({"id": "intent", "kind": "task", "question": "What was the task you meant to show?",
                  "options": ["As proposed"] + ([f"Order mattered too ({', '.join(names)})"] if len(order) > 1 else []) + ["Something else"],
                  "proposal": prop, "order_seen": order, "evidence": {"image": ev.get("end"), "t_s": ev.get("end_t")},
                  "resolves": "recorded as your intended specification, before MIRROR interprets the clip"})
        return q

    def correction_candidates(self) -> list[dict]:
        meas = self.meas
        cups = [o["label"] for o in meas["objects"] if o["proxy"] == "cup"]
        blocks = [o["label"] for o in meas["objects"] if o["proxy"] == "block"]
        walls = _walls(meas)
        out = []
        if cups and walls:
            cw = _nice(meas, cups[0]) if _nice(meas, cups[0]) in ("mug", "cup") else "cup"
            ww = _nice(meas, walls[0]) if _nice(meas, walls[0]) in ("divider", "wall", "book") else "wall"
            out.append({"id": f"{cups[0]}_clearance", "label": f"Keep the {_nice(meas, cups[0])} at least 6 cm away from the {_nice(meas, walls[0])} while carrying it.",
                        "compile_text": f"Keep the {cw} at least 6 cm away from the {ww} while carrying it.",
                        "rule": "keep_clearance · cup · transport · 6 cm",
                        "why": "Adds a safety margin around the obstacle; does not change order, goals or routes you asked for."})
        if blocks:
            out.append({"id": f"{blocks[0]}_upright", "label": f"Keep the {_nice(meas, blocks[0])} upright while it is carried.",
                        "compile_text": "Keep the block upright while it is carried.", "rule": "upright · block · transport · 15°",
                        "why": "A tall box tips easily; this asks the robot not to tip it in transit."})
        out.append({"id": "no_table_contact", "label": "Nothing may touch the table outside the tray while being carried.",
                    "compile_text": "Nothing may touch the table outside the tray.",
                    "rule": "forbidden_contact · every object · transport · table outside the tray",
                    "why": "No dragging or setting things down on the way; consistent with into-the-tray."})
        return out

    def stage_report(self):
        t0 = time.time()
        from mirror.capture.session import CaptureSession
        s = CaptureSession.load(self.p("session.json"))
        tl, series = self._timeline()
        _jdump(self.p("timeline.json"), tl)
        self._chart(tl, series)
        ev = self._evidence(tl)
        timings = {}
        ts = time.time()
        if self.render_on:
            self._overlay(tl)
        timings["overlay_s"] = round(time.time() - ts, 1)
        qs = self._questions(s, tl, ev)
        _jdump(self.p("questions.json"), {"about": "Questions for the person. Answer in answers.json as "
                                                   "{id: {answer, by: 'person', at: epoch_ms, seconds_spent}} (the page writes the "
                                                   "same shape). MIRROR never answers these itself.",
                                          "questions": qs, "correction_candidates": self.correction_candidates()})
        self.mark("report", t0, n_questions=len(qs), events_2d=len(tl["events"]), **timings)
        self._write_page()

    # ------------------------------------------------------------- page
    def _write_page(self):
        """JSON for the page / studio (same shape as the wiki's phone-capture real-demo entry)."""
        st = self.state["stages"]
        qf = self.out / "questions.json"
        qd = json.loads(qf.read_text()) if qf.exists() else {}
        tlf = self.out / "timeline.json"
        tl = json.loads(tlf.read_text()) if tlf.exists() else {}
        s = self._session_quick()
        page = {"labels": self.labels(), "status": self.state.get("status"), "waiting_for": self.state.get("waiting_for"),
                "mode": self.mode, "spend_usd": round(self.ledger.spent(), 4), "cap_usd": self.ledger.cap,
                "timings_s": {k: v.get("wall_s") for k, v in st.items()},
                "confirmations_needed": qd.get("questions", []), "correction_candidates": qd.get("correction_candidates", []),
                "events_2d": [{k: e[k] for k in ("object", "from_s", "to_s", "in_tray")} for e in tl.get("events", [])]}
        if "lens" in st:
            ln = st["lens"]
            page["calibration"] = {"lens": f"{'accepted' if ln.get('accepted') else 'REJECTED'}, {ln.get('rms_px') or float('nan'):.2f} px, "
                                           f"{ln.get('n_views')} views ({ln.get('how')})"}
        if s is not None:
            ok, blocking = s.gate()
            p = s.poses.get("top")
            if p is not None:
                page.setdefault("calibration", {})["pose"] = (f"board at rest {p.pts_s:.1f} s: {'accepted' if p.accepted else 'REJECTED'}, "
                                                              f"{p.rms_px:.2f} px, hold-out {p.holdout_mm or float('nan'):.1f} mm, "
                                                              f"{p.distance_m * 100:.0f} cm")
            rc = (s.recalibration.get("top") or {})
            fc = next((c for c in rc.get("checks", []) if "focal" in c.get("name", "")), None)
            if fc:
                page.setdefault("calibration", {})["focal_check"] = (f"{100 * fc['value']:+.1f}% (limit {100 * fc['threshold']:.0f}%): "
                                                                     + ("passed" if fc.get("passed") else "recalibration required stays open")) \
                    if isinstance(fc.get("value"), (int, float)) else str(fc)
            page["gate"] = {"passed": ok, "blocking": len(blocking), "session_id": s.session_id, "evidence_kind": s.evidence_kind,
                            "blocking_items": [{"code": b.code, "message": b.message} for b in blocking]}
        for k in ("task", "scene", "run", "correct"):
            if k in st:
                page[k] = {kk: vv for kk, vv in st[k].items() if kk not in ("status",)}
        paths = {}
        for name in ("session.json", "review.md", "questions.json", "timeline.json", "timeline.png", "overlay.mp4",
                     "session_answered.json", "review_answered.md", "spec.json", "scene/scene.json", "table_to_sim.json",
                     "run/report.json", "run/rollout.json", "run/plan.json", "run/sim_run.mp4", "correct/report_after.json",
                     "correct/rollout_after.json", "correct/recheck_before.json", "correct/before_after.mp4",
                     "correct/sim_after.mp4", "correct/spec_after.json", "evidence/grounding_boxes.jpg", "spend.jsonl",
                     "summary.md", "questions_spec.json", "tracks_full.json.gz"):
            if (self.out / name).exists():
                paths[name.replace("/", "_").replace(".", "_")] = name
        page["paths"] = paths
        page["headline"] = self._headline(page)
        _jdump(self.p("page.json"), page)
        return page

    def _headline(self, page) -> str:
        st = self.state["stages"]
        if "correct" in st and st["correct"].get("after_status"):
            c = st["correct"]
            return (f"Correction '{c.get('label')}' {('(rehearsal, not your choice) ' if c.get('rehearsal') else '')}compiled to "
                    f"{c.get('rule_summary')}; replanned run: {c.get('after_status')}; your first run re-checked under it: "
                    f"{c.get('recheck_status')}.")
        if "run" in st:
            r = st["run"]
            return (f"Robot run on your layout ({r.get('spec_source')}): {r.get('status')}, {r.get('violations')} violation(s)"
                    + (f", beads {r.get('beads')}" if r.get("beads") else "") + ".")
        if "task" in st:
            return st["task"].get("summary", "")
        if page.get("gate") is not None:
            return (f"MIRROR watched the clip and has {len(page.get('confirmations_needed', []))} question(s) for you. "
                    + ("The gate is closed until they are answered" if not page["gate"]["passed"] else "The gate is open") + ".")
        return "Running."

    # ========================================================== ANSWERS
    def _load_answers(self) -> Optional[dict]:
        if not self.answers_path.exists():
            return None
        d = json.loads(self.answers_path.read_text())
        d = d.get("answers", d)
        out = {}
        for k, v in d.items():
            if isinstance(v, dict) and v.get("by") == "person" and v.get("answer") is not None:
                out[k] = v
        return out

    def stage_answers(self):
        from mirror.capture import events as EV, session as SE
        from mirror.capture.session import CaptureSession
        t0 = time.time()
        ans = self._load_answers()
        if ans is None:
            raise StageStop(f"answers: waiting for the person's answers in {self.answers_path} (questions in questions.json)")
        qs = {q["id"]: q for q in json.loads(self.p("questions.json").read_text())["questions"]}
        confirmed, rejected, corrections, applied, notes = set(), set(), [], {}, []
        for o in self.meas["objects"]:
            a = ans.get(o["label"])
            if a is None:
                continue
            at_s = float(a.get("at", time.time() * 1000)) / 1000.0
            if str(a["answer"]).strip().lower().startswith("yes"):
                confirmed.add(o["label"])
                corrections.append(EV.Correction(target=f"object:{o['label']}", field="confirm", after=str(a["answer"]),
                                                 by="person", at=at_s, seconds_spent=a.get("seconds_spent")))
            else:
                rejected.add(o["label"])
                corrections.append(EV.Correction(target=f"object:{o['label']}", field="reject", after=str(a["answer"]),
                                                 by="person", at=at_s, seconds_spent=a.get("seconds_spent")))
        applied["objects_confirmed"] = sorted(confirmed)
        applied["objects_rejected"] = sorted(rejected)
        if "camera_settings" in ans:
            notes.append("camera settings (person): " + str(ans["camera_settings"]["answer"])
                         + "; recorded only, the calibration flag is not resolved by an answer")
            applied["camera_settings"] = ans["camera_settings"]["answer"]
        s = self._build_session(confirmed, rejected, corrections, notes)
        # event answers: map the answered time onto the capture's own pick event
        ev_corr = []
        for qid, q in qs.items():
            if q.get("kind") != "event" or qid not in ans:
                continue
            a = ans[qid]
            mt = re.search(r"(\d+(?:\.\d+)?)\s*s", str(a["answer"]))
            if not mt:
                applied[qid] = f"{a['answer']} (no change)"
                continue
            x = float(mt.group(1))
            picks = [e for e in s.events if e.object == q["object"] and e.type == "pick"]
            if not picks:
                applied[qid] = f"{a['answer']}: no pick event to correct"
                continue
            inside = [e for e in picks if e.t_lo - 0.35 <= x <= e.t_hi + 0.35]
            e = min(inside or picks, key=lambda e: abs(e.t - x))
            c = EV.Correction(target=e.id, field="confirm" if inside else "t",
                              before=f"pick at {e.t:.2f} s ({e.t_lo:.2f}-{e.t_hi:.2f})",
                              after=str(a["answer"]) if inside else str(x), by="person",
                              at=float(a.get("at", time.time() * 1000)) / 1000.0, seconds_spent=a.get("seconds_spent"))
            ev_corr.append(c)
            applied[qid] = f"event {e.id} ({e.t_lo:.2f}-{e.t_hi:.2f} s) " + ("confirmed" if inside else f"moved to {x:.2f} s") + " by person"
        if ev_corr:
            s = self._build_session(confirmed, rejected, corrections + ev_corr, notes)
        if "intent" in ans:
            applied["intent"] = ans["intent"]["answer"]
            _jdump(self.p("intent.json"), {"answer": ans["intent"]["answer"], "by": "person", "at": ans["intent"].get("at"),
                                           "seconds_spent": ans["intent"].get("seconds_spent"),
                                           "proposal": qs.get("intent", {}).get("proposal"),
                                           "order_seen": qs.get("intent", {}).get("order_seen", [])})
        if "correction" in ans:
            applied["correction"] = ans["correction"]["answer"]
        applied["seconds_spent_total"] = round(sum(float(v.get("seconds_spent") or 0) for v in ans.values()), 1)
        s.save(self.p("session_answered.json"))
        lines = SE.summary_lines(s)
        self.p("review_answered.md").write_text("# Capture review (answers applied)\n\n"
                                                + "\n".join(x if x.startswith("- ") else f"- {x}" for x in lines) + "\n")
        ok, blocking = s.gate()
        self.mark("answers", t0, applied=applied, gate_passed=ok, blocking=[b.code for b in blocking],
                  events=[{"id": e.id, "object": e.object, "type": e.type, "t": round(e.t, 2),
                           "bracket": [round(e.t_lo, 2), round(e.t_hi, 2)], "status": e.status,
                           "placed_in": getattr(e, "placed_in", None)} for e in s.events])
        self._write_page()

    # ============================================================= TASK
    def _stated_spec(self, s, intent: dict):
        """The task the person said they meant (answers 'intent'), written as a spec.
        Provenance 'manual', confirmed by the person; never presented as MIRROR's inference."""
        from mirror.contracts import TaskSpec
        from mirror.interpret import validate_spec
        ans = str(intent["answer"])
        kinds_by_label = {m.track_label: m.proxy for m in s.mappings if m.confirmed_by_person}
        movers = [l for l in _movers(self.meas) if kinds_by_label.get(l) in ("cup", "block", "marker")]
        if not movers:
            raise StageStop("stated task: no confirmed objects to move")
        if ans.lower().startswith("something else"):
            raise StageStop("stated task: 'Something else' needs the task in words; MIRROR will not guess it")
        order = [l for l in intent.get("order_seen", []) if l in movers] or movers
        ev = f"your stated intent (answers/intent: '{ans}' on top of the proposal '{intent.get('proposal')}')"
        prov = {"source": "manual", "evidence": ev, "version_introduced": 1, "model": None}
        sel = lambda k: {"kind": k, "role": None, "ids": None}
        kinds = []
        for l in order:
            if kinds_by_label[l] not in kinds:
                kinds.append(kinds_by_label[l])
        goals = [{"id": f"g_inside_{k}", "type": "inside", "object": sel(k), "status": "confirmed", "provenance": prov} for k in kinds]
        ordering = []
        if ans.lower().startswith("order mattered"):
            ordering = [{"id": f"o_{a}_before_{b}", "before": sel(a), "after": sel(b), "status": "confirmed", "provenance": prov}
                        for a, b in zip(kinds, kinds[1:])]
        cons = []
        if "cup" in kinds:
            for ph in ("transport", "final"):
                cons.append({"id": f"c_upright_{ph}_cup", "type": "upright", "applies_to": sel("cup"), "phase": ph,
                             "params": {"max_tilt_deg": 15.0}, "status": "confirmed", "provenance": prov,
                             "description": f"Keep the cup ({_nice(self.meas, next(l for l in order if kinds_by_label[l] == 'cup'))}) "
                                            f"within 15 degrees of upright {'while carried' if ph == 'transport' else 'once placed'}."})
        walls = [l for l in _walls(self.meas) if kinds_by_label.get(l) == "wall"]
        if walls:
            cons.append({"id": "c_no_contact_obstacle", "type": "forbidden_contact", "applies_to": {"kind": None, "role": None, "ids": None},
                         "phase": "always", "params": {"with": ["obstacle"]}, "status": "confirmed", "provenance": prov,
                         "description": f"Go around the {_nice(self.meas, walls[0])}: nothing touches it."})
        names = [_nice(self.meas, l) for l in order]
        summary = ("Pack the " + ", ".join(names) + " into the tray" + (" in that order" if ordering else "")
                   + (f", around the {_nice(self.meas, walls[0])}" if walls else "") + (", cup upright" if "cup" in kinds else "") + ".")
        spec = TaskSpec.model_validate({
            "spec_id": f"spec_{s.session_id}_stated", "version": 1, "summary": summary,
            "bindings": [{"role": {"cup": "container"}.get(k, k), "kind": k} for k in kinds],
            "goals": goals, "ordering": ordering, "constraints": cons,
            "interpreter": "person (stated intent), not a model", "demo_id": "phone_" + s.session_id})
        errs = validate_spec(spec)
        if errs:
            raise RuntimeError("stated spec invalid: " + "; ".join(errs))
        return spec

    def stage_task(self):
        from mirror.capture import trace as T
        from mirror.capture.session import CaptureSession
        from mirror.interpret import CassetteMiss, confirm_items, propose_spec, validate_spec
        t0 = time.time()
        s = CaptureSession.load(self.p("session_answered.json"))
        ok, blocking = s.gate()
        intent_f = self.p("intent.json")
        intent = json.loads(intent_f.read_text()) if intent_f.exists() else None
        info = {"gate_passed": ok, "blocking": [{"code": b.code, "message": b.message} for b in blocking]}
        if ok:
            trace = T.to_demo_trace(s)
            (self.p("demo_trace.json")).write_text(trace.model_dump_json())
            client = self.llm()
            try:
                spec = propose_spec(trace, None if client.offline else client)
                interp = spec.interpreter
            except CassetteMiss:
                spec = propose_spec(trace, None)
                interp = spec.interpreter + " (replay had no Muse Spark recording for this capture)"
            self.p("spec_proposed.json").write_text(spec.model_dump_json(indent=1))
            ans = self._load_answers() or {}
            rev = ans.get("spec_review")
            items = [r for r in list(spec.constraints) + list(spec.ordering) if getattr(r, "status", None) == "proposed"]
            if rev is None and items:
                qs = [{"id": f"rule:{r.id}", "kind": "rule", "question": f"Keep this rule? {getattr(r, 'description', '') or r.id}",
                       "options": ["Keep it", "Drop it"], "rule": r.model_dump(mode="json")} for r in items]
                _jdump(self.p("questions_spec.json"), {"about": "Review of MIRROR's proposed rules. Answer in answers.json as "
                                                       "spec_review: {answer: {confirm: [ids], reject: [ids]}, by: 'person', at}.",
                                                       "interpreter": interp, "summary": spec.summary, "questions": qs})
                info.update(summary=f"MIRROR proposed a task from your video ({interp}); waiting for your review of "
                                    f"{len(items)} proposed rule(s).", interpreter=interp)
                self.state["stages"]["task"] = {"status": "waiting", **info}
                self._save(); self._write_page()
                raise StageStop("task: waiting for the person's review of the proposed rules (questions_spec.json)")
            a = (rev or {}).get("answer") or {}
            spec = confirm_items(spec, confirm=list(a.get("confirm", [])), reject=list(a.get("reject", [])),
                                 text="person's review of MIRROR's proposal")[0] if rev else spec
            source = f"MIRROR's proposal from your video ({interp}), reviewed by you"
        else:
            try:
                T.propose_offline_from_capture(s)
                refusal = "UNEXPECTED: proposal allowed with blocking items"
            except T.CaptureNotReviewed as e:
                refusal = "refused by CaptureSession.gate(): " + str(e)
            info["proposal_from_capture"] = refusal
            if intent is None:
                self.state["stages"]["task"] = {"status": "waiting", **info,
                                                "summary": "The gate refused a task proposal and no stated task was given."}
                self._save(); self._write_page()
                raise StageStop("task: gate refused (" + "; ".join(b.code for b in blocking) + ") and no stated task (answers 'intent')")
            spec = self._stated_spec(s, intent)
            source = "your stated task (the gate refused a proposal, so nothing was inferred from the video)"
            interp = "none (gate closed)"
        errs = validate_spec(spec)
        self.p("spec.json").write_text(spec.model_dump_json(indent=1))
        info.update(spec_source=source, interpreter=interp, spec_summary=spec.summary, spec_errors=errs,
                    rules=[{"id": r.id, "kind": k, "status": getattr(r, "status", None),
                            "text": getattr(r, "description", "") or r.id}
                           for k, coll in (("goal", spec.goals), ("ordering", spec.ordering), ("constraint", spec.constraints))
                           for r in coll],
                    summary=("Gate passed: " if ok else "Gate refused a proposal from the clip; ") + f"spec = {source}.")
        self.mark("task", t0, **info)
        self._write_page()

    # ============================================================ ROBOT
    @staticmethod
    def reach_problems(scene) -> list[str]:
        ws = scene.workspace
        out = []
        for t in scene.trays:
            tx, ty = t.pose.pos[:2]
            w, z = t.pose.quat[0], t.pose.quat[3]
            yaw = 2 * math.atan2(z, w)
            hx = t.inner_half[0] + t.wall_thickness
            hy = t.inner_half[1] + t.wall_thickness
            ex = abs(math.cos(yaw)) * hx + abs(math.sin(yaw)) * hy
            ey = abs(math.sin(yaw)) * hx + abs(math.cos(yaw)) * hy
            if not (ws.x_range[0] + 0.02 <= tx - ex and tx + ex <= ws.x_range[1] - 0.02
                    and ws.y_range[0] + 0.02 <= ty - ey and ty + ey <= ws.y_range[1] - 0.02):
                out.append(f"{t.id} not fully inside the robot's reach")
        for o in scene.objects:
            x, y = o.pose.pos[:2]
            if not (ws.x_range[0] + 0.03 <= x <= ws.x_range[1] - 0.03 and ws.y_range[0] + 0.03 <= y <= ws.y_range[1] - 0.03):
                out.append(f"{o.id} outside the robot's reach")
        return out

    def _wall_axis(self, s, label):
        """Wall axis in the table frame from its static mask (single view, approximate)."""
        import cv2
        PT = __import__('importlib').import_module('mirror.perception.track')
        from mirror.perception.masks import rle_decode
        mp = next(m for m in s.mappings if m.track_label == label)
        H = mp.real_dims["height"].value_m if "height" in mp.real_dims else 0.2
        intr, pose = s.intrinsics["top"], s.poses["top"]
        tr = PT.load_tracks(self.p("tracks_full.json.gz"))
        fr = tr.masks["top"][label]
        mask = rle_decode(fr.get(self.info("seed")["frame"]) or next(iter(fr.values())))
        ys, xs = np.nonzero(mask)
        P = np.stack([xs, ys], 1).astype(float); mu = P.mean(0)
        w_, v_ = np.linalg.eigh(np.cov((P - mu).T)); ax = v_[:, 1]
        proj = (P - mu) @ ax; a, b = np.percentile(proj, [2, 98])
        ends = np.array([mu + a * ax, mu + b * ax])
        und = cv2.undistortPoints(ends.reshape(-1, 1, 2), intr.Km, intr.dm).reshape(-1, 2)
        R = np.asarray(pose.R, float); t = np.asarray(pose.t, float).reshape(3); C = -R.T @ t

        def hit(n, z):
            d = R.T @ np.array([n[0], n[1], 1.0]); lam = (z - C[2]) / d[2]; return C + lam * d
        E = np.array([hit(n, H / 2) for n in und])
        dvec = E[1] - E[0]
        seen = float(np.linalg.norm(dvec[:2])); dirn = dvec[:2] / max(seen, 1e-9)
        return E, seen, math.atan2(-dirn[0], dirn[1])

    def _scene_for(self, s, placement):
        from mirror.capture import trace as T
        s = s.model_copy(deep=True)
        s.placement = placement
        scene = T.to_scene(s)
        notes = []
        walls = [m.track_label for m in s.mappings if m.proxy == "wall" and m.confirmed_by_person]
        if walls and scene.obstacles:
            E, seen, yaw = self._wall_axis(s, walls[0])
            mid = T._to_sim(s, (E[0] + E[1]) / 2)
            yaw_sim = yaw + math.radians(placement.yaw_deg)
            q = (math.cos(yaw_sim / 2), 0.0, 0.0, math.sin(yaw_sim / 2))
            obs = [o.model_copy(update={"pose": o.pose.model_copy(update={"pos": (float(mid[0]), float(mid[1]), 0.0), "quat": q})})
                   if k == 0 else o for k, o in enumerate(scene.obstacles)]
            scene = scene.model_copy(update={"obstacles": obs})
            notes.append({"wall": walls[0], "endpoints_table_m": E.round(4).tolist(), "seen_length_m": round(seen, 3),
                          "yaw_deg_table": round(math.degrees(yaw), 1),
                          "method": "static SAM mask major axis (2-98%), back-projected to half the measured height; single view, approximate"})
        return scene, notes

    def stage_scene(self):
        from mirror.capture import proxy as PX, trace as T
        from mirror.capture.session import CaptureSession
        t0 = time.time()
        s = CaptureSession.load(self.p("session_answered.json"))
        real = PX.WorkspacePlacement(source="default_centred")
        scene, wall_notes = self._scene_for(s, real)
        probs = self.reach_problems(scene)
        placement, label = real, ("real table frame: the board's centre sits at the simulator table's centre; your layout's "
                                  "distances and directions are kept")
        fell_back = None
        if probs:
            pts = []
            rec = s.reconstruction
            for m in s.mappings:
                if m.confirmed_by_person and m.proxy in ("cup", "block", "marker", "wall"):
                    p = T._start_rest(rec.objects[m.track_label]) if m.track_label in rec.objects else None
                    if p is not None:
                        pts.append(p[:2])
            if s.tray is not None:
                tc, th = np.array(s.tray.center_xy), np.array(s.tray.inner_half)
                pts += [tc + th, tc - th]
            for wn in wall_notes:
                pts += [np.array(wn["endpoints_table_m"][0][:2]), np.array(wn["endpoints_table_m"][1][:2])]
            pts = np.array(pts); c = (pts.min(0) + pts.max(0)) / 2
            alt = PX.WorkspacePlacement(origin_xy_m=(float(-c[0]), float(-c[1])), yaw_deg=0.0, source="auto_layout_centred")
            scene2, wall_notes2 = self._scene_for(s, alt)
            probs2 = self.reach_problems(scene2)
            shift = np.array(alt.origin_xy_m) - np.array(real.origin_xy_m)
            fell_back = {"why": probs, "shift_cm": (shift * 100).round(1).tolist(), "still": probs2}
            placement, scene, wall_notes = alt, scene2, wall_notes2
            label = (f"shifted by software ({shift[0] * 100:+.1f} cm, {shift[1] * 100:+.1f} cm) so the layout fits the robot's reach "
                     f"({'; '.join(probs)} in the real table frame); distances between objects and directions are kept")
        scene = scene.model_copy(update={"notes": scene.notes + " Placement: " + label + "."
                                         + (" Wall orientation from its mask (single view, approximate)." if wall_notes else "")})
        self.p("scene", "scene.json").write_text(scene.model_dump_json(indent=1))
        pose = s.poses.get("top")
        intr = s.intrinsics.get("top")
        tts = {"placement": placement.model_dump(), "label": label, "fell_back": fell_back,
               "convention": "sim_xy = Rz(yaw_deg) @ table_xy + origin_xy_m; z unchanged (table top z = 0 in both). The table frame is "
                             "MIRROR's board frame (mirror.capture.calibrate: printed ChArUco board, metres, z up).",
               "camera": None if pose is None else {"K": intr.K, "dist": intr.dist, "image_size": intr.image_size,
                                                    "R_table_to_cam": pose.R, "t_table_to_cam": pose.t,
                                                    "pose_frame": pose.frame_index, "pose_t_s": pose.pts_s,
                                                    "pose_accepted": pose.accepted,
                                                    "note": "x_cam = R x_table + t; calibration reuse may be flagged (see labels)"},
               "wall": wall_notes}
        _jdump(self.p("table_to_sim.json"), tts)
        self.mark("scene", t0, placement=placement.source, label=label, reach_problems=self.reach_problems(scene),
                  objects={o.id: [round(v, 3) for v in o.pose.pos] for o in scene.objects},
                  trays={t.id: [round(v, 3) for v in t.pose.pos] for t in scene.trays},
                  obstacles={o.id: [round(v, 3) for v in o.pose.pos] for o in scene.obstacles})

    @staticmethod
    def _run_summary(rep, scene) -> dict:
        cups = [o.id for o in scene.objects if o.kind == "cup"]
        oc = {o.object_id: o for o in rep.outcomes}
        beads = None
        if cups and cups[0] in oc and oc[cups[0]].beads_total:
            beads = f"{oc[cups[0]].beads_in_container} of {oc[cups[0]].beads_total} kept in the cup (simulated)"
        return {"status": rep.status, "plan_status": rep.plan_status, "plan_reason": rep.plan_reason,
                "violations": len(rep.violations), "beads": beads,
                "max_cup_tilt_deg": round(oc[cups[0]].max_transport_tilt_deg, 1) if cups and cups[0] in oc else None,
                "routes": list(rep.route_labels), "sim_time_s": round(rep.execution_time_s, 1),
                "planning_s": round(rep.planning_time_s, 2),
                "rules": [{"id": r.rule_id, "kind": r.kind, "verdict": r.verdict, "enforced": r.enforced} for r in rep.rule_results]}

    def stage_run(self):
        from mirror.contracts import SceneState, TaskSpec
        from mirror.pipeline import Session
        t0 = time.time()
        scene = SceneState.model_validate_json(self.p("scene", "scene.json").read_text())
        spec = TaskSpec.model_validate_json(self.p("spec.json").read_text())
        sess = Session(scene=scene, client=None)
        sess.specs = [spec]
        entry = sess.run(condition="phone take layout, " + self.info("task").get("spec_source", ""))
        rep = entry.rollout.report
        self.p("run", "report.json").write_text(rep.model_dump_json(indent=1))
        self.p("run", "rollout.json").write_text(entry.rollout.model_dump_json())
        self.p("run", "plan.json").write_text(entry.plan.model_dump_json(indent=1))
        sess.save_state(self.p("run", "session_state", "x").parent)
        self.mark("run", t0, rollout_id=entry.rollout_id, spec_source=self.info("task").get("spec_source"),
                  **self._run_summary(rep, scene))
        self._write_page()

    def _render(self, rollout_path: Path, out_mp4: Path, title: str):
        from mirror.sim.render import render_rollout
        with heavy_lock():
            render_rollout(str(rollout_path), str(out_mp4), camera="overview", speed=2.0, width=960, height=544, title=title)

    def stage_render(self):
        t0 = time.time()
        if not self.render_on:
            self.mark("render", t0, skipped=True)
            return
        rep = json.loads(self.p("run", "report.json").read_text())
        if rep["status"] == "planning_failed":
            self.mark("render", t0, skipped=True, why="planning failed: nothing to render")
            return
        self._render(self.p("run", "rollout.json"), self.p("run", "sim_run.mp4"),
                     "MIRROR robot run from your layout (proxies; " + ("spec = your stated task" if "stated" in
                                                                      self.info("task").get("spec_source", "") else "spec reviewed by you") + ")")
        self.mark("render", t0, path="run/sim_run.mp4")

    # ------------------------------------------------------- correction
    def _choose_correction(self):
        cands = self.correction_candidates()
        ans = self._load_answers() or {}
        a = ans.get("correction")
        if a is not None:
            txt = str(a["answer"]).strip()
            for c in cands:
                if txt in (c["id"], c["label"], c["compile_text"]):
                    return c, False, a
            return {"id": "custom", "label": txt, "compile_text": txt, "rule": "as compiled", "why": "your own words"}, False, a
        if self.rehearse:
            c = next((c for c in cands if c["id"] == self.rehearse), None)
            if c is None:
                raise SystemExit(f"--rehearse-correction: no candidate {self.rehearse!r}; candidates: {[c['id'] for c in cands]}")
            return c, True, None
        return None, None, None

    def stage_correct(self):
        from mirror.contracts import SceneState
        from mirror.interpret import CassetteMiss
        from mirror.interpret.llm import LLMClient
        from mirror.pipeline import Session
        t0 = time.time()
        cand, rehearsal, ans = self._choose_correction()
        if cand is None:
            raise StageStop("correction: waiting for the person's choice (answers 'correction'; candidates in questions.json)")
        run = self.info("run")
        if run.get("status") == "planning_failed":
            raise StageStop("correction: the first run did not plan, so there is nothing to correct against")
        sess = Session.load_state(self.p("run", "session_state", "x").parent)
        client = self.llm()
        compiler = "offline rules" if client.offline else f"{client.model} ({client.mode})"
        try:
            sess.client = client
            new_spec, rec = sess.correct(cand["compile_text"])
        except CassetteMiss:
            sess.client = LLMClient.offline_client()
            new_spec, rec = sess.correct(cand["compile_text"])
            compiler = "offline rules (replay had no Muse Spark recording for this correction)"
        before_id = run["rollout_id"]
        after = sess.run(condition="after correction: " + cand["label"])
        recheck = sess.recheck(before_id, spec_version=new_spec.version)
        scene = sess.scene
        rep_b = sess.find_rollout(before_id)[0].rollout.report
        rep_a = after.rollout.report
        self.p("correct", "spec_after.json").write_text(new_spec.model_dump_json(indent=1))
        self.p("correct", "report_after.json").write_text(rep_a.model_dump_json(indent=1))
        self.p("correct", "rollout_after.json").write_text(after.rollout.model_dump_json())
        self.p("correct", "recheck_before.json").write_text(recheck.model_dump_json(indent=1))
        sess.save_state(self.p("correct", "session_state", "x").parent)
        new_ids = {r.rule_id for r in rep_a.rule_results} - {r.rule_id for r in rep_b.rule_results}

        def verdicts(rep):
            return {r.rule_id: r.verdict for r in rep.rule_results}
        vb, vr, va = verdicts(rep_b), verdicts(recheck), verdicts(rep_a)
        table = [{"rule": rid, "new": rid in new_ids, "first_run": vb.get(rid, "not in spec"),
                  "first_run_rechecked": vr.get(rid), "after_correction": va.get(rid)}
                 for rid in list(dict.fromkeys(list(va) + list(vb)))]
        ops = [o.model_dump(mode="json") for o in rec.diff]
        rule_summary = "; ".join(f"{o['op']} {(o.get('value') or {}).get('type', '')} {(o.get('value') or {}).get('id', o.get('target_id') or '')}".strip()
                                 for o in ops) or "no change"
        timings = {}
        if self.render_on and rep_a.status != "planning_failed":
            ts = time.time()
            self._render(self.p("correct", "rollout_after.json"), self.p("correct", "sim_after.mp4"),
                         "After the correction: " + cand["label"] + (" (rehearsal)" if rehearsal else ""))
            b, a2 = self.p("run", "sim_run.mp4"), self.p("correct", "sim_after.mp4")
            if b.exists() and a2.exists():
                with heavy_lock():
                    _run(["ffmpeg", "-y", "-v", "error", "-i", str(b), "-i", str(a2), "-filter_complex",
                          "[0:v]tpad=stop_mode=clone:stop_duration=60[l];[1:v]tpad=stop_mode=clone:stop_duration=60[r];"
                          "[l][r]hstack=inputs=2,scale=1280:-2[v]", "-map", "[v]", "-shortest", "-c:v", "libx264",
                          "-pix_fmt", "yuv420p", "-crf", "23", "-movflags", "+faststart", str(self.p("correct", "before_after.mp4"))])
            timings["render_s"] = round(time.time() - ts, 1)
        self.mark("correct", t0, candidate=cand["id"], label=cand["label"], compile_text=cand["compile_text"],
                  rehearsal=bool(rehearsal), chosen_by="rehearsal (not the person's choice)" if rehearsal else "person",
                  compiler=compiler, ops=ops, rule_summary=rule_summary, spec_version_after=new_spec.version,
                  after_status=rep_a.status, recheck_status=recheck.status,
                  after=self._run_summary(rep_a, scene), recheck=self._run_summary(recheck, scene), table=table, **timings)
        self._write_page()

    # ---------------------------------------------------------- publish
    def stage_publish(self):
        t0 = time.time()
        st = self.state["stages"]
        lines = ["# MIRROR real run", "", "Labels: " + "; ".join(self.labels()), "",
                 f"Mode: {self.mode}. Spend this directory: ${self.ledger.spent():.4f} of ${self.ledger.cap:.2f} cap.", "",
                 "| stage | wall s |", "|---|---|"]
        lines += [f"| {k} | {v.get('wall_s')} |" for k, v in st.items()]
        lines.append(f"| total | {round(sum(float(v.get('wall_s') or 0) for v in st.values()), 1)} |")
        if "task" in st:
            lines += ["", f"Task: {st['task'].get('summary')}", f"Spec: {st['task'].get('spec_summary')}"]
        if "scene" in st:
            lines += ["", f"Placement: {st['scene'].get('label')}"]
        if "run" in st:
            r = st["run"]
            lines += ["", f"Run: {r.get('status')}, {r.get('violations')} violation(s), beads {r.get('beads')}, routes {r.get('routes')}"]
        if "correct" in st:
            c = st["correct"]
            lines += ["", f"Correction ({c.get('chosen_by')}): {c.get('label')} -> {c.get('rule_summary')} [{c.get('compiler')}]",
                      f"After: {c.get('after_status')}; first run re-checked: {c.get('recheck_status')}", "",
                      "| rule | first run | first run re-checked | after |", "|---|---|---|---|"]
            lines += [f"| {t['rule']}{' (new)' if t['new'] else ''} | {t['first_run']} | {t['first_run_rechecked']} | {t['after_correction']} |"
                      for t in c.get("table", [])]
        self.p("summary.md").write_text("\n".join(lines) + "\n")
        self.mark("publish", t0)
        self._write_page()


# ===================================================================== CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m mirror.capture.real_run", description=__doc__.split("\n\n")[0])
    ap.add_argument("--take", type=Path, help="task video (the person doing the task, board at rest at the start)")
    ap.add_argument("--lens", type=Path, help="lens-calibration clip (board waved in front of the same phone, same settings)")
    ap.add_argument("--intrinsics", type=Path, help="reuse an accepted lens calibration instead of --lens")
    ap.add_argument("--measurements", type=Path, help="objects, names and ruler measurements (JSON, see docs/REAL_RUN.md)")
    ap.add_argument("--out", type=Path, required=True, help="output directory (resumable)")
    ap.add_argument("--mode", choices=["record", "replay", "offline"], default=None,
                    help="record: live Meta models, every response saved; replay: recordings only, no network; "
                         "offline: no model (stops before grounding)")
    ap.add_argument("--cap-usd", type=float, default=None, help="hard spend cap for this output directory (default 10)")
    ap.add_argument("--prior-usd", type=float, default=0.0, help="spend already used against the same cap elsewhere")
    ap.add_argument("--cassettes", type=Path, help="cassette directory (default OUT/cassettes)")
    ap.add_argument("--answers", type=Path, help="the person's answers (default OUT/answers.json)")
    ap.add_argument("--seed-t", type=float, help="ground on this time instead of the automatic clean frame")
    ap.add_argument("--rehearse-correction", help="with no correction chosen by the person, rehearse this candidate "
                                                  "(labelled as a rehearsal, not the person's choice)")
    ap.add_argument("--no-render", action="store_true", help="skip video rendering")
    ap.add_argument("--dev-clip", help="label the take as a development clip with this name (never scored)")
    ap.add_argument("--label", action="append", default=[], help="extra honest label shown with every output")
    ap.add_argument("--media-cache", type=Path, help="directory with already-derived media (proxy/subclips) to reuse")
    ap.add_argument("--until", choices=STAGES, help="stop after this stage")
    ap.add_argument("--redo", choices=STAGES, help="forget this stage and every later one, then continue")
    a = ap.parse_args(argv)
    st_path = a.out / "real_run.json"
    prev = json.loads(st_path.read_text()) if st_path.exists() else {}
    mode = a.mode or prev.get("mode") or "replay"
    cap = a.cap_usd if a.cap_usd is not None else prev.get("cap_usd", 10.0)
    if mode == "replay":
        os.environ.pop("MODEL_API_KEY", None)
    if a.redo and prev:
        k = STAGES.index(a.redo)
        for s2 in STAGES[k:]:
            prev.get("stages", {}).pop(s2, None)
        _jdump(st_path, prev)
    r = RealRun(a.out, take=a.take, lens=a.lens, measurements=a.measurements, mode=mode, cap_usd=cap,
                prior_usd=a.prior_usd, cassettes=a.cassettes, intrinsics=a.intrinsics, seed_t=a.seed_t,
                answers=a.answers, rehearse_correction=a.rehearse_correction, render=not a.no_render,
                label_extra=a.label, dev_clip=a.dev_clip, media_cache=a.media_cache)
    st = r.run(until=a.until)
    print(json.dumps({"status": st.get("status"), "waiting_for": st.get("waiting_for"), "spend_usd": st.get("spend_usd"),
                      "stages": {k: v.get("wall_s") for k, v in st["stages"].items()}}, indent=1))
    return 0 if st.get("status") in ("complete", "waiting") else 2


if __name__ == "__main__":
    sys.exit(main())
