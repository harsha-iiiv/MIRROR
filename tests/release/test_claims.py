"""Narrowed claims after the 2 November audit: the published statements are
recomputed from their evidence files, and retired phrasings cannot come back."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = {"README.md": ROOT / "README.md", "CLAIMS.md": ROOT / "docs" / "CLAIMS.md",
        "EVALUATION.md": ROOT / "docs" / "EVALUATION.md", "ARCHITECTURE.md": ROOT / "docs" / "ARCHITECTURE.md"}


def _t(name: str) -> str:
    lines = [l.lstrip("> ") if l.startswith(">") else l for l in DOCS[name].read_text().splitlines()]
    return " ".join(" ".join(lines).split())                  # whitespace- and blockquote-normalised


def _all() -> dict[str, str]:
    return {k: _t(k) for k in DOCS}


# ---------------------------------------------------------------- objective_v1

@pytest.fixture(scope="module")
def obj():
    return json.loads((ROOT / "results" / "eval_objective_v1" / "attempt_1" / "report.json").read_text())


def test_objective_v1_numbers_match_report(obj):
    assert abs(obj["calibrated_rotation_penalty"] - 0.2445) < 1e-9
    p, s, b = (obj["strata"][k] for k in ("primary", "shortcut", "blocked"))
    assert p["metrics"]["original@0.2445"]["cup_tilted_planned"] == 0 == s["metrics"]["original@0.2445"]["cup_tilted_planned"]
    assert (p["seeds"], s["seeds"]) == (80, 40)
    assert p["retained_benefit"]["retained"] == 0.0 == s["retained_benefit"]["retained"]
    assert p["identical_plans"]["original@0.2445 == corrected@0.2445"] == 80
    assert s["identical_plans"]["original@0.2445 == corrected@0.2445"] == 40
    assert (p["metrics"]["original@0.05"]["violation_free_success"], s["metrics"]["original@0.05"]["violation_free_success"]) == (49, 0)
    assert f'{p["vfs"]["corrected@0.05 vs original@0.05"]["mcnemar_p_exact"]:.1e}' == "9.3e-10"
    assert f'{s["vfs"]["corrected@0.05 vs original@0.05"]["mcnemar_p_exact"]:.1e}' == "1.8e-12"
    blocked = b["metrics"]
    assert blocked["original@0.2445"]["completion"] == 10 and blocked["original@0.2445"]["beads_lost"] == 38
    for c in ("corrected@0.2445", "corrected@0.05", "never_tilt@0.2445"):
        assert blocked[c]["planning_failures_proven"] == 10
    sweep = {"0.05": (31, 49, 40, 0), "0.10": (19, 61, 27, 13), "0.20": (2, 78, 5, 35)}
    for k, (pt, pv, st, sv) in sweep.items():
        m, n = p["metrics"][f"original@{k}"], s["metrics"][f"original@{k}"]
        assert (m["cup_tilted_planned"], m["violation_free_success"], n["cup_tilted_planned"],
                n["violation_free_success"]) == (pt, pv, st, sv)


def test_objective_v1_statements_present():
    docs = _all()
    for name in ("README.md", "CLAIMS.md", "EVALUATION.md"):
        t = docs[name]
        assert "0.2445" in t and "0.000" in t and "38 beads" in t, name
        assert "eval_objective_v1" in t, name
    readme = docs["README.md"]
    assert "9.3 × 10⁻¹⁰" in readme and "1.8 × 10⁻¹²" in readme
    assert "Read this table with the planner objective in mind" in readme
    for old in ("27 / 60", "33 / 60", "4 / 30", "3 / 30"):       # old numbers stay visible
        assert old in readme
    assert "not the effect of the correction alone" in readme
    assert "Do not describe 27/60 → 60/60 or 33/60 → 60/60 as the effect of the correction alone" in docs["CLAIMS.md"]


def test_headline_is_corrections_you_can_check():
    readme = _t("README.md")
    assert readme.startswith("# MIRROR **Corrections you can check.**")
    for part in ("Old runs are re-judged from execution evidence", '"not verified", never as a pass',
                 "The corrected rule is planned and run on new layouts",
                 "When no compliant route exists, MIRROR refuses with a proof",
                 "spills 38 beads at each of the six rotation prices",
                 "the cup rule changes nothing on 120 new layouts",
                 "measure the correction against a price of 0.05 m/rad"):
        assert part in readme, part
    for gone in ("HEADLINE: owner decision pending", "Teach a simulated robot what you mean",
                 "I corrected it once, and the correction changed what it did somewhere else",
                 "deliberately tilt-tolerant planner objective"):
        assert gone not in readme, gone


def test_refusal_claim_holds_at_every_price_tested(obj):
    blocked = obj["strata"]["blocked"]["metrics"]
    originals = [k for k in blocked if k.startswith("original@")]
    assert len(originals) == 6
    for k in originals:
        assert blocked[k]["completion"] == 10 and blocked[k]["beads_lost"] == 38, k
    correcteds = [k for k in blocked if k.startswith("corrected@")]
    assert len(correcteds) == 2
    for k in correcteds:
        assert blocked[k]["completion"] == 0 and blocked[k]["planning_failures_proven"] == 10, k


def test_single_marked_placeholder_for_ordering_and_clearance():
    readme = _t("README.md")
    assert readme.count("PENDING") == 1
    i = readme.index("PENDING")
    assert "ordering and clearance corrections at the calibrated price" in readme[i: i + 200]
    for name in ("CLAIMS.md", "EVALUATION.md", "ARCHITECTURE.md"):
        assert "PENDING" not in _t(name), name
    dev = " ".join((ROOT / "docs" / "submission" / "DEVPOST.md").read_text().split())
    assert dev.count("PENDING") == 1 and "ordering and clearance" in dev[dev.index("PENDING"): dev.index("PENDING") + 200]


# ---------------------------------------------------------------- video, robustness

def test_video_reanalysis_numbers_present_and_in_report():
    rep = (ROOT / "results" / "reanalysis_v1" / "video" / "report.md").read_text()
    for s in ("18608 (0.888)", "0.99 cm", "6.43 cm", "0.54 cm", "0.291", "71010"):
        assert s in rep
    docs = _all()
    for name in ("README.md", "CLAIMS.md", "EVALUATION.md"):
        t = docs[name]
        assert "88.8%" in t and "0.99 cm" in t and "6.4 cm" in t and "71010" in t and "0.29°" in t, name
        assert "0.54 cm" in t, name


def test_robustness_reanalysis_numbers_present():
    docs = _all()
    for name in ("README.md", "CLAIMS.md", "EVALUATION.md"):
        t = docs[name]
        assert "90 of 218" in t and "−0.039" in t and "0.445" in t and "0.372" in t, name


def test_conditional_numbers_never_stand_alone():
    for name, t in _all().items():
        for m in re.finditer(r"0\.72 cm", t):
            ctx = t[max(0, m.start() - 250): m.end() + 250]
            assert "5 cm" in ctx, f"{name}: 0.72 cm without its 5 cm gate: ...{ctx}..."
        for m in re.finditer(r"2\.0°", t):
            ctx = t[max(0, m.start() - 350): m.end() + 350]
            if "tilt" in ctx:
                assert re.search(r"0\.29°|0\.3°|constant-zero|constant zero|cannot measure|not measurable", ctx), \
                    f"{name}: tilt 2.0° without its caveat: ...{ctx}..."


# ---------------------------------------------------------------- chronology, reproducibility

def test_reproducibility_and_chronology_statements():
    docs = _all()
    for name in ("README.md", "CLAIMS.md", "EVALUATION.md"):
        t = docs[name]
        assert "60002" in t and "0 of 95" in t and "outcome" in t and "not bit-exact" in t.replace("not bit-exactly", "not bit-exact"), name
    for name in ("CLAIMS.md", "EVALUATION.md"):
        assert "chronology.md" in docs[name] and "not pre-registered" in docs[name], name
    readme = docs["README.md"]
    assert "macOS" in readme and "Apple silicon" in readme


RETIRED = [
    (r"PENDING", "audit placeholders"),
    (r"95 new layouts", "fixed pair on 90 + 5 layouts"),
    (r"(?<!not )pre-registered (correction|benchmark)", "correction benchmark is not pre-registered"),
    (r"correction(-compiler)? benchmark[^.|]{0,80}(?<!not )pre-registered", "correction benchmark is not pre-registered"),
    (r"written down and pushed before its first model call", "not true for the correction benchmark"),
    (r"pushed before (its|their) first (model )?call", "not true for the correction benchmark"),
    (r"frozen before running", "frozen_at is hand-entered"),
    (r"written before any run", "frozen_at is hand-entered"),
    (r"The simulator is deterministic", "reproduction is outcome-level"),
    (r"edits barely matter|barely moved what SAM found", "robustness denominators"),
    (r"median position error 0\.72 cm and cup tilt error 2\.0° \(met\)(?!\")", "conditional video numbers"),
    (r"tilt (error )?(of )?2\.0° (was )?met(?! as registered, but)", "tilt not measurable"),
]


def _published() -> dict[str, str]:
    """The four main docs plus every other tracked Markdown and web file outside results/."""
    out = _all()
    import subprocess
    p = subprocess.run(["git", "ls-files", "*.md", "web/*.js", "web/js/*.js", "web/*.html"], cwd=ROOT,
                       capture_output=True, text=True)
    for f in (p.stdout.split() if p.returncode == 0 else []):
        main = f == "README.md" or (f.startswith("docs/") and f.count("/") == 1 and Path(f).name in DOCS)
        if not f.startswith("results/") and not main:
            out[f] = " ".join((ROOT / f).read_text(errors="ignore").split())
    return out


@pytest.mark.parametrize("pattern,why", RETIRED)
def test_retired_phrasings_absent(pattern, why):
    if pattern == "PENDING":
        docs = {k: v for k, v in _all().items() if k != "README.md"}
    else:
        docs = _published()
    for name, t in docs.items():
        for m in re.finditer(pattern, t):
            ctx = t[max(0, m.start() - 60): m.end() + 60]
            # quoting a retired phrase to retire it is allowed
            allowed = re.search(r"Do not claim|Replace \"|replaces|superseded|kept unchanged|not cited", ctx)
            assert allowed, f"{name}: retired phrasing ({why}): ...{ctx}..."
