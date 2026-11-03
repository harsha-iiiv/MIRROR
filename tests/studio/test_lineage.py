"""Demonstration lineage: generations, immutable ids, archive, stale work (A3)."""
import threading
import time

import pytest
from fastapi.testclient import TestClient

from mirror.lineage import new_generation_id, parse_rollout_id
from mirror.studio.app import create_app
from mirror.studio.session_adapter import REFERENCE_CORRECTION, StudioService
from mirror.studio.sessions import JobGate, SessionManager


def svc_fixture(**kw):
    return StudioService(backend="fixture", session_id="t", private_runtime=True, **kw)


def story(svc, scene="shortcut"):
    svc.load_demo()
    svc.wait(svc.interpret())
    svc.set_scene_preset(scene)
    j = svc.wait(svc.run(condition=scene))
    assert j.status == "done", j.error
    return j.result["rollout_id"]


def test_generation_ids_are_unique_and_increasing():
    ids = [new_generation_id() for _ in range(500)]
    assert len(set(ids)) == 500 and ids == sorted(ids)


def test_rollout_ids_are_immutable_lineage_ids():
    svc = svc_fixture()
    rid = story(svc)
    p = parse_rollout_id(rid)
    assert p and p["gen"] == svc.generation_id and p["version"] == 1 and p["label"] == "r001"
    snap = svc.snapshot()
    r = next(x for x in snap["rollouts"] if x["id"] == rid)
    assert r["label"] == "r001" and r["generation_id"] == svc.generation_id
    assert r["spec_version_id"] == f"{svc.generation_id}.v1" and len(r["content_sha256"]) == 64
    assert snap["specs"][0]["version_id"] == f"{svc.generation_id}.v1"
    assert snap["generation"]["demo"]["content_sha256"]


def test_demo_a_run_demo_b_run_never_mixes_a_into_b():
    """Acceptance: demo A -> run -> demo B -> run never shows A's motion or spec as B's."""
    svc = svc_fixture()
    rid_a = story(svc)
    gen_a = svc.generation_id
    pay_a = svc.rollout_payload(rid_a)
    demo_a = svc.rollout_payload("demo")
    svc.load_demo()                                    # demonstration B replaces A
    gen_b = svc.generation_id
    assert gen_b != gen_a
    snap = svc.snapshot()
    assert snap["specs"] == [] and snap["rollouts"] == [], "B must not inherit A's specs or rollouts"
    assert snap["archive"][-1]["id"] == gen_a and rid_a in snap["archive"][-1]["rollout_ids"]
    svc.wait(svc.interpret())
    svc.set_scene_preset("shortcut")
    rid_b = svc.wait(svc.run(condition="B")).result["rollout_id"]
    assert rid_b != rid_a and rid_b.startswith(gen_b + ".") and rid_a.startswith(gen_a + ".")
    pay_b = svc.rollout_payload(rid_b)
    assert pay_b["generation_id"] == gen_b and pay_b["spec_version_id"] == f"{gen_b}.v1"
    # A's evidence still resolves, unchanged, against A's own specification
    again = svc.rollout_payload(rid_a)
    assert again == pay_a and again["generation_id"] == gen_a and again["spec_version_id"] == f"{gen_a}.v1"
    assert "archived" not in again           # payloads are immutable: no mutable status inside them
    # the demo alias moved to B; A's demonstration playback keeps its own id
    demo_b = svc.rollout_payload("demo")
    assert demo_b["id"] != demo_a["id"] and demo_b["generation_id"] == gen_b
    assert svc.rollout_payload(demo_a["id"])["generation_id"] == gen_a
    assert [r["id"] for r in svc.snapshot()["rollouts"]] == [rid_b]


