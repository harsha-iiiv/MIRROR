"""Challenge mode API (C3 backend) on the fixture backend, plus the frozen-seed fence."""
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mirror.studio.app import create_app
from mirror.studio.challenge import (CHALLENGE_SEED_RANGE, DEV_SEED_LIMIT, FenceError, check_challenge_seed,
                                     fenced_seeds)
from mirror.studio.records import RecordStore
from mirror.studio.session_adapter import REFERENCE_CORRECTION, StudioService
from mirror.studio.sessions import SessionManager

REPO = Path(__file__).resolve().parents[2]


def manager(**kw):
    return SessionManager(lambda sid, m: StudioService(backend="fixture", session_id=sid, gate=m.gate,
                                                       budget=m.budget, one_job=True, private_runtime=True), **kw)


@pytest.fixture()
def api():
    with TestClient(create_app(manager())) as c:
        r = c.post("/api/session").json()
        c.headers.update({"X-Mirror-Session": r["session"], "X-Mirror-Token": r["token"]})
        yield c


def wait(c, job, timeout=30):
    t0 = time.time()
    while True:
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("done", "error", "cancelled"):
            return j
        assert time.time() - t0 < timeout
        time.sleep(0.02)


def frozen_layout_seeds():
    """Independent re-read of every seed list in every frozen evaluation manifest."""
    out = set()
    for f in REPO.glob("results/*/freeze.json"):
        d = json.loads(f.read_text())
        out.update(d.get("seeds") or [])
        for st in d.get("strata") or []:
            out.update(st.get("seeds") or [])
        for n in ("design.json",):
            p = f.parent / n
            if p.exists():
                dd = json.loads(p.read_text())
                out.update(dd.get("seeds") or [])
                out.update((dd.get("frames") or {}).get("seeds") or [])
    return out


def test_every_frozen_evaluation_seed_is_fenced():
    frozen = frozen_layout_seeds()
    assert len(frozen) >= 130
    assert frozen <= set(fenced_seeds())
    for s in sorted(frozen):
        with pytest.raises(FenceError):
            check_challenge_seed("mixed", s)


def test_fence_bounds():
    with pytest.raises(FenceError):
        check_challenge_seed("shortcut", DEV_SEED_LIMIT)
    with pytest.raises(FenceError):
        check_challenge_seed("mixed", CHALLENGE_SEED_RANGE[1] + 1)          # showcase range
    with pytest.raises(ValueError):
        check_challenge_seed("eval", 5)
    check_challenge_seed("shortcut", 4242)


def test_options(api):
    o = api.get("/api/challenge/options").json()
    assert {f["id"] for f in o["families"]} == {"mixed", "shortcut", "blocked", "reference"}
    assert o["fenced_seed_count"] >= 130 and any("freeze.json" in s for s in o["fence_sources"])
    assert {r["type"] for r in o["correction"]["supported"]} >= {"upright", "ordering", "keep_clearance"}
    assert "never executed as a free-form command" in o["correction"]["note"]


def test_fenced_seed_is_refused_over_http(api):
    r = api.post("/api/challenge", json={"family": "mixed", "seed": 30001})
    assert r.status_code == 403 and r.json()["error"] == "evaluation_fenced"
    r = api.post("/api/challenge", json={"family": "mixed", "seed": 50003})
    assert r.status_code == 403
    r = api.post("/api/challenge", json={"family": "mixed", "seed": 1, "edits": []})
    assert r.status_code == 403                                             # named in a frozen design


