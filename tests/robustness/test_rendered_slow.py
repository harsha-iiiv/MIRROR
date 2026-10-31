"""Slow: geometry check on a real rendered development frame."""
import pytest

pytestmark = pytest.mark.slow


def test_controls_on_a_rendered_dev_frame():
    from mirror.robustness.controls import calibrate
    from mirror.robustness.frames import dev_frames
    frames = dev_frames([3], cameras=("oblique",), moments=("start",))
    assert frames and frames[0].masks["block"].sum() > 300
    res = calibrate(frames)
    for name in ("dim_x0.35", "warm_tint", "gamma_1.8", "background_recolor", "noise_gauss"):
        assert res["positive_pass_rate"][name] == 1.0, (name, res)
    assert res["negative"]["shift_8px"]["detection_rate"] == 1.0
    assert res["negative"]["removed"]["detection_rate"] == 1.0
    assert res["collateral_flags"] == 0


def test_frames_for_seeds_is_deterministic_and_checks_visibility(tmp_path):
    import json
    from mirror.robustness.frames import FramesNotVisible, frames_for_seeds
    m1 = frames_for_seeds([8], tmp_path / "a", split="dev")
    m2 = frames_for_seeds([8], tmp_path / "b", split="dev")
    assert [f["png_sha256"] for f in m1["frames"]] == [f["png_sha256"] for f in m2["frames"]]
    assert [f["frame_id"] for f in m1["frames"]] == ["dev8_top_carry", "dev8_oblique_carry"]
    assert m1["all_visible"] and all(f["masks_sha256"] for f in m1["frames"])
    assert json.loads((tmp_path / "a" / "frames_manifest.json").read_text())["seeds"] == [8]
    with pytest.raises(FramesNotVisible):
        frames_for_seeds([8], tmp_path / "c", split="dev", min_pixels=10**6)
    assert (tmp_path / "c" / "frames_manifest.json").exists()
