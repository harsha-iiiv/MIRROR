"""The deterministic absent-object validation layer."""
import json
from pathlib import Path

import httpx
import pytest

from mirror.interpret import LLMClient, NeedsClarification, compile_correction
from mirror.interpret.auto import recorded_models
from mirror.interpret.benchmark import load_base_spec, load_cases, run_benchmark
from mirror.interpret.benchmark.score import with_validation_layer
from mirror.interpret.guard import absent_object_question
from mirror.scenes import reference_scene

REPO = Path(__file__).resolve().parents[2]
SCENE = reference_scene("shortcut")


@pytest.mark.parametrize("text", ["The jar must not be tilted.", "Keep the bowls upright.",
                                  "The spoon goes in first."])
def test_absent_object_asks(text):
    q = absent_object_question(text, load_base_spec(), SCENE)
    assert q is not None and "the cup, the block, the marker" in q.question


@pytest.mark.parametrize("text", [
    "This container must remain upright throughout transport, not just at the destination.",
    "Pack the phone before the cup.",          # a present object is named too: interpreter decides
    "The marker can tilt.", "It can tilt.",    # modal 'can' is not an object
    "Keep the cup away from the wall.", "Move faster."])
def test_no_trigger(text):
    assert absent_object_question(text, load_base_spec(), SCENE) is None


def test_scene_box_obstacle_is_present():
    scene = SCENE.model_copy(deep=True)
    from mirror.contracts import ObstacleSpec, Pose
    scene.obstacles.append(ObstacleSpec(id="box_0", kind="box", pose=Pose(pos=(0.2, 0.2, 0.05)),
                                        half=(0.03, 0.03, 0.05)))
    assert absent_object_question("Keep away from the box.", load_base_spec(), scene) is None


def test_layer_runs_on_offline_path():
    spec = load_base_spec()
    with pytest.raises(NeedsClarification):
        compile_correction(spec, "The jar must not be tilted.", None, {"scene": SCENE})


def test_layer_key_is_not_sent_to_the_model():
    from mirror.interpret.correct import _model_context
    assert _model_context({"scene": SCENE, "validation_layer": False, "focus_kind": "cup"}) == {"focus_kind": "cup"}


def test_post_hoc_layer_equals_replay_with_layer():
    """Applying the layer to stored outputs equals replaying the recorded
    model with the layer switched on (no new model calls)."""
    res_path = REPO / "results" / "corrections_benchmark_holdout_after_prompt_revision.json"
    cas = REPO / "data" / "cassettes" / "corrections_benchmark"
    models = recorded_models(cas)
    if not res_path.exists() or not models:
        pytest.skip("no recorded results")
    stored = [r for r in json.loads(res_path.read_text())["runs"] if r["interpreter"] != "offline-rules"][0]
    client = LLMClient(mode="replay", model=next(iter(models)), cassette_dir=cas,
                       transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(AssertionError("network"))))
    live = run_benchmark(client, SCENE, load_cases(REPO / "mirror/interpret/benchmark/cases_holdout.json"),
                         validation_layer=True)
    post = with_validation_layer(stored, SCENE)
    assert [c["result"]["outcome"] for c in live["cases"]] == [c["result"]["outcome"] for c in post["cases"]]
    assert live["summary"]["exact"] == post["summary"]["exact"]
    assert post["summary"]["false_accepts"] == 0
