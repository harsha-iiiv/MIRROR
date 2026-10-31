import json

import pytest

from mirror.contracts import Pose, Rollout, RolloutFrame, RolloutReport
from mirror.evaluate import FamilyUnsupported, FreezeError, write_final_report
from mirror.evaluate.final import (D3_STRATA, ROLLOUT_RULE, freeze_final, read_ledger, rehearsal_strata,
                                   run_final, select_rollouts)
from mirror.evaluate.paired import compact_rollout, make_layout_fn, paired_eval

from .stubs import StubEnv, stub_planner
from .synth import make_scene, make_spec

CALLS = []


@pytest.fixture(autouse=True)
def _frozen_tree(monkeypatch):
    """Other work on the repository must not make these tests flaky: hash a
    fixed tree. Tests that need real hashing patch again on top."""
    from mirror.evaluate import paired as pm
    monkeypatch.setattr(pm, "code_hash", lambda root=None: ("c" * 64, 1))
    monkeypatch.setattr(pm, "file_hashes", lambda: {"scripts": {"path": "scripts", "sha256": "s", "files": 1}})


def family_layout(seed, split, family="mixed"):
    CALLS.append((seed, split, family))
    s = make_scene(scene_id=f"{family}_{split}_{seed}", full_wall=(split == "blocked"))
    s.seed = seed
    return s


def legacy_layout(seed, split):
    return family_layout(seed, split)


def specs():
    return make_spec(), make_spec(corrected=True), {"source": "test pair", "offline_agrees": True}


def test_family_detection():
    assert make_layout_fn("shortcut", family_layout)(3, "dev").scene_id == "shortcut_dev_3"
    assert make_layout_fn("mixed", legacy_layout)(3, "dev").scene_id == "mixed_dev_3"
    with pytest.raises(FamilyUnsupported, match="shortcut"):
        make_layout_fn("shortcut", legacy_layout)


def test_freeze_refuses_unsupported_family(tmp_path):
    with pytest.raises(FamilyUnsupported):
        freeze_final(tmp_path / "f.json", strata=rehearsal_strata(2, 2, 1), kind="rehearsal",
                     spec_builder=specs, layout_fn=legacy_layout, ledger_path=tmp_path / "l.jsonl")
    assert not (tmp_path / "f.json").exists()


def test_final_runs_every_stratum_from_one_manifest(tmp_path):
    CALLS.clear()
    man = tmp_path / "freeze.json"
    m = freeze_final(man, strata=rehearsal_strata(2, 2, 1), kind="rehearsal", spec_builder=specs,
                     layout_fn=family_layout, ledger_path=tmp_path / "ledger.jsonl")
    assert CALLS == []                                 # no layout before the freeze
    assert [s["name"] for s in m.strata] == ["primary", "shortcut", "blocked"]
    out = tmp_path / "out"
    f = run_final(man, workers=1, save_rollouts=True, out_dir=out, spec_builder=specs,
                  planner=stub_planner, env_factory=StubEnv, layout_fn=family_layout, n_boot=200,
                  ledger_path=tmp_path / "ledger.jsonl", rollout_stage=tmp_path / "stage")
    assert {(c[1], c[2]) for c in CALLS} == {("dev", "mixed"), ("dev", "shortcut"), ("blocked", "mixed")}
    assert set(f.strata) == {"primary", "shortcut", "blocked"}
    blk = f.strata["blocked"]
    assert all(t.b.failure_class == "proven_infeasible" for t in blk.trials)
    assert all(t.a.report.status == "success" for t in blk.trials)
    paths = write_final_report(f, out)
    md = paths["markdown"].read_text()
    assert "REHEARSAL" in md and "primary: A / B" in md and "shortcut: A / B" in md
    assert (out / "primary" / "report.md").exists() and (out / "shortcut" / "paired_tilt.png").exists()
    data = json.loads(paths["json"].read_text())
    assert set(data["strata"]) == {"primary", "shortcut", "blocked"}
    # stub: original tilts the cup in every scene, so every seed is selected
    assert f.rollout_selection["rule"] == ROLLOUT_RULE
    assert f.rollout_selection["selected"]["primary"] == [0, 1]
    ro = sorted((out / "primary" / "rollouts").glob("*.json"))
    assert [p.name for p in ro] == ["seed0_corrected.json", "seed0_original.json",
                                    "seed1_corrected.json", "seed1_original.json"]
    Rollout.model_validate_json(ro[0].read_text())
    assert f.strata["primary"].trials[0].a.rollout_path == "rollouts/seed0_original.json"
    assert "Rollout selection" in md and f.ledger["writer_active"] is True


def test_final_refuses_changed_specs(tmp_path):
    man = tmp_path / "freeze.json"
    freeze_final(man, strata=rehearsal_strata(1, 1, 1), kind="rehearsal", spec_builder=specs,
                 layout_fn=family_layout, ledger_path=tmp_path / "l.jsonl")

    def other():
        a, b, p = specs()
        b.constraints[-1].params["max_tilt_deg"] = 25.0
        return a, b, p
    with pytest.raises(FreezeError, match="specification B"):
        run_final(man, spec_builder=other, planner=stub_planner, env_factory=StubEnv,
                  layout_fn=family_layout, workers=1, ledger_path=tmp_path / "l.jsonl")


