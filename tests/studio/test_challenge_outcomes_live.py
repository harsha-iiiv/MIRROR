"""Challenge corrections through the structured interpretation outcome, on the live backend
(offline interpreter, real MuJoCo). Development cases written by the developer."""
import pytest

from mirror.studio.session_adapter import REFERENCE_CORRECTION, StudioService

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def svc():
    s = StudioService(backend="live", session_id="oc", private_runtime=True)
    yield s
    s.close("t")


def correction(svc, cid, text, focus=None):
    j = svc.wait(svc.challenge_correction(cid, text, focus_kind=focus), timeout=600)
    assert j.status == "done", j.error
    return j.result


def test_outcomes_on_a_shortcut_layout(svc):
    cid = svc.wait(svc.challenge_create("reference", preset="shortcut"), timeout=900).result["challenge_id"]
    p = correction(svc, cid, "be careful")
    assert p["outcome"] == "clarify" and p["question"] and not p["changed"]
    p = correction(svc, cid, "don't tip the coffee")
    assert p["outcome"] == "clarify" and p["reason_code"] == "unknown_object"
    p = correction(svc, cid, "don't tip the coffee", focus="cup")
    assert p["outcome"] == "apply" and p["changed"] and p["alias"] and p["scope"]["objects"] == ["cup_0"]
    p = correction(svc, cid, "paint the block red")
    assert p["outcome"] == "refuse" and p["rejected"] and p["supported_changes"]
    p = correction(svc, cid, REFERENCE_CORRECTION)
    assert p["outcome"] == "apply" and p["step_provenance"]["kind"] == "offline_fallback"
    with pytest.raises(ValueError):
        svc.challenge_correction(cid, "x", focus_kind="vase")


def test_blocked_layout_reports_proof_wording_only_with_a_certificate(svc):
    cid = svc.wait(svc.challenge_create("reference", preset="blocked"), timeout=900).result["challenge_id"]
    correction(svc, cid, REFERENCE_CORRECTION)
    svc.challenge_review(cid, True)
    ex = svc.wait(svc.challenge_execute(cid), timeout=900).result
    after = svc.challenges[cid].after
    po = after["planning_outcome"]
    assert ex["status"] == "planning_failed"
    if po["kind"] == "proven_infeasible":
        assert po["headline"] == "Proven infeasible under these stated assumptions" and po["assumptions"]
        assert "not a guarantee about physical safety" in po["detail"]
    else:
        assert po["headline"] == "Planner found no compliant route"
