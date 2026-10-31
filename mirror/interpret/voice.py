"""Spoken corrections: WAV -> transcript with Muse Voice Transcribe.

``transcribe(wav)`` sends one recording to /v1/asr/transcribe in PUSH_TO_TALK
mode (the user delimits the turn, as with a microphone button). The API takes
mono 16-bit PCM WAV at 16 or 24 kHz, at most 10 minutes; other WAV formats are
converted with ffmpeg first and the conversion is recorded. The transcript is
then compiled like typed text (Session.correct_audio). Optional ``keywords``
bias recognition towards task words; they are off by default.
"""
from __future__ import annotations

import hashlib
import io
import subprocess
import wave
from pathlib import Path
from typing import Optional, Union

from pydantic import BaseModel, Field

MODEL = "muse-voice-transcribe-1.0"
TASK_KEYWORDS = ["cup", "block", "marker", "tray", "wall", "upright", "transport"]


class Transcript(BaseModel):
    text: str
    model: str
    mode: str
    audio_name: str
    audio_sha256: str                       # of the bytes sent
    source_sha256: str                      # of the file given
    converted: Optional[str] = None         # description of any format conversion
    audio_duration_s: Optional[float] = None
    keywords: list[str] = Field(default_factory=list)
    latency_s: float = 0.0
    from_cassette: bool = False
    turns: list[dict] = Field(default_factory=list)


def _wav_ok(data: bytes) -> bool:
    try:
        with wave.open(io.BytesIO(data)) as w:
            return w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() in (16000, 24000)
    except (wave.Error, EOFError):
        return False


def _to_pcm16k(data: bytes) -> bytes:
    p = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", "16000",
                        "-sample_fmt", "s16", "-f", "wav", "pipe:1"], input=data, capture_output=True, timeout=120)
    if p.returncode != 0 or not p.stdout:
        raise ValueError("could not convert the recording to 16 kHz mono 16-bit WAV: "
                         + p.stderr.decode(errors="replace")[:200])
    return p.stdout


def transcribe(wav: Union[str, Path, bytes], client=None, *, mode: str = "PUSH_TO_TALK",
               keywords: Optional[list[str]] = None, model: str = MODEL) -> Transcript:
    """Transcribe one spoken correction."""
    from mirror.meta import MetaClient
    client = client or MetaClient.from_env()
    src = wav if isinstance(wav, (bytes, bytearray)) else Path(wav).read_bytes()
    name = "audio.wav" if isinstance(wav, (bytes, bytearray)) else Path(wav).name
    data, converted = bytes(src), None
    if not _wav_ok(data):
        data = _to_pcm16k(data)
        converted = "converted to 16 kHz mono 16-bit PCM WAV with ffmpeg"
    options = {"keywords": list(keywords)} if keywords else None
    res = client.asr_transcribe(data, model=model, mode=mode, options=options)
    ms = res.data.get("audioDurationMs")
    return Transcript(text=res.text, model=res.model, mode=mode, audio_name=name,
                      audio_sha256=hashlib.sha256(data).hexdigest(), source_sha256=hashlib.sha256(src).hexdigest(),
                      converted=converted, audio_duration_s=ms / 1000.0 if isinstance(ms, (int, float)) else None,
                      keywords=list(keywords or []), latency_s=res.latency_s, from_cassette=res.from_cassette,
                      turns=list(res.data.get("turns") or []))
