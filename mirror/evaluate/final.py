"""Pre-registered evaluation: all strata from one freeze manifest.

Design (fixed before any freeze):
  * primary:  60 eval seeds 10000-10059, standard sampler, family "mixed";
  * shortcut: 30 eval seeds 20000-20029, family "shortcut" (a wall slot near
    the straight line between cup and tray; defined by geometry at sampling
    time, never by outcomes); reported in full beside the primary result;
  * blocked:  5 seeds of the blocked split, for the infeasibility check.
Both specifications come from replaying recorded model responses on the
pinned reference demonstration, checked against the offline interpreter.
"""
from __future__ import annotations

import datetime as _dt
import time
from pathlib import Path
from typing import Callable, Optional

from .models import FinalReport, FreezeManifest
from .paired import (FreezeError, blocked_case, corrected_rules, freeze_strata, make_layout_fn,
                     paired_eval, verify_manifest)

PRIMARY_SEEDS = list(range(10_000, 10_060))
SHORTCUT_SEEDS = list(range(20_000, 20_030))
BLOCKED_SEEDS = list(range(10_000, 10_005))

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


def default_specs():
    from .specs import evaluation_specs
    return evaluation_specs()


def freeze_final(path: Path | str, *, strata: Optional[list[dict]] = None, kind: str = "final",
                 spec_builder: Callable = default_specs, config: Optional[dict] = None,
                 layout_fn=None) -> FreezeManifest:
    strata = strata or D3_STRATA
    for st in strata:                      # refuse to freeze a design that cannot run
        make_layout_fn(st["family"], layout_fn)
    a, b, prov = spec_builder()
    return freeze_strata(a, b, strata, path, kind=kind, config=config, spec_provenance=prov)


def run_final(manifest_path: Path | str, *, workers: int = 2, save_rollouts: bool = False,
              out_dir: Optional[Path | str] = None, spec_builder: Callable = default_specs,
              planner=None, env_factory=None, layout_fn=None, n_boot: int = 10_000,
              max_sim_time: float = 120.0, log_root: Optional[Path] = None) -> FinalReport:
    started = time.time()
    m = FreezeManifest.model_validate_json(Path(manifest_path).read_text())
    if not m.strata:
        raise FreezeError("manifest has no strata; write it with freeze_final()")
    for st in m.strata:                    # fail before running anything
        make_layout_fn(st["family"], layout_fn)
    a, b, prov = spec_builder()
    for st in m.strata:
        verify_manifest(manifest_path, a, b, st["seeds"], st["split"], st["family"])
    reports, roles = {}, {}
    for st in m.strata:
        rdir = Path(out_dir) / st["name"] / "rollouts" if (save_rollouts and out_dir) else None
        ldir = Path(log_root) / st["name"] if log_root else None
        reports[st["name"]] = paired_eval(
            a, b, st["seeds"], split=st["split"], family=st["family"], stratum=st["name"],
            manifest_path=manifest_path, require_manifest=True, workers=workers,
            layout_fn=layout_fn, planner=planner, env_factory=env_factory, n_boot=n_boot,
            max_sim_time=max_sim_time, rollout_dir=rdir, log_dir=ldir)
        roles[st["name"]] = st.get("role", "stratum")
    ref = blocked_case(a, b, planner=planner, env_factory=env_factory,
                       rules=corrected_rules(a, b), max_sim_time=max_sim_time)
    notes = ["Every attempt in every stratum is reported; nothing is filtered by outcome."]
    if m.kind == "rehearsal":
        notes.insert(0, "REHEARSAL on development seeds; not the evaluation result.")
    return FinalReport(
        title="MIRROR pre-registered evaluation" + (" (rehearsal)" if m.kind == "rehearsal" else ""),
        manifest=m, spec_provenance=prov or m.spec_provenance, strata=reports, roles=roles,
        blocked_reference=ref,
        started_at=_dt.datetime.fromtimestamp(started, _dt.timezone.utc).isoformat(),
        finished_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        wall_time_s=round(time.time() - started, 1), notes=notes)
