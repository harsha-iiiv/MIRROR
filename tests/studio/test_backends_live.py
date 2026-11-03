"""The studio runs the articulated-arm backend (real MuJoCo). Development scenes only."""
import pytest

from mirror.studio.session_adapter import REFERENCE_CORRECTION, StudioService

pytestmark = pytest.mark.slow


def test_challenge_on_the_arm_backend():
    svc = StudioService(backend="live", session_id="arm", private_runtime=True)
    try:
        assert svc.capabilities()["backends"]["arm"] is True
        cid = svc.wait(svc.challenge_create("reference", preset="shortcut", backend="arm"), timeout=1800).result["challenge_id"]
        ch = svc.challenges[cid]
        assert ch.backend == "arm" and ch.before["backend"] == "arm"
        assert ch.before["step_provenance"]["backend"] == "arm" and "arm" in ch.before["step_provenance"]["simulator"]
        e, _ = svc.session.find_rollout(ch.before["rollout_id"])
        assert e.backend == "arm" and "backend: arm" in e.rollout.report.notes
        if e.log is not None:
            assert e.log.meta.get("backend", {}).get("name") == "arm"
        svc.wait(svc.challenge_correction(cid, REFERENCE_CORRECTION), timeout=600)
        svc.challenge_review(cid, True)
        ex = svc.wait(svc.challenge_execute(cid), timeout=1800)
        assert ex.status == "done", ex.error
        after = svc.challenges[cid].after
        assert after["backend"] == "arm" and after["backend_differs_from_before"] is False
        assert after["status"] in ("success", "planning_failed", "constraint_violated", "unverified", "execution_failed")
        if after["status"] == "planning_failed" and after["backend_failure"]:
            assert after["planning_outcome"]["kind"] != "proven_infeasible"     # an arm failure is never a proof
    finally:
        svc.close("t")
