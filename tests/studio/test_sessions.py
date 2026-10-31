"""Public-server guards: per-visitor sessions, the simulation job queue, the
model-call budget and origin checks."""
import threading
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mirror.studio.app import create_app
from mirror.studio.session_adapter import StudioService
from mirror.studio.sessions import (BudgetedClient, JobGate, ModelBudget, ServerBusy, SessionExpired,
                                    SessionManager, budget_error_class)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def fixture_manager(**kw):
    return SessionManager(lambda sid, m: StudioService(backend="fixture", session_id=sid, gate=m.gate,
                                                       budget=m.budget, one_job=True, private_runtime=True), **kw)


def wait(c, job, timeout=30):
    t0 = time.time()
    while True:
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("done", "error"):
            return j
        assert time.time() - t0 < timeout
        time.sleep(0.02)


# ---------------------------------------------------------------- sessions

def test_sessions_are_separate():
    with TestClient(create_app(fixture_manager())) as c:
        a = c.post("/api/session").json()["session"]
        b = c.post("/api/session").json()["session"]
        assert a != b and len(a) >= 20
        c.post("/api/demo/load", headers={"X-Mirror-Session": a})
        assert c.get("/api/state", headers={"X-Mirror-Session": a}).json()["demo"]
        assert c.get("/api/state", headers={"X-Mirror-Session": b}).json()["demo"] is None
        r = c.get("/api/state")
        assert r.status_code == 400 and r.json()["error"] == "no_session"
        r = c.get("/api/state", headers={"X-Mirror-Session": "made-up"})
        assert r.status_code == 404 and r.json()["error"] == "session_expired"
        assert c.get(f"/api/state?session={a}").status_code == 200


def test_lru_cap_and_idle_expiry():
    clock = Clock()
    mgr = fixture_manager(max_sessions=2, idle_s=1800, clock=clock)
    s1, _ = mgr.create()
    clock.t += 10
    s2, _ = mgr.create()
    clock.t += 10
    mgr.get(s1)                       # s1 is now the most recently used
    s3, _ = mgr.create()              # full: the least recently used idle session (s2) makes room
    assert mgr.stats()["active"] == 2
    with pytest.raises(SessionExpired):
        mgr.get(s2)
    clock.t += 1801
    mgr.sweep()
    assert mgr.stats()["active"] == 0
    with pytest.raises(SessionExpired):
        mgr.get(s1)


def test_server_busy_when_every_session_has_work():
    mgr = fixture_manager(max_sessions=1)
    sid, svc = mgr.create()
    svc.jobs["x"] = type("J", (), {"status": "running"})()
    with pytest.raises(ServerBusy):
        mgr.create()
    with TestClient(create_app(mgr)) as c:
        r = c.post("/api/session")
        assert r.status_code == 503 and r.json()["error"] == "server_busy" and r.headers["retry-after"]
    svc.jobs.clear()


def test_expired_session_closes_its_websocket():
    mgr = fixture_manager()
    with TestClient(create_app(mgr)) as c:
        sid = c.post("/api/session").json()["session"]
        with c.websocket_connect(f"/ws?session={sid}") as ws:
            assert ws.receive_json()["type"] == "hello"
            mgr.close(sid, reason="idle for too long")
            assert ws.receive_json() == {"type": "expired", "reason": "idle for too long"}
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(f"/ws?session={sid}") as ws:
                ws.receive_json()


# --------------------------------------------------------------- job queue

def test_job_gate_queues_in_order_and_reports_positions():
    gate = JobGate(max_jobs=1)

    class J:
        queue_position = 0
    a, b, c = J(), J(), J()
    seen = []
    assert gate.acquire(a)
    order = []

    def take(job, name):
        gate.acquire(job, notify=lambda j: seen.append((name, j.queue_position)))
        order.append(name)
        time.sleep(0.05)
        gate.release()
    tb = threading.Thread(target=take, args=(b, "b"))
    tb.start()
    time.sleep(0.1)
    tc = threading.Thread(target=take, args=(c, "c"))
    tc.start()
    time.sleep(0.1)
    assert gate.stats() == {"running": 1, "queued": 2, "max": 1}
    assert ("b", 1) in seen and ("c", 2) in seen
    gate.release()
    tb.join(5)
    tc.join(5)
    assert order == ["b", "c"] and ("c", 1) in seen
    assert gate.stats()["running"] == 0


