"""Round-7 registrations: design -> inputs -> freeze -> run lifecycle,
audits, ledger discipline, budgets, and the benchmark metrics."""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from mirror.evaluate import designs, prereg
from mirror.evaluate.paired import FreezeError


@pytest.fixture
def reg(tmp_path, monkeypatch):
    """A throwaway registration living in tmp_path."""
    out = tmp_path / "eval_x"
    monkeypatch.setitem(prereg.REGISTRY, "x", dict(out=str(out), code_exclude=("studio/**", "cli.py"),
                                                   requires=[("json", "dumps")], runner="json:dumps"))
    monkeypatch.setattr(prereg, "default_audit_roots", lambda: [tmp_path / "results"])
    (tmp_path / "results").mkdir()
    f = tmp_path / "in.bin"
    f.write_bytes(b"frozen input")
    return dict(name="x", tmp=tmp_path, out=out, file=f,
                files=[dict(path=str(f), sha256=prereg.sha256_file(f))])


def _design():
    return dict(seeds=[77001, 77002], thresholds={"a": 1}, hypotheses=["h"], budget_caps={"m": 2})


def test_lifecycle_and_run(reg, monkeypatch):
    prereg.write_design("x", _design())
    prereg.Ledger(prereg.paths("x")["ledger"]).append(kind="input", seed=77001)   # input stage may log
    prereg.write_inputs("x", reg["files"], provenance={"p": 1})
    fz = prereg.freeze("x")
    assert fz["inputs_sha256"] and fz["code_exclude"] == ["studio/**", "cli.py"]
    seen = {}

    def runner(design, inputs, *, ledger, budget, out_dir):
        budget.charge("m")
        ledger.append(kind="attempt", item=1)
        seen["files"] = len(inputs["files"])
        return {"ok": True}
    monkeypatch.setattr(prereg, "_runner", lambda name: runner)
    monkeypatch.setattr(prereg, "_runner_markdown", lambda name: None)
    rep = prereg.run("x")
    assert rep["status"] == "completed" and rep["budget"]["m"] == {"used": 1, "cap": 2} and seen["files"] == 1
    kinds = [e.get("kind") for e in prereg.Ledger(prereg.paths("x")["ledger"]).entries()]
    assert kinds == ["input", "run_start", "attempt", "run_end"]
    with pytest.raises(FreezeError, match="run once"):
        prereg.run("x")


def test_design_refuses_seed_already_used(reg):
    (reg["tmp"] / "results" / "old.md").write_text("| 77002 | done |\n")
    with pytest.raises(FreezeError, match="already appear"):
        prereg.write_design("x", _design())


def test_inputs_refuse_hash_already_sent(reg):
    prereg.write_design("x", _design())
    (reg["tmp"] / "results" / "meta_usage.jsonl").write_text(
        json.dumps({"media_sha256": reg["files"][0]["sha256"]}) + "\n")
    with pytest.raises(FreezeError, match="already appear"):
        prereg.write_inputs("x", reg["files"])


def test_freeze_refuses_ledger_growth_after_inputs(reg):
    prereg.write_design("x", _design())
    prereg.write_inputs("x", reg["files"])
    prereg.Ledger(prereg.paths("x")["ledger"]).append(kind="transcribe", item="early")
    with pytest.raises(FreezeError, match="ledger"):
        prereg.freeze("x")


def test_verify_refuses_changed_input_and_tampered_ledger(reg):
    prereg.write_design("x", _design())
    prereg.Ledger(prereg.paths("x")["ledger"]).append(kind="input", seed=77001)
    prereg.write_inputs("x", reg["files"])
    prereg.freeze("x")
    prereg.verify("x")
    led = prereg.paths("x")["ledger"]
    good = led.read_bytes()
    led.write_bytes(good.replace(b"77001", b"77009"))
    with pytest.raises(FreezeError, match="inputs stage changed"):
        prereg.verify("x")
    led.write_bytes(good)
    reg["file"].write_bytes(b"changed")
    with pytest.raises(FreezeError, match="input .* changed"):
        prereg.verify("x")


