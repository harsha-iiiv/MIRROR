"""Muse Spark recordings: separate directory, explicit model choice, and the
same enforced specifications as the frozen evaluation pair."""
from pathlib import Path

import httpx
import pytest

from mirror.interpret import LLMClient
from mirror.interpret.auto import proposal_recorded, recorded_models, recordings_by_model, resolve_client

REPO = Path(__file__).resolve().parents[2]
MUSE = REPO / "data" / "cassettes_muse"
DEFAULT = REPO / "data" / "cassettes"
MODEL = "muse-spark-1.3"
pytestmark = pytest.mark.skipif(MODEL not in recorded_models(MUSE), reason="no Muse Spark recordings")


def _pinned():
    from mirror.evaluate.specs import PINNED_DEMO
    import gzip
    from mirror.contracts import DemoTrace
    return DemoTrace.model_validate_json(gzip.decompress(PINNED_DEMO.read_bytes()))


def test_muse_pair_has_the_frozen_enforced_specs():
    from mirror.evaluate.specs import _build, _signature, evaluation_specs
    client = LLMClient(mode="replay", model=MODEL, cassette_dir=MUSE,
                       transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(AssertionError("network"))))
    m2, m3, _ = _build(_pinned(), client)
    f2, f3, _ = evaluation_specs()
    assert m2.interpreter == MODEL and f2.interpreter != MODEL
    assert _signature(m2) == _signature(f2) and _signature(m3) == _signature(f3)


def test_default_replay_is_unchanged():
    """With no model asked for, the default directory still replays the
    development-model recordings that the frozen evaluation uses."""
    client, _ = resolve_client(_pinned(), env={})
    assert client.mode == "replay" and client.cassette_dir == DEFAULT and client.model != MODEL


def test_explicit_model_choice():
    tr = _pinned()
    client, why = resolve_client(tr, env={"MIRROR_LLM_MODEL": MODEL, "MIRROR_CASSETTE_DIR": str(MUSE)})
    assert client.mode == "replay" and client.model == MODEL and MODEL in why
    # asking for Muse in the default directory never substitutes another model
    client, why = resolve_client(tr, env={"MIRROR_LLM_MODEL": MODEL})
    assert client.offline and MODEL in why


def test_recordings_by_model():
    rows = recordings_by_model([DEFAULT, MUSE], _pinned())
    muse = [r for r in rows if r["model"] == MODEL]
    assert muse and muse[0]["matches_demo"] and muse[0]["reported"] == MODEL
    assert proposal_recorded(_pinned(), MODEL, MUSE)
