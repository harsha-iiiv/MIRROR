"""bench_video.run against the real observe() signature, resume after an
interruption, and parallel seeds under a spend reservation."""
import inspect
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from mirror.evaluate import bench_video as bv
from mirror.evaluate.prereg import Budget, Ledger

REPO = Path(__file__).resolve().parents[2]
REF = REPO / "data" / "demos" / "video" / "reference" / "video.json"
DESIGN_OBSERVATION = {"strategy": "video", "camera": "top",
                      "event_time": "t_start of each reported grasp or release event",
                      "matching": "same event type and object kind, one-to-one, closest in time first"}
THRESHOLDS = dict(found_rate_min=0.95, centroid_cm_median_max=1.5, event_detection_min=0.9,
                  timing_s_median_max=0.3, tilt_deg_median_max=5.0, signature_equal_min=1)


def _no_net(r):
    raise AssertionError("network")


def test_observe_kwargs_follow_the_real_signature():
    from mirror.interpret.video_observe import observe
    kw = bv.observe_kwargs(observe, DESIGN_OBSERVATION)
    assert kw == {"strategy": "video"}
    params = inspect.signature(observe).parameters
    assert all(k in params and params[k].kind != inspect.Parameter.VAR_KEYWORD for k in kw)
    inspect.signature(observe).bind(Path("top.mp4"), "muse-spark-1.3", client=None, **kw)
    with pytest.raises(TypeError):        # attempt 1 passed the whole block
        inspect.signature(observe).bind(Path("top.mp4"), "muse-spark-1.3", client=None,
                                        **{k: v for k, v in DESIGN_OBSERVATION.items() if k != "camera"})


def test_observe_kwargs_drop_names_a_function_does_not_declare():
    def obs(video, model, client=None, *, strategy=None):
        return None
    assert bv.observe_kwargs(obs, {"strategy": "slow", "frame_step_s": 0.5, "event_time": "x"}) == {"strategy": "slow"}
    assert bv.observe_kwargs(lambda video, model, client=None, **kw: None, DESIGN_OBSERVATION) == {}


class _Est:
    def __init__(self):
        self.t = np.arange(3) / 5.0
        self.objects, self.found, self.tilt_deg, self.events = {}, {}, {}, []
        self.scene_checks = {"kinds": {}}


def test_run_with_real_observe_replays_and_feeds_the_specification(tmp_path):
    """The real observe() on the committed reference clip, replayed from its
    recordings for both models; the video-side proposal gets the Spark
    observations."""
    from mirror.contracts import DemoTrace
    from mirror.evaluate.specs import reference_specs
    from mirror.meta import MetaClient
    import gzip
    import shutil
    from mirror.evaluate.specs import PINNED_DEMO
    root = tmp_path / "demo"
    shutil.copytree(REF.parent, root)                      # the committed clip, plus a demonstration trace
    trace_text = gzip.decompress(PINNED_DEMO.read_bytes()).decode()
    (root / json.loads(REF.read_text()).get("trace", "trace.json")).write_text(trace_text)
    trace_gt = DemoTrace.model_validate_json(trace_text)
    calls = []
    spec = reference_specs()[0]

    def propose(trace, client, observations=None):
        calls.append(observations)
        return spec

    design = dict(seeds=[1], observation=DESIGN_OBSERVATION, spend_cap_usd=None,
                  spec_observations_model="muse-spark-1.3", thresholds=THRESHOLDS,
                  decision_rule=dict(min_f1_gain=0.05, text="rule"))
    inputs = dict(provenance=dict(demos=[dict(seed=1, manifest="demo/video.json")]))
    mc = MetaClient(mode="replay", transport=httpx.MockTransport(_no_net))
    res = bv.run(design, inputs, ledger=Ledger(tmp_path / "l.jsonl"), budget=Budget({}), out_dir=tmp_path,
                 meta_client=mc, spark_client=SimpleNamespace(usage=[]),
                 tracker=lambda dv, gt, root: SimpleNamespace(notes=[]),
                 reconstructor=lambda dv, tracks: (_Est(), None, trace_gt, {}),
                 propose=propose, repo_root=tmp_path, tracker_label="fake")
    row = res["rows"][0]
    assert "error" not in row, row.get("error")
    for m in ("muse-wonder-1.0", "muse-spark-1.3"):
        o = row["observations"][m]
        assert o["error"] is None and o["replayed"] is True and o["events_reported"] > 0
    assert row["observe_kwargs"] == {"strategy": "video"} and row["spec_observations"]["present"]
    assert calls[0] is None and calls[1] is not None and calls[1].model == "muse-spark-1.3"
    assert res["run_info"]["observe_kwargs"] == {"strategy": "video"}