def test_stratum_seeds_must_match_manifest(tmp_path):
    man = tmp_path / "freeze.json"
    freeze_final(man, strata=rehearsal_strata(2, 1, 1), kind="rehearsal", spec_builder=specs,
                 layout_fn=family_layout, ledger_path=tmp_path / "l.jsonl")
    a, b, _ = specs()
    with pytest.raises(FreezeError, match="stratum"):
        paired_eval(a, b, [0, 1, 2], split="dev", family="mixed", manifest_path=man,
                    require_manifest=True, layout_fn=family_layout, planner=stub_planner,
                    env_factory=StubEnv)


def _rollout(n=600, moving=("gripper", "cup_0")):
    frames = []
    for i in range(n):
        bodies = {f"cup_0/bead{k}": Pose(pos=(0.1 + 0.001 * i, 0.2, 0.3)) for k in range(8)}
        for name in ("gripper", "gripper_finger_l", "gripper_finger_r", "cup_0", "block_0", "marker_0"):
            dx = 0.001 * i if name in moving else 0.0
            bodies[name] = Pose(pos=(0.1 + dx, 0.2, 0.3))
        frames.append(RolloutFrame(t=i / 15, bodies=bodies, gripper_width=0.05, phase="transport:cup_0"))
    rep = RolloutReport(rollout_id="r", scene_id="s", spec_id="k", spec_version=1, status="success")
    return Rollout(report=rep, frame_hz=15.0, frames=frames)


def test_compact_rollout_omits_static_bodies_losslessly():
    from mirror.evaluate.paired import expand_frames
    ro = _rollout(200)
    text, notes = compact_rollout(ro)
    data = json.loads(text)
    assert "block_0" in data["frames"][0]["bodies"] and "block_0" not in data["frames"][5]["bodies"]
    assert "block_0" in data["frames"][-1]["bodies"]
    full = expand_frames(data["frames"])
    for i in (0, 57, 199):
        for name, pose in ro.frames[i].bodies.items():
            assert full[i]["bodies"][name]["pos"] == pytest.approx(list(pose.pos), abs=3e-4)
    Rollout.model_validate_json(text)


def test_compact_rollout_thins_beads_when_over_budget():
    text, notes = compact_rollout(_rollout(900), max_bytes=150_000)
    r = Rollout.model_validate_json(text)
    assert any("bead bodies" in n for n in notes)
    assert "cup_0/bead0" not in r.frames[1].bodies and "cup_0" in r.frames[1].bodies
    assert any(n.startswith("playback:") for n in r.report.notes)


def test_compact_rollout_halves_frame_rate_as_last_resort():
    text, notes = compact_rollout(_rollout(900, moving=("gripper", "gripper_finger_l", "gripper_finger_r",
                                                         "cup_0", "block_0", "marker_0")), max_bytes=60_000)
    r = Rollout.model_validate_json(text)
    assert r.frame_hz < 15.0 and any("halved" in n for n in notes)
    assert r.frames[-1].t == pytest.approx(899 / 15)


def test_code_drift_tolerated_only_for_rehearsal(tmp_path, monkeypatch):
    from mirror.evaluate import paired as pm
    from mirror.evaluate.paired import freeze_strata, verify_manifest
    a, b, _ = specs()
    st = rehearsal_strata(1, 1, 1)
    freeze_strata(a, b, st, tmp_path / "r.json", kind="rehearsal")
    freeze_strata(a, b, st, tmp_path / "f.json", kind="final")
    monkeypatch.setattr(pm, "code_hash", lambda root=None: ("1" * 64, 1))
    m = verify_manifest(tmp_path / "r.json", a, b, [0], "dev", "mixed", allow_code_drift=True)
    assert "code_drift" in m.config
    with pytest.raises(FreezeError, match="code"):
        verify_manifest(tmp_path / "f.json", a, b, [0], "dev", "mixed", allow_code_drift=True)


def _eval_strata():
    return [dict(name="primary", role="primary", split="eval", family="mixed", seeds=[30000, 30001]),
            dict(name="blocked", role="blocked", split="blocked", family="mixed", seeds=[30000])]


def test_d4_ranges_and_retired_seeds(tmp_path):
    assert D3_STRATA[0]["seeds"][0] == 30000 and len(D3_STRATA[0]["seeds"]) == 60
    assert D3_STRATA[1]["seeds"][0] == 40000 and D3_STRATA[2]["seeds"] == list(range(30000, 30005))
    old = [dict(name="primary", role="primary", split="eval", family="mixed", seeds=[10003])]
    with pytest.raises(FreezeError, match="retired"):
        freeze_final(tmp_path / "f.json", strata=old, spec_builder=specs, layout_fn=family_layout,
                     ledger_path=tmp_path / "l.jsonl")


