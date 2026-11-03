"""Execution backend selection (gripper baseline | arm) in the studio."""
import pytest
from fastapi.testclient import TestClient

from mirror.studio.app import create_app
from mirror.studio.session_adapter import StudioService
from mirror.studio.sessions import SessionManager


@pytest.fixture()
def api():
    mgr = SessionManager(lambda sid, m: StudioService(backend="fixture", session_id=sid, gate=m.gate,
                                                      budget=m.budget, one_job=True, private_runtime=True))
    with TestClient(create_app(mgr)) as c:
        r = c.post("/api/session").json()
        c.headers.update({"X-Mirror-Session": r["session"], "X-Mirror-Token": r["token"]})
        yield c


def test_fixture_runs_name_the_gripper_backend_and_refuse_the_arm(api):
    c = api
    c.post("/api/demo/load")
    import time
    j = c.post("/api/interpret").json()
    for _ in range(200):
        if c.get(f"/api/jobs/{j['id']}").json()["status"] == "done":
            break
        time.sleep(0.02)
    r = c.post("/api/run", json={"backend": "arm"})
    assert r.status_code == 409 and "live simulator" in r.json()["detail"]
    r = c.post("/api/run", json={"backend": "tank"})
    assert r.status_code == 409 and "backend is one of" in r.json()["detail"]
    j = c.post("/api/run", json={}).json()
    for _ in range(300):
        s = c.get(f"/api/jobs/{j['id']}").json()
        if s["status"] == "done":
            break
        time.sleep(0.02)
    assert s["result"]["backend"] == "gripper" and s["result"]["step_provenance"]["backend"] == "gripper"
    st = c.get("/api/state").json()
    assert st["rollouts"][-1]["backend"] == "gripper"
    assert st["capabilities"]["backends"] == {"gripper": True, "arm": False}
    assert c.post("/api/challenge", json={"family": "reference", "preset": "shortcut", "backend": "arm"}).status_code == 409


def test_backend_survives_save_and_load(tmp_path):
    from mirror.pipeline import Session
    from mirror.studio.fixtures import FixtureSession
    s = FixtureSession()
    s.set_demo(s.record_demo(), reason="t")
    s.interpret()
    e = s.run()
    e.backend = "arm"                     # as the live pipeline would record it
    s.save_state(tmp_path)
    s2 = FixtureSession.load_state(tmp_path)
    assert s2.rollouts[e.rollout_id].backend == "arm"
    assert s2.rollouts[e.rollout_id].lineage()["backend"] == "arm"
