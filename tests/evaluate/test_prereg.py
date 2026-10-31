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
    caps = {**v["budget_caps"], **vid["budget_caps"], **rob["budget_caps"]}
    assert caps["muse-voice-transcribe-1.0"] <= 300 and caps["muse-wonder-1.0"] <= 60
    assert vid["budget_caps"]["sam-3.1"] + rob["budget_caps"]["sam-3.1"] <= 400
    assert rob["budget_caps"]["muse-image-1.0"] <= 150
    from mirror.robustness.instructions import instructions_sha256
    assert rob["instructions_sha256"] == instructions_sha256()


# ------------------------------------------------------------------- voice ---

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

def test_robustness_scoring_and_summary():
    from mirror.evaluate.bench_robust import score_masks, summarize
    g = np.zeros((10, 10), bool)
    g[2:6, 2:6] = True
    p = np.zeros((10, 10), bool)
    p[2:6, 3:7] = True
    s = score_masks({"cup": g, "block": g}, {"cup": p}, ["cup", "block"])
    assert abs(s["cup"]["iou"] - 12 / 20) < 1e-9 and s["cup"]["centroid_px"] == 1.0 and not s["block"]["found"]
    design = dict(instructions=dict(instructions=[dict(id="e1", family="lighting", kind="model")]),
                  thresholds=dict(delta_iou_median_min=-0.05, found_rate_min=0.9, centroid_px_median_max=4.0))
    orig = {"cup": dict(found=True, iou=0.9, centroid_px=0.5)}
    rows = [dict(frame="f", edit="e1", original=orig, geometry="preserved",
                 edited={"cup": dict(found=True, iou=0.88, centroid_px=1.0)}),
            dict(frame="g", edit="e1", original=orig, geometry="changed",
                 edited={"cup": dict(found=False, iou=0.0, centroid_px=None)})]
    out = summarize(rows, design)
    assert out["groups"]["preserved"]["edits"] == 1 and out["groups"]["changed"]["edits"] == 1
    assert all(out["threshold_checks"].values())
    assert out["per_instruction"]["e1"]["geometry"] == {"preserved": 1, "changed": 1, "reframed": 0}



def test_robustness_run_with_fake_client(tmp_path):
    from mirror.evaluate import bench_robust as br
    from mirror.evaluate.prereg import Budget, Ledger
    from mirror.robustness.frames import GTFrame, encode_png
    from mirror.robustness.instructions import EditInstruction
    from dataclasses import asdict
    rgb = np.full((96, 160, 3), 200, np.uint8)
    rgb[30:60, 40:70] = (240, 240, 240)
    mask = np.zeros((96, 160), bool)
    mask[30:60, 40:70] = True
    GTFrame("f0", rgb, {"cup": mask}, {"frame_id": "f0"}).save(tmp_path)

    class Obj:
        confidence = 0.9

        def full_mask(self, w, h):
            return mask.astype(np.uint8)

    class Seg:
        width, height = 160, 96
        frames = [type("F", (), {"objects": [Obj()]})()]

    class Res:
        images = [encode_png(rgb)]
        model = "fake-image"

    class Client:
        def sam_segment(self, png, phrase):
            return Seg()

        def images_edit(self, imgs, prompt, output_format="png"):
            return Res()
    ins = [EditInstruction("same", "lighting", "model", "Change nothing."),
           EditInstruction("noise_gauss", "sensor", "local", "noise", {"op": "gaussian_noise", "sigma": 2.0, "seed": 1})]
    design = dict(instructions=dict(instructions=[asdict(i) for i in ins], sam_prompts={"cup": "white cup"}),
                  thresholds=dict(delta_iou_median_min=-0.05, found_rate_min=0.9, centroid_px_median_max=4.0))
    inputs = dict(files=[dict(path=str(tmp_path / "f0.png"))], provenance=dict(frames=["f0"]))
    b = Budget({"sam-3.1": 3, "muse-image-1.0": 1})
    res = br.run(design, inputs, ledger=Ledger(tmp_path / "l.jsonl"), budget=b, out_dir=tmp_path, client=Client(),
                 repo_root=Path("/"))
    rows = [r for r in res["rows"] if "edited" in r]
    assert len(rows) == 2 and rows[0]["edited"]["cup"]["iou"] == 1.0
    assert b.used == {"sam-3.1": 3, "muse-image-1.0": 1}



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
