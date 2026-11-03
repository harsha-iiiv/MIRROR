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
from ..lineage import file_sha256, scene_sha256, sha256_of
from .provenance import check_step, media_step, model_step, sim_step
from .uploads import MediaQuota, SessionMedia, UploadEntry, UploadLimits, UploadRejected, validate_video

SNAPSHOT_FORMAT = "mirror-studio-session/1"
STATE_FORMAT = "mirror-studio-state/1"

# Provenance vocabulary (docs/CHALLENGE_API.md section 3). Offline or recorded output is
# never labelled as a fresh model call.
PROVENANCE = ("recorded", "fresh_model_call", "offline_interpreter", "replayed_cassette", "fixture", "simulated")


class StaleGeneration(RuntimeError):
    """A request or job belongs to a demonstration generation that is no longer current."""

    def __init__(self, asked: Optional[str], current: str):
        super().__init__(f"generation {asked} is no longer current (now {current}): the demonstration or "
                         "its interpretation changed; reload the state")
        self.asked, self.current = asked, current


class JobCancelled(RuntimeError):
    pass


def provenance_of(source: Optional[str]) -> str:
    """Map an internal source label onto the public provenance vocabulary."""
    s = (source or "").lower()
    if s == "recorded":
        # Internally "recorded" means a model response replayed from a cassette.
        return "replayed_cassette"
    if s in PROVENANCE:
        return s
    if s.startswith("fixture"):
        return "fixture"
    if s in ("live", "record") or s.startswith("live"):
        return "fresh_model_call"
    if s in ("recorded", "replay", "cassette") or s.startswith("replay"):
        return "replayed_cassette"
    if "offline" in s:
        return "offline_interpreter"
    if s in ("recording", "shipped_recording"):
        return "recorded"
    return "offline_interpreter" if not s else s
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
    def __init__(self, kind: str, params: dict, generation_id: Optional[str] = None):
        self.id = "j" + uuid.uuid4().hex[:8]
        self.kind = kind
        self.params = params
        self.status = "queued"  # queued | running | done | error | cancelled
        self.progress = 0.0
        self.message = ""
        self.result: Any = None
        self.error: Optional[str] = None
        self.error_code: Optional[str] = None
        self.started = time.time()
        self.run_started: Optional[float] = None
        self.finished: Optional[float] = None
        self.queue_position = 0     # > 0 while waiting for a free simulator
        self.generation_id = generation_id      # the generation the request was made in
        self.cancel_requested = False
        self.stale = False                      # finished after its generation was replaced: result not applied

    def public(self) -> dict:
        return {"id": self.id, "kind": self.kind, "params": self.params, "status": self.status,
                "progress": round(self.progress, 3), "message": self.message, "result": self.result,
                "error": self.error, "error_code": self.error_code, "started": self.started,
                "finished": self.finished, "queue_position": self.queue_position,
                "generation_id": self.generation_id, "cancel_requested": self.cancel_requested,
                "stale": self.stale}

    @classmethod
    def interrupted(cls, d: dict) -> "Job":
        """A job that was queued or running when the server stopped (restored as failed)."""
        j = cls(d.get("kind", "?"), d.get("params") or {}, d.get("generation_id"))
        j.id, j.started = d.get("id", j.id), d.get("started", j.started)
        j.status, j.finished = "error", time.time()
        j.error_code = "interrupted"
        j.error = "the server restarted while this job was waiting or running; nothing from it was kept"
        return j


# Jobs that run the simulator (the expensive part); they share the server's job gate.
SIM_JOB_KINDS = {"run", "clarify", "evaluate", "video", "challenge", "challenge_execute"}

TOUR_DIR = REPO_ROOT / "data" / "challenge_tour"


def tour_record(version: Optional[str] = None) -> Optional[dict]:
    """The shipped, recorded challenge tour (no live inference), newest version unless named."""
    if not TOUR_DIR.exists():
        return None
    dirs = sorted(d for d in TOUR_DIR.iterdir() if (d / "record.json").exists())
    if version:
        dirs = [d for d in dirs if d.name == version]
    if not dirs:
        return None
    rec = json.loads((dirs[-1] / "record.json").read_text())
    rec["tour_version"] = dirs[-1].name
    return rec


def tour_rollout(rollout_id: str, version: Optional[str] = None) -> dict:
    rec = tour_record(version)
    if rec is None:
        raise KeyError("tour")
    if not all(c.isalnum() or c in "._-@" for c in rollout_id):
        raise KeyError(rollout_id)
    p = TOUR_DIR / rec["tour_version"] / "rollouts" / f"{rollout_id}.json"
    if not p.exists():
        raise KeyError(rollout_id)
    return json.loads(p.read_text())


def tour_summary() -> Optional[dict]:
    rec = tour_record()
    if rec is None:
        return None
    return {"record_id": rec.get("record_id"), "version": rec.get("tour_version"), "provenance": "recorded",
            "label": rec.get("label") or "Recorded tour: no live model call or simulation happens when you watch it"}

MAX_UPLOAD_MB = 40          # per video file (default; MIRROR_UPLOAD_MAX_MB, see uploads.py)
JOB_TIMEOUT_S = float(os.environ.get("MIRROR_JOB_TIMEOUT_S") or 900)
# Worst case of one video demonstration (perception's own caps): Muse Spark grounding on the first
# frame (3 per camera) plus up to 2 re-seeds per camera; SAM: per camera one video request, one
# scene-parts request and up to 2 re-seed requests.
VIDEO_WORST_GROUNDING = 10
VIDEO_WORST_SAM = 8
MAX_VIDEO_S = 90.0
MAX_WAV_S = 30.0