def test_freeze_requires_harness(reg, monkeypatch):
    monkeypatch.setitem(prereg.REGISTRY["x"], "requires", [("mirror.not_a_module", "f")])
    prereg.write_design("x", _design())
    prereg.write_inputs("x", reg["files"])
    with pytest.raises(FreezeError, match="harness not ready"):
        prereg.freeze("x")


def test_budget_cap():
    b = prereg.Budget({"sam-3.1": 2})
    b.charge("sam-3.1", 2)
    with pytest.raises(prereg.BudgetExceeded):
        b.charge("sam-3.1")


def test_registered_designs_are_well_formed():
    v, vid, rob = designs.design_voice(), designs.design_video(), designs.design_robustness()
    assert len(v["sentences"]) == 72 and {s["set"] for s in v["sentences"]} == {"frozen", "holdout"}
    assert len(v["voices"]) == 2 and "non-Meta" in v["tts"]["disclosure"]
    assert vid["seeds"] == list(range(70000, 70020)) and vid["split"] == "eval"
    assert v["budget_caps"]["muse-voice-transcribe-1.0"] <= 300 and vid["budget_caps"]["muse-wonder-1.0"] <= 60
    assert rob["frames"]["seeds"] == list(range(80000, 80008)) and rob["frames"]["split"] == "eval"
    assert rob["budget_caps"] == {"muse-image-1.0": 80, "sam-3.1": 850, "muse-spark-1.3:chooser": 40}
    assert rob["thresholds"] == dict(delta_iou_median_min=-0.05, found_rate_min=0.9, centroid_px_median_max=4.0)
    assert set(rob["prompt_conditions"]) == {"A_chooser", "B_fixed"} and "seen before" in rob["dev_run_disclosure"]
    from mirror.robustness.instructions import instructions_sha256
    assert rob["instructions_sha256"] == instructions_sha256()


def test_wer_and_normalisation():
    from mirror.evaluate.bench_voice import wer
    assert wer("Keep the cup within 15° of vertical.", "keep the cup within fifteen degrees of vertical")["wer"] == 0
    r = wer("The block goes in first.", "the clock goes in first")
    assert r["errors"] == 1 and r["ref_words"] == 5
    assert wer("a b c", "")["wer"] == 1.0


def test_noise_mix_is_reproducible_at_the_stated_snr():
    from mirror.evaluate.bench_voice import mix_snr, wav_bytes, read_wav
    t = np.arange(16000) / 16000
    clean = (8000 * np.sin(2 * math.pi * 220 * t)).astype(np.int16)
    a, b = mix_snr(clean, 5), mix_snr(clean, 5)
    assert np.array_equal(a, b) and not np.array_equal(a, mix_snr(clean, 6))
    n = a.astype(float) - clean
    snr = 10 * math.log10(np.mean(clean.astype(float) ** 2) / np.mean(n ** 2))
    assert abs(snr - 15.0) < 0.3
    x, sr = read_wav(wav_bytes(a))
    assert sr == 16000 and np.array_equal(x, a)


def test_voice_run_with_fake_transcriber(tmp_path):
    from mirror.evaluate import bench_voice as bv
    from mirror.interpret import LLMClient
    from mirror.evaluate.prereg import Budget, Ledger
    sents = {s["id"]: s for s in bv.sentences()}
    ids = ["p01", list(sents)[-1]]
    design = dict(sentences=[sents[i] for i in ids], correction=dict(scene="shortcut", cassette_dir="x"),
                  thresholds=dict(wer_max_clean=0.05, exact_drop_max_pp_clean=5))
    files = []
    for i in ids:
        p = tmp_path / f"{i}.wav"
        p.write_bytes(bv.wav_bytes(np.zeros(1600, np.int16)))
        files.append(dict(case=i, set=sents[i]["set"], voice="voice_a", condition="clean", path=str(p), sha256="0"))

    class T:
        def __init__(self, text):
            self.text, self.model = text, "fake-asr"
    said = {ids[0]: sents[ids[0]]["text"], ids[1]: "completely different words"}
    led = Ledger(tmp_path / "l.jsonl")
    res = bv.run(design, {"files": files}, ledger=led, budget=Budget(), out_dir=tmp_path,
                 transcribe=lambda p: T(said[Path(p).stem]), client=LLMClient(mode="offline"))
    g = res["summary"]["groups"]["clean/all"]
    assert g["n"] == 2 and res["rows"][0]["wer"] == 0 and res["rows"][1]["wer"] > 0.5
    assert res["rows"][0]["exact"] == res["rows"][0]["text_exact"]
    assert sum(1 for e in led.entries() if e["kind"] == "transcribe") == 2
    assert "corpus WER clean <= 0.05" in res["summary"]["threshold_checks"]


