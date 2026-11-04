"""mirror.capture.real_run safeguards: answers bound to the run, rehearsal provenance, --redo leaves nothing
stale, stated-task provenance, gate whitelist, spend caps, working copies, and the publish hook (with a fake hook)."""
import json
from pathlib import Path

import pytest

from mirror.capture import real_run as RR
from .test_real_run import _meas

SHA = "a" * 64


def _run(tmp_path, take_sha=SHA, legacy=False):
    r = RR.RealRun.__new__(RR.RealRun)
    r.meas = RR.load_measurements(_meas(tmp_path))
    r.out = tmp_path
    r.answers_path = tmp_path / "answers.json"
    r.answers_from = None
    r.publish_to = None
    r.hook = None
    r.publish_dir = None
    r.legacy_d1 = legacy
    r.state = {"stages": {"seed": {"frame": 375, "t": 12.5}, "probe": {"take": {"sha256": take_sha}}}, "inputs": {}}
    r.state_path = tmp_path / "real_run.json"
    r.mode = "offline"
    r.dev_clip = None
    r.label_extra = []
    r.ledger = RR.Ledger(tmp_path / "spend.jsonl", 1.0, project=None)
    r.log = lambda *a: None
    return r


def _questions(tmp_path, session="cap_new", take=SHA, created=1000):
    qs = [{"id": "mug", "kind": "object", "question": "Is it the mug?", "options": ["Yes, that's the mug", "No / wrong object"]},
          {"id": "intent", "kind": "task", "question": "Task?", "options": ["As proposed", "Order mattered too (mug, red box, marker)",
                                                                              "Something else"]}]
    d = {"session_id": session, "take_sha256": take, "question_set_sha256": RR._canon_sha(qs), "created_at_ms": created,
         "questions": qs}
    (tmp_path / "questions.json").write_text(json.dumps(d))
    return d


def _ans(q, answer, **over):
    a = {"answer": answer, "by": "person", "uid": "u", "at": q["created_at_ms"] + 5, "seconds_spent": 2.0,
         "session_id": q["session_id"], "take_sha256": q["take_sha256"], "question_set_sha256": q["question_set_sha256"]}
    a.update(over)
    return a


def test_answers_bound_to_this_run_are_applied_and_stale_ones_refused(tmp_path):
    r = _run(tmp_path)
    q = _questions(tmp_path)
    (tmp_path / "answers.json").write_text(json.dumps({"answers": {
        "mug": _ans(q, "Yes, that's the mug"),
        "intent": _ans(q, "As proposed", take_sha256="b" * 64)}}))
    got = r._load_answers()
    assert list(got) == ["mug"]
    assert [x["item"] for x in r.state["answers_refused"]] == ["intent"]
    assert "take_sha256" in r.state["answers_refused"][0]["reason"]


@pytest.mark.parametrize("over,why", [({"session_id": "cap_old"}, "session_id"),
                                      ({"question_set_sha256": "x" * 64}, "question_set_sha256"),
                                      ({"at": 10}, "before these questions"),
                                      ({"by": "assistant"}, "not 'person'")])
def test_each_binding_field_is_checked(tmp_path, over, why):
    r = _run(tmp_path)
    q = _questions(tmp_path)
    (tmp_path / "answers.json").write_text(json.dumps({"answers": {"mug": _ans(q, "Yes, that's the mug", **over)}}))
    assert r._load_answers() == {}
    assert why in r.state["answers_refused"][0]["reason"]


def test_d1_answers_without_binding_are_refused_for_a_new_take(tmp_path):
    """D1's page answers (no binding fields) pointed at a new take apply nothing."""
    r = _run(tmp_path, take_sha="c" * 64)
    _questions(tmp_path, take="c" * 64)
    (tmp_path / "answers.json").write_text(json.dumps({"answers": {"mug": {"answer": "Yes, that's the mug", "by": "person",
                                                                           "at": 1793770963444}}}))
    assert r._load_answers() == {}
    r2 = _run(tmp_path, take_sha="c" * 64, legacy=True)
    with pytest.raises(SystemExit):            # the legacy override is refused for any take but D1's
        r2._load_answers()


