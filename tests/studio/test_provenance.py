"""Every step response says how it was produced (M2 section 2)."""
import pytest

from mirror.studio import provenance as pv
from mirror.studio.session_adapter import REFERENCE_CORRECTION, StudioService


def test_offline_configured_is_labelled_offline_with_a_reason(monkeypatch):
    monkeypatch.setenv("MIRROR_LLM_MODE", "offline")
    svc = StudioService(backend="live", session_id="p", private_runtime=True)
    try:
        svc.load_demo()
        r = svc.wait(svc.interpret(), timeout=300).result
        sp = r["step_provenance"]
        assert sp["kind"] == "offline_fallback" and sp["model"] is None and sp["interpreter"] == "offline-rules"
        assert "configured" in sp["fallback_reason"] and sp["latency_source"] == "measured_now"
        assert sp["request_status"] == "ok" and sp["cost_usd"] is None
        c = svc.wait(svc.correct(REFERENCE_CORRECTION), timeout=300).result
        assert c["step_provenance"]["kind"] == "offline_fallback"
        assert svc.model_log[-1]["step_provenance"]["kind"] == "offline_fallback"
    finally:
        svc.close("t")


def test_replayed_cassette_is_never_called_fresh(monkeypatch):
    monkeypatch.setenv("MIRROR_LLM_MODE", "replay")
    monkeypatch.setenv("MIRROR_LLM_MODEL", "muse-spark-1.3")
    svc = StudioService(backend="live", session_id="p", private_runtime=True)
    try:
        svc.load_demo()
        sp = svc.wait(svc.interpret(), timeout=300).result["step_provenance"]
        assert sp["kind"] in ("replayed_cassette", "offline_fallback")
        assert sp["kind"] != "fresh_model"
        if sp["kind"] == "replayed_cassette":
            assert sp["model"] and sp["latency_source"] in ("recorded_with_cassette", None) and sp["cost_usd"] is None
        else:
            assert sp["request_status"] == "fallback" and "recorded" in sp["fallback_reason"]
    finally:
        svc.close("t")


def test_model_failure_is_an_explicit_fallback(monkeypatch):
    monkeypatch.setenv("MIRROR_LLM_MODE", "offline")
    from mirror.interpret import LLMError
    svc = StudioService(backend="live", session_id="p", private_runtime=True)

    class Broken:
        mode, label, usage, offline, model = "live", "muse-spark-1.3", [], False, "muse-spark-1.3"

        def complete_json(self, *a, **k):
            raise LLMError("upstream 503")
    try:
        svc.load_demo()
        svc.session.client = Broken()
        sp = svc.wait(svc.interpret(), timeout=300).result["step_provenance"]
        assert sp["kind"] == "offline_fallback" and sp["request_status"] == "fallback"
        assert "upstream 503" in sp["fallback_reason"]
    finally:
        svc.close("t")


def test_fixture_runs_say_fixture_and_rechecks_say_new_check():
    svc = StudioService(backend="fixture", session_id="p", private_runtime=True)
    svc.load_demo()
    assert svc.wait(svc.interpret()).result["step_provenance"]["kind"] == "fixture"
    svc.set_scene_preset("shortcut")
    r = svc.wait(svc.run()).result
    assert r["step_provenance"]["kind"] == "fixture" and r["step_provenance"]["simulator"].startswith("fixture")
    svc.wait(svc.correct(REFERENCE_CORRECTION))
    rc = svc.snapshot()["rechecks"][-1]
    assert rc["step_provenance"]["kind"] == "new_check" and rc["step_provenance"]["rollout_provenance"] == "fixture"


def test_recorded_view_of_a_step_is_relabelled():
    live = pv.model_step("live", model="m", latency_s=1.2, tokens=100)
    assert live["kind"] == "fresh_model"
    rec = pv.as_recorded(live)
    assert rec["kind"] == "recorded_model" and rec["recorded_from"] == "fresh_model"
    assert pv.as_recorded(pv.sim_step("live", wall_s=3))["kind"] == "recorded_playback"


def test_cost_only_from_a_configured_price(monkeypatch):
    monkeypatch.delenv("MIRROR_LLM_USD_PER_1K_TOKENS", raising=False)
    assert pv.model_step("live", model="m", latency_s=1, tokens=2000)["cost_usd"] is None
    monkeypatch.setenv("MIRROR_LLM_USD_PER_1K_TOKENS", "0.5")
    p = pv.model_step("live", model="m", latency_s=1, tokens=2000)
    assert p["cost_usd"] == 1.0 and "estimate" in p["cost_note"]
