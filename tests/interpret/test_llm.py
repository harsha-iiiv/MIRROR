import json

import pytest
from pydantic import BaseModel

from mirror.interpret.llm import CassetteMiss, LLMClient, LLMError, strict_schema
from tests.interpret.fake_api import FakeAPI


class Point(BaseModel):
    x: int
    label: str | None = None


def client(api, tmp_path, mode="live", **kw):
    return LLMClient(base_url="http://fake/v1", model="m-1", api_key="secret-key-123", mode=mode,
                     cassette_dir=tmp_path / "cas", transport=api.transport(), backoff_s=0.0, **kw)


MSG = [{"role": "user", "content": "give x"}]


def test_structured_output_and_usage(tmp_path):
    api = FakeAPI([{"x": 3, "label": None}])
    c = client(api, tmp_path)
    r = c.complete_json(MSG, Point, name="pt")
    assert r.data.x == 3 and r.model == "fake-model-2026" and r.format_used == "json_schema"
    body = api.requests[0]["body"]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert api.requests[0]["headers"]["authorization"] == "Bearer secret-key-123"
    assert c.usage[-1]["total_tokens"] == 150 and c.usage[-1]["model"] == "fake-model-2026"


def test_strict_schema_closes_objects():
    s = strict_schema(Point)
    assert s["additionalProperties"] is False and set(s["required"]) == {"x", "label"}
    assert "default" not in json.dumps(s) and "title" not in json.dumps(s)


def test_falls_back_to_json_object(tmp_path):
    api = FakeAPI([(400, {"error": {"message": "response_format json_schema is not supported"}}),
                   {"x": 1, "label": "a"}])
    c = client(api, tmp_path)
    r = c.complete_json(MSG, Point, name="pt")
    assert r.data.x == 1 and r.format_used == "json_object"
    second = api.requests[1]["body"]
    assert second["response_format"] == {"type": "json_object"}
    assert "JSON schema" in second["messages"][-1]["content"]
    api.replies.append({"x": 2, "label": None})
    assert c.complete_json(MSG, Point, name="pt").format_used == "json_object"  # remembered
    assert len(api.requests) == 3


def test_repair_round(tmp_path):
    api = FakeAPI([{"label": "missing x"}, {"x": 5, "label": None}])
    r = client(api, tmp_path).complete_json(MSG, Point, name="pt")
    assert r.data.x == 5 and r.attempts == 2
    repair = api.requests[1]["body"]["messages"][-1]["content"]
    assert "rejected by the validator" in repair and "x" in repair


def test_semantic_validator_and_failure_after_repair(tmp_path):
    api = FakeAPI([{"x": -1, "label": None}, {"x": -2, "label": None}])
    with pytest.raises(LLMError):
        client(api, tmp_path).complete_json(MSG, Point, name="pt",
                                            validate=lambda d: [] if d.x > 0 else ["x must be positive"])
    assert "x must be positive" in api.requests[1]["body"]["messages"][-1]["content"]


def test_retries_server_errors(tmp_path):
    api = FakeAPI([(503, "busy"), (500, "oops"), {"x": 7, "label": None}])
    assert client(api, tmp_path).complete_json(MSG, Point, name="pt").data.x == 7


def test_auth_error_not_retried(tmp_path):
    api = FakeAPI([(401, "bad key"), {"x": 7, "label": None}])
    with pytest.raises(LLMError, match="authentication"):
        client(api, tmp_path).complete_json(MSG, Point, name="pt")
    assert len(api.requests) == 1


def test_record_then_replay(tmp_path):
    api = FakeAPI([{"x": 9, "label": "rec"}])
    rec = client(api, tmp_path, mode="record")
    r1 = rec.complete_json(MSG, Point, name="pt")
    files = list((tmp_path / "cas").glob("*.json"))
    assert len(files) == 1
    raw = files[0].read_text()
    assert "secret-key-123" not in raw and "Bearer" not in raw
    cas = json.loads(raw)
    assert cas["response"]["model"] == "fake-model-2026" and cas["key"] == r1.key
    dead = FakeAPI([])
    rep = client(dead, tmp_path, mode="replay")
    r2 = rep.complete_json(MSG, Point, name="pt")
    assert r2.data == r1.data and r2.from_cassette and r2.model == "fake-model-2026"
    assert dead.requests == []
    with pytest.raises(CassetteMiss):
        rep.complete_json([{"role": "user", "content": "different"}], Point, name="pt")


def test_offline_client_refuses_calls(tmp_path):
    c = LLMClient(mode="offline")
    assert c.offline and c.label == "offline-rules"
    with pytest.raises(LLMError):
        c.complete_json(MSG, Point, name="pt")


def test_from_env(monkeypatch, tmp_path):
    c = LLMClient.from_env()
    assert c.mode == "offline" and c.base_url == "https://api.meta.ai/v1" and c.model == "muse-spark-1.3"
    monkeypatch.setenv("MODEL_API_KEY", "zz-key-qq")
    monkeypatch.setenv("MIRROR_LLM_MODEL", "other")
    monkeypatch.setenv("MIRROR_LLM_USAGE_LOG", str(tmp_path / "u.jsonl"))
    c = LLMClient.from_env()
    assert c.mode == "live" and c.model == "other" and c.api_key == "zz-key-qq"
    assert "zz-key-qq" not in repr(c)
    monkeypatch.setenv("MIRROR_LLM_MODE", "bogus")
    with pytest.raises(ValueError):
        LLMClient.from_env()


def test_usage_log_file(tmp_path):
    api = FakeAPI([{"x": 1, "label": None}])
    c = client(api, tmp_path, usage_log_path=tmp_path / "usage.jsonl")
    c.complete_json(MSG, Point, name="pt")
    line = json.loads((tmp_path / "usage.jsonl").read_text().strip())
    assert line["call"] == "pt" and line["prompt_tokens"] == 100 and "secret" not in json.dumps(line)