def test_frozen_audio_inputs_present_and_reproducible():
    d = prereg.paths("voice")["out"] / "inputs"
    if not d.exists():
        pytest.skip("voice inputs not generated")
    from mirror.evaluate import bench_voice as bv
    files, _ = designs.inputs_voice()
    assert len(files) == 288
    by = {(f["case"], f["voice"], f["condition"]): f for f in files}
    c, n = by[("p01", "voice_a", "clean")], by[("p01", "voice_a", "noise15db")]
    assert bv.verify_noisy(c, n, prereg.REPO_ROOT)


# ------------------------------------------------------------------- video ---

def test_video_metrics():
    from mirror.evaluate.bench_video import care_cue_found, frame_errors, match_events, relevance_correct
    gt = [dict(frame=0, object="cup_0", kind="cup", x=0.0, y=0.0, tilt_deg=0.0),
          dict(frame=1, object="cup_0", kind="cup", x=0.1, y=0.0, tilt_deg=10.0),
          dict(frame=1, object="block_0", kind="block", x=0.2, y=0.0, visible=False)]
    est = [dict(frame=0, object="cup_0", x=0.01, y=0.0, tilt_deg=2.0),
           dict(frame=1, object="cup_0", x=0.30, y=0.0)]
    e = frame_errors(gt, est)
    assert e["found_rate"]["cup"] == 0.5 and abs(e["centroid_cm"]["cup"]["median"] - 1.0) < 1e-9
    assert e["tilt_deg"]["median"] == 2.0 and e["n_visible"] == 2
    g = [dict(type="grasp", object="cup", t=1.0), dict(type="release", object="cup", t=3.0)]
    m = match_events(g, [dict(type="grasp", object="cup", t=1.2), dict(type="release", object="cup", t=4.0),
                         dict(type="grasp", object="block", t=1.0)], 0.5)
    assert m["tp"] == 1 and abs(m["precision"] - 1 / 3) < 1e-9 and m["recall"] == 0.5
    assert relevance_correct({"cup", "block"}, [dict(kind="cup", relevant=True), dict(kind="block", relevant=True),
                                               dict(kind="wall", relevant=False)])
    assert care_cue_found([dict(text="kept the cup level near the wall", object="cup")])
    assert not care_cue_found([dict(text="moved the block", object="block")])


def test_proposal_signature_ignores_ids_and_prose():
    from mirror.evaluate.bench_video import proposal_signature
    from mirror.evaluate.specs import reference_specs
    a, b = reference_specs()
    a2 = a.model_copy(deep=True)
    a2.constraints[0].id = "renamed"
    a2.summary = "different prose"
    assert proposal_signature(a) == proposal_signature(a2) != proposal_signature(b)


# -------------------------------------------------------------- robustness ---

