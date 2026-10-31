"""Correction benchmark: frozen cases, scoring, and replay reproducibility."""
import json
import re
from pathlib import Path

import httpx
import pytest

from mirror.interpret import LLMClient
from mirror.interpret.auto import recorded_models
from mirror.interpret.benchmark import load_base_spec, load_cases, run_benchmark, score_case
from mirror.interpret.benchmark.score import state_of

REPO = Path(__file__).resolve().parents[2]
CASSETTES = REPO / "data" / "cassettes" / "corrections_benchmark"
SETS = [("cases.json", "corrections_benchmark.json"),
        ("cases_holdout.json", "corrections_benchmark_holdout.json"),
        ("cases.json", "corrections_benchmark_after_prompt_revision.json"),
        ("cases_holdout.json", "corrections_benchmark_holdout_after_prompt_revision.json")]
# Results produced with the current prompts replay exactly; earlier ones need
# the prompts of their own revision and are checked for unchanged cases only.
CURRENT = [(c, r, "gpt-5.5", CASSETTES) for c, r in SETS[2:]]
MUSE_CASSETTES = REPO / "data" / "cassettes_muse" / "corrections_benchmark"
SETS += [("cases.json", "corrections_benchmark_muse.json"),
         ("cases_holdout.json", "corrections_benchmark_holdout_muse.json")]
CURRENT += [(c, r, "muse-spark-1.3", MUSE_CASSETTES) for c, r in SETS[-2:]]
ID_RE = re.compile(r"^c_(upright|forbidden_contact|keep_clearance)_(final|transport|always)_(cup|block|marker|all)$")


@pytest.mark.parametrize("cases_file,_", SETS[:2])
def test_cases_are_well_formed(cases_file, _):
    doc = load_cases(Path(__file__).resolve().parents[2] / "mirror/interpret/benchmark" / cases_file)
    ids = [c["id"] for c in doc["cases"]]
    assert len(ids) == len(set(ids))
    for c in doc["cases"]:
        g = c["gold"]
        assert g["outcome"] in ("apply", "asks", "rejects")
        if g["outcome"] == "apply":
            assert all(ID_RE.match(e["id"]) for e in g["enforced"]), c["id"]


@pytest.mark.parametrize("cases_file,results", SETS)
def test_cases_unchanged_since_results(cases_file, results):
    """The cases scored in the committed results are the cases in the repo."""
    res = REPO / "results" / results
    if not res.exists():
        pytest.skip("no committed results")
    doc = load_cases(REPO / "mirror/interpret/benchmark" / cases_file)
    assert json.loads(res.read_text())["cases_sha256"] == doc["sha256"]


def test_scoring_flags():
    base = state_of(load_base_spec())
    case = {"gold": {"outcome": "apply", "enforced": [
        {"id": "c_upright_final_cup", "params": {"max_tilt_deg": 15}},
        {"id": "c_upright_transport_cup", "params": {"max_tilt_deg": 15}}]}}
    over = {"outcome": "apply", "state": {"enforced": {
        "c_upright_final_cup": {"max_tilt_deg": 15.0}, "c_upright_transport_cup": {"max_tilt_deg": 15.0},
        "c_upright_transport_marker": {"max_tilt_deg": 15.0}}, "ordering": [], "inactive_goals": []}}
    sc = score_case(case, over, base)
    assert not sc["exact"] and sc["scope_errors"] == ["marker"]
    sc = score_case({"gold": {"outcome": "asks"}}, over, base)
    assert sc["false_accept"] and not sc["exact"]
    sc = score_case({"gold": {"outcome": "asks"}}, {"outcome": "rejects"}, base)
    assert sc["refused_correctly"] and not sc["exact"]


@pytest.mark.parametrize("cases_file,results,model,cassettes", CURRENT)
def test_model_results_replay_exactly(cases_file, results, model, cassettes):
    res_path = REPO / "results" / results
    models = recorded_models(cassettes)
    if not res_path.exists() or model not in models:
        pytest.skip("no committed model results or recordings")
    from mirror.scenes import reference_scene
    committed = [r for r in json.loads(res_path.read_text())["runs"] if r["interpreter"] != "offline-rules"]
    assert committed, "results have no model run"
    client = LLMClient(mode="replay", model=model, cassette_dir=cassettes,
                       transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(AssertionError("network"))))
    rep = run_benchmark(client, reference_scene("shortcut"),
                        load_cases(REPO / "mirror/interpret/benchmark" / cases_file))
    assert rep["summary"] == committed[0]["summary"]
    assert [c["result"]["outcome"] for c in rep["cases"]] == [c["result"]["outcome"] for c in committed[0]["cases"]]


def test_current_prompt_examples_overlap_no_case():
    from mirror.interpret.benchmark.overlap import overlap
    from mirror.interpret.prompts import EXAMPLE_PHRASES
    for f in ("cases.json", "cases_holdout.json"):
        for c in load_cases(REPO / "mirror/interpret/benchmark" / f)["cases"]:
            assert overlap(c["text"], EXAMPLE_PHRASES) == [], c["id"]
    from mirror.interpret.benchmark.score import load_base_spec  # noqa: F401
    ref = "This container must remain upright throughout transport, not just at the destination."
    assert overlap(ref, EXAMPLE_PHRASES) == []


def test_v1_prompt_overlapped_the_reference_correction():
    from mirror.interpret.benchmark.overlap import PROMPT_V1_EXAMPLES, overlap
    ref = "This container must remain upright throughout transport, not just at the destination."
    assert set(overlap(ref, PROMPT_V1_EXAMPLES)) == {"this container", "the destination"}