def test_legacy_d1_override_only_for_d1_and_labelled(tmp_path):
    r = _run(tmp_path, take_sha=RR.D1_TAKE_SHA256, legacy=True)
    _questions(tmp_path, take=RR.D1_TAKE_SHA256, created=2_000_000_000_000)
    (tmp_path / "answers.json").write_text(json.dumps({"answers": {"mug": {"answer": "Yes, that's the mug", "by": "person",
                                                                           "at": 1793770963444}}}))
    assert list(r._load_answers()) == ["mug"]
    assert "legacy" in r.state["legacy_answers"]
    assert "Answers carried over from D1's own page (legacy, same take)" in r.labels()


def test_correction_and_spec_review_bindings(tmp_path):
    r = _run(tmp_path)
    q = _questions(tmp_path)
    (tmp_path / "spec_proposed.json").write_text(json.dumps({"spec_id": "s", "constraints": []}))
    good = {"spec_sha256": r._spec_sha(), "session_id": q["session_id"], "take_sha256": SHA,
            "rules": {"c_up": {"decision": "accept", "by": "person", "uid": "u", "at": 2000, "seconds_spent": 1},
                      "c_x": {"decision": "reject", "by": "person", "uid": "u", "at": 2001}}}
    (tmp_path / "answers.json").write_text(json.dumps({
        "spec_review": good, "correction": {"choice_id": "mug_clearance", "by": "person", "uid": "u", "at": 2002,
                                            "session_id": q["session_id"], "take_sha256": SHA}}))
    got = r._load_answers()
    assert got["spec_review"]["answer"] == {"confirm": ["c_up"], "reject": ["c_x"]}
    assert got["correction"]["answer"] == "mug_clearance"
    (tmp_path / "answers.json").write_text(json.dumps({"spec_review": {**good, "spec_sha256": "0" * 64},
                                                       "correction": {"text": "x", "by": "person", "at": 2002,
                                                                      "session_id": "cap_old", "take_sha256": SHA}}))
    got = r._load_answers()
    assert "spec_review" not in got and "correction" not in got
    assert {x["item"] for x in r.state["answers_refused"]} == {"spec_review", "correction"}


def test_hook_answers_go_to_their_own_file_and_never_overwrite_the_local_one(tmp_path):
    r = _run(tmp_path)
    q = _questions(tmp_path)
    r.answers_from = "D9"
    (tmp_path / "answers.json").write_text('{"answers": {}}')
    seen = []

    def fake(event, payload):
        seen.append((event, payload))
        return {"answers": {"mug": _ans(q, "Yes, that's the mug")}, "source": "store:D9"}
    r.hook = fake
    got = r._load_answers()
    assert list(got) == ["mug"]
    assert (tmp_path / "answers.json").read_text() == '{"answers": {}}'
    assert json.loads((tmp_path / "answers_hook.json").read_text())["source"] == "store:D9"
    assert seen == [("answers", {"run_id": "D9", "keys": ["answers", "spec_review", "correction"]})]
    assert any("not used" in p for p in r.state["problems"])


def test_hook_answers_are_still_bound_to_the_run(tmp_path):
    r = _run(tmp_path)
    q = _questions(tmp_path)
    r.answers_from = "D9"
    stale = {**_ans(q, "Yes, that's the mug"), "session_id": "cap_other"}
    r.hook = lambda event, payload: {"answers": {"mug": stale}}
    assert r._load_answers() == {}
    assert r.state["answers_refused"][0]["item"] == "mug"


def test_a_failing_hook_never_stops_the_run(tmp_path):
    r = _run(tmp_path)
    _questions(tmp_path)
    r.answers_from = r.publish_to = "D9"

    def boom(event, payload):
        raise ConnectionError("down")
    r.hook = boom
    assert r._fetch_hook_answers() is None
    r._hook_status("run", "running run")
    r._publish_hook({"labels": ["x"], "paths": {}})
    assert any("hook" in p for p in r.state["problems"])


