"""Optional rendered keyframes in the proposal prompt."""
from pathlib import Path

import httpx
import pytest

from mirror.interpret import LLMClient, propose_spec_detailed, summarize_demo
from mirror.interpret.keyframes import Keyframe, image_parts, load_keyframes, select_keyframes
from mirror.interpret.llm import _compact_images
from mirror.interpret.prompts import propose_messages
from tests.interpret import scenarios
from tests.interpret.fixtures import make_demo

REPO = Path(__file__).resolve().parents[2]
PINNED = REPO / "data" / "cassettes" / "inputs" / "keyframes_reference"
CASSETTES = REPO / "data" / "cassettes" / "keyframes"


def test_selection_is_deterministic_and_bounded():
    tr = make_demo()
    a = select_keyframes(tr, 4)
    assert a == select_keyframes(tr, 4)
    assert [l for _, l in a] == ["start", "carrying the cup", "carrying the block", "end"]
    assert len(select_keyframes(tr, 2)) == 2 and select_keyframes(tr, 0) == []
    assert [l for _, l in select_keyframes(tr, 10)][-1] == "end"


def test_messages_unchanged_without_keyframes():
    tr = make_demo()
    s = summarize_demo(tr)
    assert propose_messages(s, tr.scene) == propose_messages(s, tr.scene, None)
    assert isinstance(propose_messages(s, tr.scene)[1]["content"], str)


def test_image_parts_and_storage_compaction():
    k = Keyframe(t=1.5, label="start", png=b"\x89PNG fake")
    parts = image_parts([k])
    assert parts[0]["type"] == "text" and "Rendered simulator frame" in parts[0]["text"]
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    msgs = [{"role": "user", "content": [{"type": "text", "text": "x"}] + parts}]
    stored = _compact_images(msgs)
    url = stored[0]["content"][2]["image_url"]["url"]
    assert url.startswith("data:image/png;sha256,") and "base64" not in url
    assert msgs[0]["content"][2]["image_url"]["url"].startswith("data:image/png;base64,")  # input untouched


def test_system_prompt_says_rendered_not_real():
    tr = make_demo()
    msgs = propose_messages(summarize_demo(tr), tr.scene, [Keyframe(0.0, "start", b"x")])
    assert "rendered by the simulator" in msgs[0]["content"] and "not real video" in msgs[0]["content"]


def test_recorded_keyframe_proposal_replays():
    if not PINNED.exists() or not list(CASSETTES.glob("*.json")):
        pytest.skip("no pinned keyframes or recording")
    frames = load_keyframes(PINNED)
    trace = scenarios.demos("pinned")[-1]
    import json
    model = json.loads(next(CASSETTES.glob("*.json")).read_text())["request"]["model"]
    client = LLMClient(mode="replay", model=model, cassette_dir=CASSETTES,
                       transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(AssertionError("network"))))
    it = propose_spec_detailed(trace, client, keyframes=frames)
    assert it.keyframes_used == 4 and it.from_cassette
    assert "c_upright_transport_cup" in {c.id for c in it.spec.constraints}


def test_offline_ignores_keyframes():
    tr = make_demo()
    a = propose_spec_detailed(tr, None).spec
    b = propose_spec_detailed(tr, None, keyframes=[Keyframe(0.0, "start", b"x")]).spec
    assert a == b


@pytest.mark.slow
def test_render_keyframes_matches_selection():
    from mirror.interpret.keyframes import render_keyframes
    tr = make_demo()
    frames = render_keyframes(tr, n=3, width=160, height=90)
    assert [(k.t, k.label) for k in frames] == select_keyframes(tr, 3)
    assert all(k.png.startswith(b"\x89PNG") for k in frames)
