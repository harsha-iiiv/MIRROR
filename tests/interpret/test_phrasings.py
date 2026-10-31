"""Phrasing battery for the offline correction compiler."""
import pytest

from mirror.interpret import (CorrectionRejected, NeedsClarification, compile_correction, confirm_items,
                              propose_spec)
from tests.interpret.fixtures import make_demo


@pytest.fixture(scope="module")
def spec():
    s = propose_spec(make_demo())
    s, _ = confirm_items(s, confirm=[g.id for g in s.goals] + ["c_upright_final_cup"])
    return s


APPLY = [
    ("Keep the mug upright while moving it.", {"c_upright_transport_cup": "confirmed"}),
    ("The cup should never be tipped over.", {"c_upright_always_cup": "confirmed"}),
    ("Don't tilt the cup when you carry it.", {"c_upright_transport_cup": "confirmed"}),
    ("The container can't go on its side during transport.", {"c_upright_transport_cup": "confirmed"}),
    ("Carrying the cup sideways is fine.", {"c_upright_transport_cup": "rejected"}),
    ("It's okay if the marker tilts while carried.", {"c_upright_transport_marker": "rejected"}),
    ("The pen is allowed to lie down while you move it.", {"c_upright_transport_marker": "rejected"}),
    ("The block doesn't have to stay upright.",
     {"c_upright_transport_block": "rejected", "c_upright_final_block": "rejected"}),
    ("Whether the block tilts doesn't matter.", {"c_upright_transport_block": "rejected"}),
    ("The cup must stay upright at all times.", {"c_upright_always_cup": "confirmed"}),
    ("Keep the cup within 5 degrees of vertical during transport.", {"c_upright_transport_cup": "confirmed"}),
    ("Both the cup and the marker must stay upright while carried.",
     {"c_upright_transport_cup": "confirmed", "c_upright_transport_marker": "confirmed"}),
    ("The cup has to stay upright while carried, and the marker can tilt.",
     {"c_upright_transport_cup": "confirmed", "c_upright_transport_marker": "rejected"}),
    ("Only the cup needs to stay upright.",
     {"c_upright_transport_cup": "confirmed", "c_upright_transport_marker": "rejected",
      "c_upright_transport_block": "rejected"}),
    ("Don't spill the beads.", {"c_upright_transport_cup": "confirmed"}),
    ("Make sure the cup does not hit the wall.", {"c_forbidden_contact_transport_cup": "confirmed"}),
    ("Keep the cup at least 5 cm away from the wall.", {"c_keep_clearance_transport_cup": "confirmed"}),
]


@pytest.mark.parametrize("text,expected", APPLY, ids=[t for t, _ in APPLY])
def test_applied(spec, text, expected):
    new, rec = compile_correction(spec, text)
    st = {c.id: c.status.value for c in new.constraints}
    assert {k: st.get(k) for k in expected} == expected
    before = {c.id: c.status.value for c in spec.constraints}
    mentioned = {k.rsplit("_", 1)[1] for k in expected}
    for cid, v in st.items():  # nothing outside the named objects changed
        if cid in before and before[cid] != v:
            assert cid.rsplit("_", 1)[1] in mentioned, cid


def test_clearance_distance_parsed(spec):
    new, _ = compile_correction(spec, "Keep the cup at least 5 cm away from the wall.")
    assert {c.id: c for c in new.constraints}["c_keep_clearance_transport_cup"].params == {"min_m": 0.05}


ORDER = [
    ("Pack the block first.", {"o_block_before_cup": "confirmed", "o_block_before_marker": "confirmed",
                               "o_cup_before_block": "rejected"}),
    ("The cup goes in after the block.", {"o_block_before_cup": "confirmed", "o_cup_before_block": "rejected"}),
    ("You can pack them in any order.", {"o_cup_before_block": "rejected", "o_block_before_marker": "rejected"}),
]


@pytest.mark.parametrize("text,expected", ORDER, ids=[t for t, _ in ORDER])
def test_ordering(spec, text, expected):
    new, _ = compile_correction(spec, text)
    st = {o.id: o.status.value for o in new.ordering}
    assert {k: st.get(k) for k in expected} == expected


ASK = ["They must stay upright.", "Keep it level.", "The bottle must stay upright.",
       "The cup must stay upright while carried. Actually no, it can tilt."]


@pytest.mark.parametrize("text", ASK)
def test_asks(spec, text):
    with pytest.raises(NeedsClarification):
        compile_correction(spec, text)


@pytest.mark.parametrize("text", ["Make it look nicer.", "Hurry up."])
def test_rejects(spec, text):
    with pytest.raises(CorrectionRejected):
        compile_correction(spec, text)
