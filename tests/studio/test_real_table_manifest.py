"""real_table.pipeline_source: correction provenance (review P0-3)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

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


@pytest.mark.parametrize("extra", [{}, {"rehearsal": None}])
def test_missing_rehearsal_raises(tmp_path, extra):
    d = _dir(tmp_path, "final", {"corrections": [dict(CORR, **extra)]})
    with pytest.raises(ValueError, match="rehearsal"):
        RT.pipeline_source(d)


@pytest.mark.parametrize("flag", [True, False])
def test_rehearsal_flag_kept(tmp_path, flag):
    d = _dir(tmp_path, f"f{int(flag)}", {"corrections": [dict(CORR, rehearsal=flag)]})
    rec = RT.pipeline_source(d).recorded_corrections
    assert len(rec) == 1 and rec[0]["rehearsal"] is flag
