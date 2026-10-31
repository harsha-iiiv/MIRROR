"""Robustness harness: frozen instructions, local corruptions, geometry check
on synthetic controls, and the evaluation plumbing with fake model clients."""
import json
from dataclasses import dataclass, field

import numpy as np
import pytest

from mirror.robustness import (CHOOSER, COLOUR_FREE_PROMPTS, INSTRUCTIONS, SAM_PROMPTS, by_id, check_geometry,
                               evaluate_robustness, instructions_sha256, make_counterfactuals, score_sam)
from mirror.robustness import controls as C
from mirror.robustness.counterfactuals import EditBudgetExceeded, network_edits_used
from mirror.robustness.frames import GTFrame, encode_png
from mirror.robustness.geometry import warp_similarity
from mirror.robustness.local import apply_local

FROZEN_SHA = "fd517bf09b4879f3687f3fe050f6925b3b06f5c809b41951942af22ce71b42f1"


def synthetic_frame(seed: int = 0) -> GTFrame:
    """A 384x256 frame: a shaded table, a wall, and three objects with known masks."""
    H, W = 256, 384
    yy, xx = np.mgrid[0:H, 0:W]
    rng = np.random.default_rng(seed)
    img = np.zeros((H, W, 3), np.float32)
    img[...] = np.array([200, 185, 160], np.float32) * (0.85 + 0.15 * xx[..., None] / W)
    img[:, 180:192] = (150, 148, 140)                                     # wall
    masks = {}
    cup = (np.abs(xx - 90) < 22) & (np.abs(yy - 150) < 26)
    img[cup] = (235, 235, 228)
    img[(np.abs(xx - 90) < 16) & (np.abs(yy - 144) < 18)] = (205, 205, 198)  # open top
    block = (np.abs(xx - 260) < 14) & (np.abs(yy - 80) < 14)
    img[block] = (50, 110, 215)
    marker = ((xx - 300) ** 2 / 9.0 ** 2 + (yy - 190) ** 2 / 30.0 ** 2) < 1
    img[marker] = (225, 90, 50)
    img += rng.normal(0, 1.5, img.shape)
    masks = {"cup": cup, "block": block, "marker": marker, "gripper": np.zeros_like(cup)}
    rgb = np.clip(img, 0, 255).astype(np.uint8)
    return GTFrame(f"synthetic{seed}", rgb, masks, {"camera": "synthetic", "moment": "start"})


def test_instruction_list_is_frozen():
    assert instructions_sha256() == FROZEN_SHA
    fams = {i.family for i in INSTRUCTIONS}
    assert fams == {"lighting", "background", "distractor", "material", "sensor"}
    assert all(i.kind == "local" for i in INSTRUCTIONS if i.family == "sensor")
    assert all(i.kind == "model" for i in INSTRUCTIONS if i.family != "sensor")
    assert set(SAM_PROMPTS) == {"cup", "block", "marker"}
    for i in INSTRUCTIONS:
        if i.kind == "model":
            assert "same size" in i.prompt() and "viewpoint" in i.prompt()


def test_local_corruptions_are_deterministic_and_keep_size():
    f = synthetic_frame()
    for ins in by_id(["noise_gauss", "blur_gauss", "blur_motion"]):
        a, b = apply_local(f.rgb, ins), apply_local(f.rgb, ins)
        assert a.shape == f.rgb.shape and a.dtype == np.uint8
        assert np.array_equal(a, b)
        assert not np.array_equal(a, f.rgb)


@pytest.mark.parametrize("name,fn", [
    ("dim", lambda f: C.photometric(f.rgb, (0.35, 0.35, 0.35))),
    ("warm", lambda f: C.photometric(f.rgb, (1.15, 0.9, 0.6), 10)),
    ("gamma", lambda f: C.photometric(f.rgb, gamma=1.8)),
    ("background", lambda f: C.recolor_background(f.rgb, f.masks)),
    ("noise", lambda f: apply_local(f.rgb, by_id(["noise_gauss"])[0])),
])
def test_appearance_changes_preserve_geometry(name, fn):
    f = synthetic_frame()
    rep, _ = check_geometry(f.rgb, fn(f), f.masks)
    assert rep.verdict == "preserved", (name, rep.to_dict())