def _probe_video(path: Path) -> dict:
    """Strict probe (kept name): raises UploadRejected unless duration, size and rate are known and bounded."""
    from .uploads import probe_video
    return probe_video(Path(path), UploadLimits.from_env())


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
                 private_runtime: bool = False, admission=None, client_addr: str = "local",
                 media_quota: Optional[MediaQuota] = None, records=None, state_dir: Optional[Path] = None,
                 upload_limits: Optional[UploadLimits] = None):
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
        self.demo_rollouts: dict = {}                        # demo playback id -> playback (all generations)
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
        self.transcripts: dict[str, dict] = {}
        self.admission = admission            # per-address admission (public server)
        self.client_addr = client_addr
        self.records = records                # shared immutable record store (challenge share/replay)
        self.state_dir = Path(state_dir) if state_dir else None   # persisted for restart recovery
        self.upload_limits = upload_limits or UploadLimits.from_env()
        self.media = SessionMedia(self.upload_limits, media_quota)
        self.challenges: dict = {}
        self.challenge_order: list[str] = []
        self.study = None                       # StudyRecorder when a facilitator started study mode
        self._seen_generation = self.session.generation.id
        if self.mode == "live" and (gate is not None or budget is not None):
            from .sessions import make_client
            client = make_client(budget, session_id or "local")
            if client is not None:
                self.session.client = client

    @property
    def uploads(self) -> dict:
        """Upload id -> public description (read only; managed by ``self.media``)."""
        return {vid: {**e.public(), "path": e.path} for vid, e in self.media.entries.items()}

    # -------------------------------------------------------- lineage
    @property
    def demo_playback_id(self) -> Optional[str]:
        return self.session.generation.demo_playback_id

    @property
    def demo_rollout(self) -> Optional[Rollout]:
        pid = self.demo_playback_id
        return self.demo_rollouts.get(pid) if pid else None

    @demo_rollout.setter
    def demo_rollout(self, r: Optional[Rollout]) -> None:
        pid = self.demo_playback_id
        if pid and r is not None:
            r.report.rollout_id = pid
            self.demo_rollouts[pid] = r

    @property
    def generation_id(self) -> str:
        return self.session.generation.id

    def check_generation(self, asked: Optional[str]) -> None:
        """Refuse a request that names a generation other than the current one."""
        if asked and asked != self.generation_id:
            raise StaleGeneration(asked, self.generation_id)

    def _guard(self, job: "Job") -> None:
        """Inside the lock, before a job changes anything: still the generation it was made for?"""
        if job.cancel_requested:
            raise JobCancelled("cancelled before it changed anything")
        if job.generation_id and job.generation_id != self.generation_id:
            raise StaleGeneration(job.generation_id, self.generation_id)

    def _sync_generation(self) -> None:
        """Drop adapter state that belonged to a replaced generation."""
        gid = self.generation_id
        if gid == self._seen_generation:
            return
        self._seen_generation = gid
        self.clarification = None
        if self.evaluation and (self.evaluation.get("source") or {}).get("kind") == "showcase":
            self.evaluation = None
        self.video_demo = self.video_demo if (self.video_demo or {}).get("generation_id") == gid else None
        keep = {g.id for g in self.session.all_generations()}
        self.media.forget_generations(keep)

    def _spec_vid(self, version: Optional[int] = None) -> Optional[str]:
        try:
            return self.session.spec_version_id(version)
        except RuntimeError:
            return None

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
        self._sync_generation()
        self.revision += 1
        self.persist()
        self._emit({"type": "state", "revision": self.revision, "generation_id": self.generation_id})

    # ------------------------------------------------------------- jobs
    def busy(self) -> bool:
        self._expire_jobs()
        return any(j.status in ("queued", "running") for j in self.jobs.values())

    def _expire_jobs(self) -> None:
        """A job running longer than MIRROR_JOB_TIMEOUT_S is reported failed; if its thread ever
        returns, the result is discarded (it cannot be interrupted mid-simulation)."""
        now = time.time()
        for j in list(self.jobs.values()):
            started = getattr(j, "run_started", None)
            if getattr(j, "status", None) == "running" and started and now - started > JOB_TIMEOUT_S:
                j.status, j.error_code = "error", "timeout"
                j.error = f"the job ran longer than {JOB_TIMEOUT_S:.0f} s and was abandoned; its result will not be used"
                j.stale, j.cancel_requested, j.finished = True, True, now
                self._emit({"type": "job", "job": j.public()})

    def cancel_job(self, job_id: str) -> dict:
        """Cancel a queued job at once; a running job stops at its next step boundary."""
        j = self.jobs[job_id]
        if j.status in ("queued", "running"):
            j.cancel_requested = True
            if j.status == "queued" and self.gate is None:
                pass        # the runner sees the flag before it starts
            self._emit({"type": "job", "job": j.public()})
        return j.public()

    def close(self, reason: str = "", keep_files: bool = False) -> None:
        """End this session: stop accepting work and release its resources.

        ``keep_files`` (server stopping with persistence on): media and runtime files stay
        on disk for the restored session; only in-memory resources are released."""
        if keep_files:
            self.persist()
        if self.study is not None and not keep_files:
            try:
                self.study.close()
            except Exception:  # noqa: BLE001 - never block closing
                pass
        self.closed = True
        for j in self.jobs.values():
            if j.status in ("queued", "running"):
                j.cancel_requested = True
        self._emit({"type": "expired", "reason": reason})
        self.listeners.clear()
        self.executor.shutdown(wait=False, cancel_futures=True)
        if keep_files:
            if self.media.quota is not None:
                self.media.quota.release(self.media.held_bytes())
            return
        self.media.clear()
        if self.private_runtime and hasattr(self, "_rt"):
            import shutil
            shutil.rmtree(self._rt, ignore_errors=True)

    def submit(self, kind: str, params: dict, fn: Callable[[Job], Any]) -> Job:
        if self.closed:
            raise RuntimeError("this session has ended")
        if self.one_job and self.busy():
            from .sessions import SessionBusy
            raise SessionBusy("a job is already running in this session; wait for it to finish")
        if self.admission is not None:
            self.admission.check("job", self.client_addr)
        job = Job(kind, params, self.generation_id)
        self.jobs[job.id] = job
        if len(self.jobs) > 200:                      # bounded job history
            for old in sorted((j for j in self.jobs.values() if j.status not in ("queued", "running")),
                              key=lambda j: j.started)[:len(self.jobs) - 200]:
                self.jobs.pop(old.id, None)
        gated = self.gate is not None and kind in SIM_JOB_KINDS

        def runner():
            if gated:
                job.message = "Waiting for a free simulator"
                ok = self.gate.acquire(job, notify=lambda j: self._emit({"type": "job", "job": j.public()}),
                                       cancelled=lambda: self.closed or job.cancel_requested)
                if not ok:
                    job.status = "cancelled" if job.cancel_requested and not self.closed else "error"
                    job.error = "cancelled while waiting" if job.status == "cancelled" else "session ended while waiting"
                    job.error_code = "job_cancelled"
                    job.finished = time.time()
                    self._emit({"type": "job", "job": job.public()})
                    return
            job.run_started = time.time()
            try:
                if job.cancel_requested:
                    raise JobCancelled("cancelled before it started")
                if job.generation_id != self.generation_id:
                    raise StaleGeneration(job.generation_id, self.generation_id)
                job.status = "running"
                job.message = ""
                self._emit({"type": "job", "job": job.public()})
                result = fn(job)
                if job.stale:                         # abandoned by the watchdog meanwhile
                    raise RuntimeError("finished after it was abandoned; result discarded")
                job.result = result
                job.status = "done"
                job.progress = 1.0
            except JobCancelled as exc:
                job.status, job.error, job.error_code = "cancelled", str(exc), "job_cancelled"
            except StaleGeneration as exc:
                job.status, job.error, job.error_code = "error", str(exc), "stale_generation"
                job.stale = True
            except Exception as exc:  # noqa: BLE001 - surfaced to the user
                if job.status != "error":
                    job.status = "error"
                    job.error = f"{type(exc).__name__}: {exc}"
                    job.error_code = job.error_code or "job_failed"
                    job.message = traceback.format_exc(limit=4)
            finally:
                if gated:
                    self.gate.release()
            job.finished = job.finished or time.time()
            self._emit({"type": "job", "job": job.public()})
            self._changed()

        try:
            self.executor.submit(runner)
        except RuntimeError as exc:                   # executor shut down (session closing)
            self.jobs.pop(job.id, None)
            raise RuntimeError("this session has ended") from exc
        self._emit({"type": "job", "job": job.public()})
        return job

    def _progress(self, job: Job, frac: float, message: str) -> None:
        if job.cancel_requested and job.status == "running":
            raise JobCancelled("cancelled")
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
                s.set_demo(DemoTrace.model_validate_json(gzip.decompress(pinned.read_bytes())), reason="demo_loaded")
                s._event("demo_loaded", demo_id=s.demo.demo_id, source=s.demo.source)
            elif self.mode == "live" and ref.exists():
                s.load_demo(ref)
            elif self.mode == "live":
                from ..scenes import reference_scene
                s.record_demo(reference_scene("demo"))
            else:
                s.record_demo()
            self.demo_rollout = self._make_demo_rollout()
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
                self._guard(job)
                if self.session.demo is None:
                    self.load_demo()
                t0, mark = time.time(), self._llm_mark()
                spec, fb = self._with_fallback(self.session.interpret, job)
                if fb:
                    self.fallbacks.append({"step": "interpret", "used": fb, "version": spec.version})
                lat, tok = self._llm_latency(mark, t0)
                sp = self._model_prov(fb, mark, t0, spec.interpreter)
                self._log_model(f"specification v{spec.version}", "proposes the task from the demonstration",
                                spec.interpreter, lat, self._client_source(fb), tokens=tok, step_provenance=sp)
                job.generation_id = self.generation_id      # interpreting may start a new generation
            return {"version": spec.version, "fallback": fb, "spec_version_id": self._spec_vid(spec.version),
                    "generation_id": self.generation_id, "provenance": provenance_of(self._client_source(fb)),
                    "fallback_reason": fb, "step_provenance": sp}
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
                self._guard(job)
                before = self.session.spec.version
                try:
                    t0, mark = time.time(), self._llm_mark()
                    (spec, record), fb = self._with_fallback(lambda: self.session.correct(text), job)
                    sp = self._model_prov(fb, mark, t0)
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
                                        tokens=self._llm_latency(mark, t0)[1], step_provenance=sp)
                except Exception as exc:  # noqa: BLE001 - typed outcomes from the interpreter
                    name = type(exc).__name__
                    if name == "NeedsClarification":
                        return {"version": before, "changed": False, "question": getattr(exc, "question", str(exc)),
                                "options": getattr(exc, "options", [])}
                    if name in ("CorrectionRejected", "InterpretError"):
                        return {"version": before, "changed": False, "rejected": str(exc)}
                    raise
                changed = spec.version != before
                result = {"version": spec.version, "changed": changed, "record": _dump(record),
                          "spec_version_id": self._spec_vid(spec.version), "generation_id": self.generation_id,
                          "provenance": provenance_of(self._client_source(fb)), "fallback_reason": fb,
                          "step_provenance": sp}
                question = getattr(record, "question", None) or (record.get("question") if isinstance(record, dict) else None)
                if question:
                    result["question"] = question
                # Re-check every recorded rollout under the new version, so an
                # old behaviour's violation of the new rule is visible.
                if changed:
                    self._recheck_generation(spec.version)
                return result
        return self.submit("correct", {"text": text, "transcript_id": transcript_id}, work)

    def _recheck_generation(self, version: int) -> list[dict]:
        """Re-check every rollout of the current generation under ``version`` (from step logs)."""
        out = []
        svid = self._spec_vid(version)
        for rid, e in list(self.session.rollouts.items()):
            if getattr(e, "log", None) is None:
                continue
            item = {"rollout_id": rid, "spec_version": version, "spec_version_id": svid,
                    "generation_id": self.generation_id}
            try:
                t0 = time.time()
                item["report"] = _dump(self.session.recheck(rid, version))
                item["step_provenance"] = check_step(time.time() - t0, "fixture" if self.mode == "fixture"
                                                     else "new_simulation")
            except Exception as exc:  # noqa: BLE001
                item["error"] = str(exc)
            self.rechecks = [r for r in self.rechecks
                             if not (r["rollout_id"] == rid and r.get("spec_version_id") == svid)]
            self.rechecks.append(item)
            out.append(item)
        return out

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
            got, tok = {}, {}
            for line in Path(log).read_text().splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("key") in want and not r.get("from_cassette"):
                    got[r["key"]] = float(r.get("latency_s") or 0)
                    tok[r["key"]] = int(r.get("total_tokens") or 0)
            if want and want <= set(got):
                return sum(got.values()), tokens or (sum(tok.values()) or None)
        return None, tokens

    def _model_prov(self, fb: Optional[str], mark: int, t0: float, model: Optional[str] = None) -> dict:
        """Step provenance of the interpreter calls made since ``mark`` (see provenance.py)."""
        if self.mode == "fixture":
            return model_step("fixture", model=None, latency_s=time.time() - t0)
        c = getattr(self.session, "client", None)
        new = (getattr(c, "usage", None) or [])[mark:]
        lat, tok = self._llm_latency(mark, t0)
        if fb:
            return model_step("offline rules (fallback)", model=None, latency_s=time.time() - t0, fallback_reason=fb)
        if not new:
            return model_step("offline rules", model=None, latency_s=time.time() - t0)
        live = [e for e in new if not e.get("from_cassette")]
        mdl = model or next((e.get("model") for e in new if e.get("model")), None)
        if live:
            return model_step("live", model=mdl, latency_s=lat, tokens=tok)
        return model_step("recorded", model=mdl, latency_s=lat, tokens=tok,
                          latency_source="recorded_with_cassette" if lat is not None else None)

    def _log_model(self, artifact: str, role: str, model: Optional[str], latency_s: float, source: str,
                   **extra) -> dict:
        entry = {"artifact": artifact, "role": role, "model": model or "unknown",
                 "latency_s": round(float(latency_s), 3) if latency_s is not None else None,
                 "source": source, "provenance": provenance_of(source), "at": time.time(),
                 "generation_id": self.generation_id,
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
        if self.admission is not None:
            self.admission.check("model", self.client_addr, n)
        for _ in range(n):
            why = b.take(self.session_id or "local")
            if why:
                raise RuntimeError(f"{why.capitalize()}. The recorded demonstration and typing still work.")

    def _budget_for(self, kind: str):
        return self.budget if kind == "interpreter" else self.meta_budgets.get(kind)

    def _reserve_video(self) -> dict:
        """Reserve the worst case of one video demonstration up front (all or nothing).

        Grounding requests count against the interpreter budget, with room left for the
        proposal; every SAM request against the SAM budget; the video observation against
        the video budget. Returns what was reserved, settled later against the real calls."""
        if self.mode == "fixture":
            return {}
        sid = self.session_id or "local"
        want = [("interpreter", VIDEO_WORST_GROUNDING + 1), ("sam", VIDEO_WORST_SAM), ("video", 1)]
        if self.admission is not None:
            self.admission.check("model", self.client_addr, sum(n for _k, n in want))
        held: dict = {}
        for kind, n in want:
            b = self._budget_for(kind)
            if b is None:
                continue
            why = b.reserve(sid, n)
            if why:
                for k, m in held.items():
                    self._budget_for(k).release(sid, m)
                what = {"interpreter": "Muse Spark", "sam": "SAM 3.1", "video": "video understanding"}[kind]
                raise RuntimeError(f"A video demonstration can need {n} {what} requests and {why}. "
                                   "The recorded demonstration and typing still work.")
            held[kind] = n
        if "interpreter" in held:                # leave the proposal's request to its own client
            self.budget.release(sid, 1)
            held["interpreter"] -= 1
        return held

    def _settle(self, held: dict, kind: str, used: int) -> None:
        """Charge ``used`` real requests against a reservation and give back the rest."""
        n = held.pop(kind, None)
        b = self._budget_for(kind)
        if n is None or b is None:
            return
        sid = self.session_id or "local"
        if used < n:
            b.release(sid, n - used)
        elif used > n:                           # more than the stated worst case: charge it anyway
            for _ in range(used - n):
                b.take(sid)

    def admit_upload(self, nbytes: int) -> None:
        """Reserve room for an upload of ``nbytes`` before it is written (raises UploadRejected / RateLimited)."""
        if self.closed:
            raise RuntimeError("this session has ended")
        if nbytes > self.upload_limits.max_file_bytes:
            raise UploadRejected(f"upload larger than {self.upload_limits.max_file_bytes // 2**20} MB",
                                 code="too_large", status=413)
        if self.admission is not None:
            self.admission.check("upload", self.client_addr)
            try:
                self.admission.check("upload_bytes", self.client_addr, max(1, nbytes // 2**20))
            except Exception:
                self.admission.give_back("upload", self.client_addr)
                raise
        try:
            self.media.admit(nbytes)
        except UploadRejected:
            if self.admission is not None:
                self.admission.give_back("upload", self.client_addr)
                self.admission.give_back("upload_bytes", self.client_addr, max(1, nbytes // 2**20))
            raise

    def upload_dir(self) -> Path:
        d = self._runtime_dir() / "uploads"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def accept_upload(self, path: Path, view: str, nbytes: int) -> dict:
        """Validate an admitted upload already written to ``path``. Rejected files are deleted
        and their reservation is given back; accepted ones are registered and expire if unused."""
        path = Path(path)
        try:
            if view not in ("top", "side"):
                raise UploadRejected("view must be top or side")
            size = path.stat().st_size
            if size != nbytes:
                raise UploadRejected("upload size does not match what was announced")
            if size < 64:
                raise UploadRejected("file too small to be a video")
            info = validate_video(path, self.upload_limits)
            vid = "v" + uuid.uuid4().hex[:10]
            final = self.upload_dir() / f"{vid}.mp4"
            path.replace(final)
            entry = UploadEntry(id=vid, path=str(final), view=view, bytes=size, sha256=file_sha256(final), info=info)
        except BaseException:
            path.unlink(missing_ok=True)
            self.media.unadmit(nbytes)
            raise
        self.media.add(entry)
        self.persist()
        return entry.public()

    def upload_video(self, data: bytes, view: str = "top") -> dict:
        """Bytes in memory (tests, small clients): admit, write, validate."""
        if view not in ("top", "side"):
            raise UploadRejected("view must be top or side")
        self.admit_upload(len(data))
        tmp = self.upload_dir() / f".incoming-{uuid.uuid4().hex[:8]}"
        try:
            tmp.write_bytes(data)
        except BaseException:
            tmp.unlink(missing_ok=True)
            self.media.unadmit(len(data))
            raise
        return self.accept_upload(tmp, view, len(data))

    def video_file(self, vid: str) -> str:
        if vid == "bundled-top" or vid == "bundled-side":
            b = self._meta().bundled_demo() or {}
            p = b.get(vid.split("-")[1])
            if not p:
                raise KeyError(vid)
            return str(p)
        return self.media.get(vid).path

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
            demo_id = Path(top_path).parent.name + "_from_video"     # same id whichever copy is processed
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
            demo_id = f"upload_{top}_from_video"
        media = []
        for vid, pth in ((top_id, top_path), (side_id, side_path)):
            if not vid:
                continue
            if vid in self.media.entries:
                e = self.media.acquire(vid)                # cannot expire while the job reads it
                media.append({k: v for k, v in e.public().items() if k not in ("used_by", "created")})
            else:
                info = {}
                try:
                    info = _probe_video(Path(pth))
                except Exception:  # noqa: BLE001 - a shipped file; its facts are informative only
                    info = {}
                media.append({"id": vid, "view": "top" if vid.endswith("top") or vid == top_id else "side",
                              "bundled": True, "sha256": file_sha256(pth), "bytes": Path(pth).stat().st_size, **info})
        try:
            held = self._reserve_video()
        except BaseException:
            for m in media:
                self.media.release(m["id"])
            raise

        def release_media(gen: Optional[str] = None):
            for m in media:
                self.media.release(m["id"], gen)

        def work(job):
            try:
                return _work(job)
            except BaseException:
                release_media()
                raise

        def _work(job):
            def stage(lo, hi):
                def cb(*a, **k):
                    frac = next((x for x in a if isinstance(x, (int, float))), None)
                    msg = next((x for x in a if isinstance(x, str)), k.get("message", ""))
                    self._progress(job, lo + (hi - lo) * max(0.0, min(1.0, float(frac or 0))), msg or job.message)
                return cb
            self._progress(job, 0.03, "Tracking the objects in the video (SAM 3.1)")
            per = meta.perceive(top_path, side_path, progress=stage(0.03, 0.45), ground_truth=gt, demo_id=demo_id)
            pcalls = per.get("calls") or []
            ground = [c for c in pcalls if not str(c.get("model") or "").startswith("sam")]
            samc = [c for c in pcalls if str(c.get("model") or "").startswith("sam")]
            # Charge what was actually requested from the API (live calls), give the rest back.
            self._settle(held, "interpreter", sum(1 for c in ground if c.get("source") == "live"))
            self._settle(held, "sam", sum(1 for c in samc if c.get("source") == "live"))
            self._progress(job, 0.46, "Reading the video: objects, grasps, releases, care")
            try:
                obs = meta.observe(top_path, progress=stage(0.46, 0.8))
            except Exception:
                held.pop("video", None)          # the request may have reached the API: keep it charged
                raise
            self._settle(held, "video", sum(1 for c in (obs.get("calls") or []) if c.get("source") == "live"))
            self._progress(job, 0.82, "Proposing the task from the reconstruction and the observations")
            with self.lock:
                self._guard(job)
                trace = per["trace"]
                self.session.set_demo(trace, reason="video_processed", source_media=media,
                                      calibration=_dump(per.get("calibration")) if per.get("calibration") else None)
                job.generation_id = self.generation_id
                self.tags.clear()
                self.rechecks = []
                self.evaluation = None
                self.clarification = None
                self.scene_seq = 0
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
                sp = self._model_prov(fb, mark, t0, spec.interpreter)
                self._log_model(f"specification v{spec.version}", "proposes the task from the video reconstruction",
                                spec.interpreter, lat, self._client_source(fb), tokens=tok, step_provenance=sp)
                stages = []
                if ground:
                    stages.append({"stage": "object grounding", **{"step_provenance": media_step(
                        "live" if any(c["source"] == "live" for c in ground) else "recorded", model=ground[0].get("model"),
                        latency_s=sum(float(c.get("latency_s") or 0) for c in ground))}})
                stages.append({"stage": "object tracks", "step_provenance": media_step(
                    ("live" if any(c["source"] == "live" for c in samc) else "recorded") if samc else per.get("source"),
                    model=per.get("model"), latency_s=(sum(float(c.get("latency_s") or 0) for c in samc) if samc
                                                       else per.get("latency_s")))})
                stages.append({"stage": "video observations", "step_provenance": media_step(
                    obs.get("source"), model=obs.get("model"), latency_s=obs.get("latency_s"))})
                stages.append({"stage": "specification proposal", "step_provenance": sp})
                self.demo_rollout = self._make_demo_rollout()
                self.video_demo = {
                    "generation_id": self.generation_id, "source_media": media,
                    "views": {"top": {"id": top_id, **_video_meta(per["tracks"].get("top"))},
                              **({"side": {"id": side_id, **_video_meta(per["tracks"].get("side"))}} if side_id else {})},
                    "tracks": per["tracks"], "observations": obs["observations"], "metrics": per.get("metrics"),
                    "checks": per.get("checks"), "reconstruction_s": per.get("latency_s"),
                    "run": "live" if any(c.get("source") == "live" for c in pcalls) else (
                        "fixture" if self.mode == "fixture" else "recorded"),
                    "bundled": top == "bundled", "fixture": self.mode == "fixture",
                    "trace_source": trace.source, "spec_version": spec.version,
                    "spec_version_id": self._spec_vid(spec.version), "stages": stages,
                    "evidence": _dump(getattr(trace, "evidence", None)),
                }
                release_media(self.generation_id)
                self._changed()
            return {"version": spec.version, "fallback": fb, "generation_id": self.generation_id,
                    "spec_version_id": self._spec_vid(spec.version), "provenance": provenance_of(self._client_source(fb)),
                    "fallback_reason": fb, "step_provenance": sp, "stages": stages}
        return self.submit("video", {"top": top_id, "side": side_id}, work)

    def _reset_session(self) -> None:
        """Start over: archive the current generation (nothing is mutated or reused)."""
        self.session.set_demo(None, reason="reset")
        self.tags.clear()
        self.rechecks = []
        self.evaluation = None
        self.clarification = None
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
            if self.study is None or self.study.keeps_audio:
                # kept for recorded sessions (export); in study mode only with audio consent and a retention policy
                (self._runtime_dir() / f"{tid}.wav").write_bytes(wav)
            rec = {"id": tid, "text": res["text"], "model": res.get("model"), "latency_s": res.get("latency_s"),
                   "source": res.get("source"), "provenance": provenance_of(res.get("source")),
                   "audio_sha256": sha256_of(bytes(wav)), "audio_s": info["seconds"], "at": time.time(),
                   "generation_id": self.generation_id,
                   "step_provenance": media_step(res.get("source"), model=res.get("model"),
                                                 latency_s=res.get("latency_s"))}
            self.transcripts[tid] = rec
            while len(self.transcripts) > 20:            # bounded: the oldest audio is deleted
                old = next(iter(self.transcripts))
                self.transcripts.pop(old)
                (self._runtime_dir() / f"{old}.wav").unlink(missing_ok=True)
            self._log_model("transcript", "speech to text", res.get("model"), res.get("latency_s", 0.0),
                            res.get("source", "unknown"), id=tid)
            self._changed()
            return rec
        try:
            return self.submit("voice", {"seconds": info["seconds"]}, work)
        except BaseException:
            b = self.meta_budgets.get("voice")
            if b is not None and self.mode != "fixture":
                b.release(self.session_id or "local", 1)
            raise

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
                self._guard(job)
                t0 = time.time()
                entry = self.session.run(spec_version=spec_version, condition=condition)
                wall = time.time() - t0
            rid = entry.rollout.report.rollout_id
            self.tags[rid] = list(tags or [])
            return {"rollout_id": rid, "status": entry.rollout.report.status, "label": entry.label,
                    "generation_id": entry.generation_id, "spec_version_id": entry.spec_version_id,
                    "content_sha256": entry.content_sha256,
                    "provenance": "fixture" if self.mode == "fixture" else "simulated",
                    "step_provenance": sim_step(self.mode, wall_s=wall)}
        return self.submit("run", {"spec_version": spec_version, "condition": condition}, work)

    def recheck(self, rollout_id: str, spec_version: Optional[int] = None,
                spec_version_id: Optional[str] = None) -> dict:
        with self.lock:
            entry, spec, svid, own = self.session.recheck_target(rollout_id, spec_version, spec_version_id)
            t0 = time.time()
            rep = self.session.recheck(rollout_id, spec.version if spec_version_id is None else None,
                                       spec_version_id=spec_version_id)
            item = {"rollout_id": rollout_id, "spec_version": spec.version, "spec_version_id": svid,
                    "generation_id": own, "cross_generation": not svid.startswith(own + "."), "report": _dump(rep),
                    "step_provenance": check_step(time.time() - t0, "fixture" if self.mode == "fixture"
                                                  else "new_simulation")}
            self.rechecks = [r for r in self.rechecks
                             if not (r["rollout_id"] == rollout_id and r.get("spec_version_id", r["spec_version"]) == svid)]
            self.rechecks.append(item)
            self._changed()
            return item

    def clarify(self) -> Job:
        def work(job):
            self._progress(job, 0.05, "Finding distinct valid behaviours")
            with self.lock:
                self._guard(job)
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
            rep.condition = f"candidate {i + 1}"
            rep.route_labels = [s.note for s in plan.steps if s.kind == "transport"]
            if res.status != "completed":
                rep.status = "timeout" if res.status == "timeout" else "execution_failed"
                rep.notes.append(res.reason)
            entry = self.session.add_rollout(Rollout(report=rep, visual=env.visual_scene(), frames=res.frames),
                                             res.log, plan, scene, spec, kind="c")
            rid = entry.rollout_id
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
                self._guard(job)
                t0, mark = time.time(), self._llm_mark()
                (spec, record), fb = self._with_fallback(lambda: self.session.answer(text), job)
                sp = self._model_prov(fb, mark, t0)
                if fb:
                    self.fallbacks.append({"step": "answer", "used": fb, "version": spec.version})
                self.clarification = None
            return {"version": spec.version, "record": _dump(record), "fallback": fb,
                    "spec_version_id": self._spec_vid(spec.version), "generation_id": self.generation_id,
                    "provenance": provenance_of(self._client_source(fb)), "fallback_reason": fb,
                    "step_provenance": sp}
        return self.submit("answer", {"text": text}, work)

    def evaluate(self, seeds: list[int], spec_a: Optional[int] = None, spec_b: Optional[int] = None,
                 split: str = SHOWCASE_SPLIT) -> Job:
        seeds = check_showcase(seeds, split)

        def work(job):
            s = self.session
            a = s.spec_version(spec_a) if spec_a else s.specs[0]
            b = s.spec_version(spec_b) if spec_b else s.spec
            self._progress(job, 0.02, f"Paired evaluation on {len(seeds)} layouts")
            self._guard(job)
            if self.mode == "fixture":
                rep = s.paired_eval(a, b, seeds, progress=lambda i, n: self._progress(job, i / n, f"Layout {i}/{n}"))
                rep["source"] = {"kind": "showcase", "split": SHOWCASE_SPLIT, "note": "fixture"}
            else:
                rep = self._paired_eval_live(job, a, b, seeds, split)
            rep["generation_id"] = self.generation_id
            rep["spec_a_id"], rep["spec_b_id"] = self._spec_vid(a.version), self._spec_vid(b.version)
            if self.generation_id != job.generation_id:
                raise StaleGeneration(job.generation_id, self.generation_id)
            self.evaluation = rep
            return {"pairs": len(rep.get("pairs") or rep.get("trials") or []) if isinstance(rep, dict) else None}
        return self.submit("evaluate", {"seeds": seeds, "split": split}, work)

    def _runtime_dir(self) -> Path:
        if not hasattr(self, "_rt"):
            import tempfile
            base = os.environ.get("MIRROR_STUDIO_RUNTIME")
            if self.state_dir is not None:
                self._rt = self.state_dir / "runtime"
            elif self.private_runtime:
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
        # Showcase runs get their own id prefix: repeating a seed never reuses an earlier run's ids.
        out = self._register_evaluation(_dump(rep), run_dir, a.version, b.version,
                                        log_dir=run_dir / "logs", rollout_dir=run_dir / "rollouts",
                                        prefix=f"s{uuid.uuid4().hex[:6]}-")
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

    # ------------------------------------------------------------ challenge
    def _record_store(self):
        if self.records is None:
            from .records import RecordStore
            self.records = RecordStore(self._runtime_dir() / "records")
        return self.records

    def _challenge(self, cid: str):
        ch = self.challenges.get(cid)
        if ch is None:
            raise KeyError(f"challenge {cid}")
        return ch

    def _challenge_current(self, cid: str):
        ch = self._challenge(cid)
        if ch.generation_id != self.generation_id:
            raise StaleGeneration(ch.generation_id, self.generation_id)
        return ch

    @staticmethod
    def _rollout_brief(entry, report=None) -> dict:
        rep = report or entry.rollout.report
        beads = [{"object_id": o.object_id, "beads_total": o.beads_total, "beads_in_container": o.beads_in_container}
                 for o in rep.outcomes if getattr(o, "beads_total", 0)]
        return {"rollout_id": entry.rollout_id, "label": entry.label, "status": rep.status,
                "spec_version_id": entry.spec_version_id, "content_sha256": entry.content_sha256,
                "scene_id": entry.scene.scene_id, "route_labels": list(rep.route_labels), "beads": beads,
                "plan_status": rep.plan_status, "plan_reason": rep.plan_reason,
                "violations": [{"constraint_id": v.constraint_id, "object_id": v.object_id, "t_start": v.t_start,
                                "t_end": v.t_end, "peak": v.peak, "detail": v.detail} for v in rep.violations]}

    def challenge_options(self) -> dict:
        from .challenge import options
        out = options(audio=bool(self.capabilities().get("voice")))
        out["tour"] = tour_summary()
        return out

    def challenge_create(self, family: str, seed: Optional[int] = None, preset: Optional[str] = None,
                         edits: Optional[list] = None) -> Job:
        """Build a development challenge scene and execute the current specification on it (prior behaviour)."""
        from .challenge import ChallengeState, challenge_scene
        scene, origin = challenge_scene(family, seed, list(edits or []), preset=preset, fixture=self.mode == "fixture")

        def work(job):
            prep = []
            with self.lock:
                self._guard(job)
                if self.session.demo is None:
                    self.load_demo()
                    prep.append({"step": "demonstration loaded", "demo_id": self.session.demo.demo_id,
                                 "provenance": "fixture" if self.mode == "fixture" else "recorded"})
                if not self.session.specs:
                    self._progress(job, 0.1, "Interpreting the demonstration")
                    t0, mark = time.time(), self._llm_mark()
                    spec, fb = self._with_fallback(self.session.interpret, job)
                    if fb:
                        self.fallbacks.append({"step": "interpret", "used": fb, "version": spec.version})
                    lat, tok = self._llm_latency(mark, t0)
                    sp = self._model_prov(fb, mark, t0, spec.interpreter)
                    self._log_model(f"specification v{spec.version}", "proposes the task from the demonstration",
                                    spec.interpreter, lat, self._client_source(fb), tokens=tok, step_provenance=sp)
                    prep.append({"step": "demonstration interpreted", "spec_version_id": self._spec_vid(spec.version),
                                 "provenance": provenance_of(self._client_source(fb)), "fallback_reason": fb,
                                 "step_provenance": sp})
                job.generation_id = self.generation_id
                ch = ChallengeState(self.generation_id, scene, origin, self._spec_vid())
                ch.preparation = prep
                self.challenges[ch.id] = ch
                self.challenge_order.append(ch.id)
                while len(self.challenge_order) > 10:
                    self.challenges.pop(self.challenge_order.pop(0), None)
                self.session.set_scene(scene)
                self._progress(job, 0.3, "Executing the current specification on the challenge layout")
                t0 = time.time()
                entry = self.session.run(condition=f"challenge {ch.id}: before")
                self.tags[entry.rollout_id] = ["challenge", "before"]
                ch.before = {**self._rollout_brief(entry), "step_provenance": sim_step(self.mode, wall_s=time.time() - t0)}
                ch.state = "scene_ready"
            return {"challenge_id": ch.id, "before_rollout_id": entry.rollout_id, "status": entry.rollout.report.status,
                    "generation_id": self.generation_id,
                    "provenance": "fixture" if self.mode == "fixture" else "simulated",
                    "step_provenance": ch.before["step_provenance"], "preparation": prep}
        return self.submit("challenge", {"family": family, "seed": seed, "preset": preset, "edits": len(edits or [])}, work)

    def challenge_correction(self, cid: str, text: Optional[str] = None, transcript_id: Optional[str] = None) -> Job:
        """Compile a correction for review; nothing is committed."""
        from .challenge import spec_scope
        ch = self._challenge_current(cid)
        tr = None
        if transcript_id is not None:
            tr = self.transcripts.get(transcript_id)
            if tr is None:
                raise KeyError(f"transcript {transcript_id}")
            text = text if text else tr["text"]
        text = (text or "").strip()
        if not text:
            raise ValueError("empty correction")
        if len(text) > 600:
            raise ValueError("a correction is one or two sentences (600 characters at most)")
        how = "typed" if tr is None else ("spoken" if text == tr["text"] else "spoken_edited")

        def work(job):
            self._progress(job, 0.1, "Compiling the correction")
            with self.lock:
                self._guard(job)
                base = self.session.spec
                tid = None
                if self.study is not None:
                    tid = self.study.start_attempt(
                        versions=self._versions(), scene=self._study_scene(ch),
                        lineage=self._study_lineage(self._spec_vid(base.version)),
                        input_={"kind": how, "transcript_approved": True if tr is not None else None},
                        text=text, spec_before=base, challenge_id=ch.id)["trial_id"]
                    if tr is not None:
                        self.study.keep_audio(tid, self._runtime_dir() / f"{transcript_id}.wav")
                t0, mark = time.time(), self._llm_mark()
                prop = {"text": text, "input": how, "transcript_id": transcript_id, "changed": False,
                        "base_spec_version_id": self._spec_vid(base.version), "ops": [], "diff": None, "scope": None,
                        "question": None, "options": [], "rejected": None}
                try:
                    (new, record), fb = self._with_fallback(lambda: self.session.preview_correction(text), job)
                except Exception as exc:  # noqa: BLE001 - typed outcomes from the interpreter
                    name = type(exc).__name__
                    if name == "NeedsClarification":
                        prop.update(question=getattr(exc, "question", str(exc)), options=getattr(exc, "options", []))
                    elif name in ("CorrectionRejected", "InterpretError"):
                        prop["rejected"] = str(exc)
                    else:
                        if tid:
                            self.study.update(tid, proposal={"outcome": "error", "error": f"{type(exc).__name__}: {exc}"[:300],
                                                             "provenance": None, "latency_s": round(time.time() - t0, 3)})
                            self.study.finish(tid, failure_reason="interpretation error")
                        raise
                    new, record, fb = base, None, None
                src = self._client_source(fb)
                prop.update(provenance=provenance_of(src), fallback_reason=fb,
                            model=getattr(record, "model", None) if record is not None else None,
                            step_provenance=self._model_prov(fb, mark, t0))
                q = getattr(record, "question", None) if record is not None else None
                if q:
                    prop["question"] = q
                if new.version != base.version:
                    diff, scope = spec_scope(base, new, ch.scene)
                    prop.update(changed=True, diff=diff, scope=scope,
                                ops=[op.model_dump(mode="json") for op in (record.diff if record is not None else [])],
                                proposed_version=new.version)
                    ch.pending_spec, ch.pending_base_version = new, base.version
                    lat, tok = self._llm_latency(mark, t0)
                    self._log_model(f"proposed specification v{new.version} (challenge {ch.id})",
                                    "compiles the correction for review" + {"typed": " (typed)", "spoken": " (spoken)",
                                                                            "spoken_edited": " (spoken, edited)"}[how],
                                    prop["model"] or self._client_label(), lat, src, input=transcript_id, tokens=tok)
                else:
                    ch.pending_spec, ch.pending_base_version = None, None
                    if not prop["question"] and not prop["rejected"]:
                        prop["rejected"] = "the correction did not change the specification"
                ch.proposal = prop
                ch.state = "proposed" if prop["changed"] else "scene_ready"
                if tid:
                    self._study_proposal(tid, prop, new if prop["changed"] else None)
                prop["trial_id"] = tid
            return prop
        return self.submit("challenge_correction", {"challenge_id": cid, "input": how}, work)

    def _study_proposal(self, tid: str, prop: dict, new_spec) -> None:
        errors = []
        if new_spec is not None and self.mode != "fixture":
            from ..interpret import validate_spec
            errors = list(validate_spec(new_spec))
        outcome = "apply" if prop["changed"] else "asks" if prop.get("question") else "rejects"
        sp = prop.get("step_provenance") or {}
        self.study.update(tid, proposal={
            "outcome": outcome, "ops": prop.get("ops") or [], "scope": prop.get("scope"),
            "question": prop.get("question"), "rejected": prop.get("rejected"),
            "validation": {"passed": not errors, "errors": errors, "checked": new_spec is not None},
            "provenance": prop.get("provenance"), "step_provenance": sp, "fallback_reason": prop.get("fallback_reason"),
            "latency_s": sp.get("latency_s"), "cost_usd": sp.get("cost_usd")})
        if prop.get("question"):
            self.study.update(tid, clarification={"question": prop["question"], "answered": False, "useful": None})
            self.study.event("clarification_shown", question_chars=len(prop["question"]))
        self.study.event("interpretation_returned", outcome=outcome, provenance=prop.get("provenance"),
                         fallback_reason=prop.get("fallback_reason"), latency_s=sp.get("latency_s"))
        if outcome != "apply":
            self.study.finish(tid)

    def challenge_review(self, cid: str, accept: bool, note: str = "") -> dict:
        with self.lock:
            ch = self._challenge_current(cid)
            if ch.pending_spec is None or ch.proposal is None:
                raise RuntimeError("nothing to review: submit a correction that changes the specification first")
            tid = self.study.trial_for(ch.id) if self.study is not None else None
            if not accept:
                ch.proposal = {**ch.proposal, "decision": "discarded"}
                ch.pending_spec = None
                ch.state = "scene_ready"
                if tid:
                    self.study.update(tid, review={"accepted": False})
                    self.study.finish(tid)
                self._changed()
                return ch.public()
            spec = self.session.commit_spec(ch.pending_spec, ch.pending_base_version, ch.proposal["text"])
            ch.pending_spec = None
            ch.proposal = {**ch.proposal, "decision": "accepted", "note": note[:300]}
            ch.revised_spec_version_id = self._spec_vid(spec.version)
            # Independent recheck from the recorded step logs: the planner is not consulted.
            items = self._recheck_generation(spec.version)
            out = []
            for it in items:
                e = self.session.rollouts[it["rollout_id"]]
                if "report" in it:
                    br = self._rollout_brief(e, RolloutReport.model_validate(it["report"]))
                    br["spec_version_id"] = it["spec_version_id"]
                    br["was"] = e.rollout.report.status
                    br["step_provenance"] = it.get("step_provenance")
                else:
                    br = {"rollout_id": it["rollout_id"], "error": it["error"], "status": "unknown"}
                br["is_before"] = it["rollout_id"] == (ch.before or {}).get("rollout_id")
                out.append(br)
            out.sort(key=lambda r: not r.get("is_before"))
            ch.rechecks = out
            ch.state = "reviewed"
            if tid:
                from .trials import semantic_state
                b = next((r for r in out if r.get("is_before")), None)
                self.study.update(tid, review={"accepted": True}, lineage={"revised_spec_version_id": ch.revised_spec_version_id},
                                  spec_state_after=semantic_state(spec),
                                  recheck=({"rollout_id": b["rollout_id"], "status": b.get("status"),
                                            "violations": b.get("violations", []), "provenance": "new_check"} if b else None))
                self.study.event("spec_version_created", spec_version_id=ch.revised_spec_version_id,
                                 spec_state=semantic_state(spec), source="correction")
                if b:
                    self.study.event("recheck_run", rollout_id=b["rollout_id"], verdict=b.get("status"))
            self._changed()
            return ch.public()

    def challenge_execute(self, cid: str) -> Job:
        ch = self._challenge_current(cid)
        if not ch.revised_spec_version_id:
            raise RuntimeError("accept a reviewed correction before executing")
        version = int(ch.revised_spec_version_id.rpartition(".v")[2])

        def work(job):
            self._progress(job, 0.05, "Planning with the revised specification")
            with self.lock:
                self._guard(job)
                self.session.set_scene(ch.scene)
                t0 = time.time()
                entry = self.session.run(spec_version=version, condition=f"challenge {ch.id}: after")
                self.tags[entry.rollout_id] = ["challenge", "after"]
                ch.after = {**self._rollout_brief(entry), "step_provenance": sim_step(self.mode, wall_s=time.time() - t0)}
                ch.state = "executed"
                tid = self.study.trial_for(ch.id) if self.study is not None else None
                if tid:
                    inf = entry.plan.infeasibility if isinstance(entry.plan.infeasibility, dict) else {}
                    self.study.update(tid, execution={
                        "rollout_id": entry.rollout_id, "status": entry.rollout.report.status,
                        "violations": ch.after["violations"],
                        "proven_infeasible": bool(entry.rollout.report.status == "planning_failed" and inf.get("proven")),
                        "provenance": "fixture" if self.mode == "fixture" else "simulated"})
                    self.study.event("execution_run", rollout_id=entry.rollout_id, verdict=entry.rollout.report.status,
                                     scene_key=ch.scene.scene_id)
                    self.study.finish(tid)
            return {"challenge_id": ch.id, "after_rollout_id": entry.rollout_id, "status": entry.rollout.report.status,
                    "generation_id": self.generation_id,
                    "provenance": "fixture" if self.mode == "fixture" else "simulated",
                    "step_provenance": ch.after["step_provenance"]}
        return self.submit("challenge_execute", {"challenge_id": cid}, work)

    def challenge_record(self, cid: str) -> dict:
        """The immutable record a published challenge resolves to (not yet stored)."""
        from .. import __version__ as pkg_version
        ch = self._challenge(cid)
        if ch.state not in ("executed", "published") or not ch.after or not ch.revised_spec_version_id:
            raise RuntimeError("execute the revised specification before sharing")
        base = self.session.resolve_spec_id(ch.base_spec_version_id)
        revised = self.session.resolve_spec_id(ch.revised_spec_version_id)
        gen = next(g for g in self.session.all_generations() if g.id == ch.generation_id)
        interp = (ch.proposal or {}).get("provenance")
        return {
            "format": "mirror-challenge-record/1", "created_at": round(time.time(), 3), "challenge_id": ch.id,
            "scene": {**ch.origin, "scene": ch.scene.model_dump(mode="json")},
            "generation": {"id": gen.id, "demo": {"demo_id": gen.demo_id, "content_sha256": gen.demo_sha256,
                                                  "source": gen.demo_source},
                           "source_media": gen.source_media, "calibration": gen.calibration},
            "preparation": ch.preparation,
            "spec_versions": {
                "base": {"version_id": ch.base_spec_version_id, "content_sha256": sha256_of(base),
                         "spec": base.model_dump(mode="json")},
                "revised": {"version_id": ch.revised_spec_version_id, "content_sha256": sha256_of(revised),
                            "spec": revised.model_dump(mode="json")}},
            "correction": {k: (ch.proposal or {}).get(k) for k in ("text", "input", "ops", "diff", "scope", "provenance",
                                                                    "fallback_reason", "model")},
            "transcript": ({k: v for k, v in self.transcripts[ch.proposal["transcript_id"]].items()}
                           if (ch.proposal or {}).get("transcript_id") in self.transcripts else None),
            "rechecks": ch.rechecks,
            "rollouts": {"before": ch.before, "after": ch.after},
            "provenance": {"interpreter": interp, "recorded": False,
                           "simulation": "fixture" if self.mode == "fixture" else "mujoco",
                           "server_version": os.environ.get("MIRROR_CODE_VERSION") or pkg_version},
        }

    def challenge_publish(self, cid: str) -> dict:
        with self.lock:
            ch = self._challenge(cid)
            if ch.record_id:
                return {"record_id": ch.record_id, "share_path": f"?challenge={ch.record_id}"}
            record = self.challenge_record(cid)
            ids = [ch.before["rollout_id"], ch.after["rollout_id"]]
            payloads = {rid: self.rollout_payload(rid) for rid in ids}
            rid = self._record_store().put(record, payloads)
            ch.record_id = rid
            ch.state = "published"
            self._changed()
            return {"record_id": rid, "share_path": f"?challenge={rid}"}

    def challenge_public(self, cid: str) -> dict:
        return self._challenge(cid).public()

    # ------------------------------------------------------------ study mode
    def _versions(self) -> dict:
        from .provenance import interpreter_version
        from .trials import code_commit
        c = getattr(self.session, "client", None)
        try:
            interp = self.session.spec.interpreter
        except RuntimeError:
            interp = None
        return {"code_commit": code_commit(), "server_version": os.environ.get("MIRROR_SERVER_VERSION") or "0.3",
                "prompt_revision": interpreter_version()["prompt_revision"],
                "model": None if self.mode == "fixture" or getattr(c, "offline", True) else getattr(c, "label", None),
                "interpreter": interp}

    def study_start(self, cfg: dict) -> dict:
        from .trials import StudyRecorder, code_commit
        if self.study is not None:
            self.study.close()
        store = os.environ.get("MIRROR_TRIAL_STORE") or None
        log = os.environ.get("MIRROR_STUDY_LOG") or None
        self.study = StudyRecorder(cfg, Path(store) if store else None, Path(log) if log else None)
        self.study.event("session_start", app_commit=code_commit(), task_packet_sha256=cfg.get("task_packet_sha256"),
                         consent={k: self.study.cfg["consent"][k] for k in ("text", "audio", "quote")})
        self._changed()
        return self.study.public()

    def study_task(self, task: Optional[str], task_origin: Optional[str] = None,
                   condition: Optional[str] = None) -> dict:
        if self.study is None:
            raise RuntimeError("study mode is not on")
        try:
            spec = self.session.spec
        except RuntimeError:
            spec = None
        self.study.set_task(task, task_origin, condition, self._spec_vid() if spec else None, spec,
                            getattr(self.session.scene, "scene_id", None))
        return self.study.public()

    def study_end(self, reason: str = "participant_done") -> dict:
        if self.study is None:
            raise RuntimeError("study mode is not on")
        self.study.event("task_end", reason=reason if reason in ("participant_done", "time_limit", "gave_up",
                                                                 "technical_problem") else "participant_done")
        out = self.study.public()
        self.study.close()
        self.study = None
        self._changed()
        return out

    def study_comprehension(self, trial_id: str, answer: str) -> dict:
        if self.study is None or trial_id not in self.study.attempts:
            raise KeyError(f"trial {trial_id}")
        if answer not in ("fresh", "recorded", "unsure"):
            raise ValueError("answer is fresh, recorded or unsure")
        rec = self.study.attempts[trial_id]
        truth = ((rec.get("execution") or {}).get("provenance") or (rec.get("proposal") or {}).get("provenance"))
        self.study.update(trial_id, comprehension={"answer": answer, "truth": truth})
        return self.study.attempts[trial_id]["comprehension"]

    def study_clarification(self, trial_id: str, answered: bool, useful: Optional[int] = None) -> dict:
        if self.study is None or trial_id not in self.study.attempts:
            raise KeyError(f"trial {trial_id}")
        if useful is not None and not 1 <= int(useful) <= 5:
            raise ValueError("useful is a rating from 1 to 5")
        rec = self.study.attempts[trial_id]
        cl = rec.get("clarification") or {"question": (rec.get("proposal") or {}).get("question")}
        cl.update(answered=bool(answered), useful=int(useful) if useful is not None else None)
        self.study.update(trial_id, clarification=cl)
        if answered:
            self.study.event("clarification_answered", chars=0)
        return cl

    def _study_scene(self, ch=None) -> dict:
        if ch is not None:
            o = ch.origin
            return {k: o.get(k) for k in ("family", "split", "seed", "edits", "scene_id", "content_sha256")}
        sc = self.session.scene
        return {"family": None, "split": getattr(sc, "split", None), "seed": getattr(sc, "seed", None), "edits": [],
                "scene_id": getattr(sc, "scene_id", None), "content_sha256": scene_sha256(sc) if sc else None}

    def _study_lineage(self, base_vid: Optional[str], revised_vid: Optional[str] = None) -> dict:
        g = self.session.generation
        return {"generation_id": g.id, "demo_id": g.demo_id, "demo_sha256": g.demo_sha256,
                "base_spec_version_id": base_vid, "revised_spec_version_id": revised_vid}

    # ------------------------------------------------------------ manual form
    def manual_edit(self, edits: list, note: str = "") -> dict:
        """Apply a manual structured edit (no model) as one new specification version."""
        from .manual import ManualEditError, apply_manual
        with self.lock:
            base = self.session.spec
            base_vid = self._spec_vid(base.version)
            tid = None
            if self.study is not None:
                rec = self.study.start_attempt(versions=self._versions(), scene=self._study_scene(),
                                               lineage=self._study_lineage(base_vid),
                                               input_={"kind": "manual_form", "edits": len(edits or [])},
                                               text=None, spec_before=base)
                tid = rec["trial_id"]
            try:
                new, record, errors = apply_manual(base, edits, note, fixture=self.mode == "fixture")
            except ManualEditError as exc:
                new, record, errors = None, None, [str(exc)]
            ok = new is not None and not errors
            if self.study is not None:
                self.study.event("manual_edit_saved", valid=ok, errors=len(errors))
            if not ok:
                if tid:
                    self.study.update(tid, proposal={"outcome": "error", "ops": [], "scope": None, "question": None,
                                                     "rejected": None, "validation": {"passed": False, "errors": errors},
                                                     "provenance": None, "fallback_reason": None, "latency_s": None,
                                                     "cost_usd": None})
                    self.study.finish(tid, failure_reason="invalid manual edit")
                raise ValueError("the manual edit is not a valid specification: " + "; ".join(errors))
            spec = self.session.commit_spec(new, base.version, record.text)
            vid = self._spec_vid(spec.version)
            from .challenge import spec_scope
            diff, scope = spec_scope(base, spec, self.session.scene)
            if tid:
                from .trials import semantic_state
                self.study.update(tid, proposal={"outcome": "apply", "ops": [o.model_dump(mode="json") for o in record.diff],
                                                 "scope": scope, "question": None, "rejected": None,
                                                 "validation": {"passed": True, "errors": []}, "provenance": None,
                                                 "fallback_reason": None, "latency_s": None, "cost_usd": None},
                                  review={"accepted": True}, lineage={"revised_spec_version_id": vid},
                                  spec_state_after=semantic_state(spec))
                self.study.event("spec_version_created", spec_version_id=vid, spec_state=semantic_state(spec),
                                 source="manual")
            self._changed()
            return {"spec_version_id": vid, "version": spec.version, "diff": diff, "scope": scope, "trial_id": tid,
                    "step_provenance": {"kind": "manual", "label": "Manual edit (no model)", "model": None,
                                        "interpreter": "manual-form", "request_status": "ok", "latency_s": None,
                                        "cost_usd": None, "fallback_reason": None}}

    # ------------------------------------------------------------ persistence
    def persist(self) -> None:
        """Write this session to ``state_dir`` (restart recovery). Never raises into a request."""
        if self.state_dir is None or self.closed:
            return
        try:
            with self.lock:
                d = self.state_dir
                d.mkdir(parents=True, exist_ok=True)
                self.session.save_state(d / "pipeline")
                state = {
                    "format": STATE_FORMAT, "saved_at": time.time(), "session_id": self.session_id, "mode": self.mode,
                    "revision": self.revision, "tags": self.tags, "rechecks": self.rechecks[-200:],
                    "model_log": self.model_log[-200:], "fallbacks": self.fallbacks[-50:],
                    "transcripts": self.transcripts, "video_demo": self.video_demo, "scene_seq": self.scene_seq,
                    "uploads": [{**e.public(), "path": e.path, "info": e.info} for e in self.media.entries.values()],
                    "challenges": [self.challenges[c].to_state() for c in self.challenge_order if c in self.challenges],
                    "evaluation": self.evaluation if (self.evaluation or {}).get("source", {}).get("kind") != "loaded" else None,
                    "jobs": [j.public() for j in sorted(self.jobs.values(), key=lambda j: j.started)[-30:]],
                }
                tmp = d / f"service.json.tmp{uuid.uuid4().hex[:6]}"
                tmp.write_text(json.dumps(state, default=str))
                tmp.replace(d / "service.json")
        except Exception as exc:  # noqa: BLE001 - persistence is best effort; the live state is authoritative
            self.fallbacks.append({"step": "persist", "used": f"state not saved: {type(exc).__name__}: {exc}"[:200]})

    def restore(self, state_dir: Path) -> None:
        """Load a session written by :meth:`persist` (after a server restart)."""
        from .challenge import ChallengeState
        state_dir = Path(state_dir)
        state = json.loads((state_dir / "service.json").read_text())
        if state.get("format") != STATE_FORMAT:
            raise ValueError("unknown session state format")
        if state.get("mode") != self.mode:
            raise ValueError(f"saved session ran on the {state.get('mode')} backend, this server is {self.mode}")
        cls = type(self.session)
        client = getattr(self.session, "client", None)
        with self.lock:
            self.session = cls.load_state(state_dir / "pipeline", client=client)
            if hasattr(self, "_fixture_meta"):
                self._fixture_meta.session = self.session
            self._seen_generation = self.session.generation.id
            self.revision = int(state.get("revision") or 0) + 1
            self.tags = dict(state.get("tags") or {})
            self.rechecks = list(state.get("rechecks") or [])
            self.model_log = list(state.get("model_log") or [])
            self.fallbacks = list(state.get("fallbacks") or [])
            self.transcripts = dict(state.get("transcripts") or {})
            self.video_demo = state.get("video_demo")
            self.scene_seq = int(state.get("scene_seq") or 0)
            self.evaluation = state.get("evaluation")
            for u in state.get("uploads") or []:
                if Path(u["path"]).exists() and (self.media.quota is None or self.media.quota.reserve(u["bytes"])):
                    self.media.entries[u["id"]] = UploadEntry(id=u["id"], path=u["path"], view=u["view"], bytes=u["bytes"],
                                                              sha256=u["sha256"], info=u.get("info") or {},
                                                              created=u.get("created") or time.time(),
                                                              used_by=list(u.get("used_by") or []))
            for c in state.get("challenges") or []:
                try:
                    ch = ChallengeState.from_state(c)
                except Exception:  # noqa: BLE001 - an unreadable challenge is dropped, not guessed
                    continue
                self.challenges[ch.id] = ch
                self.challenge_order.append(ch.id)
            for jd in state.get("jobs") or []:
                j = Job.interrupted(jd) if jd.get("status") in ("queued", "running") else None
                if j is None:
                    j = Job(jd.get("kind", "?"), jd.get("params") or {}, jd.get("generation_id"))
                    for k in ("id", "status", "progress", "message", "result", "error", "error_code", "started",
                              "finished", "stale"):
                        if k in jd:
                            setattr(j, k, jd[k])
                self.jobs[j.id] = j
            if self.session.demo is not None:
                try:
                    self.demo_rollout = self._make_demo_rollout()
                except Exception:  # noqa: BLE001 - playback only
                    pass
        self._changed()

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
        from .challenge import apply_scene_edit
        with self.lock:
            base = self.session.scene
            if base is None:
                raise RuntimeError("no scene loaded")
            sc = apply_scene_edit(base, edit)
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
            self.session.set_demo(trace, reason="teleop_recorded")
            self.demo_rollout = self._make_demo_rollout()
            self.video_demo = None
            self._changed()
            return self.snapshot()

    # ---------------------------------------------------------- output
    def rollout_payload(self, rollout_id: str) -> dict:
        """Playback payload. Ids are immutable, so a cached payload never goes stale;
        "demo" is an alias of the current generation's demonstration playback id."""
        if rollout_id == "demo":
            if self.demo_playback_id is None:
                raise KeyError("demo")
            rollout_id = self.demo_playback_id
        if rollout_id in self.payload_cache:
            return self.payload_cache[rollout_id]
        with self.lock:
            if rollout_id in self.eval_entries:
                e = self.eval_entries[rollout_id]
                payload = compact_rollout(self._eval_rollout(rollout_id), digits=self.payload_digits, extra={
                    "kind": "eval", "scene": e["scene"].model_dump(mode="json") if e["scene"] else None, "plan": None})
                self.payload_cache[rollout_id] = payload
                return payload
            if rollout_id in self.demo_rollouts:
                gen = next((g for g in self.session.all_generations() if g.demo_playback_id == rollout_id), None)
                extra = {"beads_hidden": self.mode == "live", "kind": "demonstration", "alias": "demo",
                         "generation_id": gen.id if gen else None,
                         "demo_sha256": gen.demo_sha256 if gen else None}
                payload = compact_rollout(self.demo_rollouts[rollout_id], extra=extra, digits=self.payload_digits)
                payload["id"] = rollout_id
            else:
                e, owner = self.session.find_rollout(rollout_id)
                payload = compact_rollout(e.rollout, digits=self.payload_digits, extra={
                    "kind": "rollout", "scene": e.scene.model_dump(mode="json"),
                    "plan": _plan_summary(e.plan), **e.lineage(),
                    "provenance": "fixture" if self.mode == "fixture" else "simulated"})
                if payload.get("visual") is None:
                    payload["visual"] = self.visual_for(e.scene)
            self.payload_cache[rollout_id] = payload
            if len(self.payload_cache) > 64:              # bounded memory; payloads are rebuilt on demand
                for k in list(self.payload_cache)[:len(self.payload_cache) - 64]:
                    if k != rollout_id:
                        self.payload_cache.pop(k, None)
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
                                 "has_frames": bool(e.rollout.frames), "trace": self.trace_cache[rid],
                                 **{k: v for k, v in e.lineage().items() if k != "rollout_id"},
                                 "provenance": "fixture" if self.mode == "fixture" else "simulated"})
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
                        "playback_id": self.demo_playback_id, "content_sha256": s.generation.demo_sha256,
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
                "generation": s.generation.public(),
                "archive": [a.public() for a in s.archive][-10:],
                "specs": [{**sp.model_dump(mode="json"), "version_id": s.spec_version_id(sp),
                           "content_sha256": sha256_of(sp), "generation_id": s.generation.id} for sp in s.specs],
                "rollouts": rollouts,
                "rechecks": [r for r in self.rechecks if r.get("generation_id", s.generation.id) == s.generation.id
                             or r["rollout_id"] in s.rollouts],
                "challenges": [self.challenges[c].public() for c in self.challenge_order[-5:] if c in self.challenges],
                "study": self.study.public() if self.study is not None else None,
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
                           "showcase_seeds": [SHOWCASE_SEEDS.start, SHOWCASE_SEEDS.stop - 1],
                           "uploads": {**self.upload_limits.public(), "held_mb": round(self.media.held_bytes() / 2**20, 2)},
                           "job_timeout_s": JOB_TIMEOUT_S},
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
