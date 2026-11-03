"""Registration objective_v1: design and freeze.

The design is fixed here and frozen (results/eval_objective_v1/freeze.json,
committed) before any of its layouts is generated. The calibrated rotation
price is read from the committed calibration output; nothing about the design
depends on outcomes of this registration.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

from .common import REPO_ROOT, SWEEP, lam_key, spec_pair
from .conditions import condition_names

NAME = "objective_v1"
OUT = REPO_ROOT / "results" / "eval_objective_v1"
CALIBRATION = OUT / "calibration" / "calibration.json"
LEDGER = OUT / "ledger.jsonl"

STRATA = [
    dict(name="primary", role="primary", split="eval", family="mixed", seeds=list(range(85_000, 85_080)),
         description="standard sampler, unbiased mix of layouts"),
    dict(name="shortcut", role="stratum", split="eval", family="shortcut", seeds=list(range(86_000, 86_040)),
         description="disclosed stratum: a wall slot near the straight cup-to-tray line (geometry only)"),
    dict(name="blocked", role="blocked", split="blocked", family="mixed", seeds=list(range(85_000, 85_010)),
         description="the wall spans the workspace; the slot is the only opening (trade-off stratum)"),
]
# Ranges used or reserved elsewhere; the freeze refuses any overlap.
RESERVED = [
    ("first registration", "eval", 30_000, 40_029), ("muse registration", "eval", 50_000, 60_029),
    ("retired", "eval", 10_000, 10_059), ("retired", "eval", 20_000, 20_029),
    ("video", "eval", 70_000, 71_019), ("robustness", "eval", 80_000, 80_099),
    ("protocol v3 holdout", "eval", 90_000, 91_099), ("protocol v3 holdout", "blocked", 90_000, 90_049),
    ("first/muse blocked", "blocked", 30_000, 30_004), ("muse blocked", "blocked", 50_000, 50_004),
]
CODE_FILES = ("mirror/evaluate/objective/__init__.py", "mirror/evaluate/objective/common.py",
              "mirror/evaluate/objective/conditions.py", "mirror/evaluate/objective/design.py",
              "mirror/evaluate/objective/run.py", "mirror/evaluate/objective/report.py",
              "mirror/evaluate/objective/calibrate.py", "mirror/evaluate/objective/sweep.py",
              "scripts/run_objective_eval.py", "docs/protocols/objective_v1.md")

HYPOTHESES = [
    "H1: the original specification planned with the calibrated rotation price carries the cup on a tilted "
    "route on fewer layouts than at 0.05 (primary and shortcut strata).",
    "H2: the corrected-minus-original gap in violation-free success is smaller with both conditions planned at "
    "the calibrated price than with both at 0.05.",
    "H3: the never-tilt baseline completes no blocked layout, and on the primary and shortcut strata it is "
    "never faster on average than the corrected specification at the same price.",
]

ANALYSIS = dict(
    unit="layout (seed); every condition on the identical scene; all seeds and attempts reported",
    conditions_note="original@<cal>, original@0.05/0.10/0.20/0.40/1.0, never_tilt@<cal>, corrected@<cal>, "
                    "corrected@0.05 (the last replicates the earlier registrations' setting on fresh layouts)",
    metrics=["completion (all objects in the tray, settled)", "success (checker status)",
             "violation-free success under the corrected rule (primary endpoint)",
             "cup tilted on its planned route; corrected-rule violations; max cup tilt in transport",
             "retained beads (total minus lost) and beads lost",
             "path length (m), rotation (rad) and path cost at the calibrated price, from the executed plan",
             "execution time (s, simulated) and planning failures (with proven infeasibility records)"],
    comparisons=[
        "primary: corrected@cal vs original@cal, violation-free success, exact two-sided McNemar, Wilson 95% "
        "intervals, paired bootstrap 95% CI (10,000 resamples over seeds), per stratum",
        "retained benefit = (VFS corrected@cal - VFS original@cal) / (VFS corrected@0.05 - VFS original@0.05), "
        "per stratum, with a seed-bootstrap 95% CI; undefined if the denominator is 0",
        "sweep: every metric for original@lam at each lam, per stratum",
        "time and cost: paired mean differences (bootstrap CI) of execution time and calibrated path cost, "
        "corrected@cal vs original@cal, never_tilt@cal vs corrected@cal, never_tilt@cal vs original@cal",
        "trade-offs: layouts where a condition fails to plan or execute while another completes; blocked "
        "stratum completion and beads lost per condition",
    ],
    decision_rules="none beyond reporting; no baseline is tuned; the calibrated price is fixed before freeze",
)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def overlaps(strata=STRATA, reserved=RESERVED) -> list[str]:
    out = []
    for st in strata:
        lo, hi = min(st["seeds"]), max(st["seeds"])
        for who, split, a, b in reserved:
            if split == st["split"] and not (hi < a or lo > b):
                out.append(f'{st["name"]} {lo}-{hi} overlaps {who} {a}-{b}')
    return out


def ledger_hits(seeds_by_split: dict, roots) -> list[str]:
    """Any ledger line (in any *ledger*.jsonl under roots) for one of our (split, seed)."""
    hits = []
    for root in roots:
        root = Path(root)
        if not root.exists():
            continue
        for p in root.rglob("*ledger*.jsonl"):
            for line in p.read_text(errors="ignore").splitlines():
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if int(e.get("seed", -1)) in seeds_by_split.get(e.get("split"), set()):
                    hits.append(f"{p.name}: {e}")
    return hits


def calibrated_price() -> tuple[float, dict]:
    cal = json.loads(CALIBRATION.read_text())
    return float(cal["objective_of_record"]["rotation_penalty_m_per_rad"]), cal


def _git(*args) -> str:
    try:
        return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def freeze(path: Path, *, extra_audit_roots=()) -> dict:
    """Write the freeze manifest. Generates no layout (sampling and network are
    refused and counted while the specifications are built)."""
    from mirror.evaluate.final import audit_seed_usage, io_guard
    from mirror.evaluate.paired import _spec_ref, code_hash, runtime_versions
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"{path} exists; a freeze manifest is written once")
    bad = overlaps()
    if bad:
        raise RuntimeError("seed ranges overlap reserved ranges: " + "; ".join(bad))
    by_split: dict[str, set] = {}
    for st in STRATA:
        by_split.setdefault(st["split"], set()).update(st["seeds"])
    roots = [REPO_ROOT / "results"] + [Path(p) for p in extra_audit_roots]
    hits = ledger_hits(by_split, roots)
    if hits:
        raise RuntimeError(f"{len(hits)} ledger entries already name this registration's seeds: {hits[:3]}")
    audit = audit_seed_usage(STRATA, roots)
    if audit["hits"]:
        raise RuntimeError(f"seeds already appear in output files: {audit['hits'][:3]}")
    lam_cal, cal = calibrated_price()
    with io_guard() as io:
        a, b, prov = spec_pair()
    if io.network or io.layouts:
        raise RuntimeError("the freeze touched the network or sampled a layout")
    digest, n = code_hash()
    now = time.time()
    m = dict(
        registration=NAME, created_at=_dt.datetime.fromtimestamp(now, _dt.timezone.utc).isoformat(),
        created_unix=now, git_head_at_freeze=_git("rev-parse", "HEAD"),
        strata=STRATA, seed_count=sum(len(s["seeds"]) for s in STRATA),
        conditions=condition_names(lam_cal, SWEEP), calibrated_rotation_penalty=lam_cal,
        calibrated_key=lam_key(lam_cal), sweep=list(SWEEP),
        calibration=dict(file="results/eval_objective_v1/calibration/calibration.json", sha256=_sha(CALIBRATION),
                         objective_of_record=cal["objective_of_record"], dev_layouts=cal["dev_layouts"]),
        spec_a=_spec_ref(a), spec_b=_spec_ref(b),
        spec_provenance={k: prov.get(k) for k in ("source", "interpreter", "demo_sha256", "correction_text",
                                                 "enforced_original", "enforced_corrected", "offline_agrees")},
        controller="mirror.sim.SimEnv(scene, seed=0).execute(plan, max_sim_time=120)",
        checker="mirror.evaluate.trial.execute_trial + mirror.evaluate.paired._record (corrected rules as extra rules)",
        code_sha256=digest, code_files=n, code_scope="mirror/**/*.py (no exclusions)",
        objective_files={f: _sha(REPO_ROOT / f) for f in CODE_FILES},
        versions=runtime_versions(), ledger="results/eval_objective_v1/ledger.jsonl",
        ledger_exists_at_freeze=LEDGER.exists(),
        hypotheses=HYPOTHESES, analysis_plan=ANALYSIS, reserved_ranges_checked=RESERVED,
        seed_audit=dict(roots=audit["roots"], text_files=audit["text_files"], hits=[],
                        result="no seed of this registration found"),
        freeze_io=dict(network_connections=io.network, layouts_sampled=io.layouts),
        run_rules=["run only from a clean checkout of the commit that adds this file",
                   "every attempt is kept in its own folder and reported",
                   "MIRROR_EVAL_LEDGER is set to this registration's ledger by the runner"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(m, indent=1))
    return m


def verify(manifest: dict) -> list[str]:
    """Problems that forbid a run with the current checkout."""
    from mirror.evaluate.paired import _spec_ref, code_hash, runtime_versions
    probs = []
    digest, _ = code_hash()
    if digest != manifest["code_sha256"]:
        probs.append("code under mirror/ differs from the freeze")
    for f, h in manifest["objective_files"].items():
        p = REPO_ROOT / f
        if not p.exists() or _sha(p) != h:
            probs.append(f"{f} differs from the freeze")
    if _sha(CALIBRATION) != manifest["calibration"]["sha256"]:
        probs.append("calibration file differs from the freeze")
    a, b, _ = spec_pair()
    if _spec_ref(a) != manifest["spec_a"] or _spec_ref(b) != manifest["spec_b"]:
        probs.append("specification pair differs from the freeze")
    cur = runtime_versions()
    for k, v in manifest["versions"].items():
        if cur.get(k) != v:
            probs.append(f"{k} {v} -> {cur.get(k)}")
    return probs