def test_reinterpretation_archives_versions_instead_of_reusing_them():
    svc = svc_fixture()
    rid = story(svc)
    gen1 = svc.generation_id
    svc.wait(svc.correct(REFERENCE_CORRECTION))
    rid_v2 = svc.wait(svc.run(condition="v2")).result["rollout_id"]
    assert parse_rollout_id(rid_v2)["version"] == 2
    old_v2 = svc.session.spec
    svc.wait(svc.interpret())                          # a fresh proposal: new generation
    gen2 = svc.generation_id
    assert gen2 != gen1 and [s.version for s in svc.session.specs] == [1]
    assert svc.snapshot()["rollouts"] == []
    # the old rollout re-checks against the version it ran under, not the new v1/v2
    rc = svc.recheck(rid_v2, 2)
    assert rc["spec_version_id"] == f"{gen1}.v2" and rc["generation_id"] == gen1 and not rc["cross_generation"]
    svc.wait(svc.correct(REFERENCE_CORRECTION))
    assert svc.session.spec_version_id() == f"{gen2}.v2" != f"{gen1}.v2"
    assert svc.session.resolve_spec_id(f"{gen1}.v2") is old_v2
    # an explicit cross-generation recheck is labelled as such
    cross = svc.recheck(rid, spec_version_id=f"{gen2}.v2")
    assert cross["cross_generation"] and any("cross-generation" in n for n in cross["report"]["notes"])


def test_run_ids_never_reused_after_reset():
    svc = svc_fixture()
    seen = {story(svc)}
    for _ in range(3):
        svc._reset_session()
        seen.add(story(svc))
    assert len(seen) == 4


def test_queued_job_from_an_obsolete_generation_does_not_run():
    gate = JobGate(max_jobs=1)
    svc = svc_fixture(gate=gate)
    svc.load_demo()
    svc.wait(svc.interpret())

    class Hold:
        queue_position = 0
    blocker = Hold()
    assert gate.acquire(blocker)                       # the simulator is busy elsewhere
    job = svc.run(condition="late")
    time.sleep(0.2)
    assert job.status == "queued"
    svc.load_demo()                                    # the visitor replaces the demonstration meanwhile
    gate.release()
    svc.wait(job)
    assert job.status == "error" and job.error_code == "stale_generation" and job.stale
    assert svc.snapshot()["rollouts"] == [] and svc.session.archive[-1].rollouts == {}


def test_generation_header_rejects_stale_requests():
    mgr = SessionManager(lambda sid, m: StudioService(backend="fixture", session_id=sid, gate=m.gate,
                                                      budget=m.budget, one_job=True, private_runtime=True))
    with TestClient(create_app(mgr)) as c:
        r = c.post("/api/session").json()
        h = {"X-Mirror-Session": r["session"], "X-Mirror-Token": r["token"]}
        g1 = c.post("/api/demo/load", headers=h).json()["generation"]["id"]
        g2 = c.post("/api/demo/load", headers={**h, "X-Mirror-Generation": g1}).json()["generation"]["id"]
        assert g2 != g1
        bad = c.post("/api/interpret", headers={**h, "X-Mirror-Generation": g1})
        assert bad.status_code == 409 and bad.json()["error"] == "stale_generation"
        assert bad.json()["current_generation"] == g2
        ok = c.post("/api/interpret", headers={**h, "X-Mirror-Generation": g2}).json()
        assert ok["generation_id"] == g2
        assert c.get("/api/state", headers={**h, "X-Mirror-Generation": g1}).status_code == 200   # reads are fine


def test_rechecks_follow_the_generation():
    svc = svc_fixture()
    rid = story(svc)
    svc.wait(svc.correct(REFERENCE_CORRECTION))
    rc = [r for r in svc.snapshot()["rechecks"] if r["rollout_id"] == rid]
    assert rc and rc[0]["spec_version_id"] == f"{svc.generation_id}.v2"
    assert rc[0]["report"]["status"] == "constraint_violated"
    svc.load_demo()
    assert svc.snapshot()["rechecks"] == []


def test_challenge_and_job_results_name_their_generation():
    svc = svc_fixture()
    svc.load_demo()
    j = svc.wait(svc.interpret())
    assert j.public()["generation_id"] == svc.generation_id == j.result["generation_id"]
    assert j.result["provenance"] == "fixture" or j.result["provenance"] == "offline_interpreter"
