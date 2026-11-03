"""F-A1 audit reproduction.

1. Plan-only rotation-cost sweep over the 90 frozen layouts of the Muse
   registration (primary 50000-50059, shortcut 60000-60029): for each layout
   and rotation price, does the ORIGINAL specification's plan carry the cup
   on a tilted route? Layouts are regenerated from their seeds and checked
   against the scene hashes stored in results/eval_muse/report.json. The
   layout sampler records these accesses in a separate ledger
   (MIRROR_EVAL_LEDGER must point at a new file; the registration's own
   ledger is never written). Nothing is executed.
2. Recount, from the saved reports of both registrations, the paired trials
   where only the corrected specification was violation-free, and in how
   many of them the corrected run finished faster (execution_time_s).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .common import (CUP, REPO_ROOT, SWEEP, config_with, lam_key, scene_sha, spec_pair, tilted,
                     transport_note)

AUDIT_CLAIM = {"plan_only_tilted_of_90": {"0.05": 54, "0.10": 32, "0.20": 2, "0.40": 0, "1.0": 0},
               "discordant_pairs": 113, "corrected_faster": 110}
STRATA = (("primary", "mixed"), ("shortcut", "shortcut"))
REGISTRATIONS = ("eval", "eval_muse")


def frozen_trials(reg: str = "eval_muse") -> dict:
    rep = json.loads((REPO_ROOT / "results" / reg / "report.json").read_text())
    return {(name, t["seed"]): t for name, _ in STRATA for t in rep["strata"][name]["trials"]}


def plan_sweep(lams=SWEEP) -> dict:
    from mirror.plan import plan
    from mirror.scenes import ledger_path, sample_layout
    led = ledger_path().resolve()
    protected = [(REPO_ROOT / "results" / r / "ledger.jsonl").resolve() for r in REGISTRATIONS]
    if os.environ.get("MIRROR_EVAL_LEDGER") is None or led in protected:
        raise RuntimeError("set MIRROR_EVAL_LEDGER to a new ledger file; frozen ledgers are never written")
    a, _, prov = spec_pair()
    trials = frozen_trials("eval_muse")
    rows = []
    for name, family in STRATA:
        seeds = sorted(s for (n, s) in trials if n == name)
        for seed in seeds:
            sc = sample_layout(seed, "eval", family=family)
            t = trials[(name, seed)]
            row = dict(stratum=name, seed=seed, scene_sha256=scene_sha(sc),
                       scene_matches_frozen=scene_sha(sc) == t["scene_sha256"],
                       frozen_original_route_labels=t["a"]["report"]["route_labels"], plans={})
            for lam in lams:
                t0 = time.perf_counter()
                pr = plan(a, sc, config=config_with(lam))
                note = transport_note(pr, CUP)
                row["plans"][lam_key(lam)] = dict(status=pr.status, cup_route=note["route"], cup_mode=note["mode"],
                                                 cup_tilted=tilted(note),
                                                 planning_s=round(time.perf_counter() - t0, 3))
            row["frozen_route_reproduced_at_0.05"] = (
                row["plans"]["0.05"]["cup_route"] in row["frozen_original_route_labels"])
            rows.append(row)
    counts = {}
    for lam in lams:
        k = lam_key(lam)
        per = {name: sum(r["plans"][k]["cup_tilted"] for r in rows if r["stratum"] == name) for name, _ in STRATA}
        counts[k] = dict(total=sum(per.values()), **per,
                         no_plan=sum(r["plans"][k]["status"] != "ok" for r in rows))
    return dict(rows=rows, counts=counts, layouts=len(rows),
                scenes_matching_frozen=sum(r["scene_matches_frozen"] for r in rows),
                frozen_route_reproduced_at_0_05=sum(r["frozen_route_reproduced_at_0.05"] for r in rows),
                spec_original_sha256=prov.get("enforced_original"), ledger=str(led.name))


def discordant_recount() -> dict:
    rows = []
    for reg in REGISTRATIONS:
        for (name, seed), t in sorted(frozen_trials(reg).items()):
            a, b = t["a"], t["b"]
            if a["violation_free_success"] == b["violation_free_success"]:
                continue
            ta, tb = a["report"]["execution_time_s"], b["report"]["execution_time_s"]
            rows.append(dict(registration=reg, stratum=name, seed=seed,
                             original_vfs=a["violation_free_success"], corrected_vfs=b["violation_free_success"],
                             original_exec_s=round(ta, 3), corrected_exec_s=round(tb, 3),
                             corrected_faster=tb < ta, saving_s=round(ta - tb, 3),
                             original_beads_lost=a["beads_lost"], corrected_beads_lost=b["beads_lost"]))
    by = {}
    for reg in REGISTRATIONS:
        sub = [r for r in rows if r["registration"] == reg]
        by[reg] = dict(discordant=len(sub), corrected_faster=sum(r["corrected_faster"] for r in sub),
                       only_original_vfs=sum(r["original_vfs"] for r in sub))
    savings = sorted(r["saving_s"] for r in rows)
    med = savings[len(savings) // 2] if savings else None
    return dict(rows=rows, discordant=len(rows), corrected_faster=sum(r["corrected_faster"] for r in rows),
                only_original_vfs=sum(r["original_vfs"] for r in rows), by_registration=by,
                saving_s_median=med, saving_s_min=savings[0] if savings else None,
                saving_s_max=savings[-1] if savings else None,
                discordant_with_beads_lost=sum(r["original_beads_lost"] > 0 for r in rows),
                measure="execution_time_s of the whole kit (all three objects), from each trial's saved report")


def run(out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    sweep = plan_sweep()
    rec = discordant_recount()
    match = {k: sweep["counts"][k]["total"] == v for k, v in AUDIT_CLAIM["plan_only_tilted_of_90"].items()}
    summary = dict(
        audit_claim=AUDIT_CLAIM,
        reproduced=dict(plan_only_tilted_of_90={k: sweep["counts"][k] for k in sweep["counts"]},
                        plan_only_matches_audit=match,
                        discordant_pairs=rec["discordant"], corrected_faster=rec["corrected_faster"],
                        recount_matches_audit=(rec["discordant"] == AUDIT_CLAIM["discordant_pairs"]
                                               and rec["corrected_faster"] == AUDIT_CLAIM["corrected_faster"])),
        scenes_matching_frozen=f'{sweep["scenes_matching_frozen"]}/{sweep["layouts"]}',
        frozen_route_reproduced_at_0_05=f'{sweep["frozen_route_reproduced_at_0_05"]}/{sweep["layouts"]}',
        notes=["plan-only: the original specification's plan is computed, never executed",
               "frozen layouts regenerated from seeds; accesses recorded in this folder's own ledger",
               "tilted = the cup's chosen transport mode is not upright"])
    (out_dir / "plan_sweep.json").write_text(json.dumps(sweep, indent=1))
    (out_dir / "discordant_recount.json").write_text(json.dumps(rec, indent=1))
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    return summary
