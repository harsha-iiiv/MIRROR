"""Reconstruction, observability and human-hand events from SYNTHETIC 2D
observations (exact projections of a known motion; no video)."""
import numpy as np
import pytest

from mirror.capture import events as EV
from mirror.capture import proxy as PX
from mirror.capture import reconstruct as RC
from mirror.capture import synthetic as S
from mirror.capture import sync as Y
from mirror.capture.track import Obs2D, TrackSet

from .conftest import truth_camera_models

PLAN = S.TakePlan()
ORIGIN = np.array([-0.075, 0.105, 0.0])
CAMS = S.default_cameras()
MODELS = truth_camera_models(CAMS)
OFFSETS = {"top": 0.0, "side": -0.37}
FPS = {"top": 20.0, "side": 15.0}
MUG = PX.ObjectMapping(track_label="mug", real_identity="mug (synthetic)", proxy="cup",
                       real_dims={"height": PX.Dimension(value_m=0.095, source="measured_ruler")}, confirmed_by_person=True)


def project_track(cam, fn, times, offset, *, lost=(), partial=(), status="model", half=(0.04, 0.04, 0.0475)):
    obs = []
    for k, tc in enumerate(times):
        tw = tc + offset
        if any(a <= tw < b for a, b in lost):
            obs.append(Obs2D(frame=k, t=tc, status="lost"))
            continue
        p = fn(tw)
        uv = cam.project(p[None])[0]
        corners = np.array([p + np.array([sx * half[0], sy * half[1], sz * half[2]]) for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)])
        c = cam.project(corners)
        box = (*c.min(0), *(c.max(0) - c.min(0)))
        obs.append(Obs2D(frame=k, t=tc, status=status, cx=float(uv[0]), cy=float(uv[1]), box=tuple(float(v) for v in box),
                         partial=any(a <= tw < b for a, b in partial)))
    return obs


def tracks_for(fn, *, cams=("top", "side"), lost=None, partial=None, duration=6.0):
    out = {}
    for c in cams:
        times = S.frame_times(int((duration - OFFSETS[c]) * FPS[c]), FPS[c])
        obs = project_track(CAMS[c], fn, times, OFFSETS[c], lost=(lost or {}).get(c, ()), partial=(partial or {}).get(c, ()))
        out[c] = [TrackSet(camera=c, clip_sha256="synthetic", source="model", model="synthetic-projection",
                           model_origin="recorded_replay", tracks={"mug": obs})]
    return out


def mug_world(t):
    return PLAN.mug_pose(t)[0]


SYNC = Y.SyncResult(ok=True, method="card", offset_s=0.37, scale=1.0, residual_s=0.0, uncertainty_s=0.01)


def test_two_view_triangulation_is_measured_and_accurate():
    rec = RC.reconstruct({c: MODELS[c] for c in ("top", "side")}, tracks_for(mug_world), [MUG], sync=SYNC)
    assert rec.mode == "two_view"
    tr = rec.objects["mug"]
    errs = [np.linalg.norm(np.array(p) - (mug_world(t) - ORIGIN)) for t, p, s in zip(tr.t, tr.pos, tr.pos_status) if s == "measured"]
    assert tr.coverage() > 0.95
    assert np.median(errs) < 0.004
    assert all(s in ("measured", "interpolated") for s in tr.pos_status)


def test_unsynchronised_cameras_never_triangulate():
    bad = Y.SyncResult(ok=False, reasons=["refused"])
    rec = RC.reconstruct({c: MODELS[c] for c in ("top", "side")}, tracks_for(mug_world), [MUG], sync=bad)
    assert rec.mode == "single_view"
    assert all(r is None for r in rec.objects["mug"].triangulation_residual_m)
    assert any("not synchronised" in n for n in rec.notes)


