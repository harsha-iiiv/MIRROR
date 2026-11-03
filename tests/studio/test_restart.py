"""Operational robustness: restart recovery, cancellation, timeouts, worker failure, expiry."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from mirror.studio import session_adapter as sa
from mirror.studio.app import create_app
from mirror.studio.session_adapter import REFERENCE_CORRECTION, StudioService
from mirror.studio.sessions import JobGate, SessionManager


def manager(state_dir, **kw):
    return SessionManager(lambda sid, m: StudioService(backend="fixture", session_id=sid, gate=m.gate,
                                                       budget=m.budget, one_job=True, private_runtime=True),
                          state_dir=state_dir, **kw)


def hdr(r):
    return {"X-Mirror-Session": r["session"], "X-Mirror-Token": r["token"]}


def wait(c, h, job, timeout=30):
    t0 = time.time()
    while True:
        j = c.get(f"/api/jobs/{job['id']}", headers=h).json()
        if j["status"] in ("done", "error", "cancelled"):
            return j
        assert time.time() - t0 < timeout
        time.sleep(0.02)


def test_session_recovers_after_a_server_restart(tmp_path):
    with TestClient(create_app(manager(tmp_path))) as c:
        r = c.post("/api/session").json()
        h = hdr(r)
        c.post("/api/demo/load", headers=h)
        wait(c, h, c.post("/api/interpret", headers=h).json())
        c.post("/api/scene", json={"preset": "shortcut"}, headers=h)
        rid = wait(c, h, c.post("/api/run", json={}, headers=h).json())["result"]["rollout_id"]
        wait(c, h, c.post("/api/correct", json={"text": REFERENCE_CORRECTION}, headers=h).json())
        before = c.get("/api/state", headers=h).json()
        payload = c.get(f"/api/rollouts/{rid}", headers=h).json()
    # the app (and its manager) are gone; a new process starts on the same state directory
    with TestClient(create_app(manager(tmp_path))) as c2:
        after = c2.get("/api/state", headers=h).json()
        assert after["generation"]["id"] == before["generation"]["id"]
        assert [s["version_id"] for s in after["specs"]] == [s["version_id"] for s in before["specs"]]
        assert [x["id"] for x in after["rollouts"]] == [rid]
        assert {x["rollout_id"] for x in after["rechecks"]} == {rid}
        assert c2.get(f"/api/rollouts/{rid}", headers=h).json()["bodies"] == payload["bodies"]
        assert c2.get("/api/health").json()["sessions"]["restored"] == 1
        # the restored session keeps working, and run numbers continue instead of restarting
        rid2 = wait(c2, h, c2.post("/api/run", json={}, headers=h).json())["result"]["rollout_id"]
        assert rid2 != rid and rid2.split(".")[3] == "r002"
        # the token is still required after a restart
        bad = c2.get("/api/state", headers={"X-Mirror-Session": r["session"], "X-Mirror-Token": "x"})
        assert bad.status_code == 401


def test_jobs_interrupted_by_a_restart_are_reported_not_resumed(tmp_path):
    mgr = manager(tmp_path)
    mgr.gate = JobGate(max_jobs=1)

    class Hold:
        queue_position = 0
    assert mgr.gate.acquire(Hold())
    with TestClient(create_app(mgr)) as c:
        r = c.post("/api/session").json()
        h = hdr(r)
        c.post("/api/demo/load", headers=h)
        wait(c, h, c.post("/api/interpret", headers=h).json())
        job = c.post("/api/run", json={}, headers=h).json()         # queued behind the held simulator
        time.sleep(0.2)
        c.app.state.manager.get(r["session"], r["token"]).persist()
    mgr.gate.release()
    with TestClient(create_app(manager(tmp_path))) as c2:
        j = c2.get(f"/api/jobs/{job['id']}", headers=h).json()
        assert j["status"] == "error" and j["error_code"] == "interrupted"
        assert c2.get("/api/state", headers=h).json()["rollouts"] == []


def test_idle_expiry_deletes_persisted_state(tmp_path):
    class Clock:
        t = 0.0

        def __call__(self):
            return self.t
    clock = Clock()
    mgr = manager(tmp_path, clock=clock, idle_s=60)
    sid, token, svc = mgr.create("1.2.3.4")
    svc.load_demo()
    assert (tmp_path / "sessions" / sid / "service.json").exists()
    clock.t += 61
    mgr.sweep()
    assert not (tmp_path / "sessions" / sid).exists()
    with pytest.raises(KeyError):
        mgr.get(sid, token)


def test_stale_on_disk_session_is_not_restored(tmp_path):
    mgr = manager(tmp_path, idle_s=60)
    sid, token, svc = mgr.create("1.2.3.4")
    svc.load_demo()
    mgr.close_all()                                              # server stopping: state kept
    auth = tmp_path / "sessions" / sid / "auth.json"
    a = json.loads(auth.read_text())
    a["last_seen"] -= 3600
    auth.write_text(json.dumps(a))
    with pytest.raises(KeyError):
        manager(tmp_path, idle_s=60).get(sid, token)
    assert not (tmp_path / "sessions" / sid).exists()


def test_cancel_a_queued_job():
    gate = JobGate(max_jobs=1)
    svc = StudioService(backend="fixture", session_id="c", gate=gate, private_runtime=True)
    svc.load_demo()
    svc.wait(svc.interpret())

    class Hold:
        queue_position = 0
    assert gate.acquire(Hold())
    job = svc.run()
    time.sleep(0.2)
    assert svc.cancel_job(job.id)["cancel_requested"]
    svc.wait(job, timeout=5)
    gate.release()
    assert job.status == "cancelled" and job.error_code == "job_cancelled"
    assert svc.session.rollouts == {}


def test_cancel_a_running_job_at_its_next_step():
    svc = StudioService(backend="fixture", session_id="c", private_runtime=True)
    svc.load_demo()
    svc.wait(svc.interpret())
    started = []

    def slow(job):
        started.append(1)
        for i in range(100):
            svc._progress(job, i / 100, "working")
            time.sleep(0.02)
        return {"ok": True}
    job = svc.submit("run", {}, slow)
    while not started:
        time.sleep(0.01)
    svc.cancel_job(job.id)
    svc.wait(job, timeout=5)
    assert job.status == "cancelled" and job.result is None


def test_worker_failure_is_reported_with_its_reason():
    svc = StudioService(backend="fixture", session_id="c", private_runtime=True)

    def boom(job):
        raise ValueError("simulator exploded")
    job = svc.wait(svc.submit("run", {}, boom))
    assert job.status == "error" and "simulator exploded" in job.error and job.error_code == "job_failed"
    assert not svc.busy()
    assert svc.wait(svc.submit("run", {}, lambda j: 1)).status == "done"      # the worker survives


def test_jobs_past_the_timeout_are_abandoned_and_their_results_discarded(monkeypatch):
    monkeypatch.setattr(sa, "JOB_TIMEOUT_S", 0.1)
    svc = StudioService(backend="fixture", session_id="c", private_runtime=True, one_job=True)
    release = []

    def stuck(job):
        while not release:
            time.sleep(0.02)
        return {"should": "not be used"}
    job = svc.submit("run", {}, stuck)
    time.sleep(0.3)
    assert not svc.busy() and job.status == "error" and job.error_code == "timeout"
    release.append(1)
    time.sleep(0.2)
    assert job.result is None and job.stale


def test_closed_session_refuses_work():
    svc = StudioService(backend="fixture", session_id="c", private_runtime=True)
    svc.close("test")
    with pytest.raises(RuntimeError, match="ended"):
        svc.run()
