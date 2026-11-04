"""Correction provenance from the pipeline manifest (review P0-3): a missing rehearsal flag is an error."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

D1_LABELS = ["Real phone footage", "One camera (single view, reduced)", "Calibration reuse flagged",
             "Development clip D1, never scored"]


# ---------------------------------------------------------------- correction provenance (P0-3)
def _load_cli():
    import importlib.util
    spec = importlib.util.spec_from_file_location("twin_render", Path(__file__).resolve().parents[2] / "scripts" / "twin_render.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pipe(tmp_path, correction: dict) -> Path:
    d = tmp_path / "out"
    for sub in ("run", "correct"):
        (d / sub).mkdir(parents=True)
    for q in (d / "session.json", d / "run" / "rollout.json", d / "correct" / "rollout_after.json"):
        q.write_text("{}")
    (d / "manifest.json").write_text(json.dumps({
        "session": "session.json", "rollouts": {"run": "run/rollout.json"}, "labels": D1_LABELS,
        "corrections": [dict({"name": "mug_clearance", "label": "Keep the mug 6 cm clear",
                              "rollout": "correct/rollout_after.json"}, **correction)]}))
    return d


@pytest.mark.parametrize("corr", [{}, {"rehearsal": None}, {"rehearsal": "yes"}])
def test_missing_rehearsal_is_an_error(tmp_path, corr):
    mod = _load_cli()
    with pytest.raises(ValueError, match="rehearsal"):
        mod.from_pipeline(_pipe(tmp_path, corr), SimpleNamespace(video=Path("/x/t.MOV"), correction=None))


def test_rehearsal_false_is_the_persons_and_labels_pass_through(tmp_path):
    mod = _load_cli()
    cfg = mod.from_pipeline(_pipe(tmp_path, {"rehearsal": False}), SimpleNamespace(video=Path("/x/t.MOV"), correction=None))
    assert cfg["after_labels"] == [] and cfg["labels"] == D1_LABELS
    cfg = mod.from_pipeline(_pipe(tmp_path / "b", {"rehearsal": True}), SimpleNamespace(video=Path("/x/t.MOV"), correction=None))
    assert cfg["after_labels"] == ["Rehearsal correction, not chosen by you"]


def test_correction_dir_without_manifest_entry_is_an_error(tmp_path):
    mod = _load_cli()
    d = tmp_path / "out"
    (d / "run").mkdir(parents=True)
    (d / "corrections" / "x").mkdir(parents=True)
    for q in (d / "session.json", d / "run" / "rollout.json", d / "corrections" / "x" / "rollout.json"):
        q.write_text("{}")
    with pytest.raises(ValueError, match="rehearsal"):
        mod.from_pipeline(d, SimpleNamespace(video=Path("/x/t.MOV"), correction=None))
