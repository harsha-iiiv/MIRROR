import pytest

from mirror.evaluate import corrected_rules


def test_evaluation_specs_replay_and_offline_agree():
    pytest.importorskip("mirror.interpret")
    from mirror.evaluate.specs import PINNED_DEMO, evaluation_specs
    if not PINNED_DEMO.exists():
        pytest.skip("pinned reference demonstration not present")
    try:
        a, b, prov = evaluation_specs()
    except Exception as exc:
        if "CassetteMiss" in str(exc):
            pytest.skip("recorded responses do not match the current prompts (re-record cassettes)")
        raise
    assert prov["offline_agrees"] is True
    assert b.version == a.version + 1
    rules = corrected_rules(a, b)
    assert len(rules) == 1 and rules[0].type == "upright" and rules[0].phase == "transport"
    assert rules[0].applies_to.kind == "cup"
    assert "c_upright_final_cup" in prov["enforced_original"]
    assert len(a.active_goals()) == 3


def test_disagreement_is_refused(monkeypatch):
    from mirror.evaluate import specs as sp
    real = sp._signature
    calls = {"n": 0}

    def flaky(spec):
        calls["n"] += 1
        sig = real(spec)
        if calls["n"] == 2:
            sig = dict(sig, enforced=[])
        return sig
    monkeypatch.setattr(sp, "_signature", flaky)
    if not sp.PINNED_DEMO.exists():
        pytest.skip("pinned reference demonstration not present")
    with pytest.raises(sp.SpecAgreementError):
        sp.evaluation_specs()


def test_offline_source_is_labelled():
    from mirror.evaluate import specs as sp
    if not sp.PINNED_DEMO.exists():
        pytest.skip("pinned reference demonstration not present")
    a, b, prov = sp.evaluation_specs(source="offline")
    assert prov["spec_source"] == "offline" and prov["offline_agrees"] is None
    assert len(corrected_rules(a, b)) == 1
