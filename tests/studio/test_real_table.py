"""The "Your table" data builder (mirror/studio/real_table.py): pure pieces on synthetic inputs,
plus an end-to-end build of the D1 development capture when its files are on this machine."""
from __future__ import annotations

import gzip
import json
import math
import os
from pathlib import Path

import numpy as np
import pytest

from mirror.perception.masks import rle_encode
from mirror.studio import real_table as RT


def _tracks(tmp_path: Path) -> Path:
    n, fps = 30, 30.0
    masks = {"box": {}, "wall": {}}
    for i in range(n):
        m = np.zeros((96, 54), dtype=np.uint8)
        m[20:40, 5 + i:15 + i] = 1                     # moves right one pixel per frame
        masks["box"][str(i)] = rle_encode(m)
    w = np.zeros((96, 54), dtype=np.uint8); w[50:90, 25:28] = 1
    masks["wall"]["0"] = rle_encode(w)               # one frame only: static
    p = tmp_path / "tracks.json.gz"
    with gzip.open(p, "wt") as f:
        json.dump({"fps": fps, "n_frames": n, "masks": {"top": masks}, "static": ["wall"], "source": "synthetic"}, f)
    return p


def test_contours_sampled_normalised_and_static(tmp_path):
    c = RT.contours_from_tracks(_tracks(tmp_path), hz=10)
    box, wall = c["objects"]["box"], c["objects"]["wall"]
    assert c["step"] == 3 and box["t"][:3] == [0.0, 0.1, 0.2]
    assert len(box["poly"]) == 10 and box["visible"] == 1.0
    xs = box["poly"][0][0::2]
    assert all(0.0 <= v <= 1.0 for v in box["poly"][0])
    assert min(xs) == pytest.approx(5 / 54, abs=0.02)
    assert min(box["poly"][-1][0::2]) > min(xs)        # the outline moved with the object
    assert wall["static"] is True and len(wall["poly"]) == 1


def test_series_tilt_and_gap():
    scene = {"objects": [{"id": "cup_0", "kind": "cup", "size": {"outer_half": [0.03, 0.03, 0.05]}}],
             "obstacles": [{"pose": {"pos": [0.0, 0.0, 0.0], "quat": [1, 0, 0, 0]}, "length": 0.4, "thickness": 0.02}]}
    tilt = math.radians(10)
    payload = {"frame_hz": 10, "t": [0.0, 0.1],
               "bodies": {"cup_0": [[0.11, 0.0, 0.05, 1, 0, 0, 0], [0.11, 0.0, 0.05, math.cos(tilt / 2), math.sin(tilt / 2), 0, 0]]}}
    s = RT.series(payload, scene, hz=10)
    assert s["tilt"] == [0.0, pytest.approx(10.0, abs=0.01)]
    # wall thickness on x (half 0.01): surface gap 0.11 - 0.01 - 0.03 = 7 cm
    assert s["gap_cm"][0] == pytest.approx(7.0, abs=0.05)


def test_rule_rows_and_summary():
    rep = {"status": "success", "violations": [], "route_labels": ["cup_0:around:wall_0:-y"], "outcomes": [
        {"object_id": "cup_0", "in_target": True, "beads_total": 8, "beads_in_container": 7, "max_transport_tilt_deg": 2.0},
        {"object_id": "block_0", "in_target": False, "beads_total": 0, "max_transport_tilt_deg": 0.1}],
        "rule_results": [{"rule_id": "g", "kind": "goal", "type": "inside", "verdict": "satisfied", "enforced": True, "objects": ["cup_0"]}]}
    s = RT._summary(rep)
    assert s["beads"] == {"kept": 7, "total": 8} and s["in_target"] == 1 and s["objects"] == 2
    assert RT._rule_rows(rep)[0]["verdict"] == "satisfied"


def test_write_index(tmp_path):
    for i, built in (("A", "2026-11-03T10:00"), ("B", "2026-11-04T09:00")):
        d = tmp_path / i; d.mkdir()
        (d / "table.json").write_text(json.dumps({"format": RT.TABLE_FORMAT, "id": i, "built_at": built, "labels": ["x"]}))
    rows = RT.write_index(tmp_path)
    assert [r["id"] for r in rows] == ["B", "A"] and rows[0]["path"] == "B/table.json"


def test_export_refuses_unlabelled_or_foreign_tables(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("export_static", Path(__file__).resolve().parents[2] / "scripts" / "export_static.py")
    ex = importlib.util.module_from_spec(spec); spec.loader.exec_module(ex)
    bad = tmp_path / "bad"; bad.mkdir(); (bad / "table.json").write_text(json.dumps({"format": "other", "id": "x"}))
    with pytest.raises(SystemExit):
        ex.copy_tables(tmp_path / "site", [bad])
    nol = tmp_path / "nol"; nol.mkdir(); (nol / "table.json").write_text(json.dumps({"format": RT.TABLE_FORMAT, "id": "x", "labels": []}))
    with pytest.raises(SystemExit):
        ex.copy_tables(tmp_path / "site", [nol])
    ok = tmp_path / "ok"; ok.mkdir(); (ok / "table.json").write_text(json.dumps({"format": RT.TABLE_FORMAT, "id": "D9", "labels": ["Real phone footage"]}))
    assert ex.copy_tables(tmp_path / "site", [ok]) == ["D9"]
    assert json.loads((tmp_path / "site/data/table/index.json").read_text())["tables"][0]["id"] == "D9"


D1 = RT.D1_ROOT / "D1" / "session.json"


@pytest.mark.slow
@pytest.mark.skipif(not D1.exists() or not RT.D1_CLOUD.exists(), reason="D1 capture files not on this machine")
def test_build_d1_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRROR_LLM_MODE", "offline")
    t = RT.build(RT.d1_source(), tmp_path / "D1", video=False)
    assert t["model_calls_on_build"] == 0
    assert "Calibration reuse flagged" in t["labels"] and "Real phone footage" in t["labels"]
    assert t["gate"]["passed"] is False and t["gate"]["blocking"][0]["code"] == "recalibration_required"
    assert t["gate"]["proposal"].startswith("refused")
    assert t["task"]["source"] == "person_stated_intent"
    assert all(o["confirmed_by_person"] for o in t["objects"])
    assert t["robot"]["run"]["summary"]["status"] == "success"
    by = {c["id"]: c for c in t["robot"]["corrections"]}
    assert by["mug_clearance"]["outcome"] == "changed"
    assert by["mug_clearance"]["recheck"]["status"] == "constraint_violated"
    assert by["mug_clearance"]["after"]["summary"]["violations"] == 0
    assert by["box_upright"]["outcome"] == "already_held"
    assert by["no_table_contact"]["outcome"] == "question"
    for c in t["robot"]["corrections"]:
        assert c["compiler"].startswith("offline rules")
    assert (tmp_path / "D1/rollouts/base.json").exists() and (tmp_path / "D1/contours.json").exists()
