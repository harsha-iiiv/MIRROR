"""The append-only evaluation ledger (D4). No test here generates an
evaluation layout: the writer is exercised directly, and its placement in
sample_layout is checked structurally and with dev seeds."""
import ast
import json
import warnings
from pathlib import Path

import pytest

from mirror.scenes import sampling
from mirror.scenes import EVAL_SEED_BASE, ledger_path, sample_layout


def _lines(p: Path):
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def test_conftest_redirects_the_ledger_away_from_the_repository():
    assert ledger_path() != sampling.DEFAULT_LEDGER
    assert sampling.DEFAULT_LEDGER.parts[-3:] == ("results", "eval", "ledger.jsonl")


def test_writer_appends_one_line_per_access(tmp_path, monkeypatch):
    p = tmp_path / "sub" / "ledger.jsonl"
    monkeypatch.setenv(sampling.LEDGER_ENV, str(p))
    sampling._record_eval_access(30001, "eval", "mixed")
    sampling._record_eval_access(40002, "eval", "shortcut", caller="some.module")
    rows = _lines(p)
    assert [(r["seed"], r["split"], r["family"]) for r in rows] == [(30001, "eval", "mixed"),
                                                                    (40002, "eval", "shortcut")]
    assert set(rows[0]) == {"seed", "split", "family", "unix", "caller"}
    assert rows[0]["caller"] == __name__ or rows[0]["caller"].endswith("test_ledger")
    assert rows[1]["caller"] == "some.module"


def test_writer_failure_warns_and_does_not_raise(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv(sampling.LEDGER_ENV, str(blocker / "ledger.jsonl"))   # parent is a file
    with pytest.warns(RuntimeWarning, match="evaluation ledger"):
        sampling._record_eval_access(30000, "eval", "mixed")


def test_dev_and_low_blocked_seeds_are_not_recorded(tmp_path, monkeypatch):
    p = tmp_path / "ledger.jsonl"
    monkeypatch.setenv(sampling.LEDGER_ENV, str(p))
    sample_layout(3, "dev")
    sample_layout(3, "dev", family="shortcut")
    sample_layout(2, "blocked")
    assert _lines(p) == []


def test_sample_layout_records_eval_access_before_generating():
    src = Path(sampling.__file__).read_text()
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "sample_layout")
    calls = [(n.lineno, n.func.id) for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    record = [ln for ln, name in calls if name == "_record_eval_access"]
    draws = [ln for ln, name in calls if name in ("_draw", "_draw_shortcut")]
    assert record and draws and max(record) < min(draws)
    # Guard covers both evaluation splits from EVAL_SEED_BASE up, every family.
    guard = next(n for n in ast.walk(fn) if isinstance(n, ast.If)
                 and any(isinstance(c, ast.Call) and getattr(c.func, "id", "") == "_record_eval_access"
                         for c in ast.walk(n)))
    text = ast.unparse(guard.test)
    assert "'eval'" in text and "'blocked'" in text and "EVAL_SEED_BASE" in text and "family" not in text
