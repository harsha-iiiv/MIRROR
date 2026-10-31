from mirror.interpret import compile_correction, confirm_items, propose_spec, spec_diff
from tests.interpret.fixtures import make_demo


def test_diff_shows_status_and_resolution_changes():
    s1 = propose_spec(make_demo())
    s2, _ = confirm_items(s1, confirm=["c_upright_final_cup"])
    s3, _ = compile_correction(s2, "Keep the cup within 10 degrees of upright while carried.")
    d = spec_diff(s2, s3)
    by = {(x["id"], x["change"]) for x in d}
    assert ("c_upright_transport_cup", "status") in by
    assert ("c_upright_transport_cup", "params") in by
    assert ("u_upright_transport_cup", "status") in by
    assert all(x["id"].endswith("_cup") for x in d)
    assert spec_diff(s3, s3) == []


def test_diff_added_constraint():
    s1 = propose_spec(make_demo())
    s2, _ = compile_correction(s1, "The cup must not touch the wall.")
    added = [x for x in spec_diff(s1, s2) if x["change"] == "added"]
    assert [x["id"] for x in added] == ["c_forbidden_contact_transport_cup"]
