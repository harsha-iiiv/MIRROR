
"""Second registration ("muse"): refusal on prior seed use, no layouts or
network during the freeze, and the first registration left untouched."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from mirror.evaluate.final import (MUSE_STRATA, REGISTRATIONS, REPO_ROOT, audit_seed_usage,
                                   freeze_registration, io_guard)
from mirror.evaluate.paired import FreezeError
from mirror.evaluate.specs import reference_specs


def _builder():
    a, b = reference_specs()
    return a, b, {"spec_source": "replay", "requested_model": "test", "cassette_dir": "x"}


def _overrides(tmp_path, **kw):
    d = dict(manifest=str(tmp_path / "freeze.json"), ledger=str(tmp_path / "ledger.jsonl"),
             out=str(tmp_path), audit_roots=[tmp_path / "results"], compare_to=None)
    d.update(kw)
    return d


def test_seed_ranges_are_the_registered_ones():
    s = {st["name"]: st for st in REGISTRATIONS["muse"]["strata"]}
    assert s["primary"]["seeds"] == list(range(50000, 50060)) and s["primary"]["family"] == "mixed"
    assert s["shortcut"]["seeds"] == list(range(60000, 60030)) and s["shortcut"]["family"] == "shortcut"
    assert s["blocked"]["seeds"] == list(range(50000, 50005)) and s["blocked"]["split"] == "blocked"
    assert REGISTRATIONS["muse"]["ledger"] != REGISTRATIONS["first"]["ledger"]


def test_refuses_if_its_seeds_are_already_in_its_ledger(tmp_path):
    (tmp_path / "results").mkdir()
    led = tmp_path / "ledger.jsonl"
    led.write_text(json.dumps({"seed": 50003, "split": "eval", "family": "mixed", "unix": 1.0,
                               "caller": "x"}) + "\n")
    with pytest.raises(FreezeError, match="already in its ledger"):
        freeze_registration("muse", overrides=_overrides(tmp_path), spec_builder=_builder)
    assert not (tmp_path / "freeze.json").exists()


def test_refuses_if_a_seed_appears_in_any_report(tmp_path):
    (tmp_path / "results" / "x").mkdir(parents=True)
    (tmp_path / "results" / "x" / "report.md").write_text("| 60012 | success |\n")
    with pytest.raises(FreezeError, match="already appear"):
        freeze_registration("muse", overrides=_overrides(tmp_path), spec_builder=_builder)
    assert audit_seed_usage(MUSE_STRATA, [tmp_path / "results"])["hits"][0]["seeds"] == [60012]
    # decimals and longer numbers are not seed tokens
    (tmp_path / "results" / "x" / "report.md").write_text("t=50003.5 id 500031 x1.50004\n")
    assert audit_seed_usage(MUSE_STRATA, [tmp_path / "results"])["hits"] == []


def test_freeze_records_plan_and_samples_nothing(tmp_path):
    (tmp_path / "results").mkdir()
    m = freeze_registration("muse", overrides=_overrides(tmp_path), spec_builder=_builder,
                            config={"workers": 2, "max_sim_time": 120.0})
    assert m.config["registration"] == "muse" and m.config["freeze_io"]["layouts_sampled"] == 0
    assert m.config["freeze_io"]["network_connections"] == 0
    assert "never loses beads" in m.config["hypothesis"] and m.config["analysis_plan"]["identical_to"]
    assert [st["seeds"][0] for st in m.strata] == [50000, 60000, 50000]
    assert not (tmp_path / "ledger.jsonl").exists()


def test_io_guard_counts_and_refuses():
    import socket
    import mirror.scenes as scenes
    with io_guard() as io:
        with pytest.raises(RuntimeError):
            scenes.sample_layout(1, "dev")
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", 9))
    assert io.layouts == 1 and io.network == 1
    assert scenes.sample_layout(1, "dev") is not None      # restored


def test_first_registration_files_unchanged():
    if shutil.which("git") is None or not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    tag = subprocess.run(["git", "rev-parse", "-q", "--verify", "eval-final"], cwd=REPO_ROOT,
                         capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    if tag.returncode:
        pytest.skip("tag eval-final not present")
    diff = subprocess.run(["git", "diff", "--quiet", "eval-final", "--", "results/eval"], cwd=REPO_ROOT,
                          timeout=60, stdin=subprocess.DEVNULL)
    assert diff.returncode == 0, "results/eval differs from the eval-final tag"
    extra = subprocess.run(["git", "ls-files", "--others", "--", "results/eval"], cwd=REPO_ROOT,
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    assert extra.stdout.strip() == "", f"untracked files under results/eval: {extra.stdout[:200]}"


EXCLUDE = ("sim/render.py", "studio/**", "cli.py")


def test_code_hash_exclusions(tmp_path):
    from mirror.evaluate.paired import code_hash
    (tmp_path / "sim").mkdir()
    (tmp_path / "studio" / "ui").mkdir(parents=True)
    for rel in ("sim/env.py", "sim/render.py", "studio/ui/app.py", "cli.py", "plan.py"):
        (tmp_path / rel).write_text("x = 1\n")
    full, n_full = code_hash(tmp_path)
    part, n_part = code_hash(tmp_path, exclude=EXCLUDE)
    assert (n_full, n_part) == (5, 2)
    for rel in ("sim/render.py", "studio/ui/app.py", "cli.py"):
        (tmp_path / rel).write_text("x = 2\n")
    assert code_hash(tmp_path, exclude=EXCLUDE)[0] == part and code_hash(tmp_path)[0] != full
    (tmp_path / "sim/env.py").write_text("x = 2\n")
    assert code_hash(tmp_path, exclude=EXCLUDE)[0] != part
    assert REGISTRATIONS["muse"]["code_exclude"] == EXCLUDE
    assert "code_exclude" not in REGISTRATIONS["first"]


def test_hashed_modules_import_excluded_code_only_in_keyframe_rendering():
    """Static check: outside the excluded paths, the only import of excluded
    code is the lazy one inside interpret.keyframes.render_keyframes (optional
    prompt images; not used by evaluation_specs or run_final)."""
    import ast
    pkg = REPO_ROOT / "mirror"
    found = []
    for p in sorted(pkg.rglob("*.py")):
        rel = p.relative_to(pkg).as_posix()
        if rel in ("sim/render.py", "cli.py") or rel.startswith("studio/"):
            continue
        tree = ast.parse(p.read_text())
        parents = {}
        for node in ast.walk(tree):
            for ch in ast.iter_child_nodes(node):
                parents[ch] = node
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if node.level and rel.startswith("sim/") and node.level == 1:
                    mod = "mirror.sim." + mod if mod else "mirror.sim"
                names = [mod] + [f"{mod}.{a.name}" for a in node.names]
            if any(n in ("mirror.sim.render", "mirror.cli") or n.startswith("mirror.studio") for n in names):
                fn = node
                while fn in parents and not isinstance(fn, ast.FunctionDef):
                    fn = parents[fn]
                found.append((rel, getattr(fn, "name", "<module>")))
    # Allowed: modules that are not on the muse evaluation path (the
    # subprocess test below checks that the path never loads mirror.sim.render).
    allowed_prefixes = ("interpret/keyframes.py", "perception/", "robustness/")
    assert found and all(rel.startswith(allowed_prefixes) for rel, _ in found), found
    assert ("interpret/keyframes.py", "render_keyframes") in found


def test_evaluation_path_does_not_load_excluded_modules():
    code = (
        "import sys\n"
        "import mirror.evaluate, mirror.evaluate.final, mirror.evaluate.trial, mirror.plan, mirror.sim, mirror.scenes\n"
        "from mirror.evaluate.specs import evaluation_specs\n"
        "from mirror.evaluate.final import REPO_ROOT\n"
        "try:\n"
        "    evaluation_specs(source='replay', model='muse-spark-1.3', cassette_dir=REPO_ROOT / 'data/cassettes_muse')\n"
        "except Exception as exc:\n"
        "    print('specs-skipped', type(exc).__name__)\n"
        "bad = [m for m in sys.modules if m == 'mirror.sim.render' or m == 'mirror.cli' or m.startswith(('mirror.studio', 'mirror.perception', 'mirror.robustness'))]\n"
        "print('LOADED', bad)\n")
    r = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True,
                       timeout=120, stdin=subprocess.DEVNULL)
    assert "LOADED []" in r.stdout, r.stdout + r.stderr