def test_robustness_adapter_charges_caps_and_checks_thresholds(tmp_path):
    from types import SimpleNamespace
    from mirror.evaluate import bench_robust as br
    from mirror.evaluate.prereg import Budget, BudgetExceeded, Ledger
    from mirror.robustness.frames import GTFrame
    rgb = np.zeros((8, 8, 3), np.uint8)
    for f in ("f0", "f1"):
        GTFrame(f, rgb, {"cup": np.ones((8, 8), bool)}, {"frame_id": f}).save(tmp_path)
    design = designs.design_robustness()
    design["instructions"] = dict(design["instructions"], instructions=design["instructions"]["instructions"][:2])
    inputs = dict(files=[dict(path=str(tmp_path / "f0.png"))], provenance=dict(frames=["f1", "f0"]))
    seen = {}
    kept = dict(kept_iou_delta_median=-0.01, kept_found_rate=0.95, kept_centroid_err_px_median=1.0)

    def evaluate(frames, ins, **kw):
        seen.update(frames=[g.frame_id for g in frames], ins=[i.id for i in ins], **kw)
        for _ in range(3):
            kw["client"].sam_segment(b"png", "x")
        kw["client"].images_edit([b"png"], "p")
        return dict(summary={"A/corrected": {"all_edits": kept}, "A/uncorrected": {"all_edits": {}},
                             "B/corrected": {"all_edits": dict(kept, kept_found_rate=0.5)}, "_edits": {}},
                    edits=[dict(frame_id="f1", instruction_id="light_dim", status="ok",
                                geometry={"verdict": "preserved", "registration": {"applied": True, "scale": 1.0, "dx": 3, "dy": 0}})],
                    prompts_used={"A": {"f1": {"prompts": {"cup": None, "block": "blue cube"}}}}, rows=[])
    client = SimpleNamespace(usage=[], sam_segment=lambda *a, **k: None, images_edit=lambda *a, **k: None)
    b = Budget(design["budget_caps"])
    res = br.run(design, inputs, ledger=Ledger(tmp_path / "l.jsonl"), budget=b, out_dir=tmp_path, client=client,
                 evaluate=evaluate, repo_root=Path("/"))
    assert seen["frames"] == ["f1", "f0"] and seen["uncorrected_conditions"] == ("A",) and seen["split"] == "eval"
    assert seen["conditions"] == {"A": "chooser", "B": {"cup": "open box", "block": "small cube", "marker": "cylinder"}}
    assert b.used == {"sam-3.1": 3, "muse-image-1.0": 1}
    chk = res["summary"]["threshold_checks"]
    assert all(chk["A/corrected"].values()) and not all(chk["B/corrected"].values())
    assert res["summary"]["chooser_missing"] == {"f1": ["cup"]} and not res["stopped"]
    b2 = Budget(dict(design["budget_caps"], **{"sam-3.1": 2}))
    res2 = br.run(design, inputs, ledger=Ledger(tmp_path / "l2.jsonl"), budget=b2, out_dir=tmp_path, client=client,
                  evaluate=evaluate, repo_root=Path("/"))
    assert res2["stopped"] and "sam-3.1" in res2["stopped"]


def _gt():
    def mask(c):
        return {c: {"rle": {}}}
    frames = []
    for i in range(3):
        frames.append({"i": i, "t": i / 15, "held": "cup_0" if i == 1 else None,
                       "objects": {"cup_0": {"pos": [0.1 * i, 0.0, 0.05]}, "block_0": {"pos": [0.2, 0.1, 0.02]}},
                       "cup_tilt_deg": {"cup_0": 3.0 * i},
                       "masks": {"top": {"cup_0": {}, **({"block_0": {}} if i != 2 else {})}}})
    return {"objects": [{"id": "cup_0", "kind": "cup"}, {"id": "block_0", "kind": "block"}],
            "events": [{"t": 0.05, "type": "grasp", "object_id": "cup_0"}, {"t": 0.12, "type": "release", "object_id": "cup_0"}],
            "frames": frames}


def test_ground_truth_readers():
    from mirror.evaluate import bench_video as bv
    gt = _gt()
    rows = bv.gt_frames(gt)
    assert len(rows) == 6 and sum(not r["visible"] for r in rows) == 1
    assert bv.gt_events(gt) == [dict(type="grasp", object="cup", t=0.05), dict(type="release", object="cup", t=0.12)]
    assert bv.gt_relevant(gt) == {"cup", "tray"}
    assert bv.gt_cup_kept_upright(gt)
    gt["frames"][1]["cup_tilt_deg"]["cup_0"] = 40.0
    assert not bv.gt_cup_kept_upright(gt)


