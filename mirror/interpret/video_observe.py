"""Watch a demonstration video with a Muse model and report what it saw.

``observe(video, model)`` uploads the clip through the Files API and asks a
Muse model (muse-spark-1.3 or muse-wonder-1.0) for structured observations:
which objects matter, grasp / release / carry events with times, cues of care
with evidence timestamps, and what it is unsure about. Output is constrained by
a strict JSON schema. Code then checks the observations against the clip
(times inside the video, known object kinds, start <= end) and lists every
problem in ``issues``; nothing is silently repaired.

Observations are evidence from a model watching pixels. They can be wrong and
are never used to grade a run. ``propose_spec(..., observations=...)`` adds
them to the proposal prompt as secondary evidence.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field

from mirror.interpret.llm import strict_schema

KINDS = ("cup", "block", "marker", "tray", "wall", "gripper", "other")
MODELS = ("muse-spark-1.3", "muse-wonder-1.0")
ObjectKind = Literal["cup", "block", "marker", "tray", "wall", "gripper", "other"]


class ObservedObject(BaseModel):
    kind: ObjectKind = Field(description="closest kind from the vocabulary")
    description: str = Field(description="what it looks like in the video, e.g. 'small white open box'")
    relevant: bool = Field(description="true if the task acts on it or it shapes the motion")
    reason: str


class ObservedEvent(BaseModel):
    type: Literal["grasp", "release", "carry"]
    object: ObjectKind
    t_start: float = Field(description="seconds from the start of the video")
    t_end: float = Field(description="seconds from the start of the video; equal to t_start for an instant")
    evidence: str = Field(description="what in the video shows it")


class CareCue(BaseModel):
    object: ObjectKind
    cue: str = Field(description="the careful behaviour, e.g. 'kept level while passing the wall'")
    t_start: float
    t_end: float
    evidence: str


class ObservationUncertainty(BaseModel):
    about: str
    question: str


class ObservationDraft(BaseModel):
    """What the model returns (strict schema)."""
    summary: str = Field(description="one sentence: what the person or robot does")
    objects: list[ObservedObject]
    events: list[ObservedEvent]
    care_cues: list[CareCue]
    uncertainties: list[ObservationUncertainty]


class VideoObservations(ObservationDraft):
    """Model observations plus provenance and code checks."""
    model: str
    requested_model: str
    strategy: str = "video"          # video | slow | frames
    strategy_params: dict = Field(default_factory=dict)
    video_sha256: str
    video_name: str
    duration_s: Optional[float] = None
    latency_s: float = 0.0
    from_cassette: bool = False
    usage: dict = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)

    def prompt_view(self) -> dict:
        """The part shown to the proposal model (no provenance noise)."""
        return {"source": f"{self.model} watching the demonstration video (may be wrong)",
                **ObservationDraft.model_validate(self.model_dump()).model_dump()}


OBSERVE_SYSTEM = """\
You watch a video of a tabletop demonstration: objects are packed into a
destination tray. Report only what the video shows.

Vocabulary of object kinds: cup (open-top container), block, marker (standing
cylinder), tray (destination), wall (obstacle), gripper (the hand or robot
gripper), other.

Report:
- objects: every distinct object, its kind, a short visual description, whether
  it matters to the task and why.
- events: grasp, release and carry of each object, with times in seconds from
  the start of the video. A carry spans from grasp to release.
- care_cues: behaviour that suggests a requirement, such as keeping an object
  level, slowing down, or keeping distance from something, with the times where
  the video shows it and what shows it. Report none if you see none.
- uncertainties: what the video does not settle (for example why an object was
  carried a certain way).
Do not guess numbers you cannot see; describe evidence instead."""


def video_duration(path: Union[str, Path]) -> Optional[float]:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                              "default=nw=1:nk=1", str(path)], capture_output=True, text=True, timeout=30,
                             stdin=subprocess.DEVNULL)
        return float(out.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def check_observations(d: ObservationDraft, duration_s: Optional[float]) -> list[str]:
    issues = []
    timed = [("event", e.type, e.object, e.t_start, e.t_end) for e in d.events] + \
            [("care cue", "cue", c.object, c.t_start, c.t_end) for c in d.care_cues]
    for what, typ, obj, a, b in timed:
        if a > b:
            issues.append(f"{what} {typ} {obj}: t_start {a} after t_end {b}")
        if a < 0 or (duration_s is not None and b > duration_s + 0.5):
            issues.append(f"{what} {typ} {obj}: time {a}-{b} s outside the video (0-{duration_s} s)")
    for o in {e.object for e in d.events}:
        if o not in {x.kind for x in d.objects}:
            issues.append(f"event on {o}, which is not in the object list")
    return issues


STRATEGIES = ("video", "slow", "frames")
# Chosen per model from the temporal-resolution comparison on the clean reference
# render (82 s, 960x540): the normal video gave the lowest grasp/release timing
# error for both models and is the cheapest; slowing it 4x or sending 1 s stills
# did not help. observe(strategy=...) overrides.
DEFAULT_STRATEGY = {"muse-spark-1.3": "video", "muse-wonder-1.0": "video"}

FRAMES_NOTE = """

