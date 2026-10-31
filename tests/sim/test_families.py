import pytest

from mirror.scenes import EVAL_SEED_BASE, SHORTCUT_EVAL_SEED_BASE, sample_layout, validate_layout


def _crossing(sc):
    cup, tray, wall = sc.objects[0], sc.trays[0], sc.obstacles[0]
    wx = wall.pose.pos[0]
    (cx, cy), (tx, ty) = cup.pose.pos[:2], tray.pose.pos[:2]
    return cy + (wx - cx) / (tx - cx) * (ty - cy)


def test_default_family_is_mixed():
    assert sample_layout(5, "dev") == sample_layout(5, "dev", family="mixed")
    assert sample_layout(5, "dev").scene_id == "dev_5"


def test_shortcut_slot_always_contains_the_cup_to_tray_line():
    for seed in range(40):
        sc = sample_layout(seed, "dev", family="shortcut")
        assert validate_layout(sc) == []
        wall = sc.obstacles[0]
        assert len(wall.slots) == 1
        s = wall.slots[0]
        lo, hi = wall.pose.pos[1] + s.y_min, wall.pose.pos[1] + s.y_max
        assert lo <= _crossing(sc) <= hi
        assert sc.scene_id == f"dev_shortcut_{seed}"


def test_shortcut_family_differs_and_is_deterministic():
    a = sample_layout(3, "dev", family="shortcut")
    assert a == sample_layout(3, "dev", family="shortcut")
    assert a != sample_layout(3, "dev")


def test_family_seed_ranges():
    sample_layout(EVAL_SEED_BASE, "eval")
    sample_layout(SHORTCUT_EVAL_SEED_BASE, "eval", family="shortcut")
    with pytest.raises(ValueError):
        sample_layout(EVAL_SEED_BASE + 5, "eval", family="shortcut")
    with pytest.raises(ValueError):
        sample_layout(EVAL_SEED_BASE, "dev", family="shortcut")
    with pytest.raises(ValueError):
        sample_layout(1, "dev", family="other")
    with pytest.raises(ValueError):
        sample_layout(1, "blocked", family="shortcut")
