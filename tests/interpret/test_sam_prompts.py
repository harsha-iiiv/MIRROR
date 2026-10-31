"""Muse decides, SAM segments: recorded choose_prompts on the reference render."""
from pathlib import Path

import httpx
import pytest

pytest.importorskip("meta_sam_parser")

from mirror.interpret.sam_prompts import choose_prompts  # noqa: E402
from mirror.meta import MetaClient  # noqa: E402

FX = Path(__file__).resolve().parents[2] / "tests" / "meta" / "fixtures"


def _replay():
    return MetaClient(mode="replay", transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(AssertionError("net"))))


def test_top_view_prompts_each_match_one_object():
    ps = choose_prompts(FX / "ref_top_frame0.png", "muse-spark-1.3", _replay())
    assert ps.attempts == 2 and {c.kind for c in ps.choices} == {"cup", "block", "marker", "tray", "wall", "table"}
    assert all(c.ok and c.sam_count == 1 and c.verified is None for c in ps.choices)
    assert ps.prompts()["block"] == "blue cube"


def test_verification_is_recorded_and_can_reject():
    ps = choose_prompts(FX / "ref_top_frame0.png", "muse-spark-1.3", _replay(), verify=True)
    wall = next(c for c in ps.choices if c.kind == "wall")
    assert wall.verified is False and not wall.ok  # a correct box the model rejected (see status note)


def test_side_view_unique_but_wrong_is_possible():
    ps = choose_prompts(FX / "ref_side_frame0.png", "muse-spark-1.3", _replay())
    tray = next(c for c in ps.choices if c.kind == "tray")
    # the tray is hidden behind the wall in this view; the phrase uniquely matched the table
    assert tray.ok and tray.phrase == "tan board"