def test_one_job_per_session_and_queue_position_in_jobs():
    mgr = fixture_manager()
    mgr.gate = JobGate(max_jobs=1)
    with TestClient(create_app(mgr)) as c:
        sids = [c.post("/api/session").json()["session"] for _ in range(2)]
        h = [{"X-Mirror-Session": s} for s in sids]
        for hh in h:
            c.post("/api/demo/load", headers=hh)
            j = c.post("/api/interpret", headers=hh).json()
            while c.get(f"/api/jobs/{j['id']}", headers=hh).json()["status"] != "done":
                time.sleep(0.02)
        # Hold the only simulator slot so both sessions' runs must queue.
        class Hold:
            queue_position = 0
        mgr.gate.acquire(Hold())
        j0 = c.post("/api/run", json={}, headers=h[0]).json()
        r = c.post("/api/run", json={}, headers=h[0])
        assert r.status_code == 409 and r.json()["error"] == "session_busy"
        j1 = c.post("/api/run", json={}, headers=h[1]).json()
        time.sleep(0.3)
        q0 = c.get(f"/api/jobs/{j0['id']}", headers=h[0]).json()
        q1 = c.get(f"/api/jobs/{j1['id']}", headers=h[1]).json()
        assert (q0["status"], q0["queue_position"]) == ("queued", 1)
        assert (q1["status"], q1["queue_position"]) == ("queued", 2)
        assert c.get("/api/health").json()["jobs"] == {"running": 1, "queued": 2, "max": 1}
        mgr.gate.release()
        c.headers.update(h[0]); assert wait(c, j0)["status"] == "done"
        c.headers.update(h[1]); assert wait(c, j1)["status"] == "done"


# ------------------------------------------------------------ model budget

def test_budget_counts_per_session_and_per_hour():
    clock = Clock()
    b = ModelBudget(per_hour=3, per_session=2, clock=clock)
    assert b.take("a") is None and b.take("a") is None
    assert "session" in b.take("a")
    assert b.take("b") is None
    assert "hour" in b.take("c")
    assert b.remaining_hour() == 0 and b.remaining_session("b") == 1
    clock.t += 3601
    assert b.take("c") is None and b.remaining_hour() == 2


def _live_service(budget):
    pytest.importorskip("mirror.interpret")
    from mirror.interpret import LLMClient, LLMError

    class Failing(LLMClient):
        def complete_json(self, *a, **k):
            raise LLMError("connection refused")
    svc = StudioService(backend="live", session_id="s1", budget=budget, gate=JobGate(2), one_job=True)
    svc.session.client = BudgetedClient(Failing(mode="live", api_key="test", model="m"), budget, "s1")
    return svc


def test_model_error_and_budget_fall_back_to_offline_interpreter(monkeypatch):
    monkeypatch.setenv("MIRROR_LLM_MODE", "live")
    budget = ModelBudget(per_hour=40, per_session=1)
    svc = _live_service(budget)
    svc.load_demo()
    job = svc.wait(svc.interpret(), timeout=120)
    assert job.status == "done", job.error
    assert svc.session.spec.interpreter == "offline-rules"
    assert "model request failed" in svc.fallbacks[-1]["used"]
    job = svc.wait(svc.interpret(), timeout=120)          # second request: budget already used
    assert job.status == "done"
    assert "budget" in svc.fallbacks[-1]["used"] and svc.fallbacks[-1]["step"] == "interpret"
    snap = svc.snapshot()
    assert snap["llm"]["mode"] == "live" and snap["llm"]["calls_remaining_session"] == 0
    assert "test" not in str(snap["llm"])                  # the key never reaches the browser
    svc.close()


def test_budget_exception_is_a_model_error():
    pytest.importorskip("mirror.interpret")
    from mirror.interpret import LLMError
    assert issubclass(budget_error_class(), LLMError)


