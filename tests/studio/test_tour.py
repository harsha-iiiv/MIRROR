"""The recorded Challenge-mode tour: integrity, labelling and the fence."""
import json

from fastapi.testclient import TestClient

from mirror.lineage import parse_rollout_id
from mirror.studio.app import create_app
from mirror.studio.challenge import fenced_seeds
from mirror.studio.records import record_id_of
from mirror.studio.session_adapter import TOUR_DIR, StudioService, tour_record
from mirror.studio.sessions import SessionManager


def test_tour_record_is_intact_and_labelled_as_a_recording():
    rec = tour_record()
    assert rec is not None
    body = {k: v for k, v in rec.items() if k != "tour_version"}
    assert record_id_of(body) == rec["record_id"], "the shipped tour was edited after it was recorded"
    prov = rec["provenance"]
    assert prov["recorded"] is True and prov["interpreter"] == "offline_interpreter"
    assert "no model call" in prov["recorded_with"]["interpreter"] and "+uncommitted" not in prov["recorded_with"]["code_version"]
    assert "Recorded tour" in rec["label"] and "no model call" in rec["label"]
    assert rec["correction"]["provenance"] == "offline_interpreter"


def test_tour_uses_only_development_layouts_and_ships_every_rollout_it_names():
    rec = tour_record()
    fenced = fenced_seeds()
    seeds = [rec["scene"]["seed"]] + [t["seed"] for t in rec.get("transfer", [])]
    assert all(s not in fenced and s < 10000 for s in seeds)
    assert rec["scene"]["split"] == "dev"
    ids = [rec["rollouts"]["before"]["rollout_id"], rec["rollouts"]["after"]["rollout_id"]]
    for t in rec.get("transfer", []):
        ids += [t["base"]["rollout_id"], t["revised"]["rollout_id"]]
    d = TOUR_DIR / rec["tour_version"] / "rollouts"
    for rid in ids:
        p = json.loads((d / f"{rid}.json").read_text())
        assert p["id"] == rid and parse_rollout_id(rid)["gen"] == rec["generation"]["id"]
    assert {f.stem for f in d.glob("*.json")} == set(ids)


def test_tour_tells_the_before_after_story_from_physics():
    rec = tour_record()
    before, after = rec["rollouts"]["before"], rec["rollouts"]["after"]
    rb = next(r for r in rec["rechecks"] if r.get("is_before"))
    assert rb["status"] == "constraint_violated" and rb["violations"]
    assert after["status"] == "success" and after["rollout_id"] != before["rollout_id"]
    cup = lambda r: next(b for b in r["beads"] if b["object_id"].startswith("cup"))  # noqa: E731
    assert cup(after)["beads_in_container"] == cup(after)["beads_total"]
    assert cup(before)["beads_in_container"] < cup(before)["beads_total"]
    assert set(rec["correction"]["scope"]["unaffected_objects"]) >= {"block_0", "marker_0"}
    for t in rec["transfer"]:
        assert t["base_under_revised"]["status"] == "constraint_violated" and t["revised"]["status"] == "success"


def test_tour_endpoints_need_no_session():
    mgr = SessionManager(lambda sid, m: StudioService(backend="fixture", session_id=sid, private_runtime=True))
    with TestClient(create_app(mgr)) as c:
        rec = c.get("/api/tour").json()
        assert rec["provenance"]["recorded"] is True
        rid = rec["rollouts"]["after"]["rollout_id"]
        assert c.get(f"/api/tour/rollouts/{rid}").json()["id"] == rid
        assert c.get("/api/tour/rollouts/..%2Fsecrets").status_code == 404
        assert c.get("/api/health").json()["challenge"]["tour"] is True
