"""Development versus held-out access.

Development cases live in this package (dev_cases.json) and are written by the
project team. Held-out cases are written by independent authors and are never
stored in the repository before the run: the custodian keeps the bundle, and
the freeze manifest commits to its sha256, case count and per-author counts.
``load_holdout`` refuses to open a bundle that is inside the repository, that
does not match the frozen commitment, or when no frozen manifest is given.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from .schema import CaseFile

PKG_DIR = Path(__file__).resolve().parent
REPO_ROOT = PKG_DIR.parents[2]
DEV_CASES = PKG_DIR / "dev_cases.json"
DEV_LANGUAGE_CASES = PKG_DIR / "dev_language_cases.json"

# Seed ranges reserved for protocol v3 (see design.json). Development cases may
# only use development layouts and the reference scenes; held-out cases get
# their layouts from the reserved evaluation ranges, assigned at freeze time.
DEV_SEEDS = (9600, 9699)
HOLDOUT_SEEDS = {"eval:mixed": (90000, 90199), "eval:shortcut": (91000, 91099), "blocked:mixed": (90000, 90049)}


class AccessError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_dev_cases(path: Optional[Path] = None) -> CaseFile:
    p = Path(path or DEV_CASES)
    cf = CaseFile.model_validate_json(p.read_text())
    if cf.split != "dev":
        raise AccessError(f"{p} is a {cf.split} case file; development runs read only split 'dev'")
    for c in cf.cases:
        s = c.scene
        if s.kind == "layout":
            if s.split != "blocked" and s.split != "dev":
                raise AccessError(f"development case {c.id} uses split {s.split!r}; only dev or blocked "
                                  f"development seeds are allowed")
            if not DEV_SEEDS[0] <= s.seed <= DEV_SEEDS[1]:
                raise AccessError(f"development case {c.id} uses seed {s.seed}, outside the reserved "
                                  f"development range {DEV_SEEDS}")
    return cf


def commitment(path: Path) -> dict[str, Any]:
    """What the custodian publishes before the freeze: hashes and counts only."""
    raw = Path(path).read_bytes()
    cf = CaseFile.model_validate_json(raw)
    if cf.split != "holdout":
        raise AccessError("a commitment is made only for a held-out bundle")
    per_author: dict[str, int] = {}
    per_cat: dict[str, int] = {}
    for c in cf.cases:
        per_author[c.author_id] = per_author.get(c.author_id, 0) + 1
        per_cat[c.category] = per_cat.get(c.category, 0) + 1
    ids = sorted(c.id for c in cf.cases)
    return {"sha256": hashlib.sha256(raw).hexdigest(), "n_cases": len(cf.cases),
            "n_turns": sum(len(c.turns) for c in cf.cases),
            "case_ids_sha256": hashlib.sha256(json.dumps(ids).encode()).hexdigest(),
            "per_author": dict(sorted(per_author.items())), "per_category": dict(sorted(per_cat.items()))}


def _inside_repo(path: Path) -> bool:
    try:
        Path(path).resolve().relative_to(REPO_ROOT)
        return True
    except ValueError:
        return False


def load_holdout(path: Path, manifest: Optional[dict]) -> CaseFile:
    if manifest is None or not manifest.get("frozen_at"):
        raise AccessError("held-out cases open only against a frozen manifest")
    if _inside_repo(path) and not manifest.get("run_finished"):
        raise AccessError("the held-out bundle must stay outside the repository until the run has finished")
    want = (manifest.get("holdout_commitment") or {}).get("sha256")
    got = sha256_file(path)
    if want != got:
        raise AccessError(f"held-out bundle sha256 {got[:16]} does not match the commitment {str(want)[:16]}")
    cf = CaseFile.model_validate_json(Path(path).read_text())
    if cf.split != "holdout":
        raise AccessError("bundle is not a held-out case file")
    return cf
