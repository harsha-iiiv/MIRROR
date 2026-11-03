"""Local load fixture (no production stress): many scripted clients against one in-process
server on the fixture backend. Checks that every bound holds under concurrency."""
import threading
import time

import pytest
from fastapi.testclient import TestClient

from mirror.studio.admission import Admission
from mirror.studio.app import create_app
from mirror.studio.session_adapter import StudioService
from mirror.studio.sessions import JobGate, SessionManager


def test_bounds_hold_under_concurrent_scripted_clients(monkeypatch):
    monkeypatch.setenv("MIRROR_TRUST_PROXY", "1")
    gate = JobGate(max_jobs=2)
    peak = {"running": 0}
    real_acquire = gate.acquire

    def watched(job, *a, **k):
        ok = real_acquire(job, *a, **k)
        peak["running"] = max(peak["running"], gate.running)
        return ok
    gate.acquire = watched
    mgr = SessionManager(lambda sid, m: StudioService(backend="fixture", session_id=sid, gate=m.gate, budget=m.budget,
                                                      one_job=True, private_runtime=True),
                         max_sessions=6, gate=gate, evict_min_idle_s=300,
                         admission=Admission(limits={"session": 3, "job": 20, "request": 400}))
    results = {"created": 0, "busy": 0, "limited": 0, "jobs_done": 0, "errors": []}
    lock = threading.Lock()

    with TestClient(create_app(mgr)) as c:
        def client(i):
            ip = f"198.51.100.{i % 12}"
            try:
                r = c.post("/api/session", headers={"X-Forwarded-For": ip})
                with lock:
                    if r.status_code == 503:
                        results["busy"] += 1
                        return
                    if r.status_code == 429:
                        results["limited"] += 1
                        return
                    results["created"] += 1
                h = {"X-Mirror-Session": r.json()["session"], "X-Mirror-Token": r.json()["token"], "X-Forwarded-For": ip}
                c.post("/api/demo/load", headers=h)
                j = c.post("/api/interpret", headers=h).json()
                for _ in range(300):
                    s = c.get(f"/api/jobs/{j['id']}", headers=h).json()
                    if s.get("status") in ("done", "error", "cancelled") or s.get("error") == "session_expired":
                        break
                    time.sleep(0.02)
                r = c.post("/api/run", json={}, headers=h)
                if r.status_code == 200:
                    j = r.json()
                    for _ in range(300):
                        s = c.get(f"/api/jobs/{j['id']}", headers=h).json()
                        if s.get("status") in ("done", "error", "cancelled") or "error" in s and "status" not in s:
                            break
                        time.sleep(0.02)
                    if s.get("status") == "done":
                        with lock:
                            results["jobs_done"] += 1
            except Exception as exc:  # noqa: BLE001
                with lock:
                    results["errors"].append(repr(exc))

        threads = [threading.Thread(target=client, args=(i,)) for i in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(120)
        stats = mgr.stats()
        health = c.get("/api/health", headers={"X-Forwarded-For": "203.0.113.1"}).json()
    assert not results["errors"], results["errors"][:3]
    assert stats["active"] <= 6 and peak["running"] <= 2
    assert results["created"] + results["busy"] + results["limited"] == 40
    assert results["busy"] > 0                     # the server said "busy" instead of evicting active visitors
    assert results["jobs_done"] >= 1
    assert health["jobs"]["running"] == 0
