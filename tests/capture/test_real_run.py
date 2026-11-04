"""mirror.capture.real_run: the one-command real-footage pipeline (fast unit tests).

The end-to-end proof on real footage (D1, replayed from its recorded
cassettes) is tests/capture/test_real_run_d1.py (slow, skipped without the
D1 data)."""
import json
from pathlib import Path

import numpy as np
import pytest

from mirror.capture import real_run as RR

REPO = Path(__file__).resolve().parents[2]


def _meas(tmp_path, **over):
    m = {"print_check": {"measured_bar_mm": 50.0, "measured_square_mm": 30.0, "measured_by": "person"},
         "table_name": "the table",
         "objects": [{"label": "mug", "name": "the white mug", "short": "mug", "proxy": "cup", "dims": {"height": 0.12}},
                     {"label": "box", "name": "the red box", "short": "red box", "proxy": "block"},
                     {"label": "pen", "name": "the pen", "short": "marker", "proxy": "marker"},
                     {"label": "tray", "name": "the grey tray", "proxy": "tray",
                      "dims": {"width": {"value_m": 0.30, "note": "inside"}, "depth": 0.2}},
                     {"label": "divider", "name": "the divider", "short": "divider", "proxy": "wall",
                      "dims": {"width": 0.4, "depth": 0.025, "height": 0.29}}]}
    m.update(over)
    p = tmp_path / "m.json"
    p.write_text(json.dumps(m))
    return p


def test_measurements_validated(tmp_path):
    m = RR.load_measurements(_meas(tmp_path))
    assert [o["label"] for o in m["objects"]][:2] == ["mug", "box"]
    maps = RR.mappings_from(m, confirmed={"mug"})
    assert maps[0].confirmed_by_person and not maps[1].confirmed_by_person
    assert maps[0].real_dims["height"].source == "measured_ruler"
    assert maps[3].real_dims["width"].note == "inside"
    with pytest.raises(SystemExit):
        RR.load_measurements(_meas(tmp_path, print_check={"measured_bar_mm": 50.0}))
    bad = json.loads(_meas(tmp_path).read_text())
    bad["objects"][0]["proxy"] = "robot"
    (tmp_path / "b.json").write_text(json.dumps(bad))
    with pytest.raises(SystemExit):
        RR.load_measurements(tmp_path / "b.json")


def test_rejected_mapping_is_ignored_not_confirmed(tmp_path):
    m = RR.load_measurements(_meas(tmp_path))
    maps = RR.mappings_from(m, confirmed=set(), rejected={"pen"})
    pen = next(x for x in maps if x.track_label == "pen")
    assert pen.proxy == "ignore" and not pen.confirmed_by_person


def test_ledger_cap_checked_before_the_call(tmp_path):
    led = RR.Ledger(tmp_path / "spend.jsonl", cap_usd=1.0, prior_usd=0.5)
    led.guard(0.2, "ok call")
    assert led.charge({"model": "sam-3.1", "call": "sam_segment", "from_cassette": True, "usage": {}}) == 0.0
    c = led.charge({"model": "muse-spark-1.3", "call": "responses", "from_cassette": False,
                    "usage": {"input_tokens": 100000, "output_tokens": 50000}})
    assert c == pytest.approx((100000 * 1.25 + 50000 * 4.25) / 1e6)
    assert led.spent() == pytest.approx(0.5 + c)
    with pytest.raises(RR.SpendCapReached):
        led.guard(0.5, "a call that could cross 95% of the cap")
    assert len((tmp_path / "spend.jsonl").read_text().splitlines()) == 1    # cassette replays are not charged


def test_replay_transport_refuses_any_network_request(tmp_path):
    from mirror.meta import MetaClient, text_part
    c = MetaClient(mode="live", api_key="not-a-key", cassette_dir=tmp_path, transport=RR._no_network_transport(), max_retries=0)
    with pytest.raises(Exception) as e:
        c.responses("muse-spark-1.3", [text_part("hello")])
    assert "replay mode: refused a live request" in str(e.value) or isinstance(e.value, RR.LiveCallInReplay)
    r = MetaClient(mode="replay", cassette_dir=tmp_path, transport=RR._no_network_transport())
    from mirror.meta.cassette import MetaCassetteMiss
    with pytest.raises(MetaCassetteMiss):
        r.responses("muse-spark-1.3", [text_part("hello")])


def test_guarded_meta_stops_before_spending(tmp_path):
    class Fake:
        mode = "record"
        usage = []

        def _log(self, *a, **k):
            pass

        def responses(self, *a, **k):
            raise AssertionError("must not be called")
    led = RR.Ledger(tmp_path / "s.jsonl", cap_usd=0.03)
    g = RR.GuardedMeta(Fake(), led)
    with pytest.raises(RR.SpendCapReached):
        g.responses("muse-spark-1.3", [])


