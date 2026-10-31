"""SAM 3.1 segmentation output.

Wire format (output_text lane, one line per frame, documented by Meta and
confirmed by recorded responses):

    <0f>0<|box;x1=289;y1=159;x2=300;y2=173;w=480;h=270;c=0.906|><|mask;x=0;y=0;c=0.906;data=15,12,!...|>

``<Nf>`` is the zero-based frame index (frames with no object emit no line),
then comma-separated records: object id, one box (x2/y2 inclusive, w/h = source
size, c only with include_confidence), one mask (raster H,W covering the box,
``!`` one_bit or ``~`` lossless, base85 payload). Records and masks are decoded
with the official ``meta_sam_parser`` package; this module turns them into
plain typed objects keyed by frame and object id.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np


@dataclass(frozen=True)
class SamBox:
    left: int    # pixels, inclusive
    top: int
    right: int   # pixels, exclusive (x2 + 1)
    bottom: int  # exclusive (y2 + 1)

    @property
    def center(self) -> tuple[float, float]:
        return ((self.left + self.right) / 2.0, (self.top + self.bottom) / 2.0)

    @property
    def area(self) -> int:
        return max(0, self.right - self.left) * max(0, self.bottom - self.top)


@dataclass
class SamObject:
    object_id: int
    frame: int
    box: SamBox
    confidence: Optional[float]
    mask: Optional[np.ndarray] = field(default=None, repr=False)  # uint8 0/1, shape (box h, box w)

    def full_mask(self, width: int, height: int) -> np.ndarray:
        """The mask placed in a (height, width) frame."""
        out = np.zeros((height, width), dtype=np.uint8)
        if self.mask is None:
            return out
        h, w = self.mask.shape
        y0, x0 = self.box.top, self.box.left
        y1, x1 = min(height, y0 + h), min(width, x0 + w)
        out[y0:y1, x0:x1] = self.mask[: y1 - y0, : x1 - x0]
        return out

    @property
    def mask_centroid(self) -> Optional[tuple[float, float]]:
        if self.mask is None or not self.mask.any():
            return None
        ys, xs = np.nonzero(self.mask)
        return (float(xs.mean()) + self.box.left, float(ys.mean()) + self.box.top)


@dataclass
class SamFrame:
    index: int
    objects: list[SamObject]


@dataclass
class SamResult:
    prompt: str
    media: str                     # "image" or "video"
    width: Optional[int]
    height: Optional[int]
    frames: list[SamFrame]         # sorted by frame index; frames with no objects are absent
    raw_text: str
    model: str = "sam-3.1"
    latency_s: float = 0.0
    from_cassette: bool = False
    diagnostics: list[str] = field(default_factory=list)

    def track(self, object_id: int) -> dict[int, SamObject]:
        """frame index -> object, for one object id."""
        return {f.index: o for f in self.frames for o in f.objects if o.object_id == object_id}

    @property
    def object_ids(self) -> list[int]:
        return sorted({o.object_id for f in self.frames for o in f.objects})

    @property
    def empty(self) -> bool:
        return not self.frames


def _events(text: str) -> list[dict]:
    """A minimal Responses event sequence carrying ``text`` in one output_text lane."""
    item = "msg_replay"
    evs: list[dict] = [
        {"type": "response.created", "sequence_number": 0, "response": {"id": "resp_replay", "status": "in_progress", "output": []}},
        {"type": "response.output_item.added", "sequence_number": 1, "output_index": 0,
         "item": {"id": item, "type": "message", "role": "assistant", "status": "in_progress",
                  "content": [{"type": "output_text", "text": "", "annotations": []}]}},
        {"type": "response.content_part.added", "sequence_number": 2, "item_id": item, "output_index": 0,
         "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}},
    ]
    seq = 3
    for line in text.splitlines(keepends=True):
        evs.append({"type": "response.output_text.delta", "sequence_number": seq, "item_id": item,
                    "output_index": 0, "content_index": 0, "delta": line, "logprobs": []})
        seq += 1
    part = {"type": "output_text", "text": text, "annotations": []}
    evs += [
        {"type": "response.output_text.done", "sequence_number": seq, "item_id": item, "output_index": 0,
         "content_index": 0, "text": text, "logprobs": []},
        {"type": "response.content_part.done", "sequence_number": seq + 1, "item_id": item, "output_index": 0,
         "content_index": 0, "part": part},
        {"type": "response.output_item.done", "sequence_number": seq + 2, "output_index": 0,
         "item": {"id": item, "type": "message", "role": "assistant", "status": "completed", "content": [part]}},
        {"type": "response.completed", "sequence_number": seq + 3,
         "response": {"id": "resp_replay", "status": "completed",
                      "output": [{"id": item, "type": "message", "role": "assistant", "status": "completed",
                                  "content": [part]}]}},
    ]
    return evs


def _parse_boxes_only(text: str, prompt: str, media: str) -> SamResult:
    """Boxes and ids without mask decoding (fast; regex over the documented grammar)."""
    import re
    rec = re.compile(r"(?:^|,)(\d+)<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)(?:;c=([0-9.]+))?\|>"
                     r"<\|mask;[^|]*\|>")
    frames = []
    width = height = None
    for line in text.splitlines():
        m = re.match(r"<(\d+)f>", line)
        if not m:
            continue
        fi = int(m.group(1))
        objs = []
        for r in rec.finditer(line[m.end():]):
            oid, x1, y1, x2, y2, w, h = (int(v) for v in r.groups()[:7])
            width, height = w, h
            objs.append(SamObject(oid, fi, SamBox(x1, y1, x2 + 1, y2 + 1),
                                  float(r.group(8)) if r.group(8) else None))
        frames.append(SamFrame(fi, sorted(objs, key=lambda o: o.object_id)))
    frames.sort(key=lambda f: f.index)
    return SamResult(prompt=prompt, media=media, width=width, height=height, frames=frames, raw_text=text)


def parse_sam_output(text: str, *, prompt: str = "", media: str = "image", decode_masks: bool = True) -> SamResult:
    """Parse SAM 3.1 output text into a SamResult. With ``decode_masks`` False
    only ids, boxes and confidences are read (no mask decoding, much faster)."""
    if not decode_masks:
        return _parse_boxes_only(text, prompt, media)
    try:
        import meta_sam_parser as msp
    except ImportError as e:  # pragma: no cover
        raise ImportError("SAM output decoding needs the 'meta' extra: pip install -e '.[meta]'") from e

    fmt = msp.video_segmentation_format() if media == "video" else msp.image_segmentation_format()

    async def src():
        for e in _events(text):
            yield e

    async def run():
        fr = msp.parse_responses_stream(src(), fmt).final_result
        return await (fr() if callable(fr) else fr)

    result = asyncio.run(run()) if text else None
    frames: dict[int, dict[int, SamObject]] = {}
    width = height = None
    diags: list[str] = []
    if result is not None:
        diags = [f"{d.code}: {d.message}" for d in result.diagnostics]
        for rec in result.records:
            if rec.kind not in ("box", "mask"):
                continue
            fi = rec.frame.frame_index if rec.frame is not None else 0
            oid = int(rec.object_id)
            if rec.kind == "box":
                frames.setdefault(fi, {})[oid] = SamObject(
                    object_id=oid, frame=fi, box=SamBox(rec.left, rec.top, rec.right, rec.bottom),
                    confidence=rec.confidence)
            else:
                obj = frames.setdefault(fi, {}).get(oid)
                if obj is None:
                    b = rec.bounds
                    obj = frames[fi][oid] = SamObject(oid, fi, SamBox(b.left, b.top, b.right, b.bottom),
                                                      rec.confidence)
                if decode_masks:
                    raster = msp.decode_mask_to_raster(rec.mask)
                    obj.mask = np.frombuffer(bytes(raster), dtype=np.uint8).reshape(rec.mask.height, rec.mask.width)
    import re
    m = re.search(r";w=(\d+);h=(\d+)", text)
    if m:
        width, height = int(m.group(1)), int(m.group(2))
    out = [SamFrame(i, [frames[i][k] for k in sorted(frames[i])]) for i in sorted(frames)]
    return SamResult(prompt=prompt, media=media, width=width, height=height, frames=out, raw_text=text,
                     diagnostics=diags)
