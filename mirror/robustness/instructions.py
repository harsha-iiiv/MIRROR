"""Frozen edit instructions for the perception robustness study.

Every instruction asks for a change that should leave the geometry of the
kit objects untouched: the same objects, in the same places, at the same size,
seen from the same camera. Edits that nevertheless move, resize, remove or add
a kit object are detected by ``geometry.check_geometry`` and reported, never
dropped.

Two kinds:
  * ``model``: applied by an image-editing model (counted against the edit budget);
  * ``local``: applied by deterministic local code (camera noise and blur), so
    the sensor-noise family does not depend on a model.

The list is frozen: ``INSTRUCTIONS_SHA256`` is checked by the tests, so any
change to wording, order or parameters is visible.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

KEEP = ("Keep every object exactly where it is, with the same size, shape and outline, "
        "and keep the camera viewpoint and framing unchanged.")


@dataclass(frozen=True)
class EditInstruction:
    id: str
    family: str                 # lighting | background | distractor | material | sensor
    kind: str                   # "model" or "local"
    text: str                   # prompt sent to the model, or a description of the local operation
    params: dict = field(default_factory=dict)

    def prompt(self) -> str:
        """Full prompt sent to the image model (instruction plus the geometry clause)."""
        return f"{self.text} {KEEP}"


INSTRUCTIONS: tuple[EditInstruction, ...] = (
    EditInstruction("light_dim", "lighting", "model",
                    "Make the scene dimly lit, as if the room lights were turned down to about a third."),
    EditInstruction("light_warm", "lighting", "model",
                    "Change the lighting to warm, orange-tinted evening light from a tungsten lamp."),
    EditInstruction("light_harsh", "lighting", "model",
                    "Light the scene with one hard spotlight from the upper left so that objects cast "
                    "sharp, dark shadows."),
    EditInstruction("bg_wood", "background", "model",
                    "Replace the table surface and the background with a dark walnut wood tabletop with "
                    "visible grain."),
    EditInstruction("bg_lab_bench", "background", "model",
                    "Make the surroundings look like a cluttered laboratory bench, with tools, cables and "
                    "papers in the background, but leave the area around the objects clear."),
    EditInstruction("distractor_mug", "distractor", "model",
                    "Add a second, unrelated coffee mug standing on the table in the far corner of the "
                    "image, away from all other objects."),
    EditInstruction("distractor_paper", "distractor", "model",
                    "Add a few loose sheets of white paper lying flat on the table in empty areas, not "
                    "touching or covering any object."),
    EditInstruction("material_glossy_cup", "material", "model",
                    "Make the white cup look glossy, like glazed ceramic with bright specular highlights."),
    EditInstruction("noise_gauss", "sensor", "local",
                    "Additive Gaussian pixel noise (sigma 12 on a 0-255 scale), fixed seed.",
                    {"op": "gaussian_noise", "sigma": 12.0, "seed": 1}),
    EditInstruction("blur_gauss", "sensor", "local",
                    "Gaussian defocus blur (sigma 2.0 px).",
                    {"op": "gaussian_blur", "sigma": 2.0}),
    EditInstruction("blur_motion", "sensor", "local",
                    "Horizontal motion blur over 9 px.",
                    {"op": "motion_blur", "length": 9}),
)

# SAM 3.1 text prompts, one per object class measured. Frozen with the list.
# The simulated cup is an open-top white box: on a development frame SAM 3.1
# found nothing for "cup", "white cup" or "container", and found it for
# "white box" (chosen) and "open box".
SAM_PROMPTS: dict[str, str] = {
    "cup": "white box",
    "block": "blue block",
    "marker": "orange marker",
}


def frozen_payload() -> dict:
    return {"instructions": [asdict(i) for i in INSTRUCTIONS], "keep_clause": KEEP,
            "sam_prompts": SAM_PROMPTS}


def instructions_sha256() -> str:
    blob = json.dumps(frozen_payload(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def by_id(ids=None) -> list[EditInstruction]:
    if ids is None:
        return list(INSTRUCTIONS)
    table = {i.id: i for i in INSTRUCTIONS}
    missing = [x for x in ids if x not in table]
    if missing:
        raise KeyError(f"unknown instruction ids: {missing}")
    return [table[x] for x in ids]


FAMILIES = tuple(dict.fromkeys(i.family for i in INSTRUCTIONS))
