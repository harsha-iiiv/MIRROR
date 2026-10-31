"""Recorded model responses (data/cassettes) replay deterministically, with no
network access, and agree with the offline rules on the reference behaviour."""
import json

import httpx
import pytest

from mirror.interpret import LLMClient, OFFLINE_LABEL
from mirror.interpret.llm import DEFAULT_CASSETTE_DIR
from tests.interpret import scenarios

REF = scenarios.CORRECTIONS[0]


def _recorded_model() -> str:
    models = {json.loads(p.read_text())["request"]["model"] for p in DEFAULT_CASSETTE_DIR.glob("*.json")}
    if not models:
        pytest.fail(f"no cassettes in {DEFAULT_CASSETTE_DIR}; record them with tests/interpret/scenarios.py")
    assert len(models) == 1, f"cassettes recorded with several models: {models}"
    return models.pop()


def _no_network(request):
    raise AssertionError("replay mode must not touch the network")


def replay_client() -> LLMClient:
    return LLMClient(mode="replay", model=_recorded_model(), api_key=None,
                     transport=httpx.MockTransport(_no_network))


@pytest.fixture(scope="module")
def replayed():
    return {tr.demo_id: scenarios.run(replay_client(), tr) for tr in scenarios.demos()}


def test_replay_is_deterministic(replayed):
    again = {tr.demo_id: scenarios.run(replay_client(), tr) for tr in scenarios.demos()}
    assert again == replayed


def test_replayed_reference_correction(replayed):
    for res in replayed.values():
        assert res["interpreter"] != OFFLINE_LABEL
        out = res["corrections"][REF]
        assert out["outcome"] == "apply"
        assert out["statuses"]["c_upright_transport_cup"] == "confirmed"
        assert out["statuses"].get("c_upright_final_cup") == "confirmed"
        # scope: only cup rules changed status
        changed = {t for (op, t, _) in out["ops"] if op != "resolve_uncertainty"}
        assert changed and all(t.endswith("_cup") for t in changed)


def test_replayed_adversarial_cases(replayed):
    res = replayed["fixture_demo"]
    c = res["corrections"]
    assert c["It must stay upright the whole time."]["outcome"] == "clarify"
    assert c["The bowl must stay upright."]["outcome"] == "clarify"
    assert c["Go faster please."]["outcome"] == "reject"
    neg = c["The cup doesn't need to stay upright while it is carried."]
    assert neg["outcome"] == "apply" and neg["statuses"]["c_upright_transport_cup"] == "rejected"
    tilt = c["The marker can tilt, only the cup matters."]
    assert tilt["statuses"]["c_upright_transport_cup"] == "confirmed"
    assert all(v != "confirmed" for k, v in tilt["statuses"].items() if k.endswith("_marker"))
    ans = res["answers"][scenarios.ANSWERS[0]]
    assert ans["outcome"] == "apply" and ans["statuses"]["c_upright_transport_cup"] == "confirmed"


def test_offline_parity_on_reference(replayed):
    offline = LLMClient(mode="offline")
    for tr in scenarios.demos():
        off = scenarios.run(offline, tr)
        mod = replayed[tr.demo_id]
        assert off["interpreter"] == OFFLINE_LABEL
        kinds = lambda r: sorted(g["object"]["kind"] for g in r["proposal"]["goals"])
        assert kinds(off) == kinds(mod)
        for cid in ("c_upright_transport_cup", "c_upright_final_cup"):
            assert cid in {c["id"] for c in off["proposal"]["constraints"]}
            assert cid in {c["id"] for c in mod["proposal"]["constraints"]}
        for res in (off, mod):
            st = res["corrections"][REF]["statuses"]
            assert st["c_upright_transport_cup"] == "confirmed" and st["c_upright_final_cup"] == "confirmed"
            assert res["corrections"]["It must stay upright the whole time."]["outcome"] == "clarify"
            assert res["corrections"]["The bowl must stay upright."]["outcome"] == "clarify"
            assert res["corrections"]["Go faster please."]["outcome"] == "reject"