@pytest.mark.parametrize("cls", ["cup", "block", "marker"])
def test_moved_and_removed_objects_are_flagged(cls):
    f = synthetic_frame()
    rep, _ = check_geometry(f.rgb, C.move_object(f.rgb, f.masks, cls, 0, 8), f.masks)
    assert rep.objects[cls].verdict in ("moved", "resized"), rep.objects[cls]
    assert rep.verdict == "changed"
    assert all(o.verdict in ("kept", "untested") for k, o in rep.objects.items() if k != cls)
    rep, _ = check_geometry(f.rgb, C.remove_object(f.rgb, f.masks, cls), f.masks)
    assert rep.objects[cls].verdict in ("missing", "moved"), rep.objects[cls]


def test_global_drift_is_registered_not_blamed_on_objects():
    f = synthetic_frame()
    drifted = warp_similarity(f.rgb, 1 / 1.02, -5, 2)
    rep, ed = check_geometry(f.rgb, drifted, f.masks)
    assert rep.verdict == "preserved"
    assert rep.registration["applied"] and (rep.registration["dx"], rep.registration["dy"]) == (5, -2)
    assert rep.registration["scale"] == 1.02


def test_upscaled_output_with_same_aspect_is_accepted_and_other_aspect_is_reframed():
    from PIL import Image
    f = synthetic_frame()
    big = np.asarray(Image.fromarray(f.rgb).resize((768, 512), Image.LANCZOS))
    rep, ed = check_geometry(f.rgb, big, f.masks)
    assert rep.verdict == "preserved" and ed.shape == f.rgb.shape and rep.size_out == (768, 512)
    wide = np.asarray(Image.fromarray(f.rgb).resize((480, 256), Image.LANCZOS))
    rep, _ = check_geometry(f.rgb, wide, f.masks)
    assert rep.verdict == "reframed"


# ------------------------------------------------------------- fake clients

@dataclass
class _Box:
    left: int
    top: int
    right: int
    bottom: int


@dataclass
class _Obj:
    box: _Box
    confidence: float
    mask: np.ndarray

    def full_mask(self, w, h):
        out = np.zeros((h, w), np.uint8)
        out[self.box.top:self.box.bottom, self.box.left:self.box.right] = self.mask
        return out


@dataclass
class _Frame:
    objects: list


@dataclass
class _Seg:
    frames: list
    from_cassette: bool = False
    latency_s: float = 0.5


@dataclass
class _Res:
    images: list
    latency_s: float = 3.0
    from_cassette: bool = False
    usage: dict = field(default_factory=dict)


