"""REST and websocket surface of the studio, on the fixture backend."""
import time

import pytest
from fastapi.testclient import TestClient

from mirror.studio.app import create_app
from mirror.studio.session_adapter import REFERENCE_CORRECTION, StudioService


@pytest.fixture()
def client():
    svc = StudioService(backend="fixture")
    with TestClient(create_app(svc)) as c:
        yield c


def wait(c, job, timeout=30):
    t0 = time.time()
    while True:
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("done", "error"):
            return j
        assert time.time() - t0 < timeout
        time.sleep(0.02)


def test_full_story(client):
    c = client
    assert c.get("/api/health").json()["mode"] == "fixture"
    s = c.post("/api/demo/load").json()
    assert s["demo"]["events"]
    assert wait(c, c.post("/api/interpret").json())["status"] == "done"
    s = c.get("/api/state").json()
    assert s["specs"][0]["version"] == 1 and s["mode"] == "fixture"
    assert wait(c, c.post("/api/run", json={"condition": "demo", "tags": ["run"]}).json())["status"] == "done"
    c.post("/api/scene", json={"preset": "shortcut"})
    j = wait(c, c.post("/api/run", json={"condition": "shortcut", "tags": ["world"]}).json())
    old = j["result"]["rollout_id"]
    j = wait(c, c.post("/api/correct", json={"text": REFERENCE_CORRECTION}).json())
    assert j["result"]["changed"] and j["result"]["version"] == 2
    s = c.get("/api/state").json()
    rc = [r for r in s["rechecks"] if r["rollout_id"] == old]
    assert rc and rc[0]["report"]["status"] == "constraint_violated"
    assert wait(c, c.post("/api/run", json={}).json())["status"] == "done"
    s = c.get("/api/state").json()
    assert [r["tags"] for r in s["rollouts"]][:2] == [["run"], ["world"]]
    assert all(r["trace"] for r in s["rollouts"])
    p = c.get(f"/api/rollouts/{old}").json()
    assert p["format"] == "mirror-studio-rollout/1" and p["visual"]["bodies"]
    assert c.get("/api/rollouts/demo").json()["kind"] == "demonstration"


def test_decisions_create_a_confirmation_version(client):
    c = client
    c.post("/api/demo/load")
    wait(c, c.post("/api/interpret").json())
    s = c.post("/api/spec/decisions", json={"decisions": {"c_upright_transport": "rejected"}}).json()
    assert [sp["version"] for sp in s["specs"]] == [1, 2]
    last = s["specs"][-1]
    assert last["history"][-1]["kind"] == "confirmation"
    assert next(x for x in last["constraints"] if x["id"] == "c_upright_transport")["status"] == "rejected"
    assert next(u for u in last["uncertainties"] if u["id"] == "u_transport")["status"] == "resolved"
    assert c.post("/api/spec/decisions", json={"decisions": {"nope": "confirmed"}}).status_code == 404


def test_scene_edits_derive_custom_layouts(client):
    c = client
    c.post("/api/demo/load")
    s = c.post("/api/scene", json={"edit": {"tray": {"x": 9.0, "y": 0.1}}}).json()
    t = s["scene"]["trays"][0]
    assert s["scene"]["split"] == "custom" and t["pose"]["pos"][0] <= 0.45 - t["inner_half"][0] + 1e-9
    s = c.post("/api/scene", json={"edit": {"slot": "on"}}).json()
    wall = next(o for o in s["scene"]["obstacles"] if o["kind"] == "wall")
    slot = wall["slots"][0]
    assert slot["z_max"] - slot["z_min"] > 0.05
    lo, hi = wall["pose"]["pos"][1] - wall["length"] / 2, wall["pose"]["pos"][1] + wall["length"] / 2
    assert lo <= slot["y_min"] < slot["y_max"] <= hi
    s = c.post("/api/scene", json={"edit": {"obstacle": {"x": 0.2, "y": -0.2}}}).json()
    assert any(o["id"] == "box_0" for o in s["scene"]["obstacles"])
    s = c.post("/api/scene", json={"edit": {"remove_obstacle": "box_0"}}).json()
    assert not any(o["id"] == "box_0" for o in s["scene"]["obstacles"])
    s = c.post("/api/scene", json={"edit": {"remove_obstacle": "wall_0"}}).json()
    assert any(o["id"] == "wall_0" for o in s["scene"]["obstacles"]), "walls are part of the task family"


def test_errors_are_explicit(client):
    c = client
    assert c.post("/api/run", json={}).status_code == 409
    assert c.post("/api/correct", json={"text": "   "}).status_code == 422
    assert c.get("/api/rollouts/r999").status_code == 404
    assert c.get("/api/jobs/nope").status_code == 404
    assert c.post("/api/scene", json={}).status_code == 422
    assert c.post("/api/teleop/start").status_code == 409


def test_evaluation_and_clarification(client):
    c = client
    c.post("/api/demo/load")
    wait(c, c.post("/api/interpret").json())
    c.post("/api/scene", json={"preset": "shortcut"})
    j = wait(c, c.post("/api/clarify").json())
    assert j["status"] == "done"
    s = c.get("/api/state").json()
    clar = s["clarification"]
    assert len(clar["candidates"]) == 2
    cand_ids = {x["rollout_id"] for x in clar["candidates"]}
    assert all("candidate" in r["tags"] for r in s["rollouts"] if r["id"] in cand_ids)
    j = wait(c, c.post("/api/clarify/answer", json={"text": "No. The cup must stay upright while it is carried."}).json())
    assert j["result"]["version"] == 2
    assert c.get("/api/state").json()["clarification"] is None
    assert c.post("/api/evaluate", json={"seeds": []}).status_code == 422
    j = wait(c, c.post("/api/evaluate", json={"seeds": [10000, 10001]}).json())
    assert j["status"] == "done"
    ev = c.get("/api/state").json()["evaluation"]
    assert [p["seed"] for p in ev["pairs"]] == [10000, 10001]


def test_websocket_announces_state_changes(client):
    c = client
    with c.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        c.post("/api/demo/load")
        msg = ws.receive_json()
        assert msg["type"] == "state" and msg["revision"] >= 1
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_static_files_are_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "importmap" in r.text
    assert client.get("/js/main.js").status_code == 200
