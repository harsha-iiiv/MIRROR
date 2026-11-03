"""Impact study kit tools, tested on SYNTHETIC fixtures only (no study data exists)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "study" / "tools"))
import studykit as sk  # noqa: E402

FIX = sorted((ROOT / "study" / "fixtures").glob("synthetic_*.jsonl"))


def test_fixtures_are_valid_and_all_synthetic():
    assert len(FIX) >= 4
    for p in FIX:
        ev = sk.read_log(p)
        assert sk.validate(ev) == []
        assert all(e["synthetic"] is True and e["participant"].startswith("SYN") for e in ev)


def test_fixtures_are_reproducible(tmp_path, monkeypatch):
    import make_fixtures as mf
    monkeypatch.setattr(mf, "OUT", tmp_path)
    mf.main()
    for p in FIX:
        assert (tmp_path / p.name).read_text() == p.read_text()


def test_export_and_analyze_label_synthetic_and_keep_failures():
    tbl = sk.export(FIX)
    assert tbl["synthetic"] is True and not tbl["invalid_logs"]
    assert all("texts" not in r for r in tbl["rows"])
    md = sk.analyze(tbl)
    assert md.splitlines()[0] == sk.SYNTHETIC_BANNER
    limit = [r for r in tbl["rows"] if r["end_reason"] == "time_limit"]
    assert limit and all(not r["reached_valid"] and r["time_to_valid_or_limit_s"] == 480 for r in limit)
    assert "## Failures" in md and "time_limit" in md
    ws = [r for r in tbl["rows"] if r["wrong_scope_edits"]]
    assert len(ws) == 1 and ws[0]["final_exact"]          # mistake then fix: counted once, task still completed


def test_synthetic_and_real_never_mix(tmp_path):
    ev = sk.read_log(FIX[0])
    real = [dict(e, synthetic=False, participant="P01") for e in ev]
    p = tmp_path / "real.jsonl"
    p.write_text("".join(json.dumps(e) + "\n" for e in real))
    with pytest.raises(ValueError):
        sk.export([FIX[0], p])
    mixed = ev[:3] + real[3:]
    assert any("mixes" in x for x in sk.validate(mixed))


def test_validation_catches_consent_and_identity_problems():
    ev = sk.read_log(FIX[0])
    bad = [dict(e) for e in ev]
    i = next(k for k, e in enumerate(bad) if e["event"] == "correction_submitted")
    bad[i]["text"] = "keep the cup level"
    bad[0]["participant"] = "Jane Doe"
    errs = sk.validate(bad)
    assert any("without text consent" in x for x in errs)
    assert any("not pseudonymous" in x for x in errs)


def test_assignment_is_counterbalanced():
    rows = sk.assign(8)
    orders = [tuple((b["condition"], b["set"]) for b in r["blocks"]) for r in rows]
    assert all(orders.count(o) == 2 for o in set(orders)) and len(set(orders)) == 4
    for r in rows:
        assert {b["condition"] for b in r["blocks"]} == {"mirror", "manual"}
        assert {b["set"] for b in r["blocks"]} == {"A", "B"}
    assert sum(r["T7_order"][0] == "reteach" for r in rows) == 4


def test_task_packet_acceptance_and_reserved_layouts():
    tasks = sk.load_tasks()
    base = sk.base_state()
    dev_cases = json.loads((ROOT / "mirror/evaluate/protocol_v3/dev_cases.json").read_text())["cases"]
    dev_seeds = {c["scene"].get("seed") for c in dev_cases}
    for t in tasks.values():
        acc = sk.acceptance_state(t)
        assert acc != base
        assert all(9700 <= s <= 9749 for s in t["transfer_seeds"])
        assert not set(t["transfer_seeds"]) & dev_seeds
        if t["scene"]["kind"] == "layout":
            assert 9700 <= t["scene"]["seed"] <= 9749
        # the acceptance state survives the round trip used by the transfer check
        from mirror.evaluate.protocol_v3.edits import apply_edit, semantic_state
        from mirror.evaluate.protocol_v3.runner import base_spec
        from mirror.evaluate.protocol_v3.schema import StructuredEdit
        spec, _ = apply_edit(base_spec(), StructuredEdit.model_validate(sk.state_to_edit(acc, base)), text="x")
        assert semantic_state(spec) == acc


def test_kit_says_not_yet_run():
    for name in ("README.md", "PROTOCOL.md"):
        assert "not yet run" in (ROOT / "study" / name).read_text().lower()


@pytest.mark.slow
def test_transfer_check_runs_the_simulator():
    tbl = sk.export(FIX[:1])
    tbl["rows"] = [r for r in tbl["rows"] if r["task"] == "T1"]
    res = sk.transfer(tbl)
    assert res[0]["synthetic"] is True and len(res[0]["layouts"]) == 3