def test_single_top_view_rest_measured_lift_inferred_tilt_unobserved():
    rec = RC.reconstruct({"top": MODELS["top"]}, tracks_for(mug_world, cams=("top",)), [MUG])
    tr = rec.objects["mug"]
    by_t = dict(zip(tr.t, zip(tr.pos_status, tr.pos_sigma_m, tr.pos)))
    s0, sig0, p0 = by_t[min(tr.t, key=lambda t: abs(t - 0.5))]
    s1, sig1, p1 = by_t[min(tr.t, key=lambda t: abs(t - 3.0))]
    assert s0 == "measured" and np.linalg.norm(np.array(p0) - (mug_world(0.5) - ORIGIN)) < 0.005
    assert s1 == "inferred" and sig1 > 5 * sig0          # lifted: height assumed, parallax uncertainty reported
    assert all(st == "unobserved" for st in tr.tilt_status)
    assert all(v is None for v in tr.tilt_deg)            # never a default upright value


def test_oblique_single_view_cannot_place_the_object():
    rec = RC.reconstruct({"side": MODELS["side"]}, tracks_for(mug_world, cams=("side",)), [MUG], reference="side")
    assert all(s == "unobserved" for s in rec.objects["mug"].pos_status)


def test_short_gap_interpolated_long_gap_unobserved():
    rec = RC.reconstruct({c: MODELS[c] for c in ("top", "side")},
                         tracks_for(mug_world, lost={"top": [(0.5, 0.8), (2.0, 3.5)], "side": [(0.5, 0.8), (2.0, 3.5)]}), [MUG], sync=SYNC)
    tr = rec.objects["mug"]
    st = dict(zip(np.round(tr.t, 2), tr.pos_status))
    assert st[0.6] == "interpolated"
    assert st[2.8] == "unobserved" and tr.pos[tr.t.index(min(tr.t, key=lambda t: abs(t - 2.8)))] is None


def test_partial_occlusion_is_not_a_clean_measurement():
    rec = RC.reconstruct({c: MODELS[c] for c in ("top", "side")},
                         tracks_for(mug_world, partial={"top": [(1.0, 1.5)]}), [MUG], sync=SYNC)
    tr = rec.objects["mug"]
    k = tr.t.index(min(tr.t, key=lambda t: abs(t - 1.2)))
    assert tr.pos_status[k] == "inferred" and "partly hidden" in tr.pos_basis[k]


def test_unconfirmed_mapping_is_not_reconstructed():
    m = MUG.model_copy(update={"confirmed_by_person": False})
    rec = RC.reconstruct({"top": MODELS["top"]}, tracks_for(mug_world, cams=("top",)), [m])
    assert rec.objects == {}


def _events(lost=None, partial=None):
    rec = RC.reconstruct({c: MODELS[c] for c in ("top", "side")}, tracks_for(mug_world, lost=lost, partial=partial), [MUG], sync=SYNC)
    tray = EV.TrayRegion(center_xy=tuple((np.array([0.25, 0.04, 0]) - ORIGIN)[:2]), inner_half=(0.12, 0.09), source="person_supplied")
    return rec, EV.detect_events(rec.objects["mug"], tray)


def test_pick_and_place_brackets_contain_the_true_moments():
    _, evs = _events()
    pick = next(e for e in evs if e.type == "pick")
    place = next(e for e in evs if e.type == "place")
    assert pick.t_lo <= PLAN.pick <= pick.t_hi + 0.05
    assert place.t_lo - 0.05 <= PLAN.place <= place.t_hi + 0.05
    assert place.placed_in == "tray"
    for e in evs:
        assert e.actor == "human_hand" and e.basis == "object_motion" and "not a robot contact" in e.label


def test_occlusion_widens_the_bracket_and_requires_review():
    rec, evs = _events(lost={"top": [(1.6, 2.6)], "side": [(1.6, 2.6)]})
    pick = next(e for e in evs if e.type == "pick")
    assert pick.t_hi - pick.t_lo >= 0.9
    assert pick.t_lo <= PLAN.pick <= pick.t_hi
    assert pick.ambiguous
    items = EV.review_items(evs, rec.objects)
    assert any(i["code"] == "ambiguous_event_interval" and i["severity"] == "abstain" for i in items)


