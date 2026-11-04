"""D1 (Harsha's real phone footage, IMG_0815 + lens clip IMG_0806) replayed through
mirror.capture.real_run from its recorded cassettes: zero network, zero spend,
and the honest outcome (gate refused on the calibration flag; his stated task
run in simulation). Slow (about 15 minutes on 4 shared cores) and skipped unless
the D1 inputs are present (personal footage is not in the repository):

    MIRROR_D1_INPUTS=/workspace/mirror-team/v2/night/pipeline/D1 \
        pytest -m slow tests/capture/test_real_run_d1.py

The directory must hold measurements.json, answers_from_page.json, cassettes_d1/,
IMG_0806.MOV and IMG_0815.MOV (or MIRROR_D1_TAKE pointing at it).
MIRROR_D1_RUN=<existing real_run output> checks a finished run instead of running."""
import json
import os
from pathlib import Path

import pytest

from mirror.capture import real_run as RR

pytestmark = pytest.mark.slow
INP = Path(os.environ.get("MIRROR_D1_INPUTS", "/nonexistent"))
TAKE = Path(os.environ.get("MIRROR_D1_TAKE", str(INP / "IMG_0815.MOV")))


@pytest.fixture(scope="module")
def d1_run(tmp_path_factory):
    done = os.environ.get("MIRROR_D1_RUN")
    if done:
        return Path(done)
    if not (INP / "measurements.json").exists() or not TAKE.exists():
        pytest.skip("D1 inputs not present (personal footage, not in the repository)")
    out = tmp_path_factory.mktemp("d1")
    os.environ.pop("MODEL_API_KEY", None)
    rc = RR.main(["--take", str(TAKE), "--lens", str(INP / "IMG_0806.MOV"), "--measurements", str(INP / "measurements.json"),
                  "--out", str(out), "--mode", "replay", "--cassettes", str(INP / "cassettes_d1"), "--seed-t", "12.5",
                  "--dev-clip", "D1", "--answers", str(INP / "answers_from_page.json"),
                  "--rehearse-correction", "mug_clearance", "--no-render"])
    assert rc == 0
    return out


def test_replay_is_free_and_every_model_output_came_from_a_recording(d1_run):
    st = json.loads((d1_run / "real_run.json").read_text())
    assert st["mode"] == "replay" and st["spend_usd"] == 0.0
    assert not (d1_run / "spend.jsonl").exists() or (d1_run / "spend.jsonl").read_text().strip() == ""
    assert st["stages"]["ground"]["from_cassette"]
    assert st["stages"]["track"]["origin"] == "recorded_replay"
    assert all(v["cassette"] for v in st["stages"]["track"]["sam"].values())


def test_watch_stage_finds_the_setup_and_asks_the_same_questions(d1_run):
    st = json.loads((d1_run / "real_run.json").read_text())["stages"]
    assert st["lens"]["accepted"] and st["lens"]["n_views"] == 65
    sync = [round(e["t"], 2) for e in st["scan"]["sync_events"]]
    assert sync == [8.48, 11.68, 36.99, 40.25]
    assert st["scan"]["end_board_same_placement"] is False
    q = json.loads((d1_run / "questions.json").read_text())["questions"]
    assert [x["id"] for x in q] == ["mug", "red_box", "marker", "tray", "divider", "mug_pickup", "camera_settings", "intent"]


def test_gate_stays_closed_and_the_stated_task_runs_labelled(d1_run):
    page = json.loads((d1_run / "page.json").read_text())
    assert page["gate"]["passed"] is False
    assert [b["code"] for b in page["gate"]["blocking_items"]] == ["recalibration_required"]
    assert "Calibration reuse flagged" in page["labels"] and "Development clip D1, never scored" in page["labels"]
    assert "stated task" in page["task"]["spec_source"] and "refused" in page["task"]["proposal_from_capture"]
    assert page["run"]["result"] == "success" and page["run"]["violations"] == 0
    assert page["correct"]["rehearsal"] is True and "rehearsal" in page["correct"]["chosen_by"]
