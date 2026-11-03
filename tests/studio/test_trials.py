"""Consented trial recording and export (M2 section 4) on the fixture backend."""
import csv
import json
import time

import pytest
from fastapi.testclient import TestClient

from mirror.studio.app import create_app
from mirror.studio.session_adapter import REFERENCE_CORRECTION, StudioService
from mirror.studio.sessions import SessionManager
from mirror.studio.trials import export, load_trials, purge_expired_audio

REQUIRED = ("format", "trial_id", "participant", "synthetic", "context", "task_origin", "task", "condition", "attempt",
            "started_at", "ended_at", "task_started_at", "versions", "scene", "lineage", "input", "proposal",
            "clarification", "review", "spec_state_before", "spec_state_after", "recheck", "execution",
            "failure_reason", "comprehension")


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRROR_TRIAL_STORE", str(tmp_path / "trials"))
    monkeypatch.setenv("MIRROR_STUDY_LOG", str(tmp_path / "events.jsonl"))
    mgr = SessionManager(lambda sid, m: StudioService(backend="fixture", session_id=sid, gate=m.gate, budget=m.budget,
                                                      one_job=True, private_runtime=True))
    with TestClient(create_app(mgr)) as c:
        r = c.post("/api/session").json()
        c.headers.update({"X-Mirror-Session": r["session"], "X-Mirror-Token": r["token"]})
        c.tmp = tmp_path
        yield c


def wait(c, job):
    t0 = time.time()
    while True:
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("done", "error"):
            return j
        assert time.time() - t0 < 30
        time.sleep(0.02)


def challenge(c):
    return wait(c, c.post("/api/challenge", json={"family": "reference", "preset": "shortcut"}).json())["result"]["challenge_id"]


def test_nothing_is_recorded_without_study_mode(api):
    c = api
    cid = challenge(c)
    wait(c, c.post(f"/api/challenge/{cid}/correction", json={"text": REFERENCE_CORRECTION}).json())
    assert not (c.tmp / "trials").exists() and not (c.tmp / "events.jsonl").exists()


@pytest.mark.parametrize("cfg,msg", [
    ({"participant": "P03", "context": "usability_pilot", "consent": {}}, "consent"),
    ({"participant": "Alice", "consent": {"recorded": True}}, "anonymous"),
    ({"participant": "P03", "context": "frozen_benchmark", "consent": {"recorded": True}}, "frozen"),
    ({"participant": "P03", "context": "dev_smoke", "consent": {"recorded": True}}, "DEV"),
    ({"participant": "DEV", "context": "usability_pilot", "consent": {"recorded": True}}, "anonymous participant"),
])
def test_study_mode_refuses_bad_configurations(api, cfg, msg):
    r = api.post("/api/study/start", json=cfg)
    assert r.status_code == 422 and msg in r.json()["detail"]


def test_a_full_attempt_is_recorded_and_exported(api):
    c = api
    st = c.post("/api/study/start", json={"participant": "P03", "context": "usability_pilot", "task": "C1",
                                           "task_origin": "prepared", "consent": {"recorded": True}}).json()
    assert st["recording_trials"] and not st["audio_stored"]
    cid = challenge(c)
    prop = wait(c, c.post(f"/api/challenge/{cid}/correction", json={"text": REFERENCE_CORRECTION}).json())["result"]
    tid = prop["trial_id"]
    assert tid
    c.post(f"/api/challenge/{cid}/review", json={"accept": True})
    wait(c, c.post(f"/api/challenge/{cid}/execute").json())
    assert c.post("/api/study/comprehension", json={"trial_id": tid, "answer": "fresh"}).json()["truth"] == "fixture"
    recs = load_trials(c.tmp / "trials")
    assert len(recs) == 1
    rec = recs[0]
    assert set(REQUIRED) <= set(rec)
    assert rec["participant"] == "P03" and rec["context"] == "usability_pilot" and rec["task"] == "C1"
    assert rec["input"]["kind"] == "typed" and rec["input"]["chars"] == len(REFERENCE_CORRECTION)
    assert "text" not in rec["input"], "no text without text consent"
    assert rec["proposal"]["outcome"] == "apply" and rec["proposal"]["validation"]["passed"]
    assert rec["review"]["accepted"] and rec["lineage"]["revised_spec_version_id"].endswith(".v2")
    assert rec["recheck"]["status"] == "constraint_violated" and rec["execution"]["status"] == "success"
    assert rec["execution"]["proven_infeasible"] is False and rec["ended_at"] is not None
    assert rec["versions"]["code_commit"] and rec["scene"]["scene_id"]
    raw = json.dumps(rec)
    assert "testclient" not in raw and "X-Mirror" not in raw          # no address or headers
    out = export(c.tmp / "trials", c.tmp / "export")
    assert out["trials"] == 1
    rows = list(csv.DictReader(open(c.tmp / "export" / "trials.csv")))
    assert rows[0]["participant"] == "P03" and "input.text" not in rows[0]
    assert "usability_pilot (1 attempts)" in (c.tmp / "export" / "summary.md").read_text()
    events = [json.loads(x) for x in (c.tmp / "events.jsonl").read_text().splitlines()]
    names = [e["event"] for e in events]
    assert names[:2] == ["session_start", "correction_submitted"]
    assert {"interpretation_returned", "spec_version_created", "recheck_run", "execution_run"} <= set(names)
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert all(e["schema"] == "mirror-study-events/1" and e["participant"] == "P03" for e in events)