def test_fast_sam_parser_matches_the_official_decoder():
    pytest.importorskip("meta_sam_parser")
    from mirror.meta import sam as S
    files = sorted((REPO / "data" / "cassettes_meta" / "sam-3.1").glob("*.json"))
    text = None
    for f in files[:300]:
        t = json.loads(f.read_text())["response"].get("text", "")
        if "<|mask;" in t and t.lstrip().startswith("<0f>"):
            text = "\n".join(t.splitlines()[:3]) + "\n"
            break
    if text is None:
        pytest.skip("no recorded SAM video response in data/cassettes_meta")
    orig = getattr(S, "_orig_parse", S.parse_sam_output)
    a = orig(text, media="video")
    b = RR._fast_parse(text, media="video")
    assert [f.index for f in a.frames] == [f.index for f in b.frames]
    for fa, fb in zip(a.frames, b.frames):
        for oa, ob in zip(fa.objects, fb.objects):
            assert oa.object_id == ob.object_id
            assert np.array_equal(oa.full_mask(a.width, a.height), ob.full_mask(b.width, b.height))


def test_pick_ambiguity_found_only_for_a_short_move_just_before_the_carry():
    tl = {"events": [{"object": "mug", "from_s": 13.53, "to_s": 13.7, "in_tray": False},
                     {"object": "mug", "from_s": 14.57, "to_s": 16.77, "in_tray": True},
                     {"object": "box", "from_s": 21.0, "to_s": 23.6, "in_tray": True},
                     {"object": "pen", "from_s": 20.0, "to_s": 21.5, "in_tray": False},
                     {"object": "pen", "from_s": 27.5, "to_s": 31.3, "in_tray": True}]}
    amb = RR.RealRun._pick_ambiguities(tl)
    assert list(amb) == ["mug"]
    assert amb["mug"][0]["from_s"] == 13.53 and amb["mug"][1]["from_s"] == 14.57


def _stub_run(tmp_path, answers=None):
    r = RR.RealRun.__new__(RR.RealRun)
    r.meas = RR.load_measurements(_meas(tmp_path))
    r.out = tmp_path
    r.answers_path = tmp_path / "answers.json"
    if answers is not None:
        r.answers_path.write_text(json.dumps(answers))
    r.state = {"stages": {"seed": {"frame": 375, "t": 12.5}}}
    return r


def test_questions_cover_objects_events_calibration_and_intent(tmp_path):
    from mirror.capture.session import CaptureSession, ReviewItem
    r = _stub_run(tmp_path)
    s = CaptureSession(review=[ReviewItem(code="recalibration_required", severity="abstain", message="top: focal +7.1%")])
    tl = {"events": [{"object": "mug", "from_s": 13.5, "to_s": 13.7, "in_tray": False},
                     {"object": "mug", "from_s": 14.6, "to_s": 16.8, "in_tray": True},
                     {"object": "box", "from_s": 21.0, "to_s": 23.6, "in_tray": True},
                     {"object": "pen", "from_s": 27.5, "to_s": 31.3, "in_tray": True}]}
    qs = r._questions(s, tl, {})
    ids = [q["id"] for q in qs]
    assert ids[:5] == ["mug", "box", "pen", "tray", "divider"]
    assert "mug_pickup" in ids and "camera_settings" in ids and ids[-1] == "intent"
    intent = qs[-1]
    assert intent["order_seen"] == ["mug", "box", "pen"]
    assert "Order mattered too (mug, red box, marker)" in intent["options"]
    assert "around the divider" in intent["proposal"] and "keeping the mug upright" in intent["proposal"]
    cands = r.correction_candidates()
    assert [c["id"] for c in cands] == ["mug_clearance", "box_upright", "no_table_contact"]


def test_only_answers_by_the_person_are_applied(tmp_path):
    r = _stub_run(tmp_path, {"answers": {"mug": {"answer": "Yes, that's the mug", "by": "person", "at": 1},
                                         "box": {"answer": "Yes", "by": "mana", "at": 1},
                                         "pen": {"answer": None, "by": "person"}}})
    assert list(r._load_answers()) == ["mug"]


