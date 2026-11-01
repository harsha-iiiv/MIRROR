"""Video demonstrations, spoken corrections and the Models panel (fixture backend)."""
import io
import json
import shutil
import struct
import time
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from mirror.studio import meta_bridge as mb
from mirror.studio.app import create_app
from mirror.studio.session_adapter import StudioService, check_wav
from mirror.studio.sessions import ModelBudget, SessionManager, meta_budgets

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is needed for the fixture video")


def fixture_manager(**kw):
    return SessionManager(lambda sid, m: StudioService(backend="fixture", session_id=sid, gate=m.gate, budget=m.budget,
                                                       meta_budgets=m.meta_budgets, one_job=True,
                                                       private_runtime=True), **kw)


def wav_bytes(seconds=1.0, rate=16000, channels=1, width=2):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes((np.sin(np.arange(int(rate * seconds)) / 8) * 3000).astype("<i2").tobytes() * channels)
    return buf.getvalue()


@pytest.fixture()
def client():
    with TestClient(create_app(fixture_manager())) as c:
        sid = c.post("/api/session").json()["session"]
        c.headers.update({"X-Mirror-Session": sid})
        c.sid = sid
        yield c


def wait(c, job, timeout=60):
    t0 = time.time()
    while True:
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("done", "error"):
            return j
        assert time.time() - t0 < timeout
        time.sleep(0.05)


def test_bundled_video_demonstration_end_to_end(client):
    c = client
    s = c.get("/api/state").json()
    assert s["capabilities"]["video"] and s["capabilities"]["voice"] and s["capabilities"]["bundled_video"]
    j = wait(c, c.post("/api/video/process", json={"top": "bundled"}).json())
    assert j["status"] == "done", j["error"]
    s = c.get("/api/state").json()
    vd = s["video_demo"]
    assert vd["fixture"] and vd["bundled"] and vd["views"]["top"]["id"] == "bundled-top"
    objs = vd["tracks"]["top"]["objects"]
    assert {"cup_0", "block_0", "marker_0"} <= set(objs) and objs["cup_0"][0]["polygon"]
    assert vd["observations"]["events"] and vd["observations"]["cues"]
    assert s["specs"][0]["version"] == 1 and s["demo"]
    arts = [m["artifact"] for m in s["models"]]
    assert arts[:3] == ["object tracks (top view)", "video observations", "specification v1"]
    assert all(m["source"] == "fixture" for m in s["models"])
    r = c.get(f"/api/video/file/bundled-top?session={c.sid}")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4" and r.content[4:8] == b"ftyp"
    assert c.get("/api/rollouts/demo").json()["kind"] == "demonstration"
    # the story continues from the video demonstration
    assert wait(c, c.post("/api/run", json={}).json())["status"] == "done"


def test_upload_validation_and_processing(client):
    c = client
    assert c.post("/api/video/upload?view=top", content=b"not a video at all, definitely").status_code == 422
    assert c.post("/api/video/upload?view=front", content=b"x" * 100).status_code in (409, 422)
    wait(c, c.post("/api/video/process", json={"top": "bundled"}).json())   # renders the fixture video
    path = c.app.state.manager.get(c.sid).video_file("bundled-top")
    r = c.post("/api/video/upload?view=top", content=open(path, "rb").read())
    assert r.status_code == 200, r.text
    vid = r.json()["id"]
    j = wait(c, c.post("/api/video/process", json={"top": vid}).json())
    assert j["status"] == "done"
    assert c.get("/api/state").json()["video_demo"]["views"]["top"]["id"] == vid
    assert c.get(f"/api/video/file/{vid}?session={c.sid}").status_code == 200
    assert c.get(f"/api/video/file/nope?session={c.sid}").status_code == 404


def test_spoken_correction_and_models(client):
    c = client
    c.post("/api/demo/load")
    wait(c, c.post("/api/interpret").json())
    assert c.post("/api/voice", content=wav_bytes(rate=44100)).status_code == 422
    assert c.post("/api/voice", content=wav_bytes(channels=2)).status_code == 422
    assert c.post("/api/voice", content=b"RIFF....WAVE").status_code == 422
    j = wait(c, c.post("/api/voice", content=wav_bytes(1.5)).json())
    assert j["status"] == "done"
    tr = j["result"]
    assert tr["text"] and tr["source"] == "fixture" and tr["audio_s"] == 1.5
    assert c.post("/api/correct", json={"text": tr["text"], "transcript_id": "t-missing"}).status_code == 404
    j = wait(c, c.post("/api/correct", json={"text": tr["text"], "transcript_id": tr["id"]}).json())
    assert j["result"]["changed"]
    m = c.get("/api/models").json()
    roles = [a["role"] for a in m["artifacts"]]
    assert "speech to text" in roles and any("(spoken)" in r for r in roles)
    assert {"sam", "video", "voice"} <= set(m["budgets"])
    assert "MODEL_API_KEY" not in str(m)