def test_text_is_kept_only_with_text_consent(api):
    c = api
    c.post("/api/study/start", json={"participant": "DEV", "consent": {"recorded": True, "text": True}})
    cid = challenge(c)
    wait(c, c.post(f"/api/challenge/{cid}/correction", json={"text": "do a backflip"}).json())
    rec = load_trials(c.tmp / "trials", "dev_smoke")[0]
    assert rec["input"]["text"] == "do a backflip" and rec["proposal"]["outcome"] in ("rejects", "asks")
    assert rec["context"] == "dev_smoke" and rec["task_origin"] == "developer_written" and rec["ended_at"]


def test_contexts_are_stored_apart(api):
    c = api
    c.post("/api/study/start", json={"participant": "DEV", "consent": {"recorded": True}})
    cid = challenge(c)
    wait(c, c.post(f"/api/challenge/{cid}/correction", json={"text": REFERENCE_CORRECTION}).json())
    c.post("/api/study/start", json={"participant": "P07", "consent": {"recorded": True}})
    cid = challenge(c)
    wait(c, c.post(f"/api/challenge/{cid}/correction", json={"text": REFERENCE_CORRECTION}).json())
    assert [r["participant"] for r in load_trials(c.tmp / "trials", "dev_smoke")] == ["DEV"]
    assert [r["participant"] for r in load_trials(c.tmp / "trials", "usability_pilot")] == ["P07"]


def test_manual_condition_records_a_manual_attempt(api):
    c = api
    c.post("/api/demo/load")
    wait(c, c.post("/api/interpret").json())
    c.post("/api/study/start", json={"participant": "P04", "condition": "manual", "task": "C2",
                                      "consent": {"recorded": True}})
    r = c.post("/api/spec/manual", json={"edits": [{"action": "set_status", "id": "c_upright_transport",
                                                    "status": "confirmed"}]})
    assert r.status_code == 200, r.text
    assert r.json()["spec_version_id"].endswith(".v2") and r.json()["step_provenance"]["kind"] == "manual"
    bad = c.post("/api/spec/manual", json={"edits": [{"action": "set_status", "id": "nope", "status": "confirmed"}]})
    assert bad.status_code == 409
    recs = load_trials(c.tmp / "trials", "usability_pilot")
    assert [x["proposal"]["outcome"] for x in recs] == ["apply", "error"]
    assert recs[0]["condition"] == "manual" and recs[0]["input"]["kind"] == "manual_form" and recs[0]["attempt"] == 1
    assert recs[1]["attempt"] == 2 and recs[1]["failure_reason"] == "invalid manual edit"


def test_audio_is_not_stored_by_default_and_expires_when_it_is(tmp_path, monkeypatch):
    from mirror.studio.trials import StudyRecorder
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF....")
    rec = StudyRecorder({"participant": "P01", "consent": {"recorded": True, "audio": True}}, tmp_path / "s", None)
    assert not rec.keeps_audio                             # consent alone: no retention policy, no audio
    monkeypatch.setenv("MIRROR_STUDY_AUDIO_RETENTION_DAYS", "7")
    rec = StudyRecorder({"participant": "P01", "consent": {"recorded": True, "audio": True}}, tmp_path / "s", None)
    t = rec.start_attempt(versions={}, scene={}, lineage={}, input_={"kind": "spoken"}, text="x", spec_before=None)
    meta = rec.keep_audio(t["trial_id"], wav)
    assert meta["retention_days"] == 7 and (tmp_path / "s" / "usability_pilot" / meta["file"]).exists()
    assert purge_expired_audio(tmp_path / "s", now=time.time() + 8 * 86400) == [t["trial_id"]]
    assert not (tmp_path / "s" / "usability_pilot" / meta["file"]).exists()
    rec2 = StudyRecorder({"participant": "P02", "consent": {"recorded": True, "audio": False}}, tmp_path / "s", None)
    t2 = rec2.start_attempt(versions={}, scene={}, lineage={}, input_={"kind": "spoken"}, text="x", spec_before=None)
    assert rec2.keep_audio(t2["trial_id"], wav) is None
