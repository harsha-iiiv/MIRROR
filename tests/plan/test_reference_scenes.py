"""Behaviour on the reference scenes (docs/CONTRACTS.md)."""
import math

import pytest

from mirror.plan import plan
from mirror.scenes import reference_scene

from .fixtures import base_spec, corrected_spec, original_spec
from .helpers import cached_plan, chosen_routes, max_transport_tilt, replay_collisions
from mirror.contracts import Status


def _routes(r, oid):
    return {ro.label: ro for ro in r.routes if ro.object_id == oid and not ro.label.startswith("transit")}


def test_shortcut_unconstrained_tilts_cup_through_slot():
    r = cached_plan("shortcut", "orig")
    assert r.status == "ok", r.reason
    assert chosen_routes(r)["cup_0"].startswith("through_slot:wall_0:0:pitched")
    assert max_transport_tilt(r, "cup_0") > 80.0
    upright = _routes(r, "cup_0")["through_slot:wall_0:0:upright"]
    assert not upright.feasible and "exceeds slot opening" in upright.rejected_reason


def test_shortcut_corrected_carries_cup_around_upright():
    r = cached_plan("shortcut", "corr")
    assert r.status == "ok", r.reason
    assert chosen_routes(r)["cup_0"] == "around:wall_0:+y"
    assert max_transport_tilt(r, "cup_0") <= 15.0
    rs = _routes(r, "cup_0")
    for m in ("pitched+90", "pitched-90"):
        ro = rs[f"through_slot:wall_0:0:{m}"]
        assert not ro.feasible and "c_cup_upright_transport" in ro.rejected_reason


def test_correction_is_scoped_to_the_cup():
    orig, corr = cached_plan("shortcut", "orig"), cached_plan("shortcut", "corr")
    for oid in ("marker_0", "block_0"):
        ro, rc = _routes(orig, oid), _routes(corr, oid)
        for label in ro:
            assert ro[label].feasible == rc[label].feasible, (oid, label)
            assert ro[label].cost == pytest.approx(rc[label].cost) if ro[label].cost else True
        assert rc["through_slot:wall_0:0:pitched+90"].feasible  # the marker may still tilt
    # the marker is still allowed to tilt through the slot when that route is preferred
    r = plan(corrected_spec(), reference_scene("shortcut"), prefer="marker_0:through_slot")
    assert r.status == "ok"
    assert chosen_routes(r)["marker_0"].startswith("through_slot")
    assert max_transport_tilt(r, "marker_0") > 80.0
    assert max_transport_tilt(r, "cup_0") <= 15.0


@pytest.mark.parametrize("status", [Status.proposed, Status.rejected])
def test_unconfirmed_rules_do_not_restrict(status):
    r = plan(base_spec(status), reference_scene("shortcut"))
    assert chosen_routes(r)["cup_0"].startswith("through_slot")


def test_blocked_corrected_has_proven_infeasibility():
    r = cached_plan("blocked", "corr")
    assert r.status == "no_plan"
    cert = r.infeasibility
    assert cert and cert["proven"] is True
    assert cert["object_id"] == "cup_0" and cert["wall_id"] == "wall_0"
    assert cert["constraint_ids"] == ["c_cup_upright_transport"]
    q = cert["quantities"]
    assert q["min_vertical_span_m"] > q["openings_vertical_span_m"]
    assert q["required_along_range_m"][0] >= q["wall_along_range_m"][0]
    assert q["required_along_range_m"][1] <= q["wall_along_range_m"][1]
    assert r.steps == []


def test_blocked_unconstrained_still_plans():
    r = cached_plan("blocked", "orig")
    assert r.status == "ok", r.reason
    assert all(v.startswith("through_slot") for v in chosen_routes(r).values())


def test_demo_goes_around_the_wall():
    r = cached_plan("demo", "orig")
    assert r.status == "ok"
    assert set(chosen_routes(r).values()) == {"around:wall_0:+y"}


@pytest.mark.parametrize("scene,spec", [("shortcut", "orig"), ("shortcut", "corr"), ("blocked", "orig"),
                                        ("demo", "orig")])
def test_plans_are_geometrically_valid(scene, spec):
    r = cached_plan(scene, spec)
    sc = reference_scene(scene)
    ws = sc.workspace
    kinds = [s.kind for s in r.steps]
    assert kinds == ["move", "approach", "grasp", "lift", "transport", "place", "release", "retreat"] * 3
    for st in r.steps:
        for wp in st.waypoints:
            x, y, z = wp.pos
            assert ws.x_range[0] - 1e-9 <= x <= ws.x_range[1] + 1e-9
            assert ws.y_range[0] - 1e-9 <= y <= ws.y_range[1] + 1e-9
            assert ws.z_range[0] - 1e-9 <= z <= ws.z_range[1] + 1e-9
    assert replay_collisions(r, sc) == []
    # releases happen inside the tray, objects upright
    tray = sc.trays[0]
    for st in r.steps:
        if st.kind == "release":
            x, y, _ = st.waypoints[0].pos
            assert abs(x - tray.pose.pos[0]) < tray.inner_half[0]
            assert abs(y - tray.pose.pos[1]) < tray.inner_half[1]
            q = st.waypoints[0].quat
            assert abs(q[1]) < 1e-6 and abs(q[2]) < 1e-6  # gripper vertical at release


def test_rejection_reasons_name_only_decisive_blockers():
    r = cached_plan("blocked", "corr")
    for ro in r.routes:
        reason = ro.rejected_reason or ""
        for oid in ("cup_0", "block_0", "marker_0"):
            assert f"blocked by {oid}" not in reason and f", {oid}" not in reason, (ro.label, reason)
    cup = {ro.label: ro.rejected_reason for ro in r.routes if ro.object_id == "cup_0"}
    assert cup["direct"].endswith("(blocked by obstacle:wall_0)")
    assert "beyond the workspace limit" in cup["around:wall_0:+y"]
    assert "beyond the workspace limit" in cup["around:wall_0:-y"]
