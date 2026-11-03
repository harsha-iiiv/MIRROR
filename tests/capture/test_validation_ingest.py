"""Validation design and ingest plumbing (no video rendering)."""
import json
from pathlib import Path

import pytest

from mirror.capture import ingest as I
from mirror.capture import validation as V


def test_design_is_frozen_and_complete():
    d = V.load_design()
    assert d["version"] == 1 and d["status"] == "awaiting footage" and d["frozen_before_any_real_footage"]
    conds = {c["condition"] for c in d["clips"]["evaluation"]}
    assert {"two_view_upright", "two_view_tilted", "two_view_occlusion", "two_view_dim_light", "invalid_calibration",
            "single_view_top", "retest_1", "retest_2"} <= conds
    assert d["clips"]["development"][0]["use"].startswith("build and debug")
    for k in ("tilt_constant_zero", "tracking_manual_keyframes", "tracking_classical", "single_view"):
        assert k in d["baselines"]
    assert "unconditional" in d["unconditional_reporting"].lower() or "missing" in d["unconditional_reporting"]
    assert len(V.design_sha256()) == 64


def test_aggregate_keeps_synthetic_out_of_evidence():
    rows = [{"clip_id": "s", "split": "synthetic_rehearsal", "synthetic": True, "outcome": "scored",
             "checks": [{"name": "x", "passed": True}]}]
    agg = V.aggregate(rows)
    assert agg["status"] == "awaiting footage"
    assert agg["evaluation"]["clips"] == 0 and agg["synthetic_rehearsal_not_evidence"]["clips"] == 1


def test_spec_core_ignores_wording():
    a = {"goals": [{"id": "g1", "text": "put the cup in the tray", "object": {"kind": "cup"}, "target": "tray"}]}
    b = {"goals": [{"id": "g9", "text": "cup into tray please", "object": {"kind": "cup"}, "target": "tray"}]}
    c = {"goals": [{"id": "g1", "object": {"kind": "block"}, "target": "tray"}]}
    assert V._spec_core(a) == V._spec_core(b) != V._spec_core(c)


def test_ingest_init_template_and_refusals(tmp_path):
    assert I.main(["init", str(tmp_path)]) == 0
    cfg = json.loads((tmp_path / "capture.json").read_text())
    assert cfg["print_check"]["measured_bar_mm"] is None
    assert {o["proxy"] for o in cfg["objects"]} == {"cup", "block", "marker", "tray", "wall"}
    assert all(o["confirmed_by_person"] is False for o in cfg["objects"])
    assert I.main(["init", str(tmp_path)]) == 1          # never overwrites
    with pytest.raises(SystemExit, match="measure the printed"):
        I.main(["run", str(tmp_path)])


def test_unconditional_quantiles_keep_missing_as_missing():
    import numpy as np
    assert V._pct(np.array([1.0, 2.0, np.inf, np.inf]), 50) == 2.0
    assert V._pct(np.array([1.0, np.inf, np.inf]), 90) is None