The input is a sequence of still frames from the video, in time order, each
preceded by its time in seconds. Use those times for events and cues."""


def frame_rate(path: Union[str, Path]) -> Optional[Fraction]:
    """The video stream's frame rate as an exact fraction (ffprobe r_frame_rate)."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=r_frame_rate", "-of", "default=nw=1:nk=1", str(path)],
                             capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        r = Fraction(out.stdout.strip().splitlines()[0])
        return r if r > 0 else None
    except (OSError, ValueError, IndexError, ZeroDivisionError, subprocess.TimeoutExpired):
        return None


def _slowed(path: Path, factor: float) -> bytes:
    """The same video played ``factor`` times slower: every frame kept exactly
    once, each shown ``factor`` times longer, no audio.

    The output frame rate is set explicitly (input rate / factor). Without it the
    result depends on the ffmpeg build: 6.x keeps the input rate and duplicates
    frames (a 2.0 s clip slowed 4x came out 7.75 s), 7.x writes variable rate and
    drops the last frame's duration (7.0 s)."""
    import tempfile
    rate = frame_rate(path)
    if rate is None:
        raise RuntimeError(f"could not read the frame rate of {path}")
    out_rate = rate / Fraction(factor).limit_denominator(1000)
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "slow.mp4"
        p = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(path), "-vf", f"setpts={factor}*PTS",
                            "-r", f"{out_rate.numerator}/{out_rate.denominator}",
                            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                            "-movflags", "+faststart", str(out)], capture_output=True, timeout=600,
                           stdin=subprocess.DEVNULL)
        if p.returncode != 0:
            raise RuntimeError("ffmpeg could not slow the video: " + p.stderr.decode(errors="replace")[:300])
        return out.read_bytes()


def sample_frames(path: Path, step_s: float, *, max_frames: int = 96, width: int = 480) -> list[tuple[float, bytes]]:
    """JPEG frames every ``step_s`` seconds (exact frame times, from the video's frame rate)."""
    import io
    import imageio.v2 as imageio
    from PIL import Image
    reader = imageio.get_reader(str(path))
    try:
        fps = float(reader.get_meta_data()["fps"])
        n = reader.count_frames()
        want = []
        t = 0.0
        while t * fps < n and len(want) < max_frames:
            want.append(int(round(t * fps)))
            t += step_s
        out = []
        for idx in want:
            img = Image.fromarray(reader.get_data(min(idx, n - 1)))
            if width and img.width > width:
                img = img.resize((width, round(img.height * width / img.width)))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            out.append((idx / fps, buf.getvalue()))
        return out
    finally:
        reader.close()


def _rescale(d: ObservationDraft, factor: float) -> ObservationDraft:
    d = d.model_copy(deep=True)
    for e in d.events:
        e.t_start, e.t_end = e.t_start / factor, e.t_end / factor
    for c in d.care_cues:
        c.t_start, c.t_end = c.t_start / factor, c.t_end / factor
    return d