def test_meta_budget_names_and_refusal(monkeypatch):
    monkeypatch.setenv("MIRROR_SAM_CALLS_PER_HOUR", "3")
    monkeypatch.setenv("MIRROR_VOICE_CALLS_PER_SESSION", "1")
    b = meta_budgets()
    assert b["sam"].per_hour == 3 and b["voice"].per_session == 1 and b["video"].per_session == 2
    svc = StudioService(backend="fixture", session_id="s", meta_budgets={"voice": ModelBudget(per_hour=10, per_session=1)})
    svc.mode = "live"                      # exercise the public-server guard without a live backend
    svc._spend("voice")
    with pytest.raises(RuntimeError, match="session"):
        svc._spend("voice")


def test_check_wav():
    assert check_wav(wav_bytes(2.0))["seconds"] == 2.0
    for bad in (wav_bytes(31.0), wav_bytes(0.05), wav_bytes(rate=8000), wav_bytes(width=1), b"RIFFxxxxWAVE"):
        with pytest.raises(ValueError):
            check_wav(bad)


def test_track_normalisation_variants():
    mask = np.zeros((36, 64), dtype=bool)
    mask[10:20, 30:40] = True
    flat = mask.flatten()                   # row-major, starting with a 0-run
    counts, cur, n = [], False, 0
    for v in flat:
        if v != cur:
            counts.append(n)
            n, cur = 0, v
        n += 1
    counts.append(n)
    raw = {"top": {"fps": 10, "width": 64, "height": 36, "objects": {
        "cup_0": [{"t": 0.0, "box": [30, 10, 40, 20], "rle": {"size": [36, 64], "counts": counts}},
                  {"t": 0.02, "box": [30, 10, 40, 20]},          # dropped: above max_fps
                  {"t": 0.5, "bbox": [1, 2, 3, 4], "mask": mask.tolist(), "score": 0.9}],
        "tray_0": [{"frame": 5, "polygon": [[0, 0], [10, 0], [10, 10]]}]}}}
    out = mb.normalize_tracks(raw)
    cup = out["top"]["objects"]["cup_0"]
    assert [f["t"] for f in cup] == [0.0, 0.5]
    g = cup[0]["grid"]
    assert g["w"] == 64 and any(r[0] == 10 and r[1] == 30 and r[2] == 40 for r in g["runs"])
    assert cup[1]["box"] == [1.0, 2.0, 3.0, 4.0] and cup[1]["score"] == 0.9 and cup[1]["grid"]["runs"]
    assert out["top"]["objects"]["tray_0"][0] == {"t": 0.5, "polygon": [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0]]}
    flat_style = mb.normalize_tracks({"cup_0": [{"t": 1, "box": [0, 0, 1, 1]}]})
    assert flat_style["top"]["objects"]["cup_0"][0]["box"] == [0.0, 0.0, 1.0, 1.0]


def test_observation_normalisation_variants():
    o = mb.normalize_observations({
        "objects": [{"object_id": "cup_0", "kind": "cup", "relevant": True, "reason": "contains beads"}],
        "events": [{"time": 4.2, "type": "release", "object": "cup_0"}, {"t": 1.0, "kind": "pick", "object_id": "cup_0"},
                   {"t_start": 2.0, "t_end": 3.5, "kind": "care", "object_id": "cup_0", "description": "kept level"}],
        "uncertainties": [{"question": "Must the marker stand?"}, "Is order important?"],
        "model": "muse-wonder-1.0"})
    assert [(e["kind"], e["t"]) for e in o["events"]] == [("grasp", 1.0), ("release", 4.2)]
    assert o["cues"] == [{"t": 2.0, "kind": "care", "object_id": "cup_0", "text": "kept level", "evidence": "", "t_end": 3.5}]
    assert o["uncertainties"] == ["Must the marker stand?", "Is order important?"]
    assert o["objects"][0] == {"id": "cup_0", "kind": "cup", "matters": True, "note": "contains beads"}
    assert o["model"] == "muse-wonder-1.0"


def test_live_backend_reports_missing_stages_as_unavailable():
    cap = mb.available()
    assert set(cap) == {"perception", "observe", "voice", "bundled_video"}
    if not cap["perception"]:
        with pytest.raises(RuntimeError, match="not installed"):
            mb.perceive("x.mp4")



