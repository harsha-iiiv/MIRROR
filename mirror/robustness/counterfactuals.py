"""Counterfactual frames: model edits (Muse Image) and local sensor corruptions.

Model edits go through the shared Meta client, so every call is recorded in a
cassette and replays offline. A live edit is refused once the usage log shows
``EDIT_BUDGET`` image edits made over the network.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from .frames import decode_png, encode_png
from .instructions import EditInstruction
from .local import apply_local

EDIT_MODEL = "muse-image-1.0"
EDIT_BUDGET = 150
OUTPUT_FORMAT = "webp"
# Edits ask for the source aspect ratio, a single refinement pass and no web,
# image search or code tools: the task is a photometric edit of a given frame.
EDIT_OPTIONS = {"reasoning_strength": "low",
                "tool_enablement": {"enable_web_search": False, "enable_image_search": False,
                                    "enable_shell": False}}


class EditBudgetExceeded(RuntimeError):
    pass


@dataclass
class Counterfactual:
    instruction_id: str
    family: str
    kind: str                         # model | local
    status: str                       # ok | refused | error
    image: Optional[np.ndarray] = field(default=None, repr=False)   # raw output, HxWx3 uint8
    latency_s: float = 0.0
    from_cassette: bool = False
    out_size: tuple[int, int] = (0, 0)
    error: str = ""
    usage: dict = field(default_factory=dict)
    raw: Optional[bytes] = field(default=None, repr=False)     # model output bytes, as returned

    def summary(self) -> dict:
        return {"instruction_id": self.instruction_id, "family": self.family, "kind": self.kind,
                "status": self.status, "latency_s": round(self.latency_s, 3),
                "from_cassette": self.from_cassette, "out_size": list(self.out_size),
                "error": self.error, "usage": self.usage}


def network_edits_used(usage_log: Path | str) -> int:
    """Image edits made over the network so far, from the shared usage log."""
    p = Path(usage_log)
    if not p.exists():
        return 0
    n = 0
    for line in p.read_text().splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("model") == EDIT_MODEL and "edit" in str(rec.get("endpoint", rec.get("call", ""))):
            n += 1
    return n


def _decode_any(data: bytes) -> np.ndarray:
    import io
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        return np.asarray(im.convert("RGB")).copy()


def make_counterfactuals(frame_png: bytes, instructions: Sequence[EditInstruction], client=None, *,
                         size: Optional[tuple[int, int]] = None, budget: int = EDIT_BUDGET,
                         usage_log: Optional[Path | str] = None) -> list[Counterfactual]:
    """Apply every instruction to one frame (PNG bytes).

    ``client`` is a ``mirror.meta.MetaClient`` (default: ``MetaClient.from_env()``);
    it is needed only for model instructions. Refusals and errors are returned
    as results with ``status`` set, never raised, except when the edit budget
    would be exceeded."""
    rgb = decode_png(frame_png)
    w, h = size or (rgb.shape[1], rgb.shape[0])
    out: list[Counterfactual] = []
    for ins in instructions:
        if ins.kind == "local":
            t0 = time.perf_counter()
            img = apply_local(rgb, ins)
            out.append(Counterfactual(ins.id, ins.family, "local", "ok", img, time.perf_counter() - t0,
                                      False, (img.shape[1], img.shape[0])))
            continue
        if client is None:
            from mirror.meta import MetaClient
            client = MetaClient.from_env()
        if client.mode in ("live", "record") and usage_log is not None:
            if network_edits_used(usage_log) >= budget:
                raise EditBudgetExceeded(f"{budget} network image edits already used (see {usage_log})")
        extra = dict(EDIT_OPTIONS, size=f"{w}x{h}")
        try:
            res = client.images_edit([frame_png], ins.prompt(), model=EDIT_MODEL,
                                     output_format=OUTPUT_FORMAT, extra=extra)
        except Exception as exc:  # noqa: BLE001 - reported per edit
            msg = f"{type(exc).__name__}: {exc}"
            status = "refused" if ("content_policy" in msg or "refus" in msg.lower()) else "error"
            out.append(Counterfactual(ins.id, ins.family, "model", status, None, 0.0, False, (0, 0), msg[:500]))
            continue
        if not res.images:
            out.append(Counterfactual(ins.id, ins.family, "model", "refused", None, res.latency_s,
                                      res.from_cassette, (0, 0), "no image returned", res.usage))
            continue
        blob = res.images[0]
        if isinstance(blob, tuple):
            blob = blob[-1]
        img = _decode_any(blob)
        out.append(Counterfactual(ins.id, ins.family, "model", "ok", img, res.latency_s, res.from_cassette,
                                  (img.shape[1], img.shape[0]), "", res.usage, raw=bytes(blob)))
    return out


def save_counterfactual(cf: Counterfactual, path: Path | str) -> Optional[Path]:
    """Save a model output exactly as returned (``path`` with the output
    format's suffix). Local corruptions are recomputed from the frame, not saved."""
    if cf.kind != "model" or cf.raw is None:
        return None
    p = Path(path).with_suffix("." + OUTPUT_FORMAT)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(cf.raw)
    return p