# ------------------------------------------------------------- fakes ---

def _fake_world(monkeypatch, tmp_path, *, cost=0.1, tracker=None):
    import mirror.perception as perception
    from mirror.contracts import DemoTrace
    from mirror.evaluate.specs import reference_specs
    gt = dict(objects=[{"id": "cup_0", "kind": "cup"}], events=[{"t": 0.0, "type": "grasp", "object_id": "cup_0"}],
              frames=[{"i": i, "objects": {"cup_0": {"pos": [0, 0, 0]}}, "cup_tilt_deg": {}, "masks": {"top": {"cup_0": {}}}}
                      for i in range(15)])
    dv = SimpleNamespace(videos={"top": "top.mp4"}, fps=15.0, trace="trace.json", n_frames=15)
    monkeypatch.setattr(perception, "load_demo_video", lambda m: (dv, gt))
    monkeypatch.setattr(DemoTrace, "model_validate_json", classmethod(lambda cls, s: "trace_gt"))
    (tmp_path / "trace.json").write_text("{}")
    spec = reference_specs()[0]

    def observe(path, model, client=None, *, strategy=None):
        client.usage.append(dict(model="muse-spark-1.3", usage={"output_tokens": cost / 2 / (4.25 / 1e6)}))
        return dict(events=[], objects=[], care_cues=[], issues=[])

    def factory():
        return SimpleNamespace(usage=[]), SimpleNamespace(usage=[])
    design = dict(seeds=[1, 2, 3, 4, 5, 6], observation=DESIGN_OBSERVATION, spend_cap_usd=None,
                  observe_models=["muse-wonder-1.0", "muse-spark-1.3"], thresholds=THRESHOLDS,
                  decision_rule=dict(min_f1_gain=0.05, text="rule"))
    inputs = dict(provenance=dict(demos=[dict(seed=s, manifest=str(tmp_path / "video.json")) for s in design["seeds"]]))
    kw = dict(budget=Budget({}), out_dir=tmp_path / "out", client_factory=factory,
              tracker=tracker or (lambda dv, gt, root: SimpleNamespace(notes=[])),
              reconstructor=lambda dv, tracks: (_Est(), None, "trace_v", {}), observe=observe,
              propose=lambda tr, c, observations=None: spec, repo_root=Path("/"), tracker_label="fake")
    return design, inputs, kw


def test_restarted_run_resumes_and_never_repeats_a_finished_seed(monkeypatch, tmp_path):
    seen, crash = [], {"on": 3}

    def tracker(dv, gt, root):
        seed = len(seen) + 1
        seen.append(seed)
        if crash["on"] == seed:
            raise KeyboardInterrupt("machine restarted")        # not an Exception: the process dies
        return SimpleNamespace(notes=[])
    design, inputs, kw = _fake_world(monkeypatch, tmp_path, tracker=tracker)
    led = Ledger(tmp_path / "out" / "ledger.jsonl")
    led.append(kind="run_start")
    with pytest.raises(KeyboardInterrupt):
        bv.run(design, inputs, ledger=led, workers=1, **kw)
    assert sorted(int(p.stem) for p in (tmp_path / "out" / "seeds").glob("*.json")) == [1, 2]
    crash["on"] = None
    led.append(kind="run_start")
    res = bv.run(design, inputs, ledger=led, workers=1, **kw)
    assert [r["seed"] for r in res["rows"]] == [1, 2, 3, 4, 5, 6] and not res["not_run"]
    assert len(seen) == 3 + 4                                # 1, 2, 3 (interrupted), then 3 again, 4, 5, 6
    ri = res["run_info"]
    assert ri["attempts"] == 2 and ri["restarts"] == 1
    assert ri["resumed_from_checkpoints"] == [1, 2] and ri["rerun_after_interruption"] == [3]
    assert [r["seed"] for r in res["rows"] if r["rerun_after_interruption"]] == [3]
    md = bv.markdown(dict(result=res, status="completed", design_sha256="0" * 64, inputs_sha256="0" * 64,
                          freeze_code_sha256="0" * 64, budget={}, ledger_entries=0),
                     dict(design, decision_rule=dict(min_f1_gain=0.05, text="rule")))
    assert "restarts: 1" in md and "run again: 3" in md


