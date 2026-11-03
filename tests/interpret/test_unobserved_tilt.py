"""Tilt the evidence did not observe: ask, never assert, and never crash."""
from __future__ import annotations

from mirror.interpret.drafts import check_proposal
from mirror.interpret.offline import propose_offline
from mirror.interpret.summary import summarize_demo
from tests.interpret.fixtures import make_demo


def _summary_with(**upd_cup):
    tr = make_demo()
    s = summarize_demo(tr)
    objs = [o.model_copy(update=upd_cup) if o.kind == "cup" else o for o in s.objects]
    return s.model_copy(update={"objects": objs}), tr.scene


def _cup_rules(d, phase):
    return [r for r in d.rules if r.object_kind == "cup" and r.type == "upright" and r.phase == phase]


def test_simulator_summary_unchanged():
    tr = make_demo()
    s = summarize_demo(tr)
    d = propose_offline(s, tr.scene)
    assert all(r.regularity_ids for r in d.rules if r.type == "upright")   # no question-only rules added


def test_unobserved_final_tilt_asks_and_does_not_crash():
    s, scene = _summary_with(end_tilt_deg=None)
    d = propose_offline(s, scene)
    assert check_proposal(d, s) == [] or all("violates" not in e for e in check_proposal(d, s))
    finals = _cup_rules(d, "final")
    assert finals and (finals[0].lean == "unclear" or finals[0].regularity_ids)


def test_unobserved_carry_tilt_gets_a_question():
    s, scene = _summary_with(carry_max_tilt_deg=None,
                             observability={"tilt": {"carry_frames": 100, "carry_observed_frames": 40}})
    regs = [r for r in s.regularities if not (r.object_kind == "cup" and r.kind.startswith("carry_"))]
    s = s.model_copy(update={"regularities": regs})
    d = propose_offline(s, scene)
    t = _cup_rules(d, "transport")
    assert len(t) == 1 and t[0].lean == "unclear" and t[0].regularity_ids == []
    assert "not observed" in t[0].question and t[0].question.endswith("?")


def test_no_carry_interval_but_ended_in_tray_gets_a_question():
    """Regression (perception P3): 0 carry frames was treated as 'observed'."""
    s, scene = _summary_with(carry_max_tilt_deg=None,
                             observability={"tilt": {"carry_frames": 0, "carry_observed_frames": 0}})
    regs = [r for r in s.regularities if not (r.object_kind == "cup" and r.kind.startswith("carry_"))]
    s = s.model_copy(update={"regularities": regs})
    assert next(o for o in s.objects if o.kind == "cup").ended_in_tray
    t = _cup_rules(propose_offline(s, scene), "transport")
    assert len(t) == 1 and t[0].lean == "unclear" and "not observed" in t[0].question


def test_no_carry_interval_and_not_in_tray_asks_nothing():
    from mirror.interpret.offline import _tilt_unobserved
    s, _ = _summary_with(observability={"tilt": {"carry_frames": 0, "carry_observed_frames": 0}},
                         ended_in_tray=None)
    cup = next(o for o in s.objects if o.kind == "cup")
    assert _tilt_unobserved(cup, "transport") is False