def test_freeze_refuses_seeds_already_in_ledger(tmp_path):
    led = tmp_path / "ledger.jsonl"
    led.write_text(json.dumps({"seed": 30001, "split": "eval", "family": "mixed", "unix": 1.0,
                               "caller": "some.module"}) + "\n" +
                   json.dumps({"seed": 30001, "split": "eval", "family": "shortcut", "unix": 1.0}) + "\n")
    with pytest.raises(FreezeError, match="ledger"):
        freeze_final(tmp_path / "f.json", strata=_eval_strata(), spec_builder=specs,
                     layout_fn=family_layout, ledger_path=led)
    assert not (tmp_path / "f.json").exists()
    other = [dict(name="primary", role="primary", split="eval", family="mixed", seeds=[30002])]
    m = freeze_final(tmp_path / "g.json", strata=other, spec_builder=specs, layout_fn=family_layout,
                     ledger_path=led)
    assert m.ledger_at_freeze["entries"] == 2 and m.ledger_at_freeze["exists"]


def test_run_refuses_ledger_entry_before_freeze_and_embeds_ledger(tmp_path):
    led = tmp_path / "ledger.jsonl"
    man = tmp_path / "f.json"
    m = freeze_final(man, strata=_eval_strata(), spec_builder=specs, layout_fn=family_layout, ledger_path=led)

    def logging_layout(seed, split, family="mixed"):
        with open(led, "a") as fh:
            fh.write(json.dumps({"seed": seed, "split": split, "family": family,
                                 "unix": m.created_unix + 5, "caller": "test"}) + "\n")
        return family_layout(seed, split, family)
    f = run_final(man, workers=1, spec_builder=specs, planner=stub_planner, env_factory=StubEnv,
                  layout_fn=logging_layout, ledger_path=led, n_boot=100)
    assert f.ledger["writer_active"] and len(f.ledger["frozen_seed_entries"]) == 3
    assert f.ledger["entries_before_freeze"] == []
    with open(led, "a") as fh:
        fh.write(json.dumps({"seed": 30000, "split": "eval", "family": "mixed", "unix": m.created_unix - 60}) + "\n")
    with pytest.raises(FreezeError, match="before the freeze"):
        run_final(man, workers=1, spec_builder=specs, planner=stub_planner, env_factory=StubEnv,
                  layout_fn=family_layout, ledger_path=led)


def test_inactive_ledger_writer_is_reported(tmp_path):
    man = tmp_path / "f.json"
    freeze_final(man, strata=_eval_strata(), spec_builder=specs, layout_fn=family_layout,
                 ledger_path=tmp_path / "none.jsonl")
    f = run_final(man, workers=1, spec_builder=specs, planner=stub_planner, env_factory=StubEnv,
                  layout_fn=family_layout, ledger_path=tmp_path / "none.jsonl", n_boot=100)
    assert f.ledger["writer_active"] is False
    assert any("ledger check is vacuous" in n for n in f.notes)


def test_rollout_selection_rule():
    a, b, _ = specs()
    rep = paired_eval(a, b, list(range(12)), split="dev", layout_fn=lambda s, sp: family_layout(s, sp),
                      planner=lambda sp, sc: stub_planner(make_spec(corrected=True), sc),
                      env_factory=StubEnv, n_boot=100)
    # both conditions go around upright and succeed: only every 5th seed
    assert select_rollouts(rep, "primary") == [0, 5, 10]
    assert select_rollouts(rep, "blocked") == list(range(12))


def test_manifest_records_versions_and_file_hashes(tmp_path, monkeypatch):
    from mirror.evaluate import paired as pm
    from mirror.evaluate.paired import freeze_strata, verify_manifest
    a, b, _ = specs()
    m = freeze_strata(a, b, rehearsal_strata(1, 1, 1), tmp_path / "f.json", kind="final")
    assert {"python", "numpy", "mujoco"} <= set(m.versions)
    monkeypatch.undo()
    m = freeze_strata(a, b, rehearsal_strata(1, 1, 1), tmp_path / "g.json", kind="final")
    assert {"scripts", "demos", "cassettes"} <= set(m.file_hashes)
    monkeypatch.setattr(pm, "code_hash", lambda root=None: (m.code_sha256, m.code_files))
    monkeypatch.setattr(pm, "file_hashes", lambda: {k: dict(v, sha256="0") for k, v in m.file_hashes.items()})
    with pytest.raises(FreezeError, match="scripts"):
        verify_manifest(tmp_path / "g.json", a, b, [0], "dev", "mixed")


def test_offline_specs_refused_for_final_freeze(tmp_path):
    with pytest.raises(FreezeError, match="rehearsal"):
        freeze_final(tmp_path / "f.json", strata=_eval_strata(), spec_source="offline",
                     layout_fn=family_layout, ledger_path=tmp_path / "l.jsonl")