def test_health_reports_budget_without_secrets(monkeypatch):
    monkeypatch.setenv("MIRROR_LLM_MODE", "live")
    monkeypatch.setenv("MODEL_API_KEY", "sk-secret-value")
    monkeypatch.setenv("MIRROR_LLM_MODEL", "muse-spark-1.3")
    monkeypatch.setenv("MIRROR_LLM_BASE_URL", "https://api.example.test/v1")
    mgr = fixture_manager(budget=ModelBudget(per_hour=40, per_session=8))
    with TestClient(create_app(mgr)) as c:
        r = c.get("/api/health")
        h = r.json()
        assert "sk-secret-value" not in r.text
        assert h["llm"] == {"mode": "live", "model": "muse-spark-1.3", "endpoint_host": "api.example.test",
                            "calls_per_hour": 40, "calls_remaining_hour": 40, "calls_per_session": 8}
        assert set(h) >= {"ok", "service", "version", "backend", "sessions", "jobs", "eval", "uptime_s"}
        assert h["sessions"]["max"] == 6 and h["jobs"]["max"] >= 1 and h["eval"]["max_seeds"] == 6


def test_replay_and_offline_never_call_live(monkeypatch):
    pytest.importorskip("mirror.interpret")
    from mirror.studio.sessions import make_client
    monkeypatch.setenv("MODEL_API_KEY", "sk-secret-value")
    monkeypatch.delenv("MIRROR_LLM_MODE", raising=False)
    assert make_client(ModelBudget(), "s").mode == "offline"     # a key alone does not enable live calls
    monkeypatch.setenv("MIRROR_LLM_MODE", "replay")
    assert make_client(ModelBudget(), "s").mode == "replay"
    monkeypatch.setenv("MIRROR_LLM_MODE", "live")
    assert isinstance(make_client(ModelBudget(), "s"), BudgetedClient)


# ------------------------------------------------------------------ origins

def test_cors_allows_only_listed_origins():
    good, evil = "https://mirror-studio.example", "https://evil.example"
    with TestClient(create_app(fixture_manager(), cors_origins=[good])) as c:
        pre = c.options("/api/session", headers={"Origin": good, "Access-Control-Request-Method": "POST",
                                                  "Access-Control-Request-Headers": "x-mirror-session"})
        assert pre.headers.get("access-control-allow-origin") == good
        assert "x-mirror-session" in pre.headers.get("access-control-allow-headers", "").lower()
        pre = c.options("/api/session", headers={"Origin": evil, "Access-Control-Request-Method": "POST"})
        assert pre.headers.get("access-control-allow-origin") is None
        assert c.post("/api/session", headers={"Origin": evil}).status_code == 403
        assert c.get("/api/health", headers={"Origin": evil}).status_code == 403
        r = c.post("/api/session", headers={"Origin": good})
        assert r.status_code == 200 and r.headers["access-control-allow-origin"] == good
        assert c.post("/api/session").status_code == 200                         # no Origin: not a browser
        assert c.post("/api/session", headers={"Origin": "http://testserver"}).status_code == 200  # same origin


def test_websocket_origin_is_checked():
    good, evil = "https://mirror-studio.example", "https://evil.example"
    with TestClient(create_app(fixture_manager(), cors_origins=[good])) as c:
        sid = c.post("/api/session").json()["session"]
        with pytest.raises(WebSocketDisconnect) as e:
            with c.websocket_connect(f"/ws?session={sid}", headers={"Origin": evil}) as ws:
                ws.receive_json()
        assert e.value.code == 1008
        with c.websocket_connect(f"/ws?session={sid}", headers={"Origin": good}) as ws:
            assert ws.receive_json()["type"] == "hello"


def test_no_cors_list_means_same_origin_only():
    with TestClient(create_app(fixture_manager(), cors_origins=[])) as c:
        assert c.post("/api/session", headers={"Origin": "https://anyone.example"}).status_code == 403
        assert c.post("/api/session", headers={"Origin": "http://testserver"}).status_code == 200
