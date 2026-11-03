"""Scene resolution with access guards."""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from mirror.contracts import SceneState
from mirror.scenes import EVAL_SEED_BASE, reference_scene, sample_layout

from .access import AccessError
from .schema import SceneRef


@contextmanager
def _ledger(path: Optional[Path]):
    old = os.environ.get("MIRROR_EVAL_LEDGER")
    if path is not None:
        os.environ["MIRROR_EVAL_LEDGER"] = str(path)
    try:
        yield
    finally:
        if path is not None:
            if old is None:
                os.environ.pop("MIRROR_EVAL_LEDGER", None)
            else:
                os.environ["MIRROR_EVAL_LEDGER"] = old


def resolve(ref: SceneRef, *, allow_eval: bool = False, ledger: Optional[Path] = None) -> SceneState:
    """The SceneState for ``ref``. Evaluation-split layouts are refused unless
    ``allow_eval`` (held-out runs only), and then require a protocol ledger so
    their generation is recorded outside the earlier registrations' ledgers."""
    if ref.kind == "reference":
        return reference_scene(ref.name)
    evaluation = ref.split == "eval" or (ref.split == "blocked" and ref.seed >= EVAL_SEED_BASE)
    if evaluation and not allow_eval:
        raise AccessError(f"scene {ref.key()} is an evaluation layout; development runs cannot generate it")
    if evaluation and ledger is None:
        raise AccessError("evaluation layouts need the protocol's own ledger path")
    with _ledger(ledger if evaluation else None):
        return sample_layout(ref.seed, ref.split, ref.family)
