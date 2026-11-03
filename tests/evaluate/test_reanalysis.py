"""Derived re-analyses: they reproduce the stored summaries they start from,
read originals without changing them, and keep their denominators explicit."""
import hashlib
import json
from pathlib import Path

import pytest

from mirror.evaluate.reanalysis import chronology, manifests, replay, robustness
from mirror.evaluate.reanalysis import video as rv

ROOT = Path(__file__).resolve().parents[2]


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_robustness_reproduces_stored_headline_and_splits_denominators():
    src = ROOT / "results/eval_robustness/plan_report/report.json"
    before = _sha(src)
    rep = robustness.analyse(src, ROOT / "results/eval_robustness/report.json")
    assert _sha(src) == before
    for g, (stored, recomputed) in ((g, v["kept_iou_delta_median"]) for g, v in rep["reproduces_stored_summary"].items()):
        assert stored == recomputed, g
    a = rep["groups"]["A/corrected"]
    states = a["kept_detection_states"]
    assert sum(states.values()) == a["geometry_kept_rows"]
    assert states.get("never_prompted", 0) == a["geometry_kept_rows"] - a["geometry_kept_rows_prompted"]
    # the cup is never segmented (IoU >= 0.5) on any frame in either prompt condition
    for cond in ("A", "B"):
        assert rep["cup"][cond]["frames_cup_matched_original"] == 0
        assert rep["cup"][cond]["edited_rows_cup_matched"] == 0


def test_robustness_pair_states():
    base = dict(prompt="cube", iou=0.0, iou_delta=0.0)
    assert robustness.pair_state(dict(base, prompt=None)) == "never_prompted"
    assert robustness.pair_state(dict(base, found_original=True, found=False)) == "lost_after_edit"
    assert robustness.match_state(dict(base, iou=0.8, iou_delta=0.0)) == "matched_both"
    assert robustness.match_state(dict(base, iou=0.1, iou_delta=-0.7)) == "lost_after_edit"


def test_chronology_facts():
    rep = chronology.analyse(ROOT)
    o = rep["ordering_holdout"]
    assert o["frozen_at_is_after_run_start"] and o["frozen_at_is_after_last_model_response"]
    assert o["holdout_first_run_started"] < o["holdout_first_model_response"]
    for name, r in rep["results"].items():
        assert r["cases_sha256_matches_committed"], name
        assert r["gold_in_result_equals_committed_cases"], name
        assert r["rescore_with_committed_scorer_reproduces_summary"], name


def test_stored_paired_digests_cover_every_trial():
    rep = json.loads((ROOT / "results/eval_muse/report.json").read_text())
    d = replay.stored_digests(rep)
    assert {k: len(v) for k, v in d.items()} == {"primary": 60, "shortcut": 30, "blocked": 5}
    t = d["primary"][0]
    # the outcome digest ignores beads; the detail digest does not
    r = rep["strata"]["primary"]["trials"][0]["b"]["report"]
    r2 = json.loads(json.dumps(r))
    r2["outcomes"][0]["beads_in_container"] = (r2["outcomes"][0].get("beads_in_container") or 0) + 1
    assert replay._h(replay.outcome_view(r2)) == t["b"]["outcome_sha256"]
    assert replay._h(replay.detail_view(r2)) != t["b"]["detail_sha256"]


def test_digest_is_order_free():
    assert manifests.digest_of([("a", "1"), ("b", "2")]) == manifests.digest_of([("b", "2"), ("a", "1")])


def _rec(est_x, gt_x=0.0, found=True):
    """Synthetic one-object record (not evidence): estimate x per frame against a fixed truth."""
    n = len(est_x)
    g = dict(kind="block", x=[gt_x] * n, y=[0.0] * n, z=[0.02] * n, vis_top=[True] * n, vis_side=[True] * n,
             held=[False] * n, tilt_deg=[None] * n)
    e = dict(kind="block", frame=list(range(n)), found=[found and x is not None for x in est_x],
             x=list(est_x), y=[0.0 if x is not None else None for x in est_x], tilt_deg=None)
    return dict(ground_truth={"block_0": g}, estimates={"sam": {"block_0": e}})


def test_unconditional_position_counts_misses_and_outliers():
    rec = _rec([0.01, 0.02, None, 0.30])          # synthetic: 2 hits, 1 miss, 1 outlier
    s = rv.position_summary(rv.frame_table(rec, "sam"))
    assert s["counts"] == dict(hit=2, outlier=1, identity=0, miss=1)
    assert s["error_cm_gated_hits_only"]["n"] == 2
    assert s["error_cm_all_estimates_no_gate"]["max"] == pytest.approx(30.0)
    assert s["error_cm_misses_as_infinite"]["misses"] == 1
    assert s["error_cm_misses_hold_last"]["n"] == 4            # the miss is scored with the last detection
    assert s["fraction_within_5cm_unconditional"] == 0.5


def test_identity_error_is_separate_from_outlier():
    rec = _rec([0.0, 0.2])
    rec["ground_truth"]["marker_0"] = dict(rec["ground_truth"]["block_0"], kind="marker", x=[0.5, 0.2])
    rows = [r for r in rv.frame_table(rec, "sam")]
    assert [rv._classify(r) for r in rows] == ["hit", "identity"]


def test_crossplatform_self_comparison_is_exact():
    from mirror.evaluate.reanalysis import crossplatform
    rep = json.loads((ROOT / "results/eval_muse/report.json").read_text())
    p = crossplatform._per_trial_from_stored(rep)
    c = crossplatform.compare(p, p)
    assert c["trials_compared"] == 95 and c["detail_equal"] == 95 and c["max_abs_bead_difference"] == 0


def test_scene_numeric_compare_reports_largest_difference():
    from mirror.evaluate.reanalysis import scenes
    a = {"s:1": dict(scene_sha256="x", scene={"pose": {"pos": [0.1, 0.2]}})}
    b = {"s:1": dict(scene_sha256="y", scene={"pose": {"pos": [0.1, 0.2 + 1e-9]}})}   # synthetic layouts
    r = scenes.compare(a, b)
    assert r["hash_equal"] == 0 and r["structure_differs"] == 0
    assert r["max_abs_numeric_difference"] == pytest.approx(1e-9) and r["at"].endswith("pos[1]")


def test_video_reanalysis_rebuilt_every_reported_seed_exactly():
    rep = json.loads((ROOT / "results/reanalysis_v1/video/report.json").read_text())
    rows = [p for p in rep["per_seed"] if p["source"] == "eval_video2"]
    assert len(rows) == 16
    assert all(p["reproduces_stored_row"] and all(p["reproduces_stored_row"].values()) for p in rows)
    t = rep["sources"]["eval_video2"]["tilt"]
    assert t["gt_tilt_max_deg_over_all_seeds"] < 0.3
    assert t["constant_zero_median_of_seed_medians_deg"] < t["registered_median_of_seed_medians_deg"]
