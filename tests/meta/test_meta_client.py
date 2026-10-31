"""mirror.meta: recorded fixtures replay without network; cassette keys,
error handling and SAM parsing."""
import json

import httpx
import numpy as np
import pytest

from mirror.meta import (MetaAuthError, MetaCassetteMiss, MetaClient, MetaOffline, MetaQuotaError,
                         parse_sam_output, text_part)
from mirror.meta.cassette import normalize, request_key, sha256_bytes
from tests.meta import calls


def _no_network(request):
    raise AssertionError(f"network access in replay: {request.url}")


@pytest.fixture
def replay():
    return MetaClient(mode="replay", transport=httpx.MockTransport(_no_network))


def test_text_structured_output(replay):
    r = calls.text(replay)
    assert r.from_cassette and r.model == "muse-spark-1.3"
    assert r.json() == {"answer": "ok"}


needs_parser = pytest.mark.skipif(__import__("importlib").util.find_spec("meta_sam_parser") is None,
                                  reason="install the 'meta' extra (meta-sam-parser) to decode SAM output")


@needs_parser
def test_sam_image(replay):
    r = calls.sam_image(replay)
    assert (r.width, r.height) == (480, 270) and len(r.frames) == 1
    (obj,) = r.frames[0].objects
    assert obj.object_id == 0 and obj.box.left == 289 and obj.box.right == 301  # x2 inclusive -> exclusive
    assert obj.mask.shape == (obj.box.bottom - obj.box.top, obj.box.right - obj.box.left)
    assert 0 < obj.mask.sum() <= obj.box.area
    assert 0 < obj.confidence <= 1
    full = obj.full_mask(r.width, r.height)
    assert full.sum() == obj.mask.sum()


@needs_parser
def test_sam_video_tracks_one_object(replay):
    r = calls.sam_video(replay)
    assert r.media == "video" and [f.index for f in r.frames] == list(range(8))
    track = r.track(0)
    assert len(track) == 8
    # the cube is moved between the keyframes of the clip
    assert track[0].box.center != track[7].box.center


def test_video_understanding_uses_uploaded_file(replay):
    r = calls.video(replay)
    out = r.json()
    assert set(out) == {"objects", "moving"} and out["objects"]


def test_asr(replay):
    r = calls.asr(replay)
    assert r.text == "The cup must stay upright while it is carried."


def test_image_edit(replay):
    r = calls.edit(replay)
    assert len(r.images) == 1 and r.images[0][:8] == b"\x89PNG\r\n\x1a\n"


def test_replay_miss_and_offline():
    c = MetaClient(mode="replay", transport=httpx.MockTransport(_no_network))
    with pytest.raises(MetaCassetteMiss):
        c.responses("muse-spark-1.3", [text_part("never recorded")])
    with pytest.raises(MetaOffline):
        MetaClient(mode="offline").responses("muse-spark-1.3", [text_part("x")])


def test_key_ignores_server_ids_and_inline_encoding():
    a = normalize({"x": "data:image/png;base64," + __import__("base64").b64encode(b"abc").decode()}, {})
    assert a == {"x": "data:image/png;base64,sha256:" + sha256_bytes(b"abc")}
    assert normalize({"file_id": "file-1"}, {"file-1": "sha256:aa"}) == normalize({"file_id": "file-2"},
                                                                               {"file-2": "sha256:aa"})
    assert request_key("m", "/e", {"a": 1}) != request_key("m2", "/e", {"a": 1})


def test_record_mode_writes_cassette_then_replays(tmp_path):
    def server(request):
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"status": "completed", "model": body["model"], "usage": {"input_tokens": 3},
                                         "output": [{"type": "message", "content": [
                                             {"type": "output_text", "text": '{"answer": "yes"}'}]}]})
    rec = MetaClient(mode="record", api_key="test-key", cassette_dir=tmp_path, transport=httpx.MockTransport(server),
                     usage_log=tmp_path / "usage.jsonl")
    r = rec.responses("muse-spark-1.3", [text_part("q")], schema=calls.TINY_SCHEMA)
    assert not r.from_cassette and r.json() == {"answer": "yes"}
    files = list(tmp_path.rglob("*.json"))
    assert len(files) == 1 and "test-key" not in files[0].read_text()
    assert "test-key" not in (tmp_path / "usage.jsonl").read_text()
    rep = MetaClient(mode="replay", cassette_dir=tmp_path, transport=httpx.MockTransport(_no_network))
    assert rep.responses("muse-spark-1.3", [text_part("q")], schema=calls.TINY_SCHEMA).json() == {"answer": "yes"}


@pytest.mark.parametrize("status,exc", [(401, MetaAuthError), (403, MetaAuthError), (402, MetaQuotaError),
                                        (429, MetaQuotaError)])
def test_errors_stop(tmp_path, status, exc):
    hits = []

    def server(request):
        hits.append(1)
        return httpx.Response(status, text="nope")
    c = MetaClient(mode="live", api_key="k", cassette_dir=tmp_path, transport=httpx.MockTransport(server),
                   backoff_s=0.0, max_retries=2)
    with pytest.raises(exc):
        c.responses("muse-spark-1.3", [text_part("q")])
    assert len(hits) == (3 if status == 429 else 1)


@needs_parser
def test_parse_sam_sparse_ids_and_skipped_frames():
    text = calls_text = (
        "<0f>0<|box;x1=1;y1=1;x2=2;y2=2;w=10;h=10|><|mask;x=0;y=0;data=2,2,!!!!!!|>,2<|box;x1=5;y1=5;x2=6;y2=6;"
        "w=10;h=10|><|mask;x=0;y=0;data=2,2,!!!!!!|>\n"
        "<3f>2<|box;x1=6;y1=6;x2=7;y2=7;w=10;h=10|><|mask;x=0;y=0;data=2,2,!!!!!!|>\n")
    r = parse_sam_output(text, media="video", decode_masks=False)
    assert [f.index for f in r.frames] == [0, 3]
    assert r.object_ids == [0, 2] and sorted(r.track(2)) == [0, 3]
    assert parse_sam_output("", media="image").empty


@needs_parser
def test_box_only_parse_matches_decoder(replay):
    full = calls.sam_video(replay)
    fast = parse_sam_output(full.raw_text, media="video", decode_masks=False)
    assert [(f.index, [(o.object_id, o.box, o.confidence) for o in f.objects]) for f in fast.frames] == \
           [(f.index, [(o.object_id, o.box, o.confidence) for o in f.objects]) for f in full.frames]
    assert (fast.width, fast.height) == (full.width, full.height)