def test_normalises_interpret_video_observations_shape():
    o = mb.normalize_observations({
        "summary": "Packs three objects.", "model": "muse-spark-1.3", "issues": ["event at 50 s is outside the clip"],
        "objects": [{"kind": "cup", "description": "small white open box", "relevant": True, "reason": "carried"}],
        "events": [{"type": "grasp", "object": "cup", "t_start": 3.0, "t_end": 3.0, "evidence": "fingers close"},
                   {"type": "carry", "object": "cup", "t_start": 3.0, "t_end": 9.0, "evidence": "moves"}],
        "care_cues": [{"object": "cup", "cue": "kept level near the wall", "t_start": 5.0, "t_end": 7.0, "evidence": "flat"}],
        "uncertainties": [{"about": "marker", "question": "Must the marker stand?"}]})
    assert o["events"] == [{"t": 3.0, "kind": "grasp", "object_id": "cup", "text": "", "evidence": "fingers close"}]
    assert [c["kind"] for c in o["cues"]] == ["carry", "care"]
    assert o["cues"][1]["text"] == "kept level near the wall"
    assert o["uncertainties"] == ["Must the marker stand?"]
    assert o["objects"][0]["note"] == "small white open box; carried" and o["issues"]



def test_sam_results_become_overlay_grids():
    pytest.importorskip("mirror.meta")
    from pathlib import Path
    import json as _json
    from mirror.meta import parse_sam_output
    root = Path(__file__).resolve().parents[2] / "data" / "cassettes_meta" / "sam-3.1"
    files = sorted(root.glob("*.json")) if root.exists() else []
    texts = [(_json.loads(f.read_text()).get("response") or {}).get("text") for f in files]
    texts = [x for x in texts if x and "<|box" in x]
    if not texts:
        pytest.skip("no recorded SAM response")
    res = parse_sam_output(texts[0], prompt="blue cube", media="video")
    out = mb.normalize_tracks({"top": {"blue cube": res}})
    frames = out["top"]["objects"]["blue cube"]
    assert out["top"]["width"] == res.width and frames
    assert frames[0]["box"] and frames[0]["grid"]["runs"]



def test_bundled_reference_video_is_found_and_clean_of_benchmark_seeds():
    b = mb.bundled_demo()
    if b is None:
        pytest.skip("no bundled video")
    from pathlib import Path
    assert Path(b["top"]).read_bytes()[4:8] == b"ftyp" and b["side"] and b["ground_truth"].endswith("video.json")
    man = json.loads(Path(b["ground_truth"]).read_text())
    assert man["scene_id"] == "ref_demo"                     # the reference demonstration, not a benchmark seed


def test_perception_tracks_become_overlay_frames():
    class T:                                                 # the shape of mirror.perception.Tracks
        fps, n_frames, size, static, notes = 15.0, 4, {"top": (8, 6)}, {"tray_0"}, []
        masks = {"top": {"cup_0": {0: 1, 1: 1, 2: 1, 3: 1}, "tray_0": {0: 1}, "table~inner": {0: 1}}}

        def get(self, cam, label, i):
            m = np.zeros((6, 8), dtype=bool)
            m[1:3, 2 + i:4 + i] = True
            return m
    v = mb.tracks_view(T(), max_fps=7.5)
    top = v["top"]
    assert top["width"] == 8 and top["static"] == ["tray_0"] and "table~inner" not in top["objects"]
    assert [f["t"] for f in top["objects"]["cup_0"]] == [0.0, 0.133]
    assert top["objects"]["cup_0"][1]["box"] == [4, 1, 6, 3]


def test_perception_calls_from_track_notes():
    import json as _json

    class T:
        notes = [_json.dumps({"grounding": {"top": {"calls": [{"model": "muse-spark-1.3", "latency_s": 7.9, "cassette": False,
                                                               "usage": {"total_tokens": 2527}}]}},
                              "sam": {"top": {"latency_s": 123.1, "frames": 1233, "cassette": True}},
                              "reseed": [{"camera": "side", "object": "cup_0", "sam": {"latency_s": 9.0, "frames": 40,
                                                                                      "cassette": True}},
                                         {"camera": "top", "object": "marker_0",
                                          "grounding": {"latency_s": 3.0, "cassette": False, "usage": {}}}]})]
    calls = mb._calls_from_tracks(T())
    assert [(c["model"], c["source"]) for c in calls] == [("muse-spark-1.3", "live"), ("sam-3.1", "recorded"),
                                                          ("sam-3.1", "recorded"), ("muse-spark-1.3", "live")]