def test_stated_spec_from_intent(tmp_path):
    from mirror.capture.session import CaptureSession
    r = _stub_run(tmp_path)
    maps = RR.mappings_from(r.meas, confirmed={"mug", "box", "pen", "tray", "divider"})
    s = CaptureSession(mappings=maps)
    spec = r._stated_spec(s, {"answer": "Order mattered too (mug, red box, marker)", "order_seen": ["mug", "box", "pen"],
                              "proposal": "Put the mug, red box and marker into the tray"})
    assert [o.id for o in spec.ordering] == ["o_cup_before_block", "o_block_before_marker"]
    assert {c.type for c in spec.constraints} == {"upright", "forbidden_contact"}
    assert all(g.status == "confirmed" for g in spec.goals) and spec.goals[0].provenance.source == "manual"
    assert "not a model" in spec.interpreter
    spec2 = r._stated_spec(s, {"answer": "As proposed", "order_seen": ["mug", "box", "pen"], "proposal": "p"})
    assert spec2.ordering == []
    with pytest.raises(RR.StageStop):
        r._stated_spec(s, {"answer": "Something else", "order_seen": [], "proposal": "p"})
    with pytest.raises(RR.StageStop):
        r._stated_spec(CaptureSession(mappings=RR.mappings_from(r.meas)), {"answer": "As proposed", "proposal": "p"})


def test_reach_check_flags_objects_outside_the_workspace():
    from mirror.scenes.reference import reference_scene
    sc = reference_scene("demo")
    assert RR.RealRun.reach_problems(sc) == []
    far = sc.model_copy(deep=True)
    o = far.objects[0]
    far.objects[0] = o.model_copy(update={"pose": o.pose.model_copy(update={"pos": (0.9, 0.0, o.pose.pos[2])})})
    assert any("outside the robot's reach" in p for p in RR.RealRun.reach_problems(far))


def test_cli_requires_inputs(tmp_path):
    with pytest.raises(SystemExit):
        RR.main(["--out", str(tmp_path / "o"), "--take", str(tmp_path / "missing.mov")])



def _robot_stub(tmp_path):
    from mirror.capture import real_run as RR2
    r = RR2.RealRun.__new__(RR2.RealRun)
    meas = {"print_check": {"measured_bar_mm": 50.0, "measured_square_mm": 30.0, "measured_by": "person"},
            "objects": [{"label": "mug", "name": "the mug", "short": "mug", "proxy": "cup"},
                        {"label": "tray", "name": "the tray", "proxy": "tray"}]}
    (tmp_path / "m.json").write_text(json.dumps(meas))
    r.meas = RR2.load_measurements(tmp_path / "m.json")
    r.out = tmp_path
    r.answers_path = tmp_path / "answers.json"
    r.mode = "offline"
    r.ledger = RR2.Ledger(tmp_path / "spend.jsonl", 1.0)
    r.state = {"stages": {}, "version": 1}
    r.state_path = tmp_path / "real_run.json"
    r.take = tmp_path / "take.mp4"
    r.lens = None
    r.dev_clip = None
    r.label_extra = []
    r.render_on = False
    r.rehearse = None
    r._meta = r._llm = None
    r.log = lambda *a: None
    return r


def test_gate_passed_branch_waits_for_rule_review_then_plans_and_runs(tmp_path):
    """SYNTHETIC session that passes the gate: offline proposal -> the person's
    review of the proposed rules -> scene in the real table frame -> MuJoCo run."""
    pytest.importorskip("mirror.observability")
    from mirror.contracts import DemoTrace
    if "evidence" not in DemoTrace.model_fields:
        pytest.skip("observability not integrated")
    from .test_trace import _session
    r = _robot_stub(tmp_path)
    s = _session()
    assert s.gate()[0]
    s.save(tmp_path / "session_answered.json")
    try:
        r.stage_task()
        waited = False
    except RR.StageStop as e:
        waited = True
        assert "review" in str(e)
        qs = json.loads((tmp_path / "questions_spec.json").read_text())["questions"]
        ids = [q["id"].removeprefix("rule:") for q in qs]
        (tmp_path / "answers.json").write_text(json.dumps({"spec_review": {"answer": {"confirm": ids, "reject": []},
                                                                           "by": "person", "at": 1}}))
        r.stage_task()
    t = r.info("task")
    assert t["gate_passed"] and "MIRROR's proposal" in t["spec_source"]
    assert waited or not any(x["status"] == "proposed" for x in t["rules"])
    r.stage_scene()
    sc = r.info("scene")
    assert sc["placement"] in ("default_centred", "auto_layout_centred")
    tts = json.loads((tmp_path / "table_to_sim.json").read_text())
    assert "origin_xy_m" in tts and "yaw_deg" in tts
    r.stage_run()
    run = r.info("run")
    assert run["result"] in ("success", "unverified", "task_incomplete", "constraint_violated", "planning_failed")
    page = json.loads((tmp_path / "page.json").read_text())
    assert page["run"]["result"] == run["result"] and "manifest.json" in [p.name for p in tmp_path.iterdir()]