def test_full_challenge_flow_and_shared_record(api):
    c = api
    j = wait(c, c.post("/api/challenge", json={"family": "reference", "preset": "shortcut",
                                                "edits": [{"tray": {"x": 0.26, "y": -0.10}}]}).json())
    assert j["status"] == "done", j["error"]
    cid, before = j["result"]["challenge_id"], j["result"]["before_rollout_id"]
    ch = c.get(f"/api/challenge/{cid}").json()
    assert ch["state"] == "scene_ready" and ch["before"]["rollout_id"] == before
    assert ch["scene"]["edits"] and ch["scene"]["scene"]["split"] == "custom" and len(ch["scene"]["content_sha256"]) == 64
    assert [p["step"] for p in ch["preparation"]] == ["demonstration loaded", "demonstration interpreted"]
    n_specs = len(c.get("/api/state").json()["specs"])
    # review comes before commitment
    j = wait(c, c.post(f"/api/challenge/{cid}/correction", json={"text": REFERENCE_CORRECTION}).json())
    prop = j["result"]
    assert prop["changed"] and prop["input"] == "typed" and prop["provenance"] in ("fixture", "offline_interpreter")
    assert "cup_0" in prop["scope"]["objects"] and {"block_0", "marker_0"} <= set(prop["scope"]["unaffected_objects"])
    assert prop["diff"]["changed"] and "transport" in prop["scope"]["phases"]
    assert len(c.get("/api/state").json()["specs"]) == n_specs, "a proposal must not be committed before review"
    assert c.post(f"/api/challenge/{cid}/execute").status_code == 409        # not reviewed yet
    rv = c.post(f"/api/challenge/{cid}/review", json={"accept": True}).json()
    assert rv["state"] == "reviewed" and rv["revised_spec_version_id"].endswith(".v2")
    rb = rv["rechecks"][0]
    assert rb["is_before"] and rb["rollout_id"] == before and rb["status"] == "constraint_violated" and rb["violations"]
    j = wait(c, c.post(f"/api/challenge/{cid}/execute").json())
    after = j["result"]["after_rollout_id"]
    assert j["result"]["status"] == "success" and after != before
    pub = c.post(f"/api/challenge/{cid}/publish").json()
    rid = pub["record_id"]
    assert pub["share_path"] == f"?challenge={rid}"
    assert c.post(f"/api/challenge/{cid}/publish").json()["record_id"] == rid      # idempotent
    # anyone can resolve the record, without a session
    with TestClient(c.app) as anon:
        rec = anon.get(f"/api/records/{rid}").json()
        assert rec["record_id"] == rid and rec["format"] == "mirror-challenge-record/1"
        assert rec["rollouts"]["before"]["rollout_id"] == before and rec["rollouts"]["after"]["rollout_id"] == after
        assert rec["spec_versions"]["base"]["version_id"].endswith(".v1")
        assert rec["spec_versions"]["revised"]["version_id"] == rv["revised_spec_version_id"]
        assert rec["provenance"]["simulation"] == "fixture" and rec["provenance"]["recorded"] is False
        assert rec["correction"]["text"] == REFERENCE_CORRECTION
        p = anon.get(f"/api/records/{rid}/rollouts/{after}").json()
        assert p["id"] == after and p["generation_id"] == rec["generation"]["id"]
        assert anon.get(f"/api/records/{rid}/rollouts/nope").status_code == 404
        assert anon.get("/api/records/0000000000000000").status_code == 404


def test_a_rejected_review_discards_the_proposal(api):
    c = api
    cid = wait(c, c.post("/api/challenge", json={"family": "reference", "preset": "shortcut"}).json())["result"]["challenge_id"]
    wait(c, c.post(f"/api/challenge/{cid}/correction", json={"text": REFERENCE_CORRECTION}).json())
    n = len(c.get("/api/state").json()["specs"])
    rv = c.post(f"/api/challenge/{cid}/review", json={"accept": False}).json()
    assert rv["state"] == "scene_ready" and rv["proposal"]["decision"] == "discarded"
    assert len(c.get("/api/state").json()["specs"]) == n
    assert c.post(f"/api/challenge/{cid}/review", json={"accept": True}).status_code == 409


