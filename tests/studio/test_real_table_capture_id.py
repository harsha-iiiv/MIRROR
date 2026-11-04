"""real_table.pipeline_source: capture id precedence (review P2-8)."""
from __future__ import annotations

import json
from pathlib import Path

from mirror.studio import real_table as RT


def _dir(base: Path, name: str, manifest: dict) -> Path:
    d = base / name
    (d / "correct").mkdir(parents=True)
    for q in ("session.json", "take.mp4", "correct/rollout_after.json", "correct/spec_after.json", "correct/recheck_before.json"):
        (d / q).write_text("{}")
    (d / "manifest.json").write_text(json.dumps(manifest))
    return d


CORR = {"name": "mug_clearance", "label": "Keep the mug 6 cm clear", "rollout": "correct/rollout_after.json",
        "spec": "correct/spec_after.json", "recheck": "correct/recheck_before.json"}


def test_capture_id_from_manifest_wins_for_any_dir_name(tmp_path):
    assert RT.pipeline_source(_dir(tmp_path, "final", {"capture_id": "D1"})).capture_id == "D1"
    assert RT.pipeline_source(_dir(tmp_path / "a", "out", {"capture_id": "D1"})).capture_id == "D1"


def test_capture_id_fallbacks(tmp_path):
    assert RT.pipeline_source(_dir(tmp_path / "D7", "out", {})).capture_id == "D7"       # out -> parent name
    assert RT.pipeline_source(_dir(tmp_path, "final", {})).capture_id == "final"         # else the dir name
    assert RT.pipeline_source(_dir(tmp_path / "b", "out", {"capture_id": "D1"}), "given").capture_id == "given"
