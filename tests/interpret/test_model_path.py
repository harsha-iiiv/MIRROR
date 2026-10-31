"""propose_spec / compile_correction through a (fake) model endpoint."""
import json

import pytest

from mirror.contracts import Status
from mirror.interpret import (CorrectionRejected, LLMClient, LLMError, NeedsClarification,
                              compile_correction, confirm_items, propose_spec, summarize_demo)
from mirror.interpret.offline import propose_offline
from tests.interpret.fake_api import FakeAPI
from tests.interpret.fixtures import make_demo


def mk(api, tmp_path):
    return LLMClient(base_url="http://fake/v1", model="m-1", api_key="k", mode="live",
                     cassette_dir=tmp_path, transport=api.transport(), backoff_s=0.0)


def offline_draft(trace):
    return propose_offline(summarize_demo(trace), trace.scene).model_dump(mode="json")


def test_model_proposal_is_labelled_and_validated(tmp_path):
    tr = make_demo()
    api = FakeAPI([offline_draft(tr)])
    spec = propose_spec(tr, mk(api, tmp_path))
    assert spec.interpreter == "fake-model-2026"
    assert all(c.status == Status.proposed for c in spec.constraints)
    assert spec.constraints[0].provenance.model == "fake-model-2026"
    user = api.requests[0]["body"]["messages"][1]["content"]
    assert "regularities" in user and "carry_max_tilt_deg" in user


def test_model_cannot_confirm_or_invent(tmp_path):
    tr = make_demo(tilt_kind="marker")
    bad = offline_draft(tr)
    # claims the marker stayed upright while carried, which the demo contradicts
    bad["rules"].append({"type": "upright", "object_kind": "marker", "phase": "transport", "max_tilt_deg": 15,
                         "contact_with": None, "min_clearance_m": None, "regularity_ids": [],
                         "shown_by_demo": "upright", "assumption": "", "lean": "unclear", "question": "?"})
    bad["objects"].append({"kind": "cup", "include_in_task": True, "evidence": "", "regularity_ids": []})
    good = offline_draft(tr)
    api = FakeAPI([bad, good])
    spec = propose_spec(tr, mk(api, tmp_path))
    repair = api.requests[1]["body"]["messages"][-1]["content"]
    assert "violates this rule" in repair and "list each kind once" in repair
    assert "c_upright_transport_marker" not in {c.id for c in spec.constraints}


def test_uncovered_regularity_is_rejected(tmp_path):
    tr = make_demo()
    d = offline_draft(tr)
    d["dismissed"] = []
    api = FakeAPI([d, d])
    with pytest.raises(LLMError, match="not addressed"):
        propose_spec(tr, mk(api, tmp_path))


def _spec():
    s = propose_spec(make_demo())
    s, _ = confirm_items(s, confirm=[g.id for g in s.goals] + ["c_upright_final_cup"])
    return s


def op(action, kind=None, phase=None, **kw):
    base = {"action": action, "rule": None, "target_id": None, "before_kind": None, "after_kind": None,
            "goal_kind": None, "rationale": "r"}
    if kind is not None or action in ("require_rule", "relax_rule"):
        base["rule"] = {"type": "upright", "object_kind": kind, "phase": phase, "max_tilt_deg": None,
                        "contact_with": None, "min_clearance_m": None}
    base.update(kw)
    return base


def draft(ops, outcome="apply", question=None):
    return {"outcome": outcome, "interpretation": "i", "question": question, "options": [], "ops": ops}


def test_model_correction_scope_guard(tmp_path):
    spec = _spec()
    unscoped = draft([op("require_rule", None, "transport")])
    scoped = draft([op("require_rule", "cup", "transport")])
    api = FakeAPI([unscoped, scoped])
    new, rec = compile_correction(spec, "This container must remain upright throughout transport.",
                                  mk(api, tmp_path))
    assert "scope it" in api.requests[1]["body"]["messages"][-1]["content"]
    assert {c.id: c.status for c in new.constraints}["c_upright_transport_cup"] == Status.confirmed
    assert rec.model == "fake-model-2026"


def test_model_clarify_and_reject_outcomes(tmp_path):
    spec = _spec()
    api = FakeAPI([draft([], outcome="clarify", question="Which one?"), draft([], outcome="reject")])
    c = mk(api, tmp_path)
    with pytest.raises(NeedsClarification, match="Which one"):
        compile_correction(spec, "It must stay upright.", c)
    with pytest.raises(CorrectionRejected):
        compile_correction(spec, "Make it faster.", c)


def test_model_unknown_target_repaired(tmp_path):
    spec = _spec()
    api = FakeAPI([draft([op("confirm_item", target_id="c_made_up")]),
                   draft([op("confirm_item", target_id="c_upright_transport_cup")])])
    new, _ = compile_correction(spec, "Yes the cup must stay upright while carried.", mk(api, tmp_path))
    assert "c_made_up" in api.requests[1]["body"]["messages"][-1]["content"]
    assert {c.id: c.status for c in new.constraints}["c_upright_transport_cup"] == Status.confirmed
