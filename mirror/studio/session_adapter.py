"""Studio service: one interactive session plus background jobs and events.

The studio drives ``mirror.pipeline.Session`` when the simulation, planning,
interpretation and evaluation packages are importable, and otherwise falls
back to the kinematic fixture session in ``mirror.studio.fixtures`` (clearly
labelled as such in every snapshot). Everything the browser sees is produced
by :meth:`StudioService.snapshot` and :meth:`StudioService.rollout_payload`,
which are also what ``scripts/export_static.py`` writes to disk.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

from ..contracts import (DemoTrace, ObjectSpec, ObstacleSpec, PlanResult, Pose, Rollout,
                         RolloutFrame, RolloutReport, SceneState, SlotSpec,
                         SpecOp, TaskSpec, TraySpec, VisualScene)
from ..geometry import WORLD

SNAPSHOT_FORMAT = "mirror-studio-session/1"
ROLLOUT_FORMAT = "mirror-studio-rollout/1"
REPO_ROOT = Path(__file__).resolve().parents[2]

REFERENCE_CORRECTION = "This container must remain upright throughout transport, not just at the destination."

# The studio never generates evaluation-split layouts. Its own paired runs use a
# disjoint showcase range of development seeds; pre-registered evaluation
# results are only ever loaded from the files the evaluation package wrote.
SHOWCASE_SPLIT = "dev"
SHOWCASE_SEEDS = range(9000, 9100)


def max_eval_seeds() -> int:
    """Largest showcase run one request may ask for (public server guard)."""
    try:
        return max(1, int(os.environ.get("MIRROR_MAX_EVAL_SEEDS") or 6))
    except ValueError:
        return 6


class EvaluationFenceError(RuntimeError):
    """Raised when a request would generate or run evaluation-split layouts."""


def check_showcase(seeds, split: str) -> list[int]:
    seeds = [int(x) for x in seeds]
    if split != SHOWCASE_SPLIT:
        raise EvaluationFenceError(
            f"the studio runs paired comparisons only on the '{SHOWCASE_SPLIT}' split; "
            "load pre-registered evaluation results instead")
    bad = [x for x in seeds if x not in SHOWCASE_SEEDS]
    if bad:
        raise EvaluationFenceError(
            f"seeds {bad[:5]} are outside the showcase range {SHOWCASE_SEEDS.start}-{SHOWCASE_SEEDS.stop - 1}")
    if not seeds:
        raise EvaluationFenceError("no seeds")
    return seeds


def _dump(x: Any) -> Any:
    if x is None:
        return None
    if hasattr(x, "model_dump"):
        return x.model_dump(mode="json")
    if isinstance(x, dict):
        return {k: _dump(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_dump(v) for v in x]
    if hasattr(x, "__dataclass_fields__"):
        return {k: _dump(getattr(x, k)) for k in x.__dataclass_fields__}
    return x


def packages_available() -> tuple[bool, list[str]]:
    """True when every package the live session needs exposes its contract."""
    need = {
        "mirror.pipeline": ["Session"],
        "mirror.scenes": ["reference_scene", "sample_layout"],
        "mirror.sim": ["SimEnv", "record_scripted_demo"],
        "mirror.interpret": ["propose_spec", "compile_correction", "LLMClient"],
        "mirror.plan": ["plan"],
        "mirror.evaluate": ["check"],
    }
    missing = []
    for mod, names in need.items():
        try:
            m = importlib.import_module(mod)
        except Exception as exc:  # noqa: BLE001 - report any import failure
            missing.append(f"{mod} ({type(exc).__name__})")
            continue
        for n in names:
            if not hasattr(m, n):
                missing.append(f"{mod}.{n}")
    return (not missing), missing


# -------------------------------------------------------- compact frames ---


def compact_rollout(rollout: Rollout, *, max_hz: float = 30.0, extra: Optional[dict] = None,
                    digits: int = 4) -> dict:
    """Columnar, rounded playback payload: much smaller than a raw Rollout."""
    frames = rollout.frames
    step = 1
    if frames and rollout.frame_hz > max_hz:
        step = max(1, int(round(rollout.frame_hz / max_hz)))
    frames = frames[::step]
    names: list[str] = []
    for f in rollout.frames:
        for n in f.bodies:
            if n not in names:
                names.append(n)
    # A frame may omit bodies that did not move since the previous frame (delta
    # encoded rollouts); carry their last pose forward.
    bodies: dict[str, list] = {n: [] for n in names}
    last: dict[str, Optional[list]] = {n: None for n in names}
    for k, f in enumerate(rollout.frames):
        for n, p in f.bodies.items():
            last[n] = [round(v, digits) for v in p.pos] + [round(v, digits) for v in p.quat]
        if k % step == 0:
            for n in names:
                bodies[n].append(last[n])
    out = {
        "format": ROLLOUT_FORMAT,
        "id": rollout.report.rollout_id,
        "report": rollout.report.model_dump(mode="json"),
        "visual": rollout.visual.model_dump(mode="json") if rollout.visual else None,
        "frame_hz": rollout.frame_hz / step,
        "t": [round(f.t, 3) for f in frames],
        "width": [round(f.gripper_width, 4) for f in frames],
        "phase": [f.phase for f in frames],
        "bodies": bodies,
    }
    if extra:
        out.update(extra)
    return out


def _robot_names(visual: Optional[VisualScene]) -> tuple[str, Optional[str], Optional[str]]:
    """(gripper body, left finger, right finger) names from a VisualScene."""
    gripper, left, right = "gripper", None, None
    if visual is None:
        return gripper, left, right
    dyn = [b.name for b in visual.bodies if not b.static]
    cand = [n for n in dyn if any(k in n.lower() for k in ("gripper", "palm", "hand", "finger"))]
    fingers = [n for n in cand if "finger" in n.lower() or "pad" in n.lower()]
    base = [n for n in cand if n not in fingers]
    if base:
        gripper = sorted(base, key=len)[0]
    for n in fingers:
        low = n.lower()
        if low.endswith(("_l", "left", "_1")):
            left = n
        elif low.endswith(("_r", "right", "_2")):
            right = n
    return gripper, left, right


def _finger_poses(pos, quat, width: float, left: Optional[str], right: Optional[str]) -> dict:
    """Finger bodies sit at +-width/2 along the gripper's local y (see mirror/sim model)."""
    import numpy as np
    w, x, y, z = quat
    # Rotate the local y axis by the gripper orientation.
    ry = np.array([2 * (x * y - w * z), 1 - 2 * (x * x + z * z), 2 * (y * z + w * x)])
    out = {}
    for name, sgn in ((left, 1.0), (right, -1.0)):
        if name:
            p = np.asarray(pos) + sgn * (width / 2.0) * ry
            out[name] = Pose(pos=tuple(float(v) for v in p), quat=tuple(float(v) for v in quat))
    return out


def statelog_to_rollout(log, report: RolloutReport, visual: Optional[VisualScene], frame_hz: float = 30.0) -> Rollout:
    """Playback frames sampled from a dense step log (object, bead and gripper state)."""
    import numpy as np
    gripper, left, right = _robot_names(visual)
    bead_names: dict[str, list[str]] = {}
    if visual is not None:
        for cup in log.beads:
            names = [b.name for b in visual.bodies if b.name.startswith(cup) and "bead" in b.name.lower()]
            names.sort(key=lambda n: int("".join(c for c in n.split("bead")[-1] if c.isdigit()) or 0))
            bead_names[cup] = names
    n = log.n
    if n == 0:
        return Rollout(report=report, visual=visual, frame_hz=frame_hz, frames=[])
    step = max(1, int(round(1.0 / (frame_hz * log.dt))))
    idx = list(range(0, n, step))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    frames = []
    for i in idx:
        bodies = {}
        for k, oid in enumerate(log.object_ids):
            bodies[oid] = Pose(pos=tuple(float(v) for v in log.obj_pos[i, k]), quat=tuple(float(v) for v in log.obj_quat[i, k]))
        gp, gq = log.gripper_pos[i], log.gripper_quat[i]
        bodies[gripper] = Pose(pos=tuple(float(v) for v in gp), quat=tuple(float(v) for v in gq))
        bodies.update(_finger_poses(gp, gq, float(log.gripper_width[i]), left, right))
        for cup, arr in log.beads.items():
            names = bead_names.get(cup) or [f"{cup}/bead{j}" for j in range(arr.shape[1])]
            for j, nm in enumerate(names[: arr.shape[1]]):
                bodies[nm] = Pose(pos=tuple(float(v) for v in arr[i, j]), quat=(1.0, 0.0, 0.0, 0.0))
        frames.append(RolloutFrame(t=float(log.t[i]), bodies=bodies, gripper_width=float(log.gripper_width[i]),
                                   phase=log.phase[i] if i < len(log.phase) else ""))
    return Rollout(report=report, visual=visual, frame_hz=1.0 / (step * log.dt), frames=frames)


