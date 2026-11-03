"""The revision-4 recorder: dry run makes no request; the spend cap stops it; new directories only."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _mod():
    spec = importlib.util.spec_from_file_location("record_prompt_rev4", ROOT / "scripts" / "record_prompt_rev4.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_dry_run_counts_calls_and_estimates(capsys, monkeypatch):
    m = _mod()
    def boom(*a, **k):
        raise AssertionError("no client may be built in a dry run")
    monkeypatch.setattr("mirror.interpret.LLMClient.from_env", boom)
    assert m.main([]) == 0
    out = capsys.readouterr().out
    assert "134 model calls" in out and "No request was made" in out


def _fake_factory(api, usage_tokens):
    from mirror.interpret import LLMClient
    def make(cdir):
        return LLMClient(base_url="http://fake/v1", model=m_model(), api_key="k", mode="record",
                         cassette_dir=cdir, transport=api.transport(), backoff_s=0.0)
    return make


def m_model():
    return "muse-spark-1.3"


def test_spend_cap_stops_before_exceeding(tmp_path):
    from tests.interpret.fake_api import FakeAPI
    m = _mod()
    reply = {"outcome": "clarify", "interpretation": "x", "question": "Which object?", "options": ["The cup"], "ops": []}
    api = FakeAPI([reply] * 50, model="muse-spark-1.3", usage={"prompt_tokens": 3000, "completion_tokens": 20000})   # $0.089 per call
    sets = [(n, items[:10]) for n, items in m.planned_sets()[:1]]
    rep = m.record(sets, cap_usd=0.30, out=tmp_path, resume=False, client_factory=_fake_factory(api, None),
                   cassettes=tmp_path / "cas")
    assert rep["stopped"] and "spend cap reached" in rep["stopped"]
    assert rep["usd_total"] <= 0.30
    assert len(api.requests) == 3        # 0.089 * 3 = 0.268; a 4th would risk passing the cap with the reserve


def test_refuses_non_empty_cassette_dir(tmp_path):
    m = _mod()
    d = tmp_path / "cas" / "benchmark"
    d.mkdir(parents=True)
    (d / "x.json").write_text("{}")
    with pytest.raises(SystemExit):
        m.record(m.planned_sets()[:1], cap_usd=1.0, out=tmp_path, resume=False,
                 client_factory=lambda c: None, cassettes=tmp_path / "cas")