def observe(video: Union[str, Path], model: str = "muse-spark-1.3", client=None, *,
            strategy: Optional[str] = None, duration_s: Optional[float] = None,
            slow_factor: float = 4.0, frame_step_s: float = 1.0, max_frames: int = 96, frame_width: int = 480,
            reasoning_effort: Optional[str] = None, run: Optional[int] = None) -> VideoObservations:
    """Watch ``video`` with ``model`` and return its observations.

    strategy: "video" (upload the clip), "slow" (upload it slowed ``slow_factor``
    times; reported times are divided back) or "frames" (send JPEG stills every
    ``frame_step_s`` seconds, each labelled with its time). Default per model:
    DEFAULT_STRATEGY. ``run`` > 1 labels a repeated measurement (it changes the
    request metadata, so it is recorded separately)."""
    from mirror.meta import MetaClient, file_part, image_part, text_part
    client = client or MetaClient.from_env()
    strategy = strategy or DEFAULT_STRATEGY.get(model, "video")
    if strategy not in STRATEGIES:
        raise ValueError(f"strategy must be one of {STRATEGIES}")
    path = Path(video)
    data = path.read_bytes()
    duration_s = duration_s if duration_s is not None else video_duration(path)
    meta = {"run": str(run)} if run and run > 1 else None
    instructions = OBSERVE_SYSTEM
    params: dict = {}
    if strategy == "video":
        fid = client.upload_file(path, mime="video/mp4")
        ask = "Describe this demonstration video."
        if duration_s is not None:
            ask += f" The video is {duration_s:.2f} seconds long."
        content = [text_part(ask), file_part(fid)]
    elif strategy == "slow":
        slow = _slowed(path, slow_factor)
        fid = client.upload_file(slow, filename=path.stem + f"_x{slow_factor:g}.mp4", mime="video/mp4")
        ask = "Describe this demonstration video."
        if duration_s is not None:
            ask += f" The video is {duration_s * slow_factor:.2f} seconds long."
        content = [text_part(ask), file_part(fid)]
        params = {"slow_factor": slow_factor}
    else:
        frames = sample_frames(path, frame_step_s, max_frames=max_frames, width=frame_width)
        ask = "Describe this demonstration from its frames."
        if duration_s is not None:
            ask += f" The video is {duration_s:.2f} seconds long."
        content = [text_part(ask)]
        for t, jpg in frames:
            content += [text_part(f"t = {t:.2f} s"), image_part(jpg, "image/jpeg")]
        instructions = OBSERVE_SYSTEM + FRAMES_NOTE
        params = {"frame_step_s": frame_step_s, "n_frames": len(frames), "frame_width": frame_width}
    res = client.responses(model, content, schema=strict_schema(ObservationDraft),
                           schema_name="video_observations", instructions=instructions,
                           reasoning_effort=reasoning_effort, metadata=meta, name="observe_video")
    draft = ObservationDraft.model_validate(res.json())
    if strategy == "slow":
        draft = _rescale(draft, slow_factor)
    if run and run > 1:
        params["run"] = run
    return VideoObservations(**draft.model_dump(), model=res.model, requested_model=model,
                             strategy=strategy, strategy_params=params,
                             video_sha256=hashlib.sha256(data).hexdigest(), video_name=path.name,
                             duration_s=duration_s, latency_s=res.latency_s, from_cassette=res.from_cassette,
                             usage=res.usage, issues=check_observations(draft, duration_s))


def event_timing_errors(obs: "VideoObservations", gt_events: list[dict], kind_of: dict[str, str]) -> dict:
    """Compare observed grasp/release times with ground truth.

    gt_events: [{"t", "type": grasp|release, "object_id"}]; kind_of: object id ->
    kind. Each ground-truth event is matched to the observed event of the same
    type and object kind (the k-th of that kind in time order). Returns per-event
    errors (observed t_start - true t) and summary statistics."""
    rows = []
    for typ in ("grasp", "release"):
        for kind in sorted({kind_of[e["object_id"]] for e in gt_events if e["type"] == typ}):
            truth = sorted(e["t"] for e in gt_events if e["type"] == typ and kind_of[e["object_id"]] == kind)
            seen = sorted(e.t_start for e in obs.events if e.type == typ and e.object == kind)
            for k, t in enumerate(truth):
                rows.append({"type": typ, "kind": kind, "true_t": round(t, 3),
                             "observed_t": round(seen[k], 3) if k < len(seen) else None,
                             "error_s": round(seen[k] - t, 3) if k < len(seen) else None})
    errs = [abs(r["error_s"]) for r in rows if r["error_s"] is not None]
    true_order = [(e["type"], kind_of[e["object_id"]]) for e in sorted(gt_events, key=lambda e: e["t"])]
    seen_order = [(e.type, e.object) for e in sorted(obs.events, key=lambda e: e.t_start)
                  if e.type in ("grasp", "release")]
    return {"events": rows, "matched": len(errs), "total": len(rows),
            "mean_abs_error_s": round(sum(errs) / len(errs), 3) if errs else None,
            "median_abs_error_s": round(sorted(errs)[len(errs) // 2], 3) if errs else None,
            "max_abs_error_s": round(max(errs), 3) if errs else None,
            "within_1s": sum(e <= 1.0 for e in errs), "order_correct": seen_order == true_order}


def observations_json(obs: VideoObservations) -> str:
    return json.dumps(obs.model_dump(), indent=1)
