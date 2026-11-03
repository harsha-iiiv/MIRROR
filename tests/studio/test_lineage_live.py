"""Lineage and Challenge mode on the live backend: real MuJoCo execution, offline interpreter."""
import pytest

from mirror.lineage import parse_rollout_id
from mirror.scenes import reference_scene
from mirror.studio.session_adapter import REFERENCE_CORRECTION, StudioService

pytestmark = pytest.mark.slow


def live():
    return StudioService(backend="live", session_id="live-test", private_runtime=True)


def test_demo_a_then_demo_b_with_physics():
    svc = live()
    try:
        svc.load_demo()
        svc.wait(svc.interpret(), timeout=900)
        ra = svc.wait(svc.run(condition="A"), timeout=900).result["rollout_id"]
        gen_a, motion_a = svc.generation_id, svc.rollout_payload(ra)["bodies"]
        svc.session.record_demo(reference_scene("shortcut"))     # demonstration B, recorded in another layout
        svc._changed()
        assert svc.snapshot()["specs"] == [] and svc.snapshot()["rollouts"] == []
        svc.wait(svc.interpret(), timeout=900)
        rb = svc.wait(svc.run(condition="B"), timeout=900).result["rollout_id"]
        pb = svc.rollout_payload(rb)
        assert parse_rollout_id(rb)["gen"] == svc.generation_id != gen_a
        assert pb["spec_version_id"].startswith(svc.generation_id) and pb["bodies"] != motion_a
        assert svc.session.spec.demo_id == svc.session.demo.demo_id != svc.session.archive[-1].demo.demo_id
        # A's rollout still re-checks against A's own specification
        rc = svc.recheck(ra)
        assert rc["spec_version_id"] == f"{gen_a}.v1" and rc["report"]["status"] == "success"
    finally:
        svc.close("test")


def test_challenge_on_a_development_layout_with_physics():
    svc = live()
    try:
        r = svc.wait(svc.challenge_create("shortcut", 4242), timeout=900)
        assert r.status == "done", r.error
        cid = r.result["challenge_id"]
        prop = svc.wait(svc.challenge_correction(cid, REFERENCE_CORRECTION), timeout=900).result
        assert prop["changed"] and prop["provenance"] == "offline_interpreter"
        assert prop["scope"]["objects"] == ["cup_0"]
        rv = svc.challenge_review(cid, True)
        before = rv["rechecks"][0]
        assert before["is_before"] and before["status"] == "constraint_violated"
        assert before["violations"][0]["constraint_id"].startswith("c_upright_transport")
        ex = svc.wait(svc.challenge_execute(cid), timeout=900).result
        assert ex["status"] == "success" and ex["provenance"] == "simulated"
        after = svc.challenges[cid].after
        assert all(b["beads_in_container"] == b["beads_total"] for b in after["beads"])
        pub = svc.challenge_publish(cid)
        rec = svc.records.get(pub["record_id"])
        assert rec["provenance"]["simulation"] == "mujoco" and rec["scene"]["seed"] == 4242
    finally:
        svc.close("test")
