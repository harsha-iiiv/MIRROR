import math

import pytest

from mirror.geometry import WORLD
from mirror.scenes import EVAL_SEED_BASE, reference_scene, route_exists, sample_layout, validate_layout


def test_reference_scenes_are_valid():
    for name in ("demo", "shortcut", "blocked"):
        assert validate_layout(reference_scene(name)) == []


def test_demo_wall_has_no_slot_and_one_open_end():
    s = reference_scene("demo")
    wall = s.obstacles[0]
    assert wall.slots == []
    top = wall.pose.pos[1] + wall.length / 2
    bottom = wall.pose.pos[1] - wall.length / 2
    assert top < WORLD.workspace_y[1] - 0.2 and bottom < WORLD.workspace_y[0]


def test_shortcut_slot_lies_on_the_cup_to_tray_line():
    s = reference_scene("shortcut")
    cup = next(o for o in s.objects if o.kind == "cup")
    tray = s.trays[0]
    wall = s.obstacles[0]
    wx = wall.pose.pos[0]
    t = (wx - cup.pose.pos[0]) / (tray.pose.pos[0] - cup.pose.pos[0])
    y_cross = cup.pose.pos[1] + t * (tray.pose.pos[1] - cup.pose.pos[1])
    slot = wall.slots[0]
    yc = wall.pose.pos[1]
    assert yc + slot.y_min < y_cross < yc + slot.y_max


def test_blocked_wall_spans_workspace_and_slot_is_the_only_route():
    s = reference_scene("blocked")
    wall = s.obstacles[0]
    assert wall.pose.pos[1] - wall.length / 2 < WORLD.workspace_y[0]
    assert wall.pose.pos[1] + wall.length / 2 > WORLD.workspace_y[1]
    cup = s.objects[0]
    assert route_exists(s, cup.pose.pos[:2], s.trays[0].pose.pos[:2])
    no_slot = s.model_copy(deep=True)
    no_slot.obstacles[0].slots = []
    assert not route_exists(no_slot, cup.pose.pos[:2], s.trays[0].pose.pos[:2])


def test_sampling_is_deterministic():
    # Evaluation layouts are never generated in tests (D4); the eval split
    # uses the same code path with a different seed stream.
    assert sample_layout(7, "dev") == sample_layout(7, "dev")
    assert sample_layout(7, "dev") != sample_layout(8, "dev")


def test_split_seed_ranges_are_enforced():
    # Every call below is rejected by the seed check before any layout is drawn.
    with pytest.raises(ValueError):
        sample_layout(EVAL_SEED_BASE, "dev")
    with pytest.raises(ValueError):
        sample_layout(3, "eval")
    with pytest.raises(ValueError):
        sample_layout(3, "test")


def test_sampled_layouts_are_valid_and_varied():
    scenes = [sample_layout(s, "dev") for s in range(30)]
    for sc in scenes:
        assert validate_layout(sc) == []
        assert sc.split == "dev" and sc.scene_id == f"dev_{sc.seed}"
        assert all(o.pose.pos[0] < sc.obstacles[0].pose.pos[0] for o in sc.objects)
        assert sc.trays[0].pose.pos[0] > sc.obstacles[0].pose.pos[0]
    with_slot = sum(bool(sc.obstacles[0].slots) for sc in scenes)
    assert 5 <= with_slot <= 28
    assert any(len(sc.obstacles) > 1 for sc in scenes)
    sides = {math.copysign(1, sc.obstacles[0].pose.pos[1]) for sc in scenes}
    assert sides == {-1.0, 1.0}


def test_blocked_split_layouts():
    for seed in range(5):
        sc = sample_layout(seed, "blocked")
        wall = sc.obstacles[0]
        assert wall.slots and wall.length > WORLD.workspace_y[1] - WORLD.workspace_y[0]
        assert validate_layout(sc) == []