def test_record_mode_charges_the_ledger_and_saves_a_cassette(tmp_path):
    """Record mode against a fake endpoint: the live call is charged once, saved as a
    cassette, and the identical request is then served from the cassette for free."""
    import httpx
    from mirror.meta import MetaClient, text_part
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"status": "completed", "model": "muse-spark-1.3",
                                         "usage": {"input_tokens": 1000, "output_tokens": 2000},
                                         "output": [{"type": "message", "content": [{"type": "output_text", "text": "{}"}]}]})
    led = RR.Ledger(tmp_path / "spend.jsonl", cap_usd=1.0)
    c = RR.GuardedMeta(MetaClient(mode="record", api_key="test", cassette_dir=tmp_path / "cas",
                                  transport=httpx.MockTransport(handler)), led)
    r1 = c.responses("muse-spark-1.3", [text_part("find the mug")])
    r2 = c.responses("muse-spark-1.3", [text_part("find the mug")])
    assert not r1.from_cassette and r2.from_cassette and len(calls) == 1
    assert led.spent() == pytest.approx((1000 * 1.25 + 2000 * 4.25) / 1e6)
    assert len(list((tmp_path / "cas").rglob("*.json"))) == 1
    replay = MetaClient(mode="replay", cassette_dir=tmp_path / "cas", transport=RR._no_network_transport())
    assert replay.responses("muse-spark-1.3", [text_part("find the mug")]).from_cassette



def test_c_sam_decoder_matches_the_official_decoder():
    pytest.importorskip("meta_sam_parser")
    import re
    if RR._c_decoder() is None:
        pytest.skip("no C compiler: the official decoder is used")
    n = 0
    for f in sorted((REPO / "data" / "cassettes_meta" / "sam-3.1").glob("*.json"))[:300]:
        t = json.loads(f.read_text())["response"].get("text", "")
        for line in t.splitlines()[:2]:
            m = re.match(r"<(\d+)f>", line)
            if not m:
                continue
            for r in RR._REC.finditer(line[m.end():]):
                a = (int(r.group(11)), int(r.group(12)), r.group(13))
                assert np.array_equal(RR._dec(a), RR._decode_fast(*a))
                n += 1
        if n >= 40:
            break
    if n == 0:
        pytest.skip("no recorded SAM masks in data/cassettes_meta")



def test_upscaled_moments_equal_the_full_resolution_import():
    from types import SimpleNamespace
    from mirror.capture import track as TR
    rng = np.random.default_rng(3)
    thr = TR.TRACK_THRESHOLDS["elongation_for_axis"]
    for k in (2, 3):
        a = np.zeros((40, 60), bool)
        a[5:30, 10:18] = True
        a[rng.integers(0, 40, 30), rng.integers(0, 60, 30)] = True
        full = np.repeat(np.repeat(a, k, 0), k, 1)
        meta = SimpleNamespace(pts_s=[0.0], sha256="x")
        ref = TR.from_masks(meta, {"o": {0: full}}, camera="top", model="m", model_origin="recorded_replay").tracks["o"][0]
        got = RR._upscaled_moments(a, k, thr)
        assert got["cx"] == pytest.approx(ref.cx) and got["cy"] == pytest.approx(ref.cy)
        assert got["box"] == pytest.approx(ref.box) and got["elongation"] == pytest.approx(ref.elongation)
        assert (got["axis_deg"] is None) == (ref.axis_deg is None)
        if ref.axis_deg is not None:
            assert got["axis_deg"] == pytest.approx(ref.axis_deg)
    assert RR._upscaled_moments(np.zeros((4, 4), bool), 2, thr) is None



def test_correction_choice_person_first_then_labelled_rehearsal(tmp_path):
    r = _stub_run(tmp_path, {"correction": {"answer": "box_upright", "by": "person", "at": 1}})
    r.rehearse = "mug_clearance"
    c, rehearsal, a = r._choose_correction()
    assert c["id"] == "box_upright" and rehearsal is False
    r2 = _stub_run(tmp_path / "x", None) if (tmp_path / "x").mkdir() is None else None
    r2.rehearse = "mug_clearance"
    c, rehearsal, a = r2._choose_correction()
    assert c["id"] == "mug_clearance" and rehearsal is True and a is None
    r3 = _stub_run(tmp_path / "y", None) if (tmp_path / "y").mkdir() is None else None
    r3.rehearse = None
    assert r3._choose_correction() == (None, None, None)
    r4 = _stub_run(tmp_path / "z", {"correction": {"answer": "Stay 10 cm from the tray edge", "by": "person", "at": 1}}) \
        if (tmp_path / "z").mkdir() is None else None
    r4.rehearse = None
    c, rehearsal, _ = r4._choose_correction()
    assert c["id"] == "custom" and c["compile_text"] == "Stay 10 cm from the tray edge" and rehearsal is False