def test_publish_hook_loads_module_function(tmp_path, monkeypatch):
    mod = tmp_path / "my_hook.py"
    mod.write_text("CALLS = []\ndef hook(event, payload):\n    CALLS.append((event, payload))\n    return {'ok': 1}\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    h = RR.PublishHook("my_hook:hook")
    assert h("status", {"run_id": "R", "p": tmp_path, "x": float("nan")}) == {"ok": 1}
    import my_hook
    assert my_hook.CALLS == [("status", {"run_id": "R", "p": str(tmp_path), "x": None})]
    with pytest.raises(SystemExit):
        RR.PublishHook("no_function_given")


def test_questions_node_matches_the_schema(tmp_path):
    r = _run(tmp_path)
    q = _questions(tmp_path)
    node = r._questions_node("mirror/capture/D9/pipeline")
    assert set(node) == {"session_id", "take_sha256", "question_set_sha256", "created_at_ms", "items"}
    assert node["question_set_sha256"] == q["question_set_sha256"]
    it = node["items"]["mug"]
    assert set(it) >= {"kind", "question", "proposal", "options", "evidence"} and set(it["evidence"]) == {"time_s", "image_path"}


def test_stated_spec_marks_default_thresholds_as_default(tmp_path):
    from mirror.capture.session import CaptureSession
    r = _run(tmp_path)
    s = CaptureSession(mappings=RR.mappings_from(r.meas, confirmed={"mug", "box", "pen", "tray", "divider"}))
    spec = r._stated_spec(s, {"answer": "As proposed", "order_seen": ["mug", "box", "pen"],
                              "proposal": "Put the mug, red box and marker into the tray, going around the divider, keeping the mug upright."})
    assert all(g.provenance.source == "manual" for g in spec.goals)
    assert {c.provenance.source for c in spec.constraints} == {"default"}
    assert all("not your words" in c.provenance.evidence for c in spec.constraints)
    with pytest.raises(RR.StageStop):     # free text is never read as a task
        r._stated_spec(s, {"answer": "pack them quickly", "order_seen": [], "proposal": "p"})


def test_rehearsal_correction_is_never_recorded_as_the_persons_words(tmp_path, monkeypatch):
    # the deterministic offline compiler, whatever the caller's environment says: with
    # MIRROR_LLM_MODE=replay this missed a cassette, and with a model key set it would call the model
    monkeypatch.setenv("MIRROR_LLM_MODE", "offline")
    from mirror.evaluate.specs import reference_specs
    from mirror.pipeline import Session
    from mirror.scenes.reference import reference_scene
    sess = Session(scene=reference_scene("demo"), client=None)
    sess.specs = [reference_specs()[0]]
    new, rec = sess.correct("Keep the cup at least 6 cm away from the wall while carrying it.")
    assert any(c.provenance.source == "user_correction" for c in new.constraints)    # what the compiler writes
    new2, rec2 = RR.RealRun._mark_rehearsal(sess, new, rec, {"id": "mug_clearance", "label": "Keep the mug 6 cm away"})
    blob = new2.model_dump_json()
    assert "user_correction" not in json.dumps([c.provenance.model_dump() for c in new2.constraints if
                                                c.provenance.version_introduced == new2.version])
    assert "REHEARSAL" in blob and "not chosen by the person" in new2.summary
    assert all(o.rationale.startswith("Rehearsal, not the person:") for o in rec2.diff)
    assert sess.specs[-1] is new2


def test_redo_moves_stale_outputs_and_manifest_lists_only_done_corrections(tmp_path):
    r = _run(tmp_path)
    for rel in ("intent.json", "spec.json", "correct/rollout_after.json", "questions_spec.json"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("{}")
    r.state["stages"].update({"answers": {"status": "done"}, "task": {"status": "done"},
                              "correct": {"status": "done", "rehearsal": True}})
    stale = r.forget("answers")
    assert stale is not None and (stale / "intent.json").exists() and (stale / "correct" / "rollout_after.json").exists()
    assert not (tmp_path / "intent.json").exists() and not (tmp_path / "correct").exists()
    assert "correct" not in r.state["stages"] and "answers" not in r.state["stages"]


def test_missing_rehearsal_flag_is_an_error_not_false(tmp_path):
    r = _run(tmp_path)
    r.take, r.lens = tmp_path / "t.mp4", None
    (tmp_path / "correct").mkdir()
    (tmp_path / "correct" / "rollout_after.json").write_text("{}")
    r.state["stages"]["correct"] = {"status": "done", "candidate": "x"}
    with pytest.raises(RuntimeError):
        r._write_page()


def test_gate_refusal_other_than_calibration_stops_before_a_layout_scene(tmp_path):
    from mirror.capture.session import CaptureSession, ReviewItem
    r = _run(tmp_path)
    r.take, r.lens = tmp_path / "t.mp4", None
    s = CaptureSession(mappings=RR.mappings_from(r.meas, confirmed={"mug", "box", "pen", "tray", "divider"}),
                       review=[ReviewItem(code="camera_moved", severity="abstain", message="top: moved 9 mm")])
    s.save(tmp_path / "session_answered.json")
    (tmp_path / "intent.json").write_text(json.dumps({"answer": "As proposed", "proposal": "p", "order_seen": []}))
    with pytest.raises(RR.StageStop) as e:
        r.stage_task()
    assert "geometry refused" in str(e.value) and "camera_moved" in str(e.value)
    assert not (tmp_path / "spec.json").exists()
    assert "Gate open: camera moved" in r.labels()


def test_labels_name_the_software_shift_and_the_stated_task(tmp_path):
    r = _run(tmp_path)
    r.state["stages"]["scene"] = {"status": "done", "placement": "auto_layout_centred", "shift_cm": [38.7, -5.9],
                                  "still_out_of_reach": []}
    r.state["stages"]["task"] = {"status": "done", "spec_source": "your stated task (the gate refused ...)"}
    lab = r.labels()
    assert "Layout shifted by software (+38.7 cm, -5.9 cm) to fit the robot's reach" in lab
    assert "Stated task (gate refused): not inferred from the video" in lab


def test_working_copy_plan():
    assert RR.working_copy_plan(1080, 1920, 29.97) is None
    assert RR.working_copy_plan(1920, 1080, 30.0) is None
    p = RR.working_copy_plan(3840, 2160, 59.94)
    assert p["size"] == [1920, 1080] and p["fps"] == 30
    p = RR.working_copy_plan(2160, 3840, 30.0)
    assert p["size"] == [1080, 1920] and p["fps"] is None
    p = RR.working_copy_plan(1080, 1920, 60.0)
    assert p["size"] == [1080, 1920] and p["fps"] == 30


def test_project_ledger_cap_and_conservative_retry_charges(tmp_path):
    proj = tmp_path / "project.jsonl"
    proj.write_text(json.dumps({"est_cost_usd": 28.4}) + "\n")
    led = RR.Ledger(tmp_path / "out" / "spend.jsonl", cap_usd=10.0, project=proj, project_cap_usd=30.0)
    led.guard(0.05, "small call")
    with pytest.raises(RR.SpendCapReached) as e:
        led.guard(0.2, "a call over the project cap")
    assert "project" in str(e.value)

    class T:
        count = 0
    t = T()

    def call_with_retries():
        t.count += 3        # 1 attempt + 2 internal retries
        return "ok"
    assert RR._billed_call(t, led, 0.04, "x", call_with_retries) == "ok"
    assert led.spent() == pytest.approx(0.08)
    assert RR._ledger_sum(proj) == pytest.approx(28.48)

    def failing():
        t.count += 2
        raise RuntimeError("stream ended")
    with pytest.raises(RuntimeError):
        RR._billed_call(t, led, 0.04, "x", failing)
    assert led.spent() == pytest.approx(0.16)


def test_prior_spend_is_persisted(tmp_path):
    take = tmp_path / "t.mov"
    take.write_bytes(b"x")
    r = RR.RealRun(tmp_path / "o", take=take, intrinsics=tmp_path / "i.json", measurements=_meas(tmp_path), prior_usd=0.33)
    r2 = RR.RealRun(tmp_path / "o", measurements=_meas(tmp_path))
    assert r2.ledger.prior == pytest.approx(0.33)


def test_strip_abs_paths():
    out = RR._strip_abs({"take": "/data/clips/IMG_1.MOV", "pub": "/srv/review/D2/pipeline/a.json",
                         "rel": "run/rollout.json", "ratio": "/s", "error_trace": "/tmp/x"})
    assert out == {"take": "local:IMG_1.MOV", "pub": "local:a.json", "rel": "run/rollout.json", "ratio": "/s"}


def test_publish_hook_gets_page_questions_status_with_relative_media(tmp_path):
    r = _run(tmp_path)
    q = _questions(tmp_path)
    r.publish_to = "D9"
    r.publish_dir = tmp_path / "pub"
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence" / "mug_seed.jpg").write_bytes(b"j")
    qd = json.loads((tmp_path / "questions.json").read_text())
    qd["questions"][0]["evidence"] = {"image": "evidence/mug_seed.jpg", "t_s": 12.5}
    (tmp_path / "questions.json").write_text(json.dumps(qd))
    calls = []
    r.hook = lambda event, payload: calls.append((event, json.loads(json.dumps(payload, default=str))))
    page = {"labels": ["x"], "paths": {"overlay_mp4": "overlay.mp4", "tracks_full_json_gz": "tracks_full.json.gz"},
            "notes": ["/data/secret/IMG_0815.MOV"], "confirmations_needed": qd["questions"]}
    r._publish_hook(page)
    assert [e for e, _ in calls] == ["publish"]
    p = calls[0][1]
    assert p["run_id"] == "D9" and p["publish_dir"] == str(tmp_path / "pub")
    pg = p["page"]
    assert pg["media"] == {"overlay_mp4": "overlay.mp4"}    # tracks are never published
    assert pg["confirmations_needed"][0]["evidence"]["image_path"] == "evidence/mug_seed.jpg"
    assert "/data/secret" not in json.dumps(pg)
    assert p["questions"]["items"]["mug"]["evidence"]["image_path"] == "evidence/mug_seed.jpg"
    assert p["questions"]["question_set_sha256"] == q["question_set_sha256"]
    assert set(p["status"]) == {"stage", "updated_at_ms", "message"}
    r._publish_hook(page)                                    # unchanged question set: not sent again
    assert calls[1][1]["questions"] is None


def test_publish_to_without_a_hook_is_refused(tmp_path):
    take = tmp_path / "t.mov"
    take.write_bytes(b"x")
    (tmp_path / "i.json").write_text("{}")
    with pytest.raises(SystemExit, match="publish-hook"):
        RR.RealRun(tmp_path / "o", take=take, intrinsics=tmp_path / "i.json", measurements=_meas(tmp_path), publish_to="D9")


def test_unknown_choice_id_is_refused_not_read_as_free_text(tmp_path):
    r = _run(tmp_path)
    r.rehearse = None
    q = _questions(tmp_path)
    (tmp_path / "answers.json").write_text(json.dumps({"correction": {"choice_id": "red_box_upright", "by": "person", "at": 2000,
                                                                      "session_id": q["session_id"], "take_sha256": SHA}}))
    with pytest.raises(RR.StageStop):
        r._choose_correction()
    (tmp_path / "answers.json").write_text(json.dumps({"correction": {"text": "Stay 10 cm from the tray edge", "by": "person",
                                                                      "at": 2000, "session_id": q["session_id"], "take_sha256": SHA}}))
    c, rehearsal, _ = r._choose_correction()
    assert c["id"] == "custom" and rehearsal is False


def test_something_else_with_text_is_recorded_but_never_a_task(tmp_path):
    from mirror.capture.session import CaptureSession
    r = _run(tmp_path)
    s = CaptureSession(mappings=RR.mappings_from(r.meas, confirmed={"mug", "box", "pen", "tray", "divider"}))
    with pytest.raises(RR.StageStop) as e:
        r._stated_spec(s, {"answer": "Something else: pack the box last", "order_seen": [], "proposal": "p"})
    assert "pack the box last" in str(e.value)
