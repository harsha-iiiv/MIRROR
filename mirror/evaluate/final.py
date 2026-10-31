"""Pre-registered evaluation: all strata from one freeze manifest.

Design (fixed before any freeze; seed ranges re-registered after the first
review because 10000-10007 had been run during development):
  * primary:  60 eval seeds 30000-30059, standard sampler, family "mixed";
  * shortcut: 30 eval seeds 40000-40029, family "shortcut" (a wall slot near
    the straight line between cup and tray; defined by geometry at sampling
    time, never by outcomes); reported in full beside the primary result;
  * blocked:  5 seeds 30000-30004 of the blocked split, for the infeasibility check.
Seeds 10000-10059 and 20000-20029 are retired and refused.

An append-only ledger (results/eval/ledger.jsonl, written by the layout
sampler for every evaluation-split layout) must contain no entry for a frozen
seed before the freeze; the final report embeds the ledger entries.
Both specifications come from replaying recorded model responses on the
pinned reference demonstration, checked against the offline interpreter.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Callable, Optional

from .models import FinalReport, FreezeManifest
from .paired import (FreezeError, blocked_case, corrected_rules, freeze_strata, make_layout_fn,
                     paired_eval, verify_manifest)

PRIMARY_SEEDS = list(range(30_000, 30_060))
SHORTCUT_SEEDS = list(range(40_000, 40_030))
BLOCKED_SEEDS = list(range(30_000, 30_005))
RETIRED_RANGES = [(10_000, 10_059), (20_000, 20_029)]

REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = REPO_ROOT / "results" / "eval" / "ledger.jsonl"
LEDGER_SPLITS = ("eval", "blocked")

D3_STRATA = [
    dict(name="primary", role="primary", split="eval", family="mixed", seeds=PRIMARY_SEEDS,
         description="standard sampler, unbiased mix of layouts"),
    dict(name="shortcut", role="stratum", split="eval", family="shortcut", seeds=SHORTCUT_SEEDS,
         description="disclosed stratum: a wall slot near the straight cup-to-tray line, "
                     "defined by geometry at sampling time"),
    dict(name="blocked", role="blocked", split="blocked", family="mixed", seeds=BLOCKED_SEEDS,
         description="the wall spans the workspace; the slot is the only opening"),
]


def rehearsal_strata(n_mixed: int = 6, n_shortcut: int = 6, n_blocked: int = 2) -> list[dict]:
    """Same structure on development seeds, for verifying the pipeline."""
    return [
        dict(name="primary", role="primary", split="dev", family="mixed",
             seeds=list(range(0, n_mixed)), description="rehearsal on dev seeds"),
        dict(name="shortcut", role="stratum", split="dev", family="shortcut",
             seeds=list(range(500, 500 + n_shortcut)), description="rehearsal on dev seeds"),
        dict(name="blocked", role="blocked", split="blocked", family="mixed",
             seeds=list(range(0, n_blocked)), description="rehearsal on dev seeds"),
    ]


# ------------------------------------------------------------------ ledger ---

def _seed_floor() -> int:
    try:
        from mirror.scenes import EVAL_SEED_BASE
        return int(EVAL_SEED_BASE)
    except ImportError:
        return 10_000


def check_not_retired(strata: list[dict]) -> None:
    bad = sorted({s for st in strata if st["split"] in LEDGER_SPLITS for s in st["seeds"]
                  if any(lo <= s <= hi for lo, hi in RETIRED_RANGES)})
    if bad:
        raise FreezeError(f"seeds {bad[:6]}{'...' if len(bad) > 6 else ''} are retired "
                          f"(observed during development); use the re-registered ranges")


def read_ledger(path: Path | str = LEDGER_PATH) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for i, line in enumerate(path.read_text().splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            out.append({"line": i + 1, "unparsable": line[:200]})
            continue
        e.setdefault("unix", e.get("time", e.get("t")))
        out.append(e)
    return out


def ledger_hits(entries: list[dict], strata: list[dict]) -> list[dict]:
    """Ledger entries that name a frozen (seed, split, family)."""
    keys = {(int(s), st["split"], st.get("family", "mixed")) for st in strata
            if st["split"] in LEDGER_SPLITS for s in st["seeds"]}
    hits = []
    for e in entries:
        if "seed" not in e:
            if "unparsable" in e:
                hits.append(e)       # cannot rule it out
            continue
        k = (int(e["seed"]), e.get("split"), e.get("family", "mixed"))
        if k in keys:
            hits.append(e)
    return hits


def _ledger_summary(path: Path) -> dict:
    entries = read_ledger(path)
    return dict(path="results/eval/ledger.jsonl" if Path(path) == LEDGER_PATH else Path(path).name,
                exists=Path(path).exists(), entries=len(entries),
                sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).exists() else None)


def default_specs(source: str = "replay"):
    from .specs import evaluation_specs
    return evaluation_specs(source=source)


def freeze_final(path: Path | str, *, strata: Optional[list[dict]] = None, kind: str = "final",
                 spec_builder: Callable = default_specs, config: Optional[dict] = None,
                 layout_fn=None, ledger_path: Path | str = LEDGER_PATH,
                 spec_source: str = "replay") -> FreezeManifest:
    """Write the multi-stratum freeze manifest. Refuses retired seeds, strata
    whose family cannot be generated, and any frozen seed that already has a
    ledger entry (its layout was generated before the freeze)."""
    strata = strata or D3_STRATA
    if spec_source != "replay" and kind != "rehearsal":
        raise FreezeError("the final evaluation uses replayed specifications; other sources are for rehearsals")
    check_not_retired(strata)
    for st in strata:                      # refuse to freeze a design that cannot run
        make_layout_fn(st["family"], layout_fn)
    hits = ledger_hits(read_ledger(ledger_path), strata)
    if hits:
        raise FreezeError(f"{len(hits)} ledger entr{'y' if len(hits) == 1 else 'ies'} for frozen seeds "
                          f"already exist (first: {hits[0]}); these layouts were generated before the freeze")
    a, b, prov = spec_builder(spec_source) if spec_builder is default_specs else spec_builder()
    return freeze_strata(a, b, strata, path, kind=kind, config=config, spec_provenance=prov,
                         ledger_at_freeze=_ledger_summary(Path(ledger_path)))


ROLLOUT_RULE = ("Rollout JSON is committed for every seed where either condition carried an object "
                "covered by the corrected rule above that rule's tilt tolerance (15 degrees for the "
                "cup) or did not end in success or touched an obstacle; for every seed of the blocked "
                "stratum; and for every 5th seed of each stratum in seed order (indices 0, 5, 10, ...) "
                "regardless of outcome. All reports and metrics cover every seed.")


def select_rollouts(report, role: str) -> list[int]:
    tol = min([float(c["params"].get("max_tilt_deg", 15.0)) for c in report.corrected_rules
               if c["type"] == "upright"] or [15.0])
    seeds = []
    for i, t in enumerate(report.trials):
        flagged = any(r.corrected_max_tilt_deg > tol or r.report.status != "success" or r.obstacle_contacts
                      for r in (t.a, t.b))
        if role == "blocked" or i % 5 == 0 or flagged:
            seeds.append(t.seed)
    return seeds


def run_final(manifest_path: Path | str, *, workers: int = 2, save_rollouts: bool = False,
              out_dir: Optional[Path | str] = None, spec_builder: Callable = default_specs,
              planner=None, env_factory=None, layout_fn=None, n_boot: int = 10_000,
              max_sim_time: float = 120.0, log_root: Optional[Path] = None,
              ledger_path: Path | str = LEDGER_PATH,
              rollout_stage: Optional[Path | str] = None) -> FinalReport:
    started = time.time()
    m = FreezeManifest.model_validate_json(Path(manifest_path).read_text())
    if not m.strata:
        raise FreezeError("manifest has no strata; write it with freeze_final()")
    if m.kind != "rehearsal":
        check_not_retired(m.strata)
    early = [e for e in ledger_hits(read_ledger(ledger_path), m.strata)
             if not isinstance(e.get("unix"), (int, float)) or e["unix"] < m.created_unix]
    if early:
        raise FreezeError(f"ledger shows {len(early)} frozen layout(s) generated before the freeze "
                          f"(first: {early[0]})")
    for st in m.strata:                    # fail before running anything
        make_layout_fn(st["family"], layout_fn)
    if spec_builder is default_specs:
        a, b, prov = default_specs(m.spec_provenance.get("spec_source", "replay"))
    else:
        a, b, prov = spec_builder()
    drift_ok = m.kind == "rehearsal"
    for st in m.strata:
        verify_manifest(manifest_path, a, b, st["seeds"], st["split"], st["family"],
                        allow_code_drift=drift_ok)
    reports, roles = {}, {}
    if save_rollouts and out_dir and rollout_stage is None:
        rollout_stage = Path(out_dir).parent / "tmp" / Path(out_dir).name / "rollouts_all"
    for st in m.strata:
        rdir = Path(rollout_stage) / st["name"] if (save_rollouts and out_dir) else None
        ldir = Path(log_root) / st["name"] if log_root else None
        reports[st["name"]] = paired_eval(
            a, b, st["seeds"], split=st["split"], family=st["family"], stratum=st["name"],
            manifest_path=manifest_path, require_manifest=True, workers=workers,
            layout_fn=layout_fn, planner=planner, env_factory=env_factory, n_boot=n_boot,
            max_sim_time=max_sim_time, rollout_dir=rdir, log_dir=ldir, allow_code_drift=drift_ok)
        roles[st["name"]] = st.get("role", "stratum")
    ref = blocked_case(a, b, planner=planner, env_factory=env_factory,
                       rules=corrected_rules(a, b), max_sim_time=max_sim_time)
    notes = ["Every attempt in every stratum is reported; nothing is filtered by outcome."]
    # rollout selection: copy the pre-declared subset next to the reports
    selection: dict = {}
    if save_rollouts and out_dir:
        selection = {"rule": ROLLOUT_RULE, "selected": {}, "staged_all_in": "results/tmp (not versioned)"}
        for name, rep in reports.items():
            chosen = set(select_rollouts(rep, roles[name]))
            selection["selected"][name] = sorted(chosen)
            dst = Path(out_dir) / name / "rollouts"
            dst.mkdir(parents=True, exist_ok=True)
            for t in rep.trials:
                for rec in (t.a, t.b):
                    src = Path(rollout_stage) / name / f"seed{t.seed}_{rec.condition}.json"
                    if t.seed in chosen and src.exists():
                        shutil.copyfile(src, dst / src.name)
                        rec.rollout_path = f"rollouts/{src.name}"
                    else:
                        rec.rollout_path = None
    # ledger: every entry for a frozen seed, all of which must postdate the freeze
    hits = ledger_hits(read_ledger(ledger_path), m.strata)
    expected = [st for st in m.strata if st["split"] in LEDGER_SPLITS
                and any(s >= _seed_floor() for s in st["seeds"])]
    ledger = dict(_ledger_summary(Path(ledger_path)), frozen_seed_entries=hits,
                  entries_before_freeze=[e for e in hits if (e.get("unix") or 0) < m.created_unix],
                  writer_active=bool(hits) or not expected)
    if expected and not hits:
        notes.append("The layout ledger recorded no entries for the frozen evaluation seeds during this "
                     "run; the ledger writer is not active, so the ledger check is vacuous for this run.")
    if m.kind == "rehearsal":
        notes.insert(0, "REHEARSAL on development seeds; not the evaluation result.")
    return FinalReport(
        title="MIRROR pre-registered evaluation" + (" (rehearsal)" if m.kind == "rehearsal" else ""),
        manifest=m, spec_provenance=prov or m.spec_provenance, strata=reports, roles=roles,
        blocked_reference=ref, ledger=ledger, rollout_selection=selection,
        started_at=_dt.datetime.fromtimestamp(started, _dt.timezone.utc).isoformat(),
        finished_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        wall_time_s=round(time.time() - started, 1), notes=notes)
