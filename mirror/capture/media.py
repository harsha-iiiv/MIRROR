"""Phone clip metadata, original frame timestamps and frame decoding.

Phones record variable frame rate video, so frame times are read from the
container (``best_effort_timestamp_time`` of every decoded frame) and decoded
frames are matched to them one to one (``-fps_mode passthrough``). Nothing
assumes a constant frame rate, and nothing is resampled.

``fingerprint`` keeps the capture settings that invalidate a calibration when
they change: resolution, rotation, device make/model and any focal-length or
lens metadata the phone writes.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Iterator, Optional

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

PROBE_TIMEOUT_S = 120
LENS_TAG_HINTS = ("focal", "lens", "zoom", "camera.lens", "camera.focal")
DEVICE_TAGS = {
    "make": ("com.apple.quicktime.make", "make", "manufacturer", "com.android.manufacturer"),
    "model": ("com.apple.quicktime.model", "model", "com.android.model"),
    "software": ("com.apple.quicktime.software", "software", "com.android.version"),
}


class ClipError(ValueError):
    pass


class CaptureFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    width: int                    # display width after rotation
    height: int
    rotation_deg: int = 0
    make: Optional[str] = None
    model: Optional[str] = None
    lens_tags: dict[str, str] = Field(default_factory=dict)

    def differences(self, other: "CaptureFingerprint") -> list[str]:
        out = []
        if (self.width, self.height) != (other.width, other.height):
            out.append(f"resolution changed {self.width}x{self.height} -> {other.width}x{other.height}")
        if self.rotation_deg != other.rotation_deg:
            out.append(f"rotation changed {self.rotation_deg} -> {other.rotation_deg}")
        for k in ("make", "model"):
            a, b = getattr(self, k), getattr(other, k)
            if a and b and a != b:
                out.append(f"device {k} changed {a!r} -> {b!r}")
        for k in sorted(set(self.lens_tags) & set(other.lens_tags)):
            if self.lens_tags[k] != other.lens_tags[k]:
                out.append(f"lens metadata {k} changed {self.lens_tags[k]!r} -> {other.lens_tags[k]!r} (zoom or lens switch)")
        return out


class ClipMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    sha256: str
    codec: Optional[str] = None
    width: int
    height: int
    rotation_deg: int = 0
    nominal_fps: Optional[float] = None
    duration_s: Optional[float] = None
    n_frames: int
    pts_s: list[float]
    variable_frame_rate: bool
    has_audio: bool = False
    creation_time: Optional[str] = None
    fingerprint: CaptureFingerprint
    tags: dict[str, str] = Field(default_factory=dict)

    def frame_at(self, t: float) -> int:
        """Index of the frame whose timestamp is nearest to ``t``."""
        a = np.asarray(self.pts_s)
        return int(np.argmin(np.abs(a - t)))


def _run(cmd, timeout=PROBE_TIMEOUT_S) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise ClipError(f"{Path(cmd[0]).name} failed: {r.stderr.strip()[:300]}")
    return r.stdout


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _rotation(stream: dict) -> int:
    rot = 0
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(round(float(sd["rotation"])))
    if "rotate" in (stream.get("tags") or {}):
        rot = int(stream["tags"]["rotate"])
    return rot % 360


def probe(path) -> ClipMeta:
    """Probe a clip; raises ClipError if it cannot be decoded or has no frames."""
    path = Path(path)
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise ClipError("ffprobe is required")
    info = json.loads(_run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]))
    vids = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    if not vids:
        raise ClipError("no video stream")
    v = vids[0]
    out = _run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                "frame=best_effort_timestamp_time,pts_time", "-of", "csv=p=0", str(path)], timeout=600)
    pts = []
    for line in out.splitlines():
        vals = [x for x in line.strip().split(",") if x not in ("", "N/A")]
        if vals:
            pts.append(float(vals[-1]))
    if not pts:
        raise ClipError("no decodable frames")
    if any(b <= a for a, b in zip(pts, pts[1:])):
        raise ClipError("frame timestamps are not strictly increasing")
    rot = _rotation(v)
    w, h = int(v["width"]), int(v["height"])
    if rot in (90, 270):
        w, h = h, w
    tags = {}
    for src in (info.get("format", {}).get("tags") or {}, v.get("tags") or {}):
        tags.update({str(k): str(val) for k, val in src.items()})
    low = {k.lower(): val for k, val in tags.items()}
    dev = {}
    for key, names in DEVICE_TAGS.items():
        for n in names:
            if n in low:
                dev[key] = low[n]
                break
    lens = {k: val for k, val in low.items() if any(hint in k for hint in LENS_TAG_HINTS)}
    fr = v.get("avg_frame_rate", "0/1")
    try:
        num, den = fr.split("/")
        nominal = float(num) / float(den) if float(den) else None
    except Exception:
        nominal = None
    d = np.diff(pts)
    vfr = bool(len(d) > 2 and (d.max() - d.min()) > 0.25 * np.median(d))
    dur = info.get("format", {}).get("duration")
    return ClipMeta(
        path=str(path), sha256=sha256_file(path), codec=v.get("codec_name"), width=w, height=h, rotation_deg=rot,
        nominal_fps=nominal, duration_s=float(dur) if dur else None, n_frames=len(pts), pts_s=pts,
        variable_frame_rate=vfr, has_audio=any(s.get("codec_type") == "audio" for s in info.get("streams", [])),
        creation_time=low.get("creation_time") or low.get("com.apple.quicktime.creationdate"),
        fingerprint=CaptureFingerprint(width=w, height=h, rotation_deg=rot, make=dev.get("make"),
                                       model=dev.get("model"), lens_tags=lens),
        tags=tags)


def iter_frames(meta: ClipMeta, *, start: int = 0, stop: Optional[int] = None, step: int = 1,
                gray: bool = False) -> Iterator[tuple[int, float, np.ndarray]]:
    """Yield (index, pts_s, frame) for frames start..stop-1 (every ``step``).
    Frames are decoded one at a time (never all held in memory) at their
    display orientation; indices match ``meta.pts_s``."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise ClipError("ffmpeg is required")
    pix, ch = ("gray", 1) if gray else ("rgb24", 3)
    nbytes = meta.width * meta.height * ch
    cmd = [ffmpeg, "-v", "error", "-i", meta.path, "-map", "0:v:0", "-fps_mode", "passthrough",
           "-f", "rawvideo", "-pix_fmt", pix, "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    try:
        i = 0
        stop = meta.n_frames if stop is None else min(stop, meta.n_frames)
        while i < stop:
            buf = proc.stdout.read(nbytes)
            if len(buf) < nbytes:
                break
            if i >= start and (i - start) % step == 0:
                f = np.frombuffer(buf, np.uint8).reshape(meta.height, meta.width, *(() if ch == 1 else (3,)))
                yield i, meta.pts_s[i], f
            i += 1
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait(timeout=30)


def read_frame(meta: ClipMeta, index: int, gray: bool = False) -> np.ndarray:
    for _, _, f in iter_frames(meta, start=index, stop=index + 1, gray=gray):
        return f.copy()
    raise ClipError(f"frame {index} not decodable")


def audio_envelope(path, rate: int = 4000) -> Optional[tuple[np.ndarray, int]]:
    """Mono audio as float32 at ``rate`` Hz on the CONTAINER timeline (sample 0
    = time 0), or None if the clip has no audio."""
    ffmpeg = shutil.which("ffmpeg")
    cmd = [ffmpeg, "-v", "error", "-i", str(path), "-map", "0:a:0", "-af", "aresample=async=1:first_pts=0",
           "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=PROBE_TIMEOUT_S, stdin=subprocess.DEVNULL)
    if r.returncode != 0 or not r.stdout:
        return None
    return np.frombuffer(r.stdout, np.float32), rate
