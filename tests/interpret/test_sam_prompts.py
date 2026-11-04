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
    # The recording is keyed on the exact PNG bytes sent. PNG encoding depends on the
    # zlib build (zlib vs zlib-ng give identical pixels, different bytes), so replay
    # sends the stored annotated image; the next test checks its pixels are what
    # _outlined draws on this platform.
    ps = choose_prompts(FX / "ref_top_frame0.png", "muse-spark-1.3", _replay(), verify=True,
                        verify_image=(FX / "ref_top_frame0_outlined.png").read_bytes())
    wall = next(c for c in ps.choices if c.kind == "wall")
    assert wall.verified is False and not wall.ok  # a correct box the model rejected on the reference render


def test_outlined_image_pixels_match_the_stored_image():
    import io

    import numpy as np
    from PIL import Image

    from mirror.interpret.sam_prompts import _outlined, first_frame
    ps = choose_prompts(FX / "ref_top_frame0.png", "muse-spark-1.3", _replay())
    cand = [c for c in ps.choices if c.ok and c.box]
    drawn = Image.open(io.BytesIO(_outlined(first_frame(FX / "ref_top_frame0.png"), cand))).convert("RGB")
    stored = Image.open(FX / "ref_top_frame0_outlined.png").convert("RGB")
    assert np.array_equal(np.asarray(drawn), np.asarray(stored))


def test_side_view_unique_but_wrong_is_possible():
    ps = choose_prompts(FX / "ref_side_frame0.png", "muse-spark-1.3", _replay())
    tray = next(c for c in ps.choices if c.kind == "tray")
    # the tray is hidden behind the wall in this view; the phrase uniquely matched the table
    assert tray.ok and tray.phrase == "tan board"


def test_prompts_keeps_first_accepted_phrase_per_kind():
    """Recorded case (robustness rehearsal, dev3_top_carry, top view while carrying the
    cup): the model named three 'blocks'; 'blue cube' (the block) and 'black block'
    (the gripper) each matched exactly one object. The first, in the model's order,
    must win."""
    ps = choose_prompts(FX / "dev3_top_carry.png", "muse-spark-1.3", _replay(), kinds=("cup", "block", "marker"))
    accepted = [c.phrase for c in ps.choices if c.kind == "block" and c.ok]
    assert accepted == ["blue cube", "black block"]
    assert ps.prompts() == {"block": "blue cube"}


def test_prompts_prefers_first_attempt_over_retry():
    from mirror.interpret.sam_prompts import PromptChoice, PromptSet
    ps = PromptSet(model="m", frame_sha256="x", attempts=2, choices=[
        PromptChoice(kind="cup", phrase="white box", sam_count=1, ok=True, attempt=2),
        PromptChoice(kind="cup", phrase="open box", sam_count=1, ok=True, attempt=1),
        PromptChoice(kind="block", phrase="cube", sam_count=2, ok=False, attempt=1)])
    assert ps.prompts() == {"cup": "open box"}
