"""Muse decides, SAM segments: choose SAM prompts for the task objects.

``choose_prompts(media, model)`` shows the first frame of a demonstration to a
Muse model, which names each task-relevant thing (cup, block, marker, tray,
wall, table) as a short noun phrase SAM 3.1 matches well: colour plus shape,
no instructions. Code then validates every phrase with SAM on the same frame:
a phrase is accepted only if it yields exactly one object. Phrases that match
zero or several objects are sent back to the model once, with the counts, for
a second attempt. Unique is not the same as right (a phrase can uniquely match
the wrong thing), so a final call shows the model the SAM boxes, numbered, and
asks whether each is the intended kind. Everything is recorded; nothing is
hand-written per video.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field

from mirror.interpret.llm import strict_schema

Kind = Literal["cup", "block", "marker", "tray", "wall", "table", "other"]
DEFAULT_KINDS = ("cup", "block", "marker", "tray", "wall", "table")


class NamedObject(BaseModel):
    kind: Kind
    phrase: str = Field(description="2-4 word noun phrase: colour + shape, e.g. 'blue cube'")
    why: str


class PromptDraft(BaseModel):
    objects: list[NamedObject]


class PromptChoice(BaseModel):
    kind: str
    phrase: str
    sam_count: int                  # objects SAM found for the phrase on the first frame
    ok: bool                        # exactly one match (and, if verified, the model agreed it is that kind)
    verified: Optional[bool] = None  # model's check of the SAM box; None if not run
    verify_reason: str = ""
    box: Optional[list[int]] = None  # left, top, right, bottom of the single match
    confidence: Optional[float] = None
    attempt: int = 1


class BoxCheck(BaseModel):
    index: int
    correct: bool = Field(description="true if the outlined region is mainly the named kind")
    reason: str


class BoxChecks(BaseModel):
    checks: list[BoxCheck]


class PromptSet(BaseModel):
    model: str
    choices: list[PromptChoice]
    frame_sha256: str
    attempts: int
    latency_s: float = 0.0

    def prompts(self) -> dict[str, str]:
        """kind -> accepted phrase. If several phrases were accepted for one kind
        (each matched exactly one object, possibly the wrong one), the first wins:
        first attempt before the retry, then the order the model gave them."""
        out: dict[str, str] = {}
        for c in sorted(self.choices, key=lambda c: c.attempt):  # stable: keeps the model's order
            if c.ok and c.kind not in out:
                out[c.kind] = c.phrase
        return out


SYSTEM = """\
You name objects in an image so that a segmentation model can find them.
The segmentation model matches short concrete noun phrases: colour plus
shape works best ("blue cube", "white square box", "red cylinder"). Do not
write instructions or questions, do not use "and", commas, negation or
positions ("on the left"). Each phrase must pick out exactly one thing in the
image."""


def first_frame(media: Union[str, Path, bytes]) -> bytes:
    """PNG bytes of the first frame of a video, or the image itself."""
    if isinstance(media, (bytes, bytearray)):
        return bytes(media)
    p = Path(media)
    if p.suffix.lower() not in (".mp4", ".mov", ".m4v"):
        return p.read_bytes()
    out = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(p), "-frames:v", "1", "-f", "image2pipe",
                          "-vcodec", "png", "pipe:1"], capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
    if out.returncode != 0 or not out.stdout:
        raise RuntimeError("could not read the first frame: " + out.stderr.decode(errors="replace")[:200])
    return out.stdout


def _validate(client, frame: bytes, obj: NamedObject, attempt: int) -> PromptChoice:
    seg = client.sam_segment(frame, obj.phrase, video=False, decode_masks=False)
    objs = seg.frames[0].objects if seg.frames else []
    one = objs[0] if len(objs) == 1 else None
    return PromptChoice(kind=obj.kind, phrase=obj.phrase, sam_count=len(objs), ok=len(objs) == 1,
                        box=[one.box.left, one.box.top, one.box.right, one.box.bottom] if one else None,
                        confidence=one.confidence if one else None, attempt=attempt)


def _outlined(frame: bytes, choices: list[PromptChoice]) -> bytes:
    import io
    from PIL import Image, ImageDraw
    img = Image.open(io.BytesIO(frame)).convert("RGB")
    d = ImageDraw.Draw(img)
    for i, c in enumerate(choices, start=1):
        if c.box:
            x0, y0, x1, y1 = c.box
            d.rectangle([x0, y0, x1 - 1, y1 - 1], outline=(255, 0, 255), width=3)
            d.rectangle([x0, max(0, y0 - 18), x0 + 18, max(0, y0 - 18) + 18], fill=(255, 0, 255))
            d.text((x0 + 5, max(0, y0 - 18) + 3), str(i), fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def verify_boxes(client, model: str, frame: bytes, choices: list[PromptChoice],
                 outlined: Optional[bytes] = None) -> list[PromptChoice]:
    """One model call: is each numbered SAM box mainly the kind it was meant to be?

    ``outlined`` is the annotated PNG to send instead of drawing it here. PNG
    bytes depend on the zlib build (identical pixels encode differently, e.g.
    zlib vs zlib-ng), and recordings are keyed on the exact bytes sent, so
    replay on another platform can pass the stored image (tests do)."""
    from mirror.meta import image_part, text_part
    cand = [c for c in choices if c.ok and c.box]
    if not cand:
        return choices
    listing = "; ".join(f"{i}: {c.kind}" for i, c in enumerate(cand, start=1))
    res = client.responses(model, [text_part("Each numbered magenta rectangle should outline one thing: " + listing
                                             + ". For each number, say whether the outlined region is mainly that "
                                             "thing."), image_part(outlined or _outlined(frame, cand), "image/png")],
                           schema=strict_schema(BoxChecks), schema_name="box_checks", name="verify_prompts")
    by = {k.index: k for k in BoxChecks.model_validate(res.json()).checks}
    out = []
    for c in choices:
        if c in cand:
            k = by.get(cand.index(c) + 1)
            c = c.model_copy(update={"verified": bool(k and k.correct), "verify_reason": k.reason if k else "no answer",
                                     "ok": bool(k and k.correct)})
        out.append(c)
    return out


def choose_prompts(media: Union[str, Path, bytes], model: str = "muse-spark-1.3", client=None, *,
                   kinds: tuple[str, ...] = DEFAULT_KINDS, retry: bool = True, verify: bool = False,
                   verify_image: Optional[bytes] = None) -> PromptSet:
    """Ask ``model`` for SAM phrases for ``kinds``, validate each with SAM
    (exactly one match on the first frame) and, with ``verify``, ask the model
    once whether each SAM box really is that kind (exactly one match is
    necessary but not sufficient: a phrase can uniquely match the wrong thing).
    ``verify`` is off by default: on the reference render it caught no wrong box
    and rejected one correct box (round 7 status note)."""
    import hashlib
    from mirror.meta import MetaClient, image_part, text_part
    client = client or MetaClient.from_env()
    frame = first_frame(media)
    ask = ("Name each of these things visible in the image with one short noun phrase: "
           + ", ".join(kinds) + ". Skip a kind that is not visible.")
    res = client.responses(model, [text_part(ask), image_part(frame, "image/png")], schema=strict_schema(PromptDraft),
                           schema_name="sam_prompts", instructions=SYSTEM, name="choose_prompts")
    latency = res.latency_s
    draft = PromptDraft.model_validate(res.json())
    choices = [_validate(client, frame, o, 1) for o in draft.objects if o.kind in kinds]
    attempts = 1
    bad = [c for c in choices if not c.ok]
    if retry and bad:
        attempts = 2
        feedback = "; ".join(f"'{c.phrase}' ({c.kind}) matched {c.sam_count} objects" for c in bad)
        ask2 = (ask + " A first attempt gave: " + "; ".join(f"{c.kind}: '{c.phrase}'" for c in choices)
                + ". These did not match exactly one object: " + feedback
                + ". Give new phrases for those kinds only.")
        res2 = client.responses(model, [text_part(ask2), image_part(frame, "image/png")],
                                schema=strict_schema(PromptDraft), schema_name="sam_prompts", instructions=SYSTEM,
                                name="choose_prompts_retry")
        latency += res2.latency_s
        redo = {c.kind for c in bad}
        new = [_validate(client, frame, o, 2) for o in PromptDraft.model_validate(res2.json()).objects
               if o.kind in redo]
        by_kind = {c.kind: c for c in new}
        choices = [by_kind.get(c.kind, c) if not c.ok and by_kind.get(c.kind) and
                   (by_kind[c.kind].ok or not c.ok) else c for c in choices]
    if verify:
        choices = verify_boxes(client, model, frame, choices, outlined=verify_image)
    return PromptSet(model=res.model, choices=choices, frame_sha256=hashlib.sha256(frame).hexdigest(),
                     attempts=attempts, latency_s=latency)
