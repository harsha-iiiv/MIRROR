import json

import pytest

from mirror.contracts import Pose, Rollout, RolloutFrame, RolloutReport
from mirror.evaluate import FamilyUnsupported, FreezeError, write_final_report
from mirror.evaluate.final import freeze_final, rehearsal_strata, run_final
from mirror.evaluate.paired import compact_rollout, make_layout_fn, paired_eval

from .stubs import StubEnv, stub_planner
from .synth import make_scene, make_spec

CALLS = []


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
                     spec_builder=specs, layout_fn=legacy_layout)
    assert not (tmp_path / "f.json").exists()


def test_final_runs_every_stratum_from_one_manifest(tmp_path):
    CALLS.clear()
    man = tmp_path / "freeze.json"
    m = freeze_final(man, strata=rehearsal_strata(2, 2, 1), kind="rehearsal", spec_builder=specs,
                     layout_fn=family_layout)
    assert CALLS == []                                 # no layout before the freeze
    assert [s["name"] for s in m.strata] == ["primary", "shortcut", "blocked"]
    out = tmp_path / "out"
    f = run_final(man, workers=1, save_rollouts=True, out_dir=out, spec_builder=specs,
                  planner=stub_planner, env_factory=StubEnv, layout_fn=family_layout, n_boot=200)
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
    ro = sorted((out / "primary" / "rollouts").glob("*.json"))
    assert [p.name for p in ro] == ["seed0_corrected.json", "seed0_original.json",
                                    "seed1_corrected.json", "seed1_original.json"]
    Rollout.model_validate_json(ro[0].read_text())
    assert f.strata["primary"].trials[0].a.rollout_path == "rollouts/seed0_original.json"


def test_final_refuses_changed_specs(tmp_path):
    man = tmp_path / "freeze.json"
    freeze_final(man, strata=rehearsal_strata(1, 1, 1), kind="rehearsal", spec_builder=specs,
                 layout_fn=family_layout)

    def other():
        a, b, p = specs()
        b.constraints[-1].params["max_tilt_deg"] = 25.0
        return a, b, p
    with pytest.raises(FreezeError, match="specification B"):
        run_final(man, spec_builder=other, planner=stub_planner, env_factory=StubEnv,
                  layout_fn=family_layout, workers=1)


def test_stratum_seeds_must_match_manifest(tmp_path):
    man = tmp_path / "freeze.json"
    freeze_final(man, strata=rehearsal_strata(2, 1, 1), kind="rehearsal", spec_builder=specs,
                 layout_fn=family_layout)
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
