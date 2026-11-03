"""Video upload admission: probing, decode validation, bounds and media lifetime.

An upload is accepted only after ffprobe reads it as a video with known,
bounded duration, dimensions, frame rate and frame count, *and* ffmpeg
decodes every video frame without an error inside a time and memory limit.
Anything else is deleted at once. Bytes are bounded per file, per session
and across the server. Environment variables (defaults in brackets):

  MIRROR_UPLOAD_MAX_MB [40]            one file
  MIRROR_UPLOAD_SESSION_MB [120]       all media one session holds
  MIRROR_UPLOAD_GLOBAL_MB [1024]       all uploaded media on the server
  MIRROR_UPLOAD_SESSION_FILES [6]      files one session holds
  MIRROR_UPLOAD_MAX_S [90]             duration
  MIRROR_UPLOAD_MAX_SIDE [1920]        width and height
  MIRROR_UPLOAD_MAX_PIXELS [2073600]   width x height (1080p)
  MIRROR_UPLOAD_MAX_FPS [60]
  MIRROR_UPLOAD_MAX_FRAMES [5400]
  MIRROR_UPLOAD_DECODE_S [60]          wall time for the decode check
  MIRROR_UPLOAD_PROBE_S [15]           wall time for ffprobe
  MIRROR_UPLOAD_MEM_MB [1536]          address-space limit of ffprobe/ffmpeg
  MIRROR_UPLOAD_TTL_S [1800]           an upload not used by a demonstration expires
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

ALLOWED_CODECS = ("h264", "hevc", "vp8", "vp9", "av1", "mpeg4")
ALLOWED_FORMATS = ("mov", "mp4", "m4a", "3gp", "3g2", "mj2", "matroska", "webm")


def _env_num(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


class UploadRejected(ValueError):
    """The upload was refused; ``code`` is the API error code, ``status`` the HTTP status."""

    def __init__(self, detail: str, code: str = "invalid_video", status: int = 422):
        super().__init__(detail)
        self.detail, self.code, self.status = detail, code, status


@dataclass
class UploadLimits:
    max_file_bytes: int = 40 * 2**20
    max_session_bytes: int = 120 * 2**20
    max_global_bytes: int = 1024 * 2**20
    max_session_files: int = 6
    min_duration_s: float = 0.5
    max_duration_s: float = 90.0
    min_side: int = 64
    max_side: int = 1920
    max_pixels: int = 1920 * 1080
    max_fps: float = 60.0
    max_frames: int = 5400
    probe_timeout_s: float = 15.0
    decode_timeout_s: float = 60.0
    mem_bytes: int = 1536 * 2**20
    ttl_s: float = 1800.0

    @classmethod
    def from_env(cls) -> "UploadLimits":
        mb = 2**20
        return cls(max_file_bytes=int(_env_num("MIRROR_UPLOAD_MAX_MB", 40) * mb),
                   max_session_bytes=int(_env_num("MIRROR_UPLOAD_SESSION_MB", 120) * mb),
                   max_global_bytes=int(_env_num("MIRROR_UPLOAD_GLOBAL_MB", 1024) * mb),
                   max_session_files=int(_env_num("MIRROR_UPLOAD_SESSION_FILES", 6)),
                   max_duration_s=_env_num("MIRROR_UPLOAD_MAX_S", 90.0),
                   max_side=int(_env_num("MIRROR_UPLOAD_MAX_SIDE", 1920)),
                   max_pixels=int(_env_num("MIRROR_UPLOAD_MAX_PIXELS", 1920 * 1080)),
                   max_fps=_env_num("MIRROR_UPLOAD_MAX_FPS", 60.0),
                   max_frames=int(_env_num("MIRROR_UPLOAD_MAX_FRAMES", 5400)),
                   probe_timeout_s=_env_num("MIRROR_UPLOAD_PROBE_S", 15.0),
                   decode_timeout_s=_env_num("MIRROR_UPLOAD_DECODE_S", 60.0),
                   mem_bytes=int(_env_num("MIRROR_UPLOAD_MEM_MB", 1536) * mb),
                   ttl_s=_env_num("MIRROR_UPLOAD_TTL_S", 1800.0))

    @classmethod
    def capture_from_env(cls) -> "UploadLimits":
        """Phone-capture clips (original phone files: larger, longer, up to 4K). MIRROR_CAPTURE_UPLOAD_*."""
        mb = 2**20
        base = cls.from_env()
        return cls(max_file_bytes=int(_env_num("MIRROR_CAPTURE_UPLOAD_MAX_MB", 300) * mb),
                   max_session_bytes=int(_env_num("MIRROR_CAPTURE_UPLOAD_SESSION_MB", 900) * mb),
                   max_global_bytes=base.max_global_bytes,
                   max_session_files=int(_env_num("MIRROR_CAPTURE_UPLOAD_SESSION_FILES", 6)),
                   max_duration_s=_env_num("MIRROR_CAPTURE_UPLOAD_MAX_S", 120.0),
                   max_side=int(_env_num("MIRROR_CAPTURE_UPLOAD_MAX_SIDE", 3840)),
                   max_pixels=int(_env_num("MIRROR_CAPTURE_UPLOAD_MAX_PIXELS", 3840 * 2160)),
                   max_fps=_env_num("MIRROR_CAPTURE_UPLOAD_MAX_FPS", 60.0),
                   max_frames=int(_env_num("MIRROR_CAPTURE_UPLOAD_MAX_FRAMES", 7200)),
                   probe_timeout_s=base.probe_timeout_s,
                   decode_timeout_s=_env_num("MIRROR_CAPTURE_UPLOAD_DECODE_S", 300.0),
                   mem_bytes=int(_env_num("MIRROR_CAPTURE_UPLOAD_MEM_MB", 3072) * mb),
                   ttl_s=_env_num("MIRROR_CAPTURE_UPLOAD_TTL_S", 7200.0))

    def public(self) -> dict:
        mb = 2**20
        return {"max_file_mb": round(self.max_file_bytes / mb, 1), "max_session_mb": round(self.max_session_bytes / mb, 1),
                "max_session_files": self.max_session_files, "duration_s": [self.min_duration_s, self.max_duration_s],
                "max_side": self.max_side, "max_pixels": self.max_pixels, "max_fps": self.max_fps,
                "max_frames": self.max_frames, "codecs": list(ALLOWED_CODECS), "expires_unused_after_s": self.ttl_s}


def tools_available() -> bool:
    return bool(shutil.which("ffprobe") and shutil.which("ffmpeg"))


def _limit_resources(mem_bytes: int, cpu_s: int) -> Callable[[], None]:
    def apply():
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 2))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        except (ImportError, ValueError, OSError):
            pass
        os.setsid()                                 # own process group: a timeout kills every child
    return apply


def _run(cmd: list[str], timeout: float, limits: UploadLimits) -> subprocess.CompletedProcess:
    """Run a media tool with a wall-time, CPU and memory bound; kill its whole group on timeout."""
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, preexec_fn=_limit_resources(limits.mem_bytes, int(timeout) + 5))
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, 9)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.communicate()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _rate(s: Optional[str]) -> Optional[float]:
    num, _, den = (s or "").partition("/")
    try:
        n, d = float(num), float(den or 1)
    except ValueError:
        return None
    if not d or not math.isfinite(n / d) or n / d <= 0:
        return None
    return n / d


def probe_video(path: Path, limits: UploadLimits) -> dict:
    """ffprobe facts about the first video stream; raises UploadRejected unless all are known and in bounds."""
    if not tools_available():
        raise UploadRejected("video uploads need ffprobe and ffmpeg on the server; they are not installed",
                             code="uploads_unavailable", status=503)
    cmd = [shutil.which("ffprobe"), "-v", "error", "-probesize", "32M", "-analyzeduration", "20M",
           "-show_entries", "stream=index,codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,"
           "nb_frames,duration:format=format_name,duration", "-of", "json", str(path)]
    try:
        r = _run(cmd, limits.probe_timeout_s, limits)
    except subprocess.TimeoutExpired:
        raise UploadRejected(f"reading the file took longer than {limits.probe_timeout_s:.0f} s")
    if r.returncode != 0:
        why = (r.stderr or "").strip().splitlines()
        raise UploadRejected("the file could not be read as a video" + (f" ({why[-1][:160]})" if why else ""))
    try:
        d = json.loads(r.stdout or "{}")
    except ValueError:
        raise UploadRejected("the file could not be read as a video (unreadable probe output)")
    fmt = d.get("format") or {}
    names = set((fmt.get("format_name") or "").split(","))
    if not names & set(ALLOWED_FORMATS):
        raise UploadRejected(f"unsupported container {fmt.get('format_name') or 'unknown'}; use MP4/MOV or WebM")
    vids = [s for s in d.get("streams") or [] if s.get("codec_type") == "video"]
    if not vids:
        raise UploadRejected("the file has no video stream")
    st = vids[0]
    codec = st.get("codec_name") or "unknown"
    if codec not in ALLOWED_CODECS:
        raise UploadRejected(f"unsupported video codec {codec}; use H.264, HEVC, VP8/VP9, AV1 or MPEG-4")
    w, h = st.get("width"), st.get("height")
    if not isinstance(w, int) or not isinstance(h, int) or w <= 0 or h <= 0:
        raise UploadRejected("the video's dimensions are unknown")
    if min(w, h) < limits.min_side or max(w, h) > limits.max_side or w * h > limits.max_pixels:
        raise UploadRejected(f"video is {w}x{h}; allowed: sides {limits.min_side}-{limits.max_side} px "
                             f"and at most {limits.max_pixels} pixels per frame")
    dur = None
    for raw in (st.get("duration"), fmt.get("duration")):
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(v) and v > 0:
            dur = v
            break
    if dur is None:
        raise UploadRejected("the video's duration is unknown")
    if dur < limits.min_duration_s or dur > limits.max_duration_s:
        raise UploadRejected(f"video is {dur:.1f} s long; allowed {limits.min_duration_s:g}-{limits.max_duration_s:g} s")
    fps = _rate(st.get("avg_frame_rate")) or _rate(st.get("r_frame_rate"))
    if fps is None:
        raise UploadRejected("the video's frame rate is unknown")
    if fps > limits.max_fps + 1e-6 or fps < 1.0:
        raise UploadRejected(f"frame rate {fps:.1f} fps; allowed 1-{limits.max_fps:g} fps")
    try:
        frames = int(st.get("nb_frames"))
    except (TypeError, ValueError):
        frames = int(round(dur * fps))
    if frames <= 0 or frames > limits.max_frames:
        raise UploadRejected(f"video has about {frames} frames; at most {limits.max_frames} are accepted")
    return {"duration": round(dur, 3), "width": w, "height": h, "fps": round(fps, 3), "frames": frames,
            "codec": codec, "container": fmt.get("format_name")}


def decode_check(path: Path, info: dict, limits: UploadLimits) -> int:
    """Decode every frame of the first video stream; returns the decoded frame count."""
    cmd = [shutil.which("ffmpeg"), "-nostdin", "-v", "error", "-xerror", "-threads", "1", "-i", str(path),
           "-map", "0:v:0", "-an", "-sn", "-dn", "-f", "null", "-progress", "pipe:1", "-nostats", "-"]
    try:
        r = _run(cmd, limits.decode_timeout_s, limits)
    except subprocess.TimeoutExpired:
        raise UploadRejected(f"decoding the video took longer than {limits.decode_timeout_s:.0f} s")
    decoded = 0
    for line in (r.stdout or "").splitlines():
        if line.startswith("frame="):
            try:
                decoded = int(line.split("=", 1)[1])
            except ValueError:
                pass
    errs = [x for x in (r.stderr or "").strip().splitlines() if x.strip()]
    if r.returncode != 0 or errs:
        raise UploadRejected("the video does not decode cleanly" + (f" ({errs[-1][:160]})" if errs else ""))
    if decoded <= 0:
        raise UploadRejected("no video frame could be decoded")
    if decoded > limits.max_frames:
        raise UploadRejected(f"video decodes to {decoded} frames; at most {limits.max_frames} are accepted")
    expected = info.get("frames") or 0
    if expected and decoded < 0.5 * expected:
        raise UploadRejected(f"only {decoded} of about {expected} frames decode; the file looks truncated")
    return decoded


def validate_video(path: Path, limits: UploadLimits) -> dict:
    info = probe_video(path, limits)
    info["decoded_frames"] = decode_check(path, info, limits)
    return info


class MediaQuota:
    """Bytes of uploaded media held across the whole server (all sessions)."""

    def __init__(self, max_bytes: int):
        self.max_bytes = max_bytes
        self.used = 0
        self._lock = threading.Lock()

    def reserve(self, n: int) -> bool:
        with self._lock:
            if self.used + n > self.max_bytes:
                return False
            self.used += n
            return True

    def release(self, n: int) -> None:
        with self._lock:
            self.used = max(0, self.used - n)

    def stats(self) -> dict:
        with self._lock:
            return {"used_mb": round(self.used / 2**20, 2), "max_mb": round(self.max_bytes / 2**20, 1)}


@dataclass
class UploadEntry:
    id: str
    path: str
    view: str
    bytes: int
    sha256: str
    info: dict
    created: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    in_use: int = 0                      # jobs reading the file right now
    used_by: list = field(default_factory=list)   # generation ids built from this file

    def public(self) -> dict:
        return {"id": self.id, "view": self.view, "bytes": self.bytes, "sha256": self.sha256, **self.info,
                "created": self.created, "used_by": list(self.used_by)}


class SessionMedia:
    """Uploads held by one session, with expiry and safe deletion."""

    def __init__(self, limits: UploadLimits, quota: Optional[MediaQuota] = None,
                 clock: Callable[[], float] = time.time):
        self.limits = limits
        self.quota = quota
        self.clock = clock
        self.entries: dict[str, UploadEntry] = {}
        self._lock = threading.RLock()

    def held_bytes(self) -> int:
        return sum(e.bytes for e in self.entries.values())

    def room(self) -> int:
        """Largest next upload this session may send (before the global check)."""
        with self._lock:
            if len(self.entries) >= self.limits.max_session_files:
                return 0
            return max(0, min(self.limits.max_file_bytes, self.limits.max_session_bytes - self.held_bytes()))

    def admit(self, nbytes: int, limits: Optional["UploadLimits"] = None) -> None:
        """Reserve room for a file of ``nbytes`` (call before writing it) or raise UploadRejected.
        ``limits`` overrides the per-file/session limits (phone-capture clips); the global quota is shared."""
        lim = limits or self.limits
        with self._lock:
            self.sweep()
            if nbytes > lim.max_file_bytes:
                raise UploadRejected(f"upload larger than {lim.max_file_bytes // 2**20} MB",
                                     code="too_large", status=413)
            if len(self.entries) >= max(lim.max_session_files, self.limits.max_session_files):
                raise UploadRejected(f"this session already holds {len(self.entries)} videos (the limit); "
                                     "start again with a new demonstration", code="too_large", status=413)
            if self.held_bytes() + nbytes > lim.max_session_bytes:
                raise UploadRejected(f"this session's media would exceed {lim.max_session_bytes // 2**20} MB",
                                     code="too_large", status=413)
            if self.quota is not None and not self.quota.reserve(nbytes):
                raise UploadRejected("the server's upload storage is full; try again later or use the recordings",
                                     code="storage_full", status=507)

    def unadmit(self, nbytes: int) -> None:
        if self.quota is not None:
            self.quota.release(nbytes)

    def add(self, entry: UploadEntry) -> UploadEntry:
        """Register a validated upload whose bytes were admitted. An unused earlier
        upload for the same view is replaced (deleted)."""
        with self._lock:
            for old in [e for e in self.entries.values() if e.view == entry.view and not e.used_by and not e.in_use]:
                self._delete(old.id, "replaced by a newer upload")
            self.entries[entry.id] = entry
            return entry

    def get(self, vid: str) -> UploadEntry:
        with self._lock:
            self.sweep()
            return self.entries[vid]

    def acquire(self, vid: str) -> UploadEntry:
        with self._lock:
            e = self.get(vid)
            e.in_use += 1
            e.last_used = self.clock()
            return e

    def release(self, vid: str, generation_id: Optional[str] = None) -> None:
        with self._lock:
            e = self.entries.get(vid)
            if e is None:
                return
            e.in_use = max(0, e.in_use - 1)
            e.last_used = self.clock()
            if generation_id and generation_id not in e.used_by:
                e.used_by.append(generation_id)

    def _delete(self, vid: str, why: str = "") -> None:
        e = self.entries.pop(vid, None)
        if e is None:
            return
        Path(e.path).unlink(missing_ok=True)
        self.unadmit(e.bytes)

    def forget_generations(self, keep: set) -> None:
        """Drop references to generations that no longer exist (so their media can expire)."""
        with self._lock:
            for e in self.entries.values():
                e.used_by = [g for g in e.used_by if g in keep]

    def sweep(self) -> list[str]:
        """Delete uploads not used by a kept generation and idle longer than the TTL."""
        now = self.clock()
        gone = []
        with self._lock:
            for e in list(self.entries.values()):
                if not e.in_use and not e.used_by and now - e.last_used > self.limits.ttl_s:
                    self._delete(e.id, "expired")
                    gone.append(e.id)
        return gone

    def clear(self) -> None:
        with self._lock:
            for vid in list(self.entries):
                self._delete(vid, "session closed")
