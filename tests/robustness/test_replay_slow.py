"""Slow: the rehearsal replays from recorded model calls with no network."""
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow
ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "results" / "robustness_rehearsal" / "report.json"


@pytest.mark.skipif(not REPORT.exists(), reason="rehearsal report not present")
def test_rehearsal_replays_from_cassettes(tmp_path):
    from mirror.meta import MetaClient
    from mirror.robustness import by_id, evaluate_robustness, frames_for_seeds
    from mirror.robustness.frames import load_frames
    frames_for_seeds([8], tmp_path, split="dev")
    frames = [f for f in load_frames(tmp_path) if f.frame_id == "dev8_oblique_carry"]
    rep = evaluate_robustness(frames, by_id(["light_dim", "noise_gauss"]), client=MetaClient(mode="replay"))
    assert rep["sam"]["network_calls"] == 0
    saved = json.loads(REPORT.read_text())
    key = lambda r: (r["condition"], r["correction"], r["instruction_id"], r["cls"])
    want = {key(r): r["iou"] for r in saved["rows"] if r["frame_id"] == "dev8_oblique_carry"}
    got = {key(r): r["iou"] for r in rep["rows"]}
    assert got and all(got[k] == want[k] for k in got)
    assert rep["prompts_used"]["A"]["dev8_oblique_carry"]["prompts"] == \
        saved["prompts_used"]["A"]["dev8_oblique_carry"]["prompts"]