def test_observation_view_uses_span_start():
    from mirror.evaluate.bench_video import observation_view
    obs = dict(events=[dict(type="grasp", object="cup", t_start=1.0, t_end=2.0), dict(type="carry", object="cup", t_start=1, t_end=5)],
               objects=[dict(kind="cup", relevant=True)], care_cues=[dict(object="cup", cue="kept level", evidence="near wall")],
               issues=["event outside clip"])
    v = observation_view(obs)
    assert v["events"] == [dict(type="grasp", object="cup", t=1.0)] and v["cues"][0]["text"] == "kept level near wall"
    assert v["issues"] == ["event outside clip"]


def test_costs():
    from mirror.evaluate import costs
    spark = dict(model="muse-spark-1.3", usage={"input_tokens": 1_000_000, "input_tokens_details": {"cached_tokens": 200_000},
                                               "output_tokens": 100_000}, latency_s=2.0)
    assert abs(costs.record_cost(spark) - (0.8 * 1.25 + 0.2 * 0.15 + 0.1 * 4.25)) < 1e-9
    assert costs.record_cost(dict(spark, from_cassette=True)) == 0.0
    assert abs(costs.record_cost(dict(model="muse-voice-transcribe-1.0", usage={"audio_bytes": 44 + 32000 * 3600})) - 0.18) < 1e-9
    assert costs.record_cost(dict(model="sam-3.1", usage={"images_processed": 1})) == 0.0025
    assert abs(costs.record_cost(dict(model="sam-3.1", usage={"video_frames_processed": 1233})) - 0.2466) < 1e-9
    assert abs(costs.record_cost(dict(model="sam-3.1", usage={"video_frames_processed": 10})) - 0.01) < 1e-9
    assert costs.record_cost(dict(model="muse-image-1.0", usage={"images": 2})) == 0.02
    s = costs.summarize_usage([spark, dict(spark, from_cassette=True)])
    assert s["muse-spark-1.3"]["network_calls"] == 1 and s["muse-spark-1.3"]["replayed_calls"] == 1



def test_amendments_are_add_only_and_before_inputs(reg):
    prereg.write_design("x", _design())
    with pytest.raises(FreezeError, match="published"):
        prereg.write_amendment("x", 1, {"thresholds": {"a": 2}}, "no")
    prereg.write_amendment("x", 1, {"threshold_scope": "tracking events only"}, "clarifies scope")
    d = prereg.load_design("x")
    assert d["threshold_scope"] == "tracking events only" and d["thresholds"] == {"a": 1}
    prereg.write_inputs("x", reg["files"])
    with pytest.raises(FreezeError, match="before any input"):
        prereg.write_amendment("x", 2, {"more": 1}, "late")
    fz = prereg.freeze("x")
    assert list(fz["amendments"]) == ["design_amendment_1.json"]
    am = prereg.paths("x")["out"] / "design_amendment_1.json"
    am.write_text(am.read_text().replace("clarifies scope", "edited"))
    with pytest.raises(FreezeError, match="amendments changed"):
        prereg.verify("x")



class _FakeEst:
    def __init__(self, n, fps):
        self.t = np.arange(n) / fps
        self.objects = {"cup_0": np.zeros((n, 3))}
        self.found = {"cup_0": np.ones(n, bool)}
        self.tilt_deg = {}
        self.events = [{"type": "grasp", "object": "cup_0", "t": 0.0}]
        self.scene_checks = {"kinds": {"cup_0": "cup"}}