class FakeClient:
    """Edits darken the frame (or refuse one prompt); SAM returns the ground
    truth mask of the class named by the prompt, found by colour."""
    mode = "replay"

    def __init__(self, frame, refuse=()):
        self.frame = frame
        self.refuse = refuse
        self.edits = 0
        self.sam_calls = 0

    def images_edit(self, images, prompt, **kw):
        self.edits += 1
        assert kw["extra"]["size"] == "384x256"
        if any(r in prompt for r in self.refuse):
            raise RuntimeError("400 content_policy_violation")
        return _Res([encode_png(C.photometric(self.frame.rgb, (0.6, 0.6, 0.6)))])

    PHRASES = {**{v: k for k, v in SAM_PROMPTS.items()}, **{v: k for k, v in COLOUR_FREE_PROMPTS.items()},
               "pale tub": "cup", "azure cube": "block", "rust stick": "marker"}

    def sam_segment(self, png, prompt, video=False):
        self.sam_calls += 1
        cls = self.PHRASES[prompt]
        m = self.frame.masks[cls]
        ys, xs = np.nonzero(m)
        b = _Box(int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        return _Seg([_Frame([_Obj(b, 0.9, m[b.top:b.bottom, b.left:b.right].astype(np.uint8))])])


def test_make_counterfactuals_reports_refusals_without_raising():
    f = synthetic_frame()
    cl = FakeClient(f, refuse=("coffee mug",))
    out = make_counterfactuals(f.png(), by_id(["light_dim", "distractor_mug", "blur_gauss"]), cl)
    assert [o.status for o in out] == ["ok", "refused", "ok"]
    assert out[2].kind == "local" and cl.edits == 2


def test_budget_guard_counts_network_edits(tmp_path):
    log = tmp_path / "usage.jsonl"
    log.write_text("\n".join(json.dumps({"model": "muse-image-1.0", "endpoint": "/images/edits"})
                             for _ in range(3)) + "\n" + json.dumps({"model": "sam-3.1", "endpoint": "/responses"}))
    assert network_edits_used(log) == 3
    f = synthetic_frame()
    cl = FakeClient(f)
    cl.mode = "record"
    with pytest.raises(EditBudgetExceeded):
        make_counterfactuals(f.png(), by_id(["light_dim"]), cl, budget=3, usage_log=log)


@dataclass
class _Choice:
    kind: str
    phrase: str
    ok: bool = True

    def model_dump(self):
        return {"kind": self.kind, "phrase": self.phrase, "ok": self.ok}


@dataclass
class _PromptSet:
    choices: list
    model: str = "fake-chooser"
    attempts: int = 1
    latency_s: float = 1.0
    frame_sha256: str = ""


def fake_chooser(png, model, client, kinds):
    names = {"cup": "pale tub", "block": "azure cube", "marker": "rust stick"}
    return _PromptSet([_Choice(k, names[k]) for k in kinds])


def test_evaluate_robustness_end_to_end_with_fakes(tmp_path):
    f = synthetic_frame()
    cl = FakeClient(f, refuse=("coffee mug",))
    ins = by_id(["light_dim", "distractor_mug", "noise_gauss"])
    rep = evaluate_robustness([f], ins, client=cl, out_dir=tmp_path, chooser=fake_chooser,
                              conditions={"A": CHOOSER, "B": COLOUR_FREE_PROMPTS})
    assert (tmp_path / "report.json").exists() and (tmp_path / "report.md").exists()
    assert rep["prompts_used"]["A"][f.frame_id]["prompts"] == {"cup": "pale tub", "block": "azure cube",
                                                               "marker": "rust stick"}
    # two conditions x (original + two ok edits) x three classes; the uncorrected images equal the
    # corrected ones here (no drift), so they are not sent again
    assert cl.sam_calls == 2 * 3 * 3
    keys = {(r["condition"], r["correction"]) for r in rep["rows"] if r["version"] != "original"}
    assert keys == {("A", "corrected"), ("A", "uncorrected"), ("B", "corrected")}
    orig = [r for r in rep["rows"] if r["version"] == "original"]
    assert all(r["iou"] == 1.0 and r["centroid_err_px"] == 0.0 for r in orig)
    s = rep["summary"]
    assert s["_edits"]["refused"] == 1
    assert s["A/corrected"]["lighting"]["geometry"] == {"kept": 3}
    assert s["B/corrected"]["sensor"]["geometry"] == {"preserved": 3}
    assert s["A/corrected"]["all_edits"]["kept_iou_delta_median"] == 0.0
    assert len(rep["corrections"]) == 2 and all("dx" in c for c in rep["corrections"])


def test_drift_shows_in_uncorrected_scores_only(tmp_path):
    f = synthetic_frame()

    class Drifting(FakeClient):
        def images_edit(self, images, prompt, **kw):
            self.edits += 1
            return _Res([encode_png(warp_similarity(C.photometric(self.frame.rgb, (0.7, 0.7, 0.7)), 1.0, -6, 0))])

    cl = Drifting(f)
    rep = evaluate_robustness([f], by_id(["light_dim"]), client=cl, chooser=fake_chooser)
    corr = rep["corrections"][0]
    assert (corr["dx"], corr["dy"]) == (6, 0)
    a = {(r["correction"], r["cls"]): r for r in rep["rows"] if r["condition"] == "A" and r["version"] == "light_dim"}
    # the fake SAM returns the ground-truth mask regardless of the image, so scores are equal; what differs is that
    # an uncorrected image was sent at all (one extra call per class)
    assert ("uncorrected", "cup") in a and cl.sam_calls == 2 * 3 + 3 + 3 + 3


def test_score_sam_uses_top_confidence_match_not_the_best_iou():
    f = synthetic_frame()
    m = f.masks["block"]
    ys, xs = np.nonzero(m)
    b = _Box(int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    good = _Obj(b, 0.5, m[b.top:b.bottom, b.left:b.right].astype(np.uint8))
    wrong = _Obj(_Box(0, 0, 10, 10), 0.9, np.ones((10, 10), np.uint8))
    s = score_sam(_Seg([_Frame([good, wrong])]), m)
    assert s["iou"] == 0.0 and s["iou_best"] == 1.0 and s["n_matches"] == 2
