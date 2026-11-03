"""Video benchmark v3: metrics, design and generator (no model calls)."""
import json
from pathlib import Path

import pytest

from mirror.perception.videobench import metrics
from mirror.perception.videobench.harness import spec_comparison

REPO = Path(__file__).resolve().parents[2]
DESIGN = REPO / "mirror" / "perception" / "videobench" / "design_v3.json"


def _gt(n=10, tilt=lambda i: 0.0, visible=lambda i: True):
    frames = []
    for i in range(n):
        masks = {"top": ({"cup_0": {}} if visible(i) else {})}
        frames.append(dict(i=i, t=i * 0.2, held="cup_0" if 2 <= i <= 7 else None,
                           cup_tilt_deg={"cup_0": tilt(i)},
                           objects={"cup_0": {"pos": [0.1 * i, 0.0, 0.05]}, "block_0": {"pos": [0.5, 0.5, 0.02]}},
                           masks=masks))
    return dict(frames=frames, objects=[{"id": "cup_0", "kind": "cup"}])


def test_localisation_counts_misses_and_reports_unconditional_shares():
    gt = _gt()
    est = lambda oid, t: None if t > 1.0 else (round(t / 0.2) * 0.1 + 0.01, 0.0)   # noqa: E731
    r = metrics.localisation(gt, est, ["cup_0"])["all"]
    assert r["visible"] == 10 and r["missing"] == 4
    assert r["within_2cm_of_visible"] == pytest.approx(0.6)          # not 1.0 over answered frames
    assert r["error_m_answered"]["n"] == 6


def test_identity_error_is_separate_from_outlier():
    gt = _gt(n=3)
    est = lambda oid, t: (0.5, 0.5)    # sitting on the block  # noqa: E731
    r = metrics.localisation(gt, est, ["cup_0"])["all"]
    assert r["outliers"] == 3 and r["identity_errors"] == 3


def test_abstention_is_not_scored_as_zero_and_false_upright_is_counted():
    gt = _gt(tilt=lambda i: 40.0 if 4 <= i <= 5 else 0.0)
    zero = metrics.tilt(gt, lambda t: 0.0, "cup_0")
    assert zero["false_upright_frames"] == 2 and zero["upright_decision"] == "upright"
    assert zero["upright_decision_correct"] is False
    abst = metrics.tilt(gt, lambda t: None, "cup_0")
    assert abst["coverage"] == 0 and abst["within_5deg_of_carry"] == 0
    assert abst["false_upright_frames"] == 0 and abst["abstained_on_tilted_frames"] == 2
    assert abst["upright_decision"] == "abstained" and abst["upright_decision_correct"] is None


def test_spec_comparison_separates_model_variability():
    sigs = {"ground_truth": {"offline": "A", "model": ["A", "A", "B"]},
            "two_view": {"offline": "A", "model": ["A", "C", "A"]},
            "top_only": {"offline": "D", "model": ["D", "D", "D"]}}
    c = spec_comparison(sigs)
    assert c["ground_truth_repeats_distinct"] == 2
    assert c["two_view"]["modal_equal_to_ground_truth_modal"] is True
    assert c["top_only"]["modal_equal_to_ground_truth_modal"] is False
    assert c["offline_equal"] == {"two_view": True, "top_only": False}


def test_design_is_complete_and_its_seeds_are_unused():
    d = json.loads(DESIGN.read_text())
    assert d["status"].startswith("design") and "NOT run" in d["status"]
    seeds = d["demonstrations"]["seeds"]
    assert len(seeds) == 24 and len(set(seeds)) == 24
    for s in seeds:
        assert not list(REPO.glob(f"results/*/inputs/seed{s}")), s
    assert set(d["hypotheses_and_thresholds"]) >= {"H1", "H2", "H3", "H4", "H5", "H6"}
    assert d["budget"]["cap_usd"] >= d["budget"]["expected_usd"]["total"]


@pytest.mark.slow
def test_tilted_demo_reaches_the_commanded_tilt():
    from mirror.perception.videobench.demos import record_tilted_demo, true_cup_carry_tilt
    from mirror.scenes import sample_layout
    tr = record_tilted_demo(sample_layout(9101, "dev"), 35.0)
    t = true_cup_carry_tilt(tr)
    assert 30 <= t["max_deg"] <= 40