def trace_to_rollout(trace: DemoTrace, visual: Optional[VisualScene]) -> Rollout:
    """Playback for a recorded demonstration trace.

    A trace records object and gripper poses only, so cup beads are not shown
    when playing a trace (``beads_hidden`` in the payload), rather than drawn
    in invented positions.
    """
    gripper_name, left, right = _robot_names(visual)
    frames = []
    for f in trace.frames:
        bodies = dict(f.objects)
        bodies[gripper_name] = f.gripper
        bodies.update(_finger_poses(f.gripper.pos, f.gripper.quat, f.gripper_width, left, right))
        frames.append(RolloutFrame(t=f.t, bodies=bodies, gripper_width=f.gripper_width, phase=""))
    held = None
    ev = sorted(trace.events, key=lambda e: e.t)
    for fr in frames:
        for e in ev:
            if e.t <= fr.t and e.type in ("grasp", "release"):
                held = e.object_id if e.type == "grasp" else None
        fr.phase = f"transport:{held}" if held else ""
    report = RolloutReport(rollout_id="demo", scene_id=trace.scene.scene_id, spec_id="", spec_version=0,
                           condition="demonstration", status="success", completion=True, checker="none",
                           notes=[f"demonstration trace ({trace.source})", trace.source_note] if trace.source_note
                           else [f"demonstration trace ({trace.source})"])
    return Rollout(report=report, visual=visual, frame_hz=trace.sample_hz, frames=frames)


# ---------------------------------------------------------------- jobs ---


class Job:
    def __init__(self, kind: str, params: dict):
        self.id = "j" + uuid.uuid4().hex[:8]
        self.kind = kind
        self.params = params
        self.status = "queued"  # queued | running | done | error
        self.progress = 0.0
        self.message = ""
        self.result: Any = None
        self.error: Optional[str] = None
        self.started = time.time()
        self.finished: Optional[float] = None
        self.queue_position = 0     # > 0 while waiting for a free simulator

    def public(self) -> dict:
        return {"id": self.id, "kind": self.kind, "params": self.params, "status": self.status,
                "progress": round(self.progress, 3), "message": self.message, "result": self.result,
                "error": self.error, "started": self.started, "finished": self.finished,
                "queue_position": self.queue_position}


# Jobs that run the simulator (the expensive part); they share the server's job gate.
SIM_JOB_KINDS = {"run", "clarify", "evaluate", "video"}

MAX_UPLOAD_MB = 40          # per video file
MAX_VIDEO_S = 90.0
MAX_WAV_S = 30.0


