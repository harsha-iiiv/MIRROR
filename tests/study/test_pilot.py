"""Usability pilot tools on SYNTHETIC trial-export fixtures (no pilot has run)."""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "study" / "tools"))
import pilotkit as pk  # noqa: E402

FIX = ROOT / "study" / "pilot" / "fixtures"
PILOT = sorted(FIX.glob("synthetic_SYN*.jsonl"))
DEV = FIX / "synthetic_dev_smoke.jsonl"


def test_fixtures_valid_synthetic_and_reproducible(tmp_path, monkeypatch):
    recs = pk.read(PILOT)
    assert pk.validate(recs) == [] and all(r["synthetic"] for r in recs)
    import make_pilot_fixtures as mf
    monkeypatch.setattr(mf, "OUT", tmp_path)
    mf.main()
    for p in PILOT + [DEV]:
        assert (tmp_path / p.name).read_text() == p.read_text()


def test_cards_describe_outcomes_not_sentences():
    cards = pk.load_cards()
    assert {"C7", "C8", "C9", "C10"} <= set(cards)
    for c in cards.values():
        assert '"' not in c["outcome"] and "\u201c" not in c["outcome"]      # nothing quoted to copy
        assert c["scene"]["kind"] == "reference" or 9740 <= c["scene"]["seed"] <= 9749
    assert cards["C7"]["expected_first"] == "asks" and cards["C9"]["expected_first"] == "rejects"


def test_absent_object_card_uses_a_term_outside_every_lexicon():
    from mirror.evaluate.protocol_v3.matrix import absent_term_status
    assert absent_term_status(pk.load_cards()["C8"]["absent_term"]) == "unseen"


def test_contexts_and_synthetic_never_pool():
    recs = pk.read(PILOT + [DEV])
    with pytest.raises(ValueError):
        pk.analyze(recs)
    md = pk.analyze(recs, "usability_pilot")
    assert md.splitlines()[0] == pk.SYNTHETIC_BANNER
    assert "preliminary usability evidence only" in md
    dev = pk.analyze(pk.read([DEV]), "dev_smoke")
    assert "not independent user validation" in dev
    real = [dict(r, synthetic=False, participant="P01") for r in pk.read(PILOT[:1])]
    assert any("mix" in e for e in pk.validate(pk.read(PILOT[:1]) + real))


def test_measures_from_fixtures():
    rows = pk.task_rows(pk.read(PILOT))
    by = {(r["participant"], r["task"], r["condition"]): r for r in rows}
    assert by[("SYN01", "C2", "mirror")]["clarification_followed_by_correct"] == 1
    assert by[("SYN01", "C5", "manual")]["unresolved_failure"] == "participant gave up"
    assert by[("SYN02", "C4", "manual")]["attempts_with_wrong_phase"] == 1
    assert by[("SYN02", "C4", "manual")]["completed"]
    assert by[("SYN03", "C4", "mirror")]["attempts_with_wrong_object"] == 1
    assert by[("SYN01", "C10", "mirror")]["completed"]               # proven stop counts for the blocked card
    assert by[("SYN01", "C8", "mirror")]["first_attempt_correct"]     # asked, changed nothing
    assert sum(r["comprehension_asked"] for r in rows) == 6
    assert sum(r["comprehension_correct"] for r in rows) == 5


def test_classify_change_wrong_object_and_phase():
    start = pk.sk.base_state()
    acc = pk.acceptance_state(pk.load_cards()["C4"], start)          # marker upright while carried
    after = json.loads(json.dumps(acc))
    after["enforced"]["c_upright_final_marker"] = {"max_tilt_deg": 15.0}
    after["enforced"]["c_upright_transport_block"] = {"max_tilt_deg": 15.0}
    c = pk.classify_change(after, start, acc)
    assert c == {"wrong_object": ["c_upright_transport_block"], "wrong_phase": ["c_upright_final_marker"]}


def test_assignment_rotates_orders_and_probes():
    rows = pk.assign(4)
    assert len({tuple((b["condition"], b["set"]) for b in r["blocks"]) for r in rows}) == 4
    assert len({r["probes_mirror_only"][0] for r in rows}) == 4
    for r in rows:
        assert {b["condition"] for b in r["blocks"]} == {"mirror", "manual"}


def test_csv_drops_text_unless_asked(tmp_path):
    recs = pk.read(PILOT)
    n = pk.export_csv(recs, tmp_path / "t.csv")
    head = next(csv.reader(open(tmp_path / "t.csv")))
    assert n == len(recs) and "text" not in head and "provenance" in head


def test_protocol_labels():
    txt = " ".join((ROOT / "study" / "pilot" / "PROTOCOL.md").read_text().lower().split())
    assert "prepared, not run" in txt and "developer smoke test" in txt and "preliminary usability evidence" in txt
