"""Video observations (Muse Spark / Muse Wonder) and their optional use in proposals."""
import gzip
from pathlib import Path

import httpx
import pytest

from mirror.contracts import DemoTrace
from mirror.interpret import LLMClient, propose_spec_detailed
from mirror.interpret.auto import proposal_recorded
from mirror.interpret.llm import strict_schema
from mirror.interpret.prompts import propose_messages
from mirror.interpret.summary import summarize_demo
from mirror.interpret.video_observe import (ObservationDraft, ObservedEvent, check_observations, observe)
from mirror.meta import MetaClient

REPO = Path(__file__).resolve().parents[2]
CLIP = REPO / "tests" / "meta" / "fixtures" / "keyframes_clip.mp4"


def _no_net(r):
    raise AssertionError("network")


def _pinned():
    from mirror.evaluate.specs import PINNED_DEMO
    return DemoTrace.model_validate_json(gzip.decompress(PINNED_DEMO.read_bytes()))


@pytest.mark.parametrize("model", ["muse-spark-1.3", "muse-wonder-1.0"])
def test_observe_replays(model):
    obs = observe(CLIP, model, MetaClient(mode="replay", transport=httpx.MockTransport(_no_net)), duration_s=2.0,
                  strategy="video")
    assert obs.model == model and obs.from_cassette and obs.video_name == "keyframes_clip.mp4"
    assert obs.objects and {o.kind for o in obs.objects} <= {"cup", "block", "marker", "tray", "wall", "gripper", "other"}
    assert isinstance(obs.issues, list)
    assert set(obs.prompt_view()) >= {"source", "objects", "events", "care_cues", "uncertainties"}


def test_schema_is_strict():
    sch = strict_schema(ObservationDraft)
    assert sch["additionalProperties"] is False and set(sch["required"]) == set(sch["properties"])


def test_checks_flag_bad_times():
    d = ObservationDraft(summary="s", objects=[], care_cues=[], uncertainties=[], events=[
        ObservedEvent(type="carry", object="cup", t_start=3.0, t_end=1.0, evidence=""),
        ObservedEvent(type="grasp", object="cup", t_start=9.0, t_end=9.0, evidence="")])
    issues = check_observations(d, duration_s=2.0)
    assert any("after t_end" in i for i in issues) and any("outside the video" in i for i in issues)
    assert any("not in the object list" in i for i in issues)


def test_no_observations_keeps_requests_identical():
    tr = _pinned()
    s = summarize_demo(tr)
    assert propose_messages(s, tr.scene) == propose_messages(s, tr.scene, None, None)
    # the recorded proposal requests still match (development model and Muse Spark)
    assert proposal_recorded(tr, "gpt-5.5", REPO / "data" / "cassettes")
    assert proposal_recorded(tr, "muse-spark-1.3", REPO / "data" / "cassettes_muse")


def test_observations_change_only_the_prompt_and_replay():
    tr = _pinned()
    obs = observe(CLIP, "muse-spark-1.3", MetaClient(mode="replay", transport=httpx.MockTransport(_no_net)),
                  strategy="video")
    s = summarize_demo(tr)
    with_obs = propose_messages(s, tr.scene, None, obs.prompt_view())
    assert "video_observations" in with_obs[1]["content"] and "video_observations" in with_obs[0]["content"]
    client = LLMClient(mode="replay", model="muse-spark-1.3", cassette_dir=REPO / "data" / "cassettes_muse",
                       transport=httpx.MockTransport(_no_net))
    it = propose_spec_detailed(tr, client, observations=obs)
    assert it.from_cassette and it.observations_model == "muse-spark-1.3"
    assert all(c.status.value == "proposed" for c in it.spec.constraints)  # observations never confirm anything


def test_frame_sampling_and_slowing():
    from mirror.interpret.video_observe import _slowed, sample_frames, video_duration
    frames = sample_frames(CLIP, 0.5, width=240)
    assert [round(t, 2) for t, _ in frames] == [0.0, 0.5, 1.0, 1.5] and frames[0][1][:2] == b"\xff\xd8"
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        f.write(_slowed(CLIP, 4.0))
        f.flush()
        assert abs(video_duration(f.name) - 4 * video_duration(CLIP)) < 0.3


def test_rescale_and_timing_errors():
    from mirror.interpret.video_observe import VideoObservations, _rescale, event_timing_errors
    d = ObservationDraft(summary="s", objects=[], care_cues=[], uncertainties=[], events=[
        ObservedEvent(type="grasp", object="cup", t_start=8.0, t_end=8.0, evidence=""),
        ObservedEvent(type="release", object="cup", t_start=20.0, t_end=20.0, evidence="")])
    r = _rescale(d, 4.0)
    assert [e.t_start for e in r.events] == [2.0, 5.0]
    obs = VideoObservations(**r.model_dump(), model="m", requested_model="m", video_sha256="x", video_name="v")
    gt = [{"t": 2.5, "type": "grasp", "object_id": "cup_0"}, {"t": 4.0, "type": "release", "object_id": "cup_0"},
          {"t": 6.0, "type": "grasp", "object_id": "block_0"}]
    m = event_timing_errors(obs, gt, {"cup_0": "cup", "block_0": "block"})
    assert m["matched"] == 2 and m["total"] == 3 and m["mean_abs_error_s"] == 0.75 and not m["order_correct"]