def _probe_video(path: Path) -> dict:
    """Duration and size from ffprobe when it is installed (best effort)."""
    import shutil
    import subprocess
    exe = shutil.which("ffprobe")
    if not exe:
        return {}
    try:
        r = subprocess.run([exe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height,r_frame_rate:format=duration", "-of", "json", str(path)],
                           capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
        d = json.loads(r.stdout or "{}")
        st = (d.get("streams") or [{}])[0]
        num, _, den = (st.get("r_frame_rate") or "0/1").partition("/")
        fps = float(num) / float(den or 1) if float(den or 1) else None
        return {"duration": float(d.get("format", {}).get("duration") or 0) or None,
                "width": st.get("width"), "height": st.get("height"), "fps": round(fps, 3) if fps else None}
    except Exception:  # noqa: BLE001
        return {}


def _video_meta(tr: Optional[dict]) -> dict:
    tr = tr or {}
    return {"fps": tr.get("fps"), "width": tr.get("width"), "height": tr.get("height")}


def check_wav(data: bytes) -> dict:
    """Accept only 16 kHz mono 16-bit PCM WAV of at most MAX_WAV_S seconds."""
    import struct
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("expected a WAV file (RIFF/WAVE)")
    pos, fmt, size = 12, None, None
    while pos + 8 <= len(data):
        cid, clen = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        if cid == b"fmt ":
            fmt = struct.unpack("<HHIIHH", data[pos + 8:pos + 24])
        elif cid == b"data":
            size = min(clen, len(data) - pos - 8)
            break
        pos += 8 + clen + (clen & 1)
    if fmt is None or size is None:
        raise ValueError("WAV without fmt or data chunk")
    audio_format, channels, rate, _br, _align, bits = fmt
    if audio_format != 1 or channels != 1 or rate != 16000 or bits != 16:
        raise ValueError("WAV must be 16 kHz, mono, 16-bit PCM")
    seconds = size / (rate * 2)
    if seconds > MAX_WAV_S:
        raise ValueError(f"recording longer than {MAX_WAV_S:.0f} s")
    if seconds < 0.2:
        raise ValueError("recording too short")
    return {"seconds": round(seconds, 2)}


def _is_model_error(exc: BaseException) -> bool:
    try:
        from ..interpret import LLMError
    except Exception:  # noqa: BLE001
        return False
    return isinstance(exc, LLMError)


def _budget_exc():
    from .sessions import BudgetExceeded
    return BudgetExceeded


# ------------------------------------------------------------- service ---


class StudioService:
    def __init__(self, backend: Optional[str] = None, *, session_id: Optional[str] = None, gate=None,
                 budget=None, meta_budgets: Optional[dict] = None, one_job: bool = False,
                 private_runtime: bool = False):
        backend = (backend or os.environ.get("MIRROR_STUDIO_BACKEND", "auto")).lower()
        ok, missing = packages_available()
        self.missing = missing
        if backend == "fixture" or (backend == "auto" and not ok):
            from .fixtures import FixtureSession
            self.session = FixtureSession()
            self.mode = "fixture"
        else:
            if not ok:
                raise RuntimeError("live backend requested but packages are missing: " + ", ".join(missing))
            from ..pipeline import Session
            self.session = Session()
            self.mode = "live"
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="studio-job")
        self.jobs: dict[str, Job] = {}
        self.listeners: list[Callable[[dict], None]] = []
        self.revision = 0
        self.tags: dict[str, list[str]] = {}       # rollout id -> tags
        self.rechecks: list[dict] = []
        self.evaluation: Optional[dict] = None
        self.scene_seq = 0
        self.demo_rollout: Optional[Rollout] = None
        self.clarification: Optional[dict] = None
        self.payload_cache: dict[str, dict] = {}
        self.trace_cache: dict[str, Optional[dict]] = {}
        self.eval_entries: dict[str, dict] = {}
        self.payload_digits = 4
        self.fallbacks: list[dict] = []
        self.teleop = None
        self.session_id = session_id
        self.gate = gate              # shared simulation job gate (public server)
        self.budget = budget          # shared model-call budget (public server)
        self.one_job = one_job        # refuse a second job while one is queued or running
        self.private_runtime = private_runtime
        self.meta_budgets = meta_budgets or {}
        self.closed = False
        self.model_log: list[dict] = []      # which model produced each artifact (Models panel)
        self.video_demo: Optional[dict] = None
        self.uploads: dict[str, dict] = {}
        self.transcripts: dict[str, dict] = {}
        if self.mode == "live" and (gate is not None or budget is not None):
            from .sessions import make_client
            client = make_client(budget, session_id or "local")
            if client is not None:
                self.session.client = client

    # ------------------------------------------------------------ events
    def subscribe(self, fn: Callable[[dict], None]) -> Callable[[], None]:
        self.listeners.append(fn)
        return lambda: self.listeners.remove(fn) if fn in self.listeners else None

    def _emit(self, msg: dict) -> None:
        for fn in list(self.listeners):
            try:
                fn(msg)
            except Exception:  # noqa: BLE001 - a broken listener must not stop jobs
                pass

    def _changed(self) -> None:
        self.revision += 1
        self._emit({"type": "state", "revision": self.revision})

    # ------------------------------------------------------------- jobs
    def busy(self) -> bool:
        return any(j.status in ("queued", "running") for j in self.jobs.values())

    def close(self, reason: str = "") -> None:
        """End this session: stop accepting work and release its resources."""
        self.closed = True
        self._emit({"type": "expired", "reason": reason})
        self.listeners.clear()
        self.executor.shutdown(wait=False, cancel_futures=True)
        if self.private_runtime and hasattr(self, "_rt"):
            import shutil
            shutil.rmtree(self._rt, ignore_errors=True)

    def submit(self, kind: str, params: dict, fn: Callable[[Job], Any]) -> Job:
        if self.closed:
            raise RuntimeError("this session has ended")
        if self.one_job and self.busy():
            from .sessions import SessionBusy
            raise SessionBusy("a job is already running in this session; wait for it to finish")
        job = Job(kind, params)
        self.jobs[job.id] = job
        gated = self.gate is not None and kind in SIM_JOB_KINDS

        def runner():
            if gated:
                job.message = "Waiting for a free simulator"
                ok = self.gate.acquire(job, notify=lambda j: self._emit({"type": "job", "job": j.public()}),
                                       cancelled=lambda: self.closed)
                if not ok:
                    job.status = "error"
                    job.error = "session ended while waiting"
                    job.finished = time.time()
                    return
            job.status = "running"
            job.message = ""
            self._emit({"type": "job", "job": job.public()})
            try:
                job.result = fn(job)
                job.status = "done"
                job.progress = 1.0
            except Exception as exc:  # noqa: BLE001 - surfaced to the user
                job.status = "error"
                job.error = f"{type(exc).__name__}: {exc}"
                job.message = traceback.format_exc(limit=4)
            finally:
                if gated:
                    self.gate.release()
            job.finished = time.time()
            self._emit({"type": "job", "job": job.public()})
            self._changed()

        self.executor.submit(runner)
        self._emit({"type": "job", "job": job.public()})
        return job

    def _progress(self, job: Job, frac: float, message: str) -> None:
        job.progress = frac
        job.message = message
        self._emit({"type": "job", "job": job.public()})

    def wait(self, job: Job, timeout: float = 600.0) -> Job:
        t0 = time.time()
        while job.status in ("queued", "running"):
            if time.time() - t0 > timeout:
                raise TimeoutError(f"job {job.id} did not finish")
            time.sleep(0.02)
        return job

    # ------------------------------------------------------- operations
    def load_demo(self) -> dict:
        with self.lock:
            s = self.session
            ref = REPO_ROOT / "data" / "demos" / "reference_demo.json"
            pinned = REPO_ROOT / "data" / "cassettes" / "inputs" / "reference_demo.json.gz"
            if self.mode == "live" and pinned.exists():
                # The demonstration the recorded model responses were made from.
                import gzip
                s.demo = DemoTrace.model_validate_json(gzip.decompress(pinned.read_bytes()))
                s.scene = s.demo.scene
                s._event("demo_loaded", demo_id=s.demo.demo_id, source=s.demo.source)
            elif self.mode == "live" and ref.exists():
                s.load_demo(ref)
            elif self.mode == "live":
                from ..scenes import reference_scene
                s.record_demo(reference_scene("demo"))
            else:
                s.record_demo()
            self.demo_rollout = self._make_demo_rollout()
            self.payload_cache.pop("demo", None)
            self.video_demo = None
            self._changed()
            return self.snapshot()

    def _make_demo_rollout(self) -> Rollout:
        s = self.session
        if self.mode == "fixture":
            return s.demo_rollout()
        visual = None
        try:
            from ..sim import SimEnv
            visual = SimEnv(s.demo.scene).visual_scene()
        except Exception:  # noqa: BLE001 - playback still works without geometry
            visual = None
        return trace_to_rollout(s.demo, visual)

    def _with_fallback(self, fn, job: Optional[Job] = None):
        """Run an interpreter call. If the model cannot be used (no recording in
        replay mode, budget used up, or any model error), run the offline
        rule-based interpreter instead and return the reason."""
        try:
            return fn(), None
        except Exception as exc:  # noqa: BLE001
            if self.mode != "live" or not _is_model_error(exc):
                raise
            name = type(exc).__name__
            if name == "CassetteMiss":
                why = "no recorded model response for this request"
            elif isinstance(exc, _budget_exc()):
                why = str(exc)
            else:
                why = "model request failed: " + str(exc).split("\n")[0][:160]
            from ..interpret import LLMClient
            prev = getattr(self.session, "client", None)
            self.session.client = LLMClient(mode="offline")
            try:
                if job is not None:
                    self._progress(job, 0.5, f"Using the offline interpreter ({why})")
                return fn(), f"offline-rules ({why})"
            finally:
                self.session.client = prev

    def interpret(self) -> Job:
        def work(job):
            self._progress(job, 0.1, "Reading the demonstration")
            with self.lock:
                if self.session.demo is None:
                    self.load_demo()
                t0, mark = time.time(), self._llm_mark()
                spec, fb = self._with_fallback(self.session.interpret, job)
                if fb:
                    self.fallbacks.append({"step": "interpret", "used": fb, "version": spec.version})
                lat, tok = self._llm_latency(mark, t0)
                self._log_model(f"specification v{spec.version}", "proposes the task from the demonstration",
                                spec.interpreter, lat, self._client_source(fb), tokens=tok)
            return {"version": spec.version, "fallback": fb}
        return self.submit("interpret", {}, work)

    def commit_decisions(self, decisions: dict[str, str], note: str = "") -> dict:
        """decisions: item id -> "confirmed" | "rejected" (constraints, ordering, goals)."""
        with self.lock:
            spec = self.session.spec
            known = {c.id: c.status.value for c in spec.constraints}
            known.update({o.id: o.status.value for o in spec.ordering})
            known.update({g.id: g.status.value for g in spec.goals})
            ops = []
            for item, status in decisions.items():
                if item not in known:
                    raise KeyError(f"unknown specification item {item}")
                if status not in ("confirmed", "rejected", "proposed"):
                    raise ValueError(f"bad status {status}")
                if known[item] != status:
                    ops.append(SpecOp(op="set_status", target_id=item, value={"status": status},
                                      rationale="Reviewed in the studio."))
            for u in spec.uncertainties:
                if u.related_constraint and u.related_constraint in decisions and u.status == "open":
                    st = decisions[u.related_constraint]
                    if st in ("confirmed", "rejected"):
                        ops.append(SpecOp(op="resolve_uncertainty", target_id=u.id,
                                          value={"resolution": "required" if st == "confirmed" else "not required"},
                                          rationale="Answered by the review decision."))
            if not ops:
                return self.snapshot()
            if hasattr(self.session, "confirm"):
                # Same operation the evaluation used to confirm the proposal: goals, then rules, then order.
                order = [g.id for g in spec.goals] + [c.id for c in spec.constraints] + [o.id for o in spec.ordering]
                conf = [i for i in order if decisions.get(i) == "confirmed" and known[i] != "confirmed"]
                rej = [i for i in order if decisions.get(i) == "rejected" and known[i] != "rejected"]
                if conf or rej:
                    self.session.confirm(confirm=conf, reject=rej, text=note)
                    self._changed()
                    return self.snapshot()
            text = note or "Reviewed interpretation: " + ", ".join(f"{k} {v}" for k, v in decisions.items())
            self.session.apply_ops(ops, text=text, kind="confirmation")
            self._changed()
            return self.snapshot()

    def correct(self, text: str, transcript_id: Optional[str] = None) -> Job:
        if transcript_id is not None and transcript_id not in self.transcripts:
            raise KeyError(f"transcript {transcript_id}")

        def work(job):
            self._progress(job, 0.1, "Compiling the correction")
            with self.lock:
                before = self.session.spec.version
                try:
                    t0, mark = time.time(), self._llm_mark()
                    (spec, record), fb = self._with_fallback(lambda: self.session.correct(text), job)
                    if fb:
                        self.fallbacks.append({"step": "correction", "used": fb, "version": spec.version})
                    if spec.version != before:
                        rec_model = getattr(record, "model", None) or (record.get("model") if isinstance(record, dict) else None)
                        tr = self.transcripts.get(transcript_id) if transcript_id else None
                        self._log_model(f"specification v{spec.version}",
                                        "compiles the correction" + (" (spoken, transcript edited)" if tr and tr["text"] != text
                                                                     else " (spoken)" if tr else " (typed)"),
                                        rec_model or self._client_label(), *self._llm_latency(mark, t0)[:1],
                                        self._client_source(fb), input=transcript_id,
                                        tokens=self._llm_latency(mark, t0)[1])
                except Exception as exc:  # noqa: BLE001 - typed outcomes from the interpreter
                    name = type(exc).__name__
                    if name == "NeedsClarification":
                        return {"version": before, "changed": False, "question": getattr(exc, "question", str(exc)),
                                "options": getattr(exc, "options", [])}
                    if name in ("CorrectionRejected", "InterpretError"):
                        return {"version": before, "changed": False, "rejected": str(exc)}
                    raise
                changed = spec.version != before
                result = {"version": spec.version, "changed": changed, "record": _dump(record)}
                question = getattr(record, "question", None) or (record.get("question") if isinstance(record, dict) else None)
                if question:
                    result["question"] = question
                # Re-check every recorded rollout under the new version, so an
                # old behaviour's violation of the new rule is visible.
                if changed:
                    for rid, e in list(self.session.rollouts.items()):
                        if getattr(e, "log", None) is None:
                            continue
                        try:
                            rep = self.session.recheck(rid, spec.version)
                            self.rechecks.append({"rollout_id": rid, "spec_version": spec.version,
                                                  "report": _dump(rep)})
                        except Exception as exc:  # noqa: BLE001
                            self.rechecks.append({"rollout_id": rid, "spec_version": spec.version,
                                                  "error": str(exc)})
                return result
        return self.submit("correct", {"text": text, "transcript_id": transcript_id}, work)

    # ------------------------------------------------- models and Meta stages
    def _client_label(self) -> str:
        c = getattr(self.session, "client", None)
        return getattr(c, "label", None) or ("fixture" if self.mode == "fixture" else "unknown")

    def _client_source(self, fallback: Optional[str] = None) -> str:
        if self.mode == "fixture":
            return "fixture"
        if fallback:
            return "offline rules (fallback)"
        c = getattr(self.session, "client", None)
        mode = getattr(c, "mode", None) or os.environ.get("MIRROR_LLM_MODE") or "offline"
        return {"live": "live", "record": "live", "replay": "recorded"}.get(mode, "offline rules")

    def _llm_mark(self) -> int:
        c = getattr(self.session, "client", None)
        return len(getattr(c, "usage", None) or [])

    def _llm_latency(self, mark: int, t0: float) -> tuple[Optional[float], Optional[int]]:
        """Latency and tokens of the model calls since ``mark``. For a replayed
        recording, the latency measured when it was recorded (from the usage log
        named by MIRROR_LLM_USAGE_LOG), or None if unknown."""
        c = getattr(self.session, "client", None)
        new = (getattr(c, "usage", None) or [])[mark:]
        if not new:
            return time.time() - t0, None
        tokens = sum(int(e.get("total_tokens") or 0) for e in new) or None
        live = [e for e in new if not e.get("from_cassette")]
        if live:
            return sum(float(e.get("latency_s") or 0) for e in live), tokens
        log = os.environ.get("MIRROR_LLM_USAGE_LOG")
        if log and Path(log).exists():
            want = {e.get("key") for e in new}
            got = {}
            for line in Path(log).read_text().splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("key") in want and not r.get("from_cassette"):
                    got[r["key"]] = float(r.get("latency_s") or 0)
            if want and want <= set(got):
                return sum(got.values()), tokens
        return None, tokens

    def _log_model(self, artifact: str, role: str, model: Optional[str], latency_s: float, source: str,
                   **extra) -> dict:
        entry = {"artifact": artifact, "role": role, "model": model or "unknown",
                 "latency_s": round(float(latency_s), 3) if latency_s is not None else None,
                 "source": source, "at": time.time(),
                 **{k: v for k, v in extra.items() if v is not None}}
        self.model_log.append(entry)
        return entry

    def _meta(self):
        if self.mode == "fixture":
            from .fixtures import FixtureMeta
            if not hasattr(self, "_fixture_meta"):
                self._fixture_meta = FixtureMeta(self.session, self._runtime_dir())
            return self._fixture_meta
        from . import meta_bridge
        return meta_bridge

    def capabilities(self) -> dict:
        now = time.time()
        if getattr(self, "_cap_cache", None) and now - self._cap_cache[0] < 30:
            return dict(self._cap_cache[1])
        try:
            cap = self._meta().available()
        except Exception:  # noqa: BLE001
            cap = {"perception": False, "observe": False, "voice": False, "bundled_video": False}
        cap["video"] = bool(cap.get("perception") and cap.get("observe"))
        self._cap_cache = (now, dict(cap))
        return cap

    def _spend(self, kind: str, n: int = 1) -> None:
        """Take budget for a Meta-model request on the public server; refuse clearly when used up."""
        b = self.meta_budgets.get(kind)
        if b is None or self.mode == "fixture":
            return
        for _ in range(n):
            why = b.take(self.session_id or "local")
            if why:
                raise RuntimeError(f"{why.capitalize()}. The recorded demonstration and typing still work.")

    def upload_video(self, data: bytes, view: str = "top") -> dict:
        if view not in ("top", "side"):
            raise ValueError("view must be top or side")
        if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
            raise ValueError(f"video larger than {MAX_UPLOAD_MB} MB")
        if len(data) < 64 or data[4:8] != b"ftyp":
            raise ValueError("not an MP4 video (expected an ISO media 'ftyp' header)")
        vid = "v" + uuid.uuid4().hex[:10]
        path = self._runtime_dir() / f"{vid}.mp4"
        path.write_bytes(data)
        info = _probe_video(path)
        if info.get("duration") and info["duration"] > MAX_VIDEO_S:
            path.unlink(missing_ok=True)
            raise ValueError(f"video longer than {MAX_VIDEO_S:.0f} s")
        self.uploads[vid] = {"id": vid, "path": str(path), "view": view, "bytes": len(data), **info}
        return {k: v for k, v in self.uploads[vid].items() if k != "path"}

    def video_file(self, vid: str) -> str:
        if vid == "bundled-top" or vid == "bundled-side":
            b = self._meta().bundled_demo() or {}
            p = b.get(vid.split("-")[1])
            if not p:
                raise KeyError(vid)
            return str(p)
        return self.uploads[vid]["path"]

    def process_video(self, top: str = "bundled", side: Optional[str] = None) -> Job:
        """Video demonstration -> tracks (SAM) -> observations -> Muse Spark proposal -> new session."""
        cap = self.capabilities()
        if not cap["video"]:
            raise RuntimeError("video demonstrations are not available on this server")
        meta = self._meta()
        gt = None
        if top == "bundled":
            b = meta.bundled_demo()
            if not b:
                raise RuntimeError("no bundled demonstration video on this server")
            top_id, top_path = "bundled-top", b["top"]
            side_id, side_path = ("bundled-side", b["side"]) if b.get("side") and side in (None, "bundled") else (None, None)
            gt = b.get("ground_truth")
            # Perception writes working images next to the video; the bundled copy may live in a
            # read-only install, so each run works on its own copy in the session's runtime folder.
            src_dir = Path(top_path).parent
            dst_dir = self._runtime_dir() / f"bundled-{uuid.uuid4().hex[:8]}"
            shutil.copytree(src_dir, dst_dir, ignore=shutil.ignore_patterns("perception_work"))
            top_path = str(dst_dir / Path(top_path).name)
            if side_path:
                side_path = str(dst_dir / Path(side_path).name)
        else:
            top_id, top_path = top, self.video_file(top)
            side_id, side_path = (side, self.video_file(side)) if side else (None, None)
        self._spend("sam", 2 if side_path else 1)
        self._spend("video", 1)

        def work(job):
            def stage(lo, hi):
                def cb(*a, **k):
                    frac = next((x for x in a if isinstance(x, (int, float))), None)
                    msg = next((x for x in a if isinstance(x, str)), k.get("message", ""))
                    self._progress(job, lo + (hi - lo) * max(0.0, min(1.0, float(frac or 0))), msg or job.message)
                return cb
            self._progress(job, 0.03, "Tracking the objects in the video (SAM 3.1)")
            per = meta.perceive(top_path, side_path, progress=stage(0.03, 0.45), ground_truth=gt)
            self._progress(job, 0.46, "Reading the video: objects, grasps, releases, care")
            obs = meta.observe(top_path, progress=stage(0.46, 0.8))
            self._progress(job, 0.82, "Proposing the task from the reconstruction and the observations")
            with self.lock:
                self._reset_session()
                trace = per["trace"]
                self.session.demo = trace
                self.session.scene = trace.scene
                self.session._event("demo_loaded", demo_id=trace.demo_id, source=trace.source)
                t0, mark = time.time(), self._llm_mark()
                spec, fb = self._with_fallback(lambda: meta.propose_with_observations(trace, getattr(self.session, "client", None)
                                                                                      if self.mode == "fixture" else self.session._client(),
                                                                                      obs["raw"]), job)
                if not self.session.specs or self.session.specs[-1] is not spec:
                    self.session.specs = [spec]
                if fb:
                    self.fallbacks.append({"step": "interpret", "used": fb, "version": spec.version})
                views = ["top"] + (["side"] if side_path else [])
                pcalls = per.get("calls") or []
                ground = [c for c in pcalls if not str(c.get("model") or "").startswith("sam")]
                samc = [c for c in pcalls if str(c.get("model") or "").startswith("sam")]
                if ground:
                    self._log_model("object grounding (first frame)", "finds the table, tray, wall and objects to track",
                                    ground[0]["model"], sum(float(c.get("latency_s") or 0) for c in ground),
                                    "live" if any(c["source"] == "live" for c in ground) else "recorded", calls=len(ground))
                self._log_model("object tracks (" + " + ".join(views) + " view)", "tracks objects in the video",
                                per.get("model"), sum(float(c.get("latency_s") or 0) for c in samc) if samc else per.get("latency_s", 0.0),
                                ("live" if any(c["source"] == "live" for c in samc) else "recorded") if samc else per.get("source", "unknown"),
                                calls=len(samc) or None)
                self._log_model("video observations", "objects that matter, grasps, releases, care cues",
                                obs.get("model"), obs.get("latency_s", 0.0), obs.get("source", "unknown"),
                                calls=len(obs.get("calls") or []))
                lat, tok = self._llm_latency(mark, t0)
                self._log_model(f"specification v{spec.version}", "proposes the task from the video reconstruction",
                                spec.interpreter, lat, self._client_source(fb), tokens=tok)
                self.demo_rollout = self._make_demo_rollout()
                self.payload_cache.pop("demo", None)
                self.video_demo = {
                    "views": {"top": {"id": top_id, **_video_meta(per["tracks"].get("top"))},
                              **({"side": {"id": side_id, **_video_meta(per["tracks"].get("side"))}} if side_id else {})},
                    "tracks": per["tracks"], "observations": obs["observations"], "metrics": per.get("metrics"),
                    "checks": per.get("checks"), "reconstruction_s": per.get("latency_s"),
                    "bundled": top == "bundled", "fixture": self.mode == "fixture",
                    "trace_source": trace.source, "spec_version": spec.version,
                }
                self._changed()
            return {"version": spec.version, "fallback": fb}
        return self.submit("video", {"top": top_id, "side": side_id}, work)

    def _reset_session(self) -> None:
        """Start a fresh pipeline session (a new demonstration replaces the old story)."""
        if self.mode == "fixture":
            from .fixtures import FixtureSession
            new = FixtureSession()
            new.record_demo()
            if hasattr(self, "_fixture_meta"):
                self._fixture_meta.session = new
        else:
            from ..pipeline import Session
            new = Session()
            new.client = getattr(self.session, "client", None)
        self.session = new
        self.tags.clear()
        self.rechecks = []
        self.evaluation = None
        self.clarification = None
        self.payload_cache.clear()
        self.trace_cache.clear()
        self.scene_seq = 0

    def transcribe(self, wav: bytes) -> Job:
        info = check_wav(wav)
        meta = self._meta()
        if not self.capabilities().get("voice"):
            raise RuntimeError("speech to text is not available on this server")
        self._spend("voice", 1)

        def work(job):
            self._progress(job, 0.2, "Transcribing (Muse voice)")
            res = meta.transcribe(wav)
            tid = "t" + uuid.uuid4().hex[:8]
            (self._runtime_dir() / f"{tid}.wav").write_bytes(wav)     # kept for recorded sessions (export)
            rec = {"id": tid, "text": res["text"], "model": res.get("model"), "latency_s": res.get("latency_s"),
                   "source": res.get("source"), "audio_s": info["seconds"], "at": time.time()}
            self.transcripts[tid] = rec
            self._log_model("transcript", "speech to text", res.get("model"), res.get("latency_s", 0.0),
                            res.get("source", "unknown"), id=tid)
            self._changed()
            return rec
        return self.submit("voice", {"seconds": info["seconds"]}, work)

    def transcript_audio(self, tid: str) -> Optional[str]:
        p = self._runtime_dir() / f"{tid}.wav"
        return str(p) if p.exists() else None

    def models_public(self) -> dict:
        from .sessions import budgets_public, llm_public
        return {"artifacts": self.model_log,
                "budgets": {"interpreter": llm_public(self.budget, self.session_id) if self.budget is not None else None,
                            **budgets_public(self.meta_budgets, self.session_id)}}

    def run(self, spec_version: Optional[int] = None, condition: str = "", tags: Optional[list[str]] = None) -> Job:
        def work(job):
            self._progress(job, 0.05, "Planning")
            with self.lock:
                entry = self.session.run(spec_version=spec_version, condition=condition)
            rid = entry.rollout.report.rollout_id
            self.tags[rid] = list(tags or [])
            return {"rollout_id": rid, "status": entry.rollout.report.status}
        return self.submit("run", {"spec_version": spec_version, "condition": condition}, work)

    def recheck(self, rollout_id: str, spec_version: Optional[int] = None) -> dict:
        with self.lock:
            rep = self.session.recheck(rollout_id, spec_version)
            v = spec_version or self.session.spec.version
            item = {"rollout_id": rollout_id, "spec_version": v, "report": _dump(rep)}
            self.rechecks = [r for r in self.rechecks if not (r["rollout_id"] == rollout_id and r["spec_version"] == v)]
            self.rechecks.append(item)
            self._changed()
            return item

    def clarify(self) -> Job:
        def work(job):
            self._progress(job, 0.05, "Finding distinct valid behaviours")
            with self.lock:
                if self.mode == "live":
                    clar = self.session.clarify()
                    plans = list(getattr(self.session, "clarification_candidates", None) or [])
                    if not plans:   # older Session: plan the candidates here
                        from ..plan import candidate_plans
                        plans = candidate_plans(self.session.spec, self.session.scene, k=2)
                    cd = self._execute_candidates(job, plans, _dump(clar))
                else:
                    clar = self.session.clarify()
                    cd = _dump(clar)
                    for c in cd.get("candidates", []):
                        if c.get("rollout_id"):
                            self.tags[c["rollout_id"]] = ["candidate"]
                self.clarification = cd
            return {"clarification": cd.get("id") if isinstance(cd, dict) else None}
        return self.submit("clarify", {}, work)

    def _execute_candidates(self, job: Job, plans: list, cd: dict) -> dict:
        """Execute each candidate plan in simulation so both behaviours can be watched."""
        from ..evaluate import check
        from ..pipeline import RolloutEntry
        from ..sim import SimEnv
        spec, scene = self.session.spec, self.session.scene
        digests = (cd.get("candidates") or []) if isinstance(cd, dict) else []
        if digests and all(isinstance(d.get("index"), int) and d["index"] < len(plans) for d in digests):
            # The clarification compares a subset of the planned candidates; run exactly those.
            plans = [plans[d["index"]] for d in digests]
        out = []
        for i, p in enumerate(plans):
            plan = p if isinstance(p, PlanResult) else (getattr(p, "plan", None) or p)
            if isinstance(plan, dict) and "steps" in plan:
                plan = PlanResult.model_validate(plan)
            if not isinstance(plan, PlanResult):
                out.append({"label": f"candidate {i + 1}", "error": "candidate has no plan"})
                continue
            self._progress(job, 0.2 + 0.7 * i / max(1, len(plans)), f"Executing candidate {i + 1}")
            env = SimEnv(scene)
            res = env.execute(plan)
            rep = check(res.log, spec, scene)
            rid = f"c{len(self.session.rollouts) + 1:03d}"
            rep.rollout_id = rid
            rep.condition = f"candidate {i + 1}"
            rep.route_labels = [s.note for s in plan.steps if s.kind == "transport"]
            if res.status != "completed":
                rep.status = "timeout" if res.status == "timeout" else "execution_failed"
                rep.notes.append(res.reason)
            self.session.rollouts[rid] = RolloutEntry(Rollout(report=rep, visual=env.visual_scene(), frames=res.frames),
                                                      res.log, plan, scene, spec.version)
            self.tags[rid] = ["candidate"]
            digests = (cd.get("candidates") or []) if isinstance(cd, dict) else []
            digest = digests[i] if i < len(digests) else {}
            out.append({"label": digest.get("label", chr(65 + i)), "rollout_id": rid,
                        "routes": digest.get("routes", rep.route_labels)})
        cd = dict(cd) if isinstance(cd, dict) else {"raw": cd}
        cd["executed"] = out
        return cd

    def answer(self, text: str) -> Job:
        def work(job):
            with self.lock:
                (spec, record), fb = self._with_fallback(lambda: self.session.answer(text), job)
                if fb:
                    self.fallbacks.append({"step": "answer", "used": fb, "version": spec.version})
                self.clarification = None
            return {"version": spec.version, "record": _dump(record), "fallback": fb}
        return self.submit("answer", {"text": text}, work)

    def evaluate(self, seeds: list[int], spec_a: Optional[int] = None, spec_b: Optional[int] = None,
                 split: str = SHOWCASE_SPLIT) -> Job:
        seeds = check_showcase(seeds, split)

        def work(job):
            s = self.session
            a = s.spec_version(spec_a) if spec_a else s.specs[0]
            b = s.spec_version(spec_b) if spec_b else s.spec
            self._progress(job, 0.02, f"Paired evaluation on {len(seeds)} layouts")
            if self.mode == "fixture":
                rep = s.paired_eval(a, b, seeds, progress=lambda i, n: self._progress(job, i / n, f"Layout {i}/{n}"))
            else:
                rep = self._paired_eval_live(job, a, b, seeds, split)
            self.evaluation = rep
            return {"pairs": len(rep.get("pairs") or rep.get("trials") or []) if isinstance(rep, dict) else None}
        return self.submit("evaluate", {"seeds": seeds, "split": split}, work)

    def _runtime_dir(self) -> Path:
        if not hasattr(self, "_rt"):
            import tempfile
            base = os.environ.get("MIRROR_STUDIO_RUNTIME")
            if self.private_runtime:
                root = Path(base) if base else Path(tempfile.gettempdir())
                root.mkdir(parents=True, exist_ok=True)
                self._rt = Path(tempfile.mkdtemp(prefix="session-", dir=root))
            else:
                self._rt = Path(base) if base else Path(tempfile.mkdtemp(prefix="mirror-studio-"))
            self._rt.mkdir(parents=True, exist_ok=True)
        return self._rt

    def _paired_eval_live(self, job: Job, a: TaskSpec, b: TaskSpec, seeds: list[int], split: str) -> dict:
        """Freeze both versions, then run the evaluation package's paired comparison.

        The freeze manifest is written before any evaluation layout is generated.
        Step logs are kept so every trial can be replayed in the viewer."""
        from ..evaluate import paired_eval
        seeds = check_showcase(seeds, split)
        run_dir = self._runtime_dir() / f"showcase-{int(time.time())}-{uuid.uuid4().hex[:4]}"
        run_dir.mkdir(parents=True)
        workers = int(os.environ.get("MIRROR_STUDIO_EVAL_WORKERS", "2"))
        est = max(20.0, 2 * len(seeds) * 22.0 / max(1, workers))
        done = threading.Event()

        def ticker():
            t0 = time.time()
            while not done.wait(1.5):
                frac = min(0.95, (time.time() - t0) / est)
                self._progress(job, frac, f"Running {2 * len(seeds)} trials on {len(seeds)} fresh layouts "
                                          f"({int(time.time() - t0)} s, estimate {int(est)} s)")
        th = threading.Thread(target=ticker, daemon=True)
        th.start()
        import inspect
        kw = {}
        if "rollout_dir" in inspect.signature(paired_eval).parameters:
            kw["rollout_dir"] = run_dir / "rollouts"
        try:
            rep = paired_eval(a, b, seeds, split=SHOWCASE_SPLIT, workers=workers, log_dir=run_dir / "logs", **kw)
        finally:
            done.set()
        (run_dir / "report.json").write_text(rep.model_dump_json())
        out = self._register_evaluation(_dump(rep), run_dir, a.version, b.version,
                                        log_dir=run_dir / "logs", rollout_dir=run_dir / "rollouts")
        out["source"] = {"kind": "showcase", "split": SHOWCASE_SPLIT,
                         "note": "development seeds run in the studio; not the pre-registered evaluation"}
        return out

    def _register_evaluation(self, out: dict, base: Path, va: Optional[int], vb: Optional[int], *,
                             log_dir: Optional[Path] = None, rollout_dir: Optional[Path] = None,
                             prefix: str = "") -> dict:
        """Make every trial of an evaluation report replayable in the viewer.

        Layouts are rebuilt only for the showcase development split. For any
        other split the minimap and labels come from a sketch of the recorded
        rollout's own geometry, so no evaluation layout is ever generated here."""
        from ..contracts import RolloutReport as _RR
        split = out.get("split", "eval")
        out["spec_a"] = va if va is not None else out.get("manifest", {}).get("spec_a", {}).get("version")
        out["spec_b"] = vb if vb is not None else out.get("manifest", {}).get("spec_b", {}).get("version")
        studio = {"rollout_ids": {}, "scenes": {}, "run_dir": base.name}

        def resolve(rel: Optional[str], default_dir: Optional[Path]) -> Optional[str]:
            if not rel:
                return None
            pth = Path(rel)
            for cand in ([pth] if pth.is_absolute() else [base / pth] + ([default_dir / pth.name] if default_dir else [])):
                if cand.exists():
                    return str(cand)
            return None

        for t in out.get("trials", []):
            seed = int(t["seed"])
            scene = None
            if split == SHOWCASE_SPLIT and int(seed) in SHOWCASE_SEEDS:
                try:
                    from ..scenes import sample_layout
                    scene = sample_layout(seed, SHOWCASE_SPLIT)
                except Exception:  # noqa: BLE001 - fall back to the sketch
                    scene = None
                if scene is not None and scene.scene_id != t.get("scene_id", scene.scene_id):
                    scene = None
            ids = {}
            for key in ("a", "b"):
                rec = t[key]
                rid = f"e{prefix}{seed}{key}"
                ids[key] = rid
                rpath = resolve(rec.get("rollout_path"), rollout_dir)
                sc = scene
                if sc is None and rpath:
                    try:
                        sc = scene_sketch(Rollout.model_validate_json(Path(rpath).read_text()), t.get("scene_id", ""))
                    except Exception:  # noqa: BLE001
                        sc = None
                self.eval_entries[rid] = {
                    "scene": sc, "report": _RR.model_validate(rec["report"]),
                    "log_path": resolve(rec.get("log_path"), log_dir) if scene is not None else None,
                    "rollout_path": rpath,
                    "spec_version": out["spec_a"] if key == "a" else out["spec_b"],
                    "seed": seed, "condition": rec.get("condition", ""), "sketch": scene is None}
                self.tags[rid] = ["eval"]
                self.trace_cache.pop(rid, None)
                self.payload_cache.pop(rid, None)
                if sc is not None:
                    studio["scenes"][str(seed)] = sc.model_dump(mode="json")
            studio["rollout_ids"][str(seed)] = ids
        out["studio"] = studio
        return out

    def load_evaluation(self, report_path: str | Path) -> dict:
        """Show saved evaluation results (load only; nothing is run or sampled).

        Accepts a single paired report (``trials``) or the pre-registered final
        report (``strata``: one paired report per stratum, each in its own folder)."""
        path = Path(report_path)
        if path.is_dir():
            cands = sorted(path.glob("*.json"), key=lambda p: (p.name != "report.json", p.name))
            path = next(p for p in cands if '"trials"' in p.read_text()[:400000] or '"strata"' in p.read_text()[:400000])
        data = json.loads(path.read_text())
        with self.lock:
            if "strata" in data:
                strata = {}
                for name, rep in data["strata"].items():
                    d = path.parent / name
                    strata[name] = self._register_evaluation(rep, d, None, None, log_dir=d / "logs",
                                                             rollout_dir=d / "rollouts", prefix=f"{name}-")
                ev = {k: v for k, v in data.items() if k != "strata"}
                ev["strata"] = strata
                ev["final"] = True
                first = next(iter(strata.values()), {})
                ev["spec_a"], ev["spec_b"] = first.get("spec_a"), first.get("spec_b")
            else:
                ev = self._register_evaluation(data, path.parent, None, None,
                                               log_dir=path.parent / "logs", rollout_dir=path.parent / "rollouts")
            ev["source"] = {"kind": "loaded", "file": path.name, "folder": path.parent.name}
            self.evaluation = ev
            self._changed()
            return self.evaluation

    def _eval_rollout(self, rid: str) -> Rollout:
        e = self.eval_entries[rid]
        if e.get("rollout_path"):
            r = Rollout.model_validate_json(Path(e["rollout_path"]).read_text())
            if r.visual is None and e["scene"] is not None:
                vd = self.visual_for(e["scene"])
                r.visual = VisualScene.model_validate(vd) if vd else None
            return r
        visual = None
        try:
            from ..sim import SimEnv
            visual = SimEnv(e["scene"]).visual_scene()
        except Exception:  # noqa: BLE001
            visual = None
        if e["scene"] is None or not e["log_path"] or not Path(e["log_path"]).exists():
            return Rollout(report=e["report"], visual=visual)
        from ..statelog import StateLog
        return statelog_to_rollout(StateLog.load(e["log_path"]), e["report"], visual)

    # ------------------------------------------------------------ scene
    def scene_presets(self) -> list[str]:
        return ["demo", "shortcut", "blocked"]

    def set_scene_preset(self, name: str) -> dict:
        with self.lock:
            if self.mode == "fixture":
                from .fixtures import fixture_scene
                self.session.set_scene(fixture_scene(name))
            else:
                self.session.set_scene(name)
            self._changed()
            return self.snapshot()

    def edit_scene(self, edit: dict) -> dict:
        """Derive a custom scene from the current one. Supported edits:
        tray {x, y}; object {id, x, y}; slot "on" | "off"; obstacle {x, y} (adds a box);
        remove_obstacle id."""
        with self.lock:
            base = self.session.scene
            if base is None:
                raise RuntimeError("no scene loaded")
            sc = base.model_copy(deep=True)
            ws = sc.workspace

            def clamp(v, lo, hi, m=0.03):
                return float(min(hi - m, max(lo + m, v)))

            if "tray" in edit:
                t = sc.trays[0]
                x, y = edit["tray"]["x"], edit["tray"]["y"]
                hx, hy = t.inner_half
                t.pose = Pose(pos=(clamp(x, ws.x_range[0] + hx, ws.x_range[1] - hx, 0.0),
                                   clamp(y, ws.y_range[0] + hy, ws.y_range[1] - hy, 0.0), t.pose.pos[2]), quat=t.pose.quat)
            if "object" in edit:
                o = next(o for o in sc.objects if o.id == edit["object"]["id"])
                o.pose = Pose(pos=(clamp(edit["object"]["x"], *ws.x_range), clamp(edit["object"]["y"], *ws.y_range),
                                   o.pose.pos[2]), quat=o.pose.quat)
            if "slot" in edit:
                for w in sc.obstacles:
                    if w.kind != "wall":
                        continue
                    if edit["slot"] == "off":
                        w.slots = []
                    elif not w.slots:
                        cy = edit.get("slot_y")
                        if cy is None:
                            # Put the slot on the straight line from the cup to the tray.
                            cup = next((o for o in sc.objects if o.kind == "cup"), None)
                            tr = sc.trays[0].pose.pos
                            if cup is not None and abs(tr[0] - cup.pose.pos[0]) > 1e-6:
                                k = (w.pose.pos[0] - cup.pose.pos[0]) / (tr[0] - cup.pose.pos[0])
                                cy = cup.pose.pos[1] + k * (tr[1] - cup.pose.pos[1])
                            else:
                                cy = w.pose.pos[1]
                        half = WORLD.slot_length / 2
                        lo = w.pose.pos[1] - w.length / 2 + 0.02
                        hi = w.pose.pos[1] + w.length / 2 - 0.02
                        cy = float(min(hi - half, max(lo + half, cy)))
                        w.slots = [SlotSpec(y_min=cy - half, y_max=cy + half, z_min=WORLD.slot_z[0], z_max=WORLD.slot_z[1])]
            if "obstacle" in edit:
                n = sum(1 for o in sc.obstacles if o.kind == "box")
                half = tuple(edit["obstacle"].get("half", (0.04, 0.04, 0.12)))
                sc.obstacles.append(ObstacleSpec(id=f"box_{n}", kind="box",
                                                 pose=Pose(pos=(clamp(edit["obstacle"]["x"], *ws.x_range),
                                                                clamp(edit["obstacle"]["y"], *ws.y_range), half[2])),
                                                 half=half))
            if "remove_obstacle" in edit:
                sc.obstacles = [o for o in sc.obstacles if o.id != edit["remove_obstacle"] or o.kind == "wall"]
            self.scene_seq += 1
            sc.scene_id = f"custom-{self.scene_seq}"
            sc.split = "custom"
            sc.seed = None
            self.session.set_scene(sc)
            self._changed()
            return self.snapshot()

    def visual_for(self, scene: SceneState) -> Optional[dict]:
        try:
            if self.mode == "fixture":
                from .fixtures import visual_scene
                return visual_scene(scene).model_dump(mode="json")
            from ..sim import SimEnv
            return SimEnv(scene).visual_scene().model_dump(mode="json")
        except Exception:  # noqa: BLE001 - the viewer can draw from SceneState instead
            return None

    # ------------------------------------------------------------ teleop
    def teleop_start(self) -> dict:
        if self.mode != "live":
            raise RuntimeError("teleoperation needs the simulator")
        from ..sim import Teleop
        from ..scenes import reference_scene
        with self.lock:
            scene = (self.session.demo.scene if self.session.demo is not None else None) or reference_scene("demo")
            self.teleop = Teleop(scene)
            vis = self.teleop.visual_scene() if hasattr(self.teleop, "visual_scene") else None
            frame = self.teleop.advance(0.05)
            return {"visual": _dump(vis) if vis is not None else self.visual_for(scene),
                    "scene": scene.model_dump(mode="json"), "frame": _frame_dict(frame, self.teleop)}

    def teleop_step(self, dpos, drot, grip, seconds: float = 1 / 15) -> dict:
        if self.teleop is None:
            raise RuntimeError("teleoperation not started")
        self.teleop.command(tuple(dpos), tuple(drot), grip)
        f = self.teleop.advance(seconds)
        return _frame_dict(f, self.teleop)

    def teleop_cancel(self) -> None:
        self.teleop = None

    def teleop_finish(self) -> dict:
        if self.teleop is None:
            raise RuntimeError("teleoperation not started")
        with self.lock:
            trace = self.teleop.finish()
            self.teleop = None
            self.session.demo = trace
            self.session.scene = trace.scene
            self.demo_rollout = self._make_demo_rollout()
            self.payload_cache.pop("demo", None)
            self._changed()
            return self.snapshot()

    # ---------------------------------------------------------- output
    def rollout_payload(self, rollout_id: str) -> dict:
        if rollout_id in self.payload_cache:
            return self.payload_cache[rollout_id]
        with self.lock:
            if rollout_id in self.eval_entries:
                e = self.eval_entries[rollout_id]
                payload = compact_rollout(self._eval_rollout(rollout_id), digits=self.payload_digits, extra={
                    "kind": "eval", "scene": e["scene"].model_dump(mode="json") if e["scene"] else None, "plan": None})
                self.payload_cache[rollout_id] = payload
                return payload
            if rollout_id == "demo":
                if self.demo_rollout is None:
                    raise KeyError("demo")
                extra = {"beads_hidden": self.mode == "live", "kind": "demonstration"}
                payload = compact_rollout(self.demo_rollout, extra=extra, digits=self.payload_digits)
            else:
                e = self.session.rollouts[rollout_id]
                plan = e.plan
                payload = compact_rollout(e.rollout, digits=self.payload_digits, extra={
                    "kind": "rollout", "scene": e.scene.model_dump(mode="json"),
                    "plan": _plan_summary(plan)})
                if payload.get("visual") is None:
                    payload["visual"] = self.visual_for(e.scene)
            self.payload_cache[rollout_id] = payload
            return payload

    def _llm_public(self) -> Optional[dict]:
        if self.budget is None and self.gate is None:
            return None
        from .sessions import llm_public
        return llm_public(self.budget, self.session_id)

    def snapshot(self) -> dict:
        with self.lock:
            s = self.session
            rollouts = []
            for rid, e in s.rollouts.items():
                if rid not in self.trace_cache:
                    self.trace_cache[rid] = trace_summary(e.rollout, e.scene)
                rollouts.append({"id": rid, "report": e.rollout.report.model_dump(mode="json"),
                                 "scene_id": e.scene.scene_id, "spec_version": e.spec_version,
                                 "scene": e.scene.model_dump(mode="json"),
                                 "plan": _plan_summary(e.plan), "tags": self.tags.get(rid, []),
                                 "has_frames": bool(e.rollout.frames), "trace": self.trace_cache[rid]})
            for rid, e in self.eval_entries.items():
                if rid not in self.trace_cache:
                    try:
                        self.trace_cache[rid] = trace_summary(self._eval_rollout(rid), e["scene"]) if e["scene"] else None
                    except Exception:  # noqa: BLE001 - a missing log only loses the minimap path
                        self.trace_cache[rid] = None
                has = bool(e.get("rollout_path") or (e["log_path"] and e["scene"] is not None))
                rollouts.append({"id": rid, "report": e["report"].model_dump(mode="json"), "sketch": e.get("sketch", False),
                                 "scene_id": e["report"].scene_id, "spec_version": e["spec_version"],
                                 "scene": e["scene"].model_dump(mode="json") if e["scene"] else None, "plan": None,
                                 "tags": ["eval"], "has_frames": has, "trace": self.trace_cache[rid]})
            demo = None
            if s.demo is not None:
                d = s.demo
                demo = {"demo_id": d.demo_id, "source": d.source, "source_note": d.source_note,
                        "scene_id": d.scene.scene_id, "sample_hz": d.sample_hz,
                        "duration": d.frames[-1].t if d.frames else 0.0,
                        "events": [e.model_dump(mode="json") for e in d.events],
                        "video_path": d.video_path, "rollout_id": "demo",
                        "scene": d.scene.model_dump(mode="json")}
            scene = s.scene.model_dump(mode="json") if s.scene is not None else None
            return {
                "format": SNAPSHOT_FORMAT,
                "mode": self.mode,
                "revision": self.revision,
                "session_id": s.session_id,
                "missing_packages": self.missing if self.mode == "fixture" else [],
                "reference_correction": REFERENCE_CORRECTION,
                "scene": scene,
                "scene_visual": self.visual_for(s.scene) if s.scene is not None else None,
                "scene_presets": self.scene_presets(),
                "demo": demo,
                "specs": [sp.model_dump(mode="json") for sp in s.specs],
                "rollouts": rollouts,
                "rechecks": self.rechecks,
                "clarification": self.clarification,
                "evaluation": self.evaluation,
                "jobs": [j.public() for j in sorted(self.jobs.values(), key=lambda j: j.started)[-12:]],
                "events": s.events[-50:],
                "teleop_available": self.mode == "live",
                "llm_mode": os.environ.get("MIRROR_LLM_MODE") or ("live" if os.environ.get("MODEL_API_KEY") else "offline"),
                "llm": self._llm_public(),
                "models": self.model_log,
                "video_demo": self.video_demo,
                "transcripts": list(self.transcripts.values())[-10:],
                "capabilities": self.capabilities(),
                "fallbacks": self.fallbacks,
                "limits": {"max_eval_seeds": max_eval_seeds(), "one_job_at_a_time": self.one_job,
                           "showcase_seeds": [SHOWCASE_SEEDS.start, SHOWCASE_SEEDS.stop - 1]},
            }


def trace_summary(rollout: Rollout, scene: SceneState, points: int = 90) -> Optional[dict]:
    """Downsampled object paths for minimaps: [x, y, z, tilt_deg] per point.

    ``points`` is the container's (first cup) path; ``objects`` holds every
    movable object's path."""
    import math
    if not rollout.frames:
        return None
    frames = rollout.frames
    step = max(1, len(frames) // points)
    idx = list(range(0, len(frames), step)) + [len(frames) - 1]
    paths = {}
    for o in scene.objects:
        pts = []
        cur = None
        k = 0
        for i, f in enumerate(frames):
            cur = f.bodies.get(o.id, cur)
            if k < len(idx) and i == idx[k]:
                while k < len(idx) and idx[k] == i:
                    k += 1
            else:
                continue
            p = cur
            if p is None:
                continue
            w, x, y, z = p.quat
            zz = 1.0 - 2.0 * (x * x + y * y)
            tilt = math.degrees(math.acos(max(-1.0, min(1.0, zz))))
            pts.append([round(p.pos[0], 3), round(p.pos[1], 3), round(p.pos[2], 3), round(tilt, 1)])
        paths[o.id] = pts
    cup = next((o.id for o in scene.objects if o.kind == "cup"), None)
    # Container tilt over time (about 200 samples) for the before/after chart.
    tilt = []
    if cup is not None:
        cur = None
        tstep = max(1, len(frames) // 200)
        for i, f in enumerate(frames):
            cur = f.bodies.get(cup, cur)
            if cur is None or (i % tstep and i != len(frames) - 1):
                continue
            w, x, y, z = cur.quat
            zz = 1.0 - 2.0 * (x * x + y * y)
            tilt.append([round(f.t, 2), round(math.degrees(math.acos(max(-1.0, min(1.0, zz)))), 1),
                         1 if f.phase.startswith(("transport", "lift", "place")) and f.phase.endswith(":" + cup) else 0])
    return {"object_id": cup, "points": paths.get(cup, []), "objects": paths, "tilt": tilt}


def scene_sketch(rollout: Rollout, scene_id: str = "") -> SceneState:
    """An approximate SceneState rebuilt from a recorded rollout's own geometry.

    Used to label and map loaded evaluation rollouts without regenerating their
    layouts. Static geoms become box obstacles (a box that does not reach the
    table is the lintel above an opening and is kept as a wall slot); objects
    take their first recorded pose; kinds come from the instance id prefix."""
    from ..contracts import Workspace
    from ..geometry import BLOCK, CUP, MARKER, WORLD
    vis = rollout.visual
    objects, trays, obstacles = [], [], []
    first = {}
    for f in rollout.frames:
        for k, v in f.bodies.items():
            first.setdefault(k, v)
    if vis is not None:
        for b in vis.bodies:
            if not b.static:
                continue
            if b.name.startswith("tray"):
                xs = [g.pos[0] for g in b.geoms]
                ys = [g.pos[1] for g in b.geoms]
                floor = min(b.geoms, key=lambda g: g.pos[2])
                trays.append(TraySpec(id=b.name, pose=Pose(pos=(sum(xs) / len(xs), sum(ys) / len(ys), floor.pos[2] + floor.size[2])),
                                      inner_half=(max(0.02, floor.size[0] - 0.008), max(0.02, floor.size[1] - 0.008))))
                continue
            if b.name in ("world", "table") or not b.geoms:
                continue
            boxes = [g for g in b.geoms if g.type == "box"]
            if not boxes:
                continue
            low = [g for g in boxes if g.pos[2] - g.size[2] < 0.02]
            high = [g for g in boxes if g.pos[2] - g.size[2] >= 0.02]
            for i, g in enumerate(low):
                obstacles.append(ObstacleSpec(id=f"{b.name}" if len(low) == 1 else f"{b.name}#{i}", kind="box",
                                              pose=Pose(pos=g.pos, quat=g.quat), half=tuple(g.size)))
            for i, g in enumerate(high):
                # The lintel spans an opening; draw it as a wall with a slot over its length.
                obstacles.append(ObstacleSpec(id=f"{b.name}~slot{i}", kind="wall",
                                              pose=Pose(pos=(g.pos[0], g.pos[1], 0.0)), length=2 * g.size[1],
                                              height=g.pos[2] + g.size[2], thickness=2 * g.size[0],
                                              slots=[SlotSpec(y_min=g.pos[1] - g.size[1], y_max=g.pos[1] + g.size[1],
                                                              z_min=0.0, z_max=g.pos[2] - g.size[2])]))
    for oid, pose in first.items():
        kind = next((k for k in ("cup", "block", "marker") if oid.startswith(k)), None)
        if kind is None or "/" in oid or "bead" in oid:
            continue
        size = {"cup": {"outer_half": list(CUP.outer_half), "wall": CUP.wall}, "block": {"half": list(BLOCK.half)},
                "marker": {"radius": MARKER.radius, "half_length": MARKER.half_length}}[kind]
        objects.append(ObjectSpec(id=oid, kind=kind, size=size, mass=0.05, pose=pose,
                                  beads=sum(1 for n in first if n.startswith(oid + "/") and "bead" in n)))
    return SceneState(scene_id=scene_id or rollout.report.scene_id, split="custom", objects=objects, trays=trays,
                      obstacles=obstacles,
                      workspace=Workspace(x_range=WORLD.workspace_x, y_range=WORLD.workspace_y, z_range=WORLD.workspace_z),
                      gripper_start=first.get("gripper", Pose(pos=(0.0, 0.0, 0.3))),
                      notes="sketch rebuilt from the recorded rollout geometry")


def _frame_dict(f: RolloutFrame, teleop=None) -> dict:
    held = None
    if teleop is not None and hasattr(teleop, "held_object"):
        try:
            held = teleop.held_object()
        except Exception:  # noqa: BLE001
            held = None
    return {"t": f.t, "width": f.gripper_width, "phase": f.phase, "held": held,
            "bodies": {k: [round(x, 5) for x in list(v.pos) + list(v.quat)] for k, v in f.bodies.items()}}


def _plan_summary(plan: Optional[PlanResult]) -> Optional[dict]:
    if plan is None:
        return None
    return {"status": plan.status, "reason": plan.reason, "planner": plan.planner,
            "object_order": plan.object_order, "planning_time_s": plan.planning_time_s,
            "routes": [r.model_dump(mode="json") for r in plan.routes],
            "infeasibility": plan.infeasibility,
            "transport_notes": [s.note for s in plan.steps if s.kind == "transport"],
            "transport": [_transport_entry(s) for s in plan.steps if s.kind == "transport"]}


def _transport_entry(step) -> dict:
    """Object id plus the route/mode fields the planner writes into a transport step note."""
    fields = {}
    for part in (step.note or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            fields[k.strip()] = v.strip()
    return {"object_id": step.object_id, "route": fields.get("route", step.note or ""), "mode": fields.get("mode", ""),
            "note": step.note}
