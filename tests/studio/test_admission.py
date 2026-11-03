"""Admission control (workstream B): session tokens, per-address limits, eviction rules.

The Origin check is not authentication: these tests drive the server the way a
script would (no Origin header) and check what it can and cannot make it do."""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mirror.studio.admission import Admission, RateLimited, client_address, normalise_address
from mirror.studio.app import create_app
from mirror.studio.session_adapter import StudioService
from mirror.studio.sessions import BudgetExceeded, BudgetedClient, ModelBudget, SessionManager


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def manager(**kw):
    return SessionManager(lambda sid, m: StudioService(backend="fixture", session_id=sid, gate=m.gate,
                                                       budget=m.budget, one_job=True, private_runtime=True), **kw)


def hdr(r):
    return {"X-Mirror-Session": r["session"], "X-Mirror-Token": r["token"]}


def test_session_token_is_required_and_checked():
    with TestClient(create_app(manager())) as c:
        r = c.post("/api/session").json()
        assert r["token"] and r["token_required"] and len(r["token"]) >= 30
        assert c.get("/api/state", headers={"X-Mirror-Session": r["session"]}).json()["error"] == "token_required"
        bad = c.get("/api/state", headers={"X-Mirror-Session": r["session"], "X-Mirror-Token": "guess"})
        assert bad.status_code == 401 and bad.json()["error"] == "token_invalid"
        assert c.get("/api/state", headers=hdr(r)).status_code == 200
        assert c.get(f"/api/state?session={r['session']}&token={r['token']}").status_code == 200
        with pytest.raises(WebSocketDisconnect) as e:
            with c.websocket_connect(f"/ws?session={r['session']}") as ws:
                ws.receive_json()
        assert e.value.code == 4401
        with c.websocket_connect(f"/ws?session={r['session']}&token={r['token']}") as ws:
            assert ws.receive_json()["type"] == "hello"


def test_only_the_token_hash_is_kept():
    mgr = manager()
    sid, token, _ = mgr.create("10.0.0.1")
    assert token not in str(mgr._tokens) and len(mgr._tokens[sid]) == 64


def test_scripted_clients_cannot_evict_an_active_visitor(monkeypatch):
    """Regression for the audit: 10 scripted POST /api/session evicted a real visitor."""
    monkeypatch.setenv("MIRROR_TRUST_PROXY", "1")
    clock = Clock()
    mgr = manager(max_sessions=3, clock=clock, evict_min_idle_s=300,
                  admission=Admission(clock=clock))
    with TestClient(create_app(mgr)) as c:
        visitor = c.post("/api/session", headers={"X-Forwarded-For": "198.51.100.7"}).json()
        codes = [c.post("/api/session", headers={"X-Forwarded-For": "203.0.113.9"}).status_code for _ in range(20)]
        assert codes[:2] == [200, 200]
        assert 429 in codes and codes.count(200) <= 12            # per-address hourly creation limit
        assert c.get("/api/state", headers=hdr(visitor)).status_code == 200
        # the script holds at most its live-session allowance; it replaced its own sessions, not others'
        assert sum(1 for a in mgr._addr.values() if a == "203.0.113.9") <= 2
        # a third network fills the server; the visitor (recently active) is not displaced
        third = [c.post("/api/session", headers={"X-Forwarded-For": f"192.0.2.{i}"}).status_code for i in range(3)]
        assert 503 in third
        assert c.get("/api/state", headers=hdr(visitor)).status_code == 200
        clock.t += 301                                             # only a session idle >= 5 min makes room
        assert c.post("/api/session", headers={"X-Forwarded-For": "192.0.2.50"}).status_code == 200


def test_forwarded_for_is_ignored_unless_the_proxy_is_trusted(monkeypatch):
    monkeypatch.delenv("MIRROR_TRUST_PROXY", raising=False)
    assert client_address("10.1.2.3", "1.2.3.4") == "10.1.2.3"
    assert client_address("10.1.2.3", "1.2.3.4, 5.6.7.8", trust_proxy=True) == "5.6.7.8"
    assert normalise_address("2001:db8::1") == normalise_address("2001:db8::ffff") == "2001:db8::/64"
    assert normalise_address("not an ip") == "unknown"
    assert client_address("127.0.0.1", None) == "loopback"                    # a local tunnel: indistinguishable
    adm = Admission(limits={"session": 1})
    for _ in range(5):
        adm.check("session", "loopback")                                      # exempt; global limits still apply


def test_request_rate_limit_answers_429_with_retry_after():
    mgr = manager(admission=Admission(limits={"request": 5}))
    with TestClient(create_app(mgr)) as c:
        codes = [c.get("/api/health").status_code for _ in range(7)]
        assert codes[:5] == [200] * 5 and codes[5] == 429
        r = c.get("/api/health")
        assert r.json()["error"] == "rate_limited" and int(r.headers["retry-after"]) >= 1


def test_job_admission_per_address():
    mgr = manager(admission=Admission(limits={"job": 2}))
    with TestClient(create_app(mgr)) as c:
        h = hdr(c.post("/api/session").json())
        c.post("/api/demo/load", headers=h)
        assert c.post("/api/interpret", headers=h).status_code == 200
        import time
        time.sleep(0.3)
        assert c.post("/api/interpret", headers=h).status_code in (200, 409)
        time.sleep(0.3)
        r = c.post("/api/interpret", headers=h)
        assert r.status_code == 429 and r.json()["error"] == "rate_limited"


def test_model_requests_are_bounded_per_address():
    adm = Admission(limits={"model": 2})

    class Inner:
        calls = 0

        def complete_json(self, *a, **k):
            Inner.calls += 1
            return {}
    c = BudgetedClient(Inner(), ModelBudget(per_hour=100, per_session=100), "s",
                       admit=lambda: adm.check("model", "1.2.3.4"))
    c.complete_json()
    c.complete_json()
    with pytest.raises(BudgetExceeded):
        c.complete_json()
    assert Inner.calls == 2


def test_admission_windows_slide():
    clock = Clock()
    adm = Admission(limits={"session": 1}, clock=clock)
    adm.check("session", "a")
    with pytest.raises(RateLimited) as e:
        adm.check("session", "a")
    assert 3500 <= e.value.retry_after <= 3601
    adm.check("session", "b")                       # other addresses are independent
    clock.t += 3601
    adm.check("session", "a")