def test_person_corrections_are_recorded_and_resolve_ambiguity():
    rec, evs = _events(lost={"top": [(1.6, 2.6)], "side": [(1.6, 2.6)]})
    pick = next(e for e in evs if e.type == "pick")
    corr = [EV.Correction(target=pick.id, field="t", before=str(pick.t), after="2.0", by="synthetic_test", seconds_spent=12.0)]
    evs = EV.apply_corrections(evs, corr)
    pick = next(e for e in evs if e.type == "pick")
    assert pick.status == "person_corrected" and pick.t == 2.0 and not pick.ambiguous
    assert not any(i.get("event") == pick.id for i in EV.review_items(evs, rec.objects))


def test_no_motion_no_events():
    rec = RC.reconstruct({c: MODELS[c] for c in ("top", "side")}, tracks_for(lambda t: mug_world(0.0)), [MUG], sync=SYNC)
    assert EV.detect_events(rec.objects["mug"]) == []


# ---------------------------------------------------------------- proxies


def test_fidelity_statement_separates_measured_supplied_and_assumed():
    m = PX.ObjectMapping(track_label="mug", real_identity="white ceramic mug", proxy="cup",
                         real_dims={"height": PX.Dimension(value_m=0.095, source="measured_ruler"),
                                    "width": PX.Dimension(value_m=0.08, source="supplied_manually")},
                         unsupported_features=["handle"], real_contents="dry rice",
                         assumptions=[PX.Assumption(name="mass_kg", value="0.30"), PX.Assumption(name="friction", value="0.8", basis="proxy_default")],
                         confirmed_by_person=True)
    s = m.fidelity_statement()
    assert "Measured: height" in s and "not measured: width" in s
    assert "Not modelled: handle" in s
    assert "dry rice" in s and "simulated, not measured" in s
    assert "not measured physical accuracy" in s


def test_mapping_review_items():
    items = PX.review_items([PX.ObjectMapping(track_label="x", real_identity="?", proxy="cup"),
                             PX.ObjectMapping(track_label="t1", real_identity="tray", proxy="tray", confirmed_by_person=True),
                             PX.ObjectMapping(track_label="t2", real_identity="lid", proxy="tray", confirmed_by_person=True),
                             PX.ObjectMapping(track_label="big", real_identity="large mug", proxy="cup", confirmed_by_person=True,
                                              real_dims={"height": PX.Dimension(value_m=0.20, source="measured_ruler")})])
    codes = {i["code"] for i in items}
    assert {"mapping_unconfirmed", "multiple_trays", "proxy_size_mismatch"} <= codes


def test_tray_review_blocks_without_a_destination_and_flags_assumed_footprints():
    from mirror.capture import session as SE
    items = SE.tray_review(None, [MUG])
    assert [i.code for i in items] == ["tray_not_mapped"] and items[0].severity == "abstain"
    assert "Map the tray" in items[0].message and "no object is mapped" in items[0].message
    tray_map = PX.ObjectMapping(track_label="tray", real_identity="tray", proxy="tray", confirmed_by_person=True)
    assert "never located" in SE.tray_review(None, [MUG, tray_map])[0].message
    ok = EV.TrayRegion(center_xy=(0.3, 0.1), inner_half=(0.12, 0.09), footprint_source="measured_ruler")
    assert SE.tray_review(ok, [MUG]) == []
    assumed = ok.model_copy(update={"footprint_source": "assumed_proxy"})
    assert [(i.code, i.severity) for i in SE.tray_review(assumed, [MUG])] == [("tray_footprint_assumed", "review")]
    huge = ok.model_copy(update={"inner_half": (0.5, 0.09)})
    assert [i.code for i in SE.tray_review(huge, [MUG])] == ["tray_size_unsupported"]


def test_gate_blocks_while_the_tray_is_not_mapped():
    from mirror.capture import session as SE
    rec = RC.reconstruct({c: MODELS[c] for c in ("top", "side")}, tracks_for(mug_world), [MUG], sync=SYNC)
    s = SE.CaptureSession(synthetic=True, mappings=[MUG], reconstruction=rec, mode=rec.mode)
    s.review = SE.tray_review(None, s.mappings)
    ok, blocking = s.gate()
    assert not ok and blocking[0].code == "tray_not_mapped"
