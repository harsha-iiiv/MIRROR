"""Freeze manifest for protocol v3.

Records, before any held-out case is opened: the design (sha256), the source
tree, the prompt revision and template hash, model ids and client modes, the
checker id, planner configuration, the reserved seed ranges with each held-out
case's assigned layout, the held-out commitment, the spend cap and the
reservation per turn. ``verify`` recomputes every hash and refuses on any
difference. A frozen manifest is never edited: changes after a failure make a
new protocol version with a new manifest.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Optional

from .access import HOLDOUT_SEEDS, PKG_DIR, REPO_ROOT

DESIGN = PKG_DIR / "design.json"


def tree_hash(root: Path, suffixes=(".py", ".json")) -> tuple[str, int]:
    h = hashlib.sha256()
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in suffixes
                   and "__pycache__" not in p.parts)
    for p in files:
        h.update(p.relative_to(root).as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest(), len(files)


def _sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def assign_seeds(commit: dict, families: dict[str, int]) -> dict[str, list[int]]:
    """Layout seeds for held-out cases, in order from the reserved ranges:
    ``families`` maps 'eval:mixed' | 'eval:shortcut' | 'blocked:mixed' to a count."""
    out = {}
    for fam, n in families.items():
        lo, hi = HOLDOUT_SEEDS[fam]
        if n > hi - lo + 1:
            raise ValueError(f"{fam}: {n} layouts requested, range has {hi - lo + 1}")
        out[fam] = list(range(lo, lo + n))
    return out


def build(*, holdout_commitment: Optional[dict], models: dict[str, Any], budget: dict[str, float],
          git_commit: str = "", layout_seeds: Optional[dict] = None, notes: str = "") -> dict[str, Any]:
    from mirror.evaluate.checker import CHECKER_ID
    from mirror.interpret.prompts import PROMPT_REVISION
    code, n = tree_hash(REPO_ROOT / "mirror")
    return {
        "protocol": "mirror-authoring-v3",
        "design_sha256": _sha(DESIGN),
        "code_sha256": code, "code_files": n, "git_commit": git_commit,
        "prompt_revision": PROMPT_REVISION,
        "prompt_template_sha256": _sha(REPO_ROOT / "mirror" / "interpret" / "prompts.py"),
        "checker": CHECKER_ID,
        "planner_config_sha256": _sha(REPO_ROOT / "mirror" / "plan" / "config.py"),
        "models": models, "budget": budget,
        "holdout_commitment": holdout_commitment, "layout_seeds": layout_seeds or {},
        "created_unix": time.time(), "notes": notes,
    }


def freeze(manifest: dict[str, Any], path: Path) -> dict[str, Any]:
    if Path(path).exists():
        raise FileExistsError(f"{path} exists; a frozen manifest is never overwritten")
    m = dict(manifest, frozen_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    body = json.dumps(m, indent=2, sort_keys=True) + "\n"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(body)
    return m


def verify(manifest: dict[str, Any]) -> list[str]:
    """Differences between the manifest and the current tree (empty = ok)."""
    from mirror.evaluate.checker import CHECKER_ID
    from mirror.interpret.prompts import PROMPT_REVISION
    errs = []
    code, _ = tree_hash(REPO_ROOT / "mirror")
    checks = [("design_sha256", _sha(DESIGN)), ("code_sha256", code), ("prompt_revision", PROMPT_REVISION),
              ("prompt_template_sha256", _sha(REPO_ROOT / "mirror" / "interpret" / "prompts.py")),
              ("checker", CHECKER_ID),
              ("planner_config_sha256", _sha(REPO_ROOT / "mirror" / "plan" / "config.py"))]
    for k, now in checks:
        if manifest.get(k) != now:
            errs.append(f"{k}: frozen {str(manifest.get(k))[:16]} != now {str(now)[:16]}")
    return errs
