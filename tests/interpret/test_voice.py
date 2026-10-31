"""Spoken corrections with Muse Voice Transcribe (recorded)."""
import io
import wave
from pathlib import Path

import httpx
import pytest

from mirror.interpret import LLMClient
from mirror.interpret.voice import _to_pcm16k, _wav_ok, transcribe
from mirror.meta import MetaClient

REPO = Path(__file__).resolve().parents[2]
WAV = REPO / "tests" / "meta" / "fixtures" / "correction_espeak.wav"
SAID = "The cup must stay upright while it is carried."


def _no_net(r):
    raise AssertionError("network")


def _replay():
    return MetaClient(mode="replay", transport=httpx.MockTransport(_no_net))


def test_transcribe_replays_with_provenance():
    t = transcribe(WAV, _replay())
    assert t.text == SAID and t.model == "muse-voice-transcribe-1.0" and t.mode == "PUSH_TO_TALK"
    assert t.from_cassette and t.converted is None and abs(t.audio_duration_s - 3.04) < 0.05
    assert t.audio_sha256 == t.source_sha256


def _wav(rate, channels):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * (channels * rate // 10))
    return buf.getvalue()


def test_format_check_and_conversion():
    assert _wav_ok(WAV.read_bytes()) and _wav_ok(_wav(24000, 1))
    assert not _wav_ok(_wav(48000, 2)) and not _wav_ok(b"not audio")
    out = _to_pcm16k(_wav(48000, 2))
    assert _wav_ok(out)


def test_session_correct_audio_logs_transcript(monkeypatch):
    from mirror.pipeline import Session
    from tests.interpret.fixtures import make_demo
    s = Session(client=LLMClient(mode="offline"), meta_client=_replay())
    s.demo = make_demo()
    s.scene = s.demo.scene
    s.interpret()
    spec, record, tr = s.correct_audio(WAV)
    assert tr.text == SAID and record.text == SAID
    ev = [e for e in s.events if e["event"] == "voice_transcribed"]
    assert ev and ev[0]["model"] == "muse-voice-transcribe-1.0" and ev[0]["from_cassette"]
    assert {c.id: c.status.value for c in spec.constraints}["c_upright_transport_cup"] == "confirmed"


def test_cli_correct_audio_replays_muse(monkeypatch):
    import io as _io
    from mirror import cli
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("MIRROR_LLM_MODE", raising=False)
    monkeypatch.delenv("MIRROR_META_MODE", raising=False)
    monkeypatch.setenv("MIRROR_LLM_MODEL", "muse-spark-1.3")
    monkeypatch.setenv("MIRROR_CASSETTE_DIR", str(REPO / "data" / "cassettes_muse"))
    buf = _io.StringIO()
    assert cli.main(["correct", "--audio", str(WAV)], stream=buf) == 0
    out = buf.getvalue()
    assert f'"{SAID}"' in out and "Compiled by: muse-spark-1.3" in out
    assert "c_upright_transport_cup.status" in out