def _video_fixture(monkeypatch, tmp_path, costs_per_call):
    """bench_video.run with every external piece faked; each observe call
    adds a usage record whose cost is costs_per_call[model]."""
    from types import SimpleNamespace
    from mirror.evaluate import bench_video as bv
    import mirror.perception as perception
    gt = dict(objects=[{"id": "cup_0", "kind": "cup"}], events=[{"t": 0.0, "type": "grasp", "object_id": "cup_0"}],
              frames=[{"i": i, "objects": {"cup_0": {"pos": [0, 0, 0]}}, "cup_tilt_deg": {}, "masks": {"top": {"cup_0": {}}}}
                      for i in range(15)])
    dv = SimpleNamespace(videos={"top": "top.mp4"}, fps=15.0, trace="trace.json", n_frames=15)
    monkeypatch.setattr(perception, "load_demo_video", lambda m: (dv, gt))
    monkeypatch.setattr(bv, "reconstruct", lambda dv, tracks: (_FakeEst(5, 5.0), None, "trace_v", {}))
    from mirror.contracts import DemoTrace
    monkeypatch.setattr(DemoTrace, "model_validate_json", classmethod(lambda cls, s: "trace_gt"))
    (tmp_path / "trace.json").write_text("{}")
    usage = []
    price = {"muse-wonder-1.0": dict(input=4.0, output=20.0), "muse-spark-1.3": dict(input=1.25, output=4.25)}

    def observe(path, model, client=None, **kw):
        tokens = costs_per_call[model] / (price[model]["output"] / 1e6)
        usage.append(dict(model=model, usage={"output_tokens": tokens}, latency_s=1.0))
        return dict(events=[dict(type="grasp", object="cup", t_start=0.2)], objects=[dict(kind="cup", relevant=True)],
                    care_cues=[], issues=[])
    from mirror.evaluate.specs import reference_specs
    spec = reference_specs()[0]
    design = dict(seeds=[1, 2, 3, 4], observation={"camera": "top"}, spend_cap_usd=0.95,
                  spend_projection_per_seed_usd={"muse-wonder-1.0": 0.3, "muse-spark-1.3": 0.1},
                  decision_rule=dict(min_f1_gain=0.05, text="rule"),
                  thresholds=dict(found_rate_min=0.95, centroid_cm_median_max=1.5, event_detection_min=0.9,
                                  timing_s_median_max=0.3, tilt_deg_median_max=5.0, signature_equal_min=1))
    inputs = dict(provenance=dict(demos=[dict(seed=s, manifest=str(tmp_path / "video.json")) for s in design["seeds"]]))
    from mirror.evaluate.prereg import Budget, Ledger
    res = bv.run(design, inputs, ledger=Ledger(tmp_path / "l.jsonl"), budget=Budget({}), out_dir=tmp_path,
                 meta_client=SimpleNamespace(usage=usage), spark_client=SimpleNamespace(usage=[]),
                 tracker=lambda dv, gt, root: None, observe=observe, propose=lambda tr, c, observations=None: spec,
                 repo_root=Path("/"), tracker_label="fake")
    return res


def test_video_spend_cap_drops_wonder_then_stops(monkeypatch, tmp_path):
    res = _video_fixture(monkeypatch, tmp_path, {"muse-wonder-1.0": 0.3, "muse-spark-1.3": 0.1})
    rows = res["rows"]
    # cap 0.95: seed 1 -> 0.4, seed 2 -> 0.8, seed 3: 0.8 + 0.4 > cap but 0.8 + 0.1 <= cap -> Wonder dropped;
    # seed 4: 0.9 + 0.1 > cap -> stop
    assert [r["seed"] for r in rows] == [1, 2, 3]
    assert "muse-wonder-1.0" not in rows[2]["observations"] and rows[2]["wonder_skipped"]
    assert res["stopped"] and res["not_run"] == [4]
    s = res["summary"]
    assert s["paired"]["seeds"] == 2 and s["models"]["muse-wonder-1.0"]["skipped"] == 1


def test_video_scores_tracking_on_sampled_frames_only(monkeypatch, tmp_path):
    res = _video_fixture(monkeypatch, tmp_path, {"muse-wonder-1.0": 0.0, "muse-spark-1.3": 0.0})
    tr = res["rows"][0]["tracking"]
    assert tr["n_visible"] == 5 and tr["found_rate_all"] == 1.0      # 5 fps samples of a 15 fps clip
    assert res["rows"][0]["signature_equal"] is True and not res["stopped"]


def test_audit_tokens_cover_frame_seeds():
    assert prereg._tokens({"frames": {"seeds": [80000]}, "seeds": [1]})["seeds"] == [1, 80000]
