"""Release checks: published numbers match the evidence files, historical
artifacts still load, the lock covers the declared dependencies, and tracked
files carry no secrets."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _vfs(report: dict, stratum: str) -> tuple[int, int, int]:
    pairs = report["strata"][stratum]["paired"]["outcome_pairs"]
    return sum(p["a_vfs"] for p in pairs), sum(p["b_vfs"] for p in pairs), len(pairs)


@pytest.mark.parametrize("folder,primary,shortcut", [("eval", (27, 60, 60), (4, 30, 30)),
                                                      ("eval_muse", (33, 60, 60), (3, 30, 30))])
def test_documented_headline_numbers_match_reports(folder, primary, shortcut):
    rep = json.loads((ROOT / "results" / folder / "report.json").read_text())
    assert _vfs(rep, "primary") == primary
    assert _vfs(rep, "shortcut") == shortcut
    assert len(rep["strata"]["blocked"]["trials"]) == 5
    readme = (ROOT / "README.md").read_text()
    for a, b, n in (primary, shortcut):
        assert f"{a} / {n}" in readme and f"**{b} / {n}**" in readme


def test_scope_statements_present_in_published_docs():
    readme = (ROOT / "README.md").read_text()
    claims = (ROOT / "docs" / "CLAIMS.md").read_text()
    ev = (ROOT / "docs" / "EVALUATION.md").read_text()
    assert "not 90 new demonstrations" in readme
    assert "not blind" in claims.lower() and "post-hoc model swap" in claims
    assert "## Scope of each result" in ev and "## Evidence provenance" in ev
    assert "95 new layouts" not in readme
    for doc in (readme, claims, ev):
        assert "deterministic regression check" in doc
    assert "outcome-level reproduction" in claims.lower() or "reproduction of outcomes" in readme
    assert "## Audit findings and narrowed claims" in ev
    assert ev.count("PENDING") >= 4                       # pending items stay visibly marked


def test_historical_rollouts_and_manifests_still_parse():
    from mirror.contracts import Rollout
    n = 0
    for folder in ("eval", "eval_muse"):
        for p in sorted((ROOT / "results" / folder).glob("*/rollouts/*.json")):
            Rollout.model_validate_json(p.read_text())
            n += 1
        json.loads((ROOT / "results" / folder / "freeze.json").read_text())
    assert n > 0


def test_lock_pins_every_declared_dependency():
    lock = {re.split(r"==", l)[0].lower().replace("_", "-"): l for l in
            (ROOT / "requirements-lock.txt").read_text().splitlines() if l and not l.startswith("#")}
    py = (ROOT / "pyproject.toml").read_text()
    names = re.findall(r'"([A-Za-z0-9_.-]+)\s*[<>=!~]', py)
    assert names
    for n in names:
        if n in ("setuptools",):
            continue
        assert n.lower().replace("_", "-") in lock, f"{n} missing from requirements-lock.txt"
    assert all("==" in v for v in lock.values())


@pytest.mark.slow
def test_tracked_files_have_no_high_severity_findings():
    sys.path.insert(0, str(ROOT / "scripts"))
    import release_scan
    rep = release_scan.scan(ROOT, tracked=True)
    assert rep["counts"]["high"] == 0, [f for f in rep["findings"] if f["severity"] == "high"][:5]


def test_scanner_masks_values(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import release_scan
    fake = "gh" + "p_" + "A" * 36
    (tmp_path / "x.txt").write_text(f"token: {fake}\n")
    rep = release_scan.scan(tmp_path, tracked=False)
    assert rep["counts"]["high"] >= 1
    assert all(fake not in f["preview"] for f in rep["findings"])