def test_parallel_seeds_respect_the_spend_reservation(monkeypatch, tmp_path):
    active, peak, lock = [0], [0], threading.Lock()

    def tracker(dv, gt, root):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.15)
        with lock:
            active[0] -= 1
        return SimpleNamespace(notes=[])
    design, inputs, kw = _fake_world(monkeypatch, tmp_path, cost=0.1, tracker=tracker)
    # each seed costs 0.2 (two observe calls at 0.1); projection 0.2, reservation x1.5 = 0.3; cap 1.0
    design.update(spend_cap_usd=1.0, spend_projection_per_seed_usd={"muse-spark-1.3": 0.2},
                  observe_models=["muse-spark-1.3", "muse-spark-1.3"], reservation_factor=1.5)
    res = bv.run(design, inputs, ledger=Ledger(tmp_path / "l.jsonl"), workers=4, **kw)
    assert 2 <= peak[0] <= 3                              # 3 reservations of 0.3 fit under 1.0, a 4th does not
    assert res["cost_usd"] <= 1.0 and res["stopped"] and res["not_run"]
    assert len(res["rows"]) + len(res["not_run"]) == 6
    lines = [json.loads(x) for x in (tmp_path / "l.jsonl").read_text().splitlines()]
    assert all("kind" in e for e in lines)                 # every ledger line intact under concurrency


def test_video2_design_keeps_every_published_rule():
    from mirror.evaluate import designs, prereg
    v1, v2 = prereg.load_design("video"), designs.design_video2()
    for k in ("thresholds", "decision_rule", "metrics", "observation", "threshold_scope", "definitions", "tracking",
              "spec_comparison", "spend_cap_usd", "spec_observations_model", "hypotheses"):
        assert v2[k] == v1[k], k
    assert v2["seeds"] == list(range(71000, 71020)) and not set(v2["seeds"]) & set(v1["seeds"])
    assert (bv.EVENT_MATCH_S, bv.EVENT_F1_TOL_S, bv.OBS_MAE_TOL_S, bv.FOUND_RADIUS_M) == (1.0, 0.5, 2.0, 0.05)


def test_default_clients_record_next_to_the_ledger(monkeypatch, tmp_path):
    """data/cassettes* are hashed by the freeze; a resumable run must not write there."""
    import mirror.interpret as interp
    import mirror.meta as meta
    seen = {}

    class M:
        @classmethod
        def from_env(cls, **kw):
            seen["meta"] = kw
            return SimpleNamespace(usage=[])

    class L:
        @classmethod
        def from_env(cls, **kw):
            seen["llm"] = kw
            return SimpleNamespace(usage=[])
    monkeypatch.setattr(meta, "MetaClient", M)
    monkeypatch.setattr(interp, "LLMClient", L)
    design, inputs, kw = _fake_world(monkeypatch, tmp_path)
    kw.pop("client_factory")
    design["seeds"] = [1]
    bv.run(design, dict(provenance=dict(demos=inputs["provenance"]["demos"][:1])), ledger=Ledger(tmp_path / "l.jsonl"),
           workers=1, **kw)
    out = tmp_path / "out"
    assert seen["meta"]["cassette_dir"] == out / "cassettes_meta" and seen["llm"]["cassette_dir"] == out / "cassettes_llm"
    assert seen["meta"]["usage_log"] == out / "meta_usage.jsonl" and seen["llm"]["usage_log_path"] == out / "llm_usage.jsonl"