def test_a_correction_outside_the_rule_language_is_not_executed(api):
    c = api
    cid = wait(c, c.post("/api/challenge", json={"family": "mixed", "seed": 4242}).json())["result"]["challenge_id"]
    prop = wait(c, c.post(f"/api/challenge/{cid}/correction", json={"text": "do a backflip"}).json())["result"]
    assert not prop["changed"] and (prop["rejected"] or prop["question"])
    assert c.post(f"/api/challenge/{cid}/review", json={"accept": True}).status_code == 409


def test_a_new_demonstration_makes_a_challenge_stale(api):
    c = api
    cid = wait(c, c.post("/api/challenge", json={"family": "mixed", "seed": 4242}).json())["result"]["challenge_id"]
    c.post("/api/demo/load")
    r = c.post(f"/api/challenge/{cid}/correction", json={"text": REFERENCE_CORRECTION})
    assert r.status_code == 409 and r.json()["error"] == "stale_generation"


def test_record_store_is_content_addressed_bounded_and_tombstoned(tmp_path):
    st = RecordStore(tmp_path, max_records=2, max_bytes=10**9)
    a = st.put({"x": 1}, {"r1": {"id": "r1"}})
    assert a == st.put({"x": 1}, {"r1": {"id": "r1"}}) and st.get(a)["x"] == 1
    time.sleep(0.01)
    b = st.put({"x": 2}, {})
    time.sleep(0.01)
    st.put({"x": 3}, {})
    from mirror.studio.records import RecordExpired
    with pytest.raises(RecordExpired):
        st.get(a)
    assert st.get(b)["x"] == 2
    with pytest.raises(KeyError):
        st.get("../../etc")


def test_records_survive_the_session_and_a_restart(tmp_path):
    with TestClient(create_app(manager(state_dir=tmp_path))) as c:
        r = c.post("/api/session").json()
        c.headers.update({"X-Mirror-Session": r["session"], "X-Mirror-Token": r["token"]})
        cid = wait(c, c.post("/api/challenge", json={"family": "reference", "preset": "shortcut"}).json())["result"]["challenge_id"]
        wait(c, c.post(f"/api/challenge/{cid}/correction", json={"text": REFERENCE_CORRECTION}).json())
        c.post(f"/api/challenge/{cid}/review", json={"accept": True})
        wait(c, c.post(f"/api/challenge/{cid}/execute").json())
        rid = c.post(f"/api/challenge/{cid}/publish").json()["record_id"]
        c.app.state.manager.close(r["session"], reason="idle for too long")
    with TestClient(create_app(manager(state_dir=tmp_path))) as c2:
        assert c2.get(f"/api/records/{rid}").json()["record_id"] == rid


def test_challenge_state_survives_a_restart(tmp_path):
    with TestClient(create_app(manager(state_dir=tmp_path))) as c:
        r = c.post("/api/session").json()
        h = {"X-Mirror-Session": r["session"], "X-Mirror-Token": r["token"]}
        cid = wait_h(c, h, c.post("/api/challenge", json={"family": "reference", "preset": "shortcut"}, headers=h).json())
        wait_h(c, h, c.post(f"/api/challenge/{cid}/correction", json={"text": REFERENCE_CORRECTION}, headers=h).json())
    with TestClient(create_app(manager(state_dir=tmp_path))) as c2:
        ch = c2.get(f"/api/challenge/{cid}", headers=h).json()
        assert ch["state"] == "proposed" and ch["proposal"]["changed"]
        rv = c2.post(f"/api/challenge/{cid}/review", json={"accept": True}, headers=h).json()
        assert rv["state"] == "reviewed"


def wait_h(c, h, job):
    t0 = time.time()
    while True:
        j = c.get(f"/api/jobs/{job['id']}", headers=h).json()
        if j["status"] in ("done", "error"):
            assert j["status"] == "done", j["error"]
            return (j["result"] or {}).get("challenge_id")
        assert time.time() - t0 < 30
        time.sleep(0.02)
