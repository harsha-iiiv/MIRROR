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

import contextlib
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import socket
import sys
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
LEDGER_PATH = None   # resolved at call time: mirror.scenes.ledger_path() (MIRROR_EVAL_LEDGER)
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


# ------------------------------------------------------- registrations ---
# A second pre-registered evaluation with the specifications compiled by
# Muse Spark (recorded responses, replayed). Fresh seeds, own ledger, own
# output directory; the analysis plan is the first registration's, unchanged.

MUSE_STRATA = [
    dict(name="primary", role="primary", split="eval", family="mixed", seeds=list(range(50_000, 50_060)),
         description="standard sampler, unbiased mix of layouts"),
    dict(name="shortcut", role="stratum", split="eval", family="shortcut", seeds=list(range(60_000, 60_030)),
         description="disclosed stratum: a wall slot near the straight cup-to-tray line, "
                     "defined by geometry at sampling time"),
    dict(name="blocked", role="blocked", split="blocked", family="mixed", seeds=list(range(50_000, 50_005)),
         description="the wall spans the workspace; the slot is the only opening"),
]

ANALYSIS_PLAN = dict(
    identical_to="first registration (results/eval)",
    code_path="mirror.evaluate.final.run_final -> paired_eval per stratum + blocked reference scene",
    conditions="A = original specification, B = corrected specification; same planner, executor and "
               "scene per seed",
    primary_endpoint="violation-free success: status success and no violation of the corrected rule, "
                     "computed for both conditions from the step log",
    metrics=["completion", "success", "violation-free success", "trials violating the corrected rule",
             "max tilt of corrected-rule objects", "seconds over tolerance", "beads lost",
             "obstacle or floor contacts", "planning/execution failures and timeouts",
             "feasible scenes wrongly rejected", "correction exercised"],
    tests="exact two-sided McNemar on paired violation-free success; Wilson 95% intervals; "
          "percentile bootstrap 95% CI of the paired difference (10,000 resamples)",
    scope_check="objects not covered by the correction keep their behaviour (scope analysis)",
    blocked_check="corrected specification on blocked seeds: planner proven:true certificate confirmed "
                  "by the evaluator's independent any-motion bound",
    reporting="every seed of every stratum reported; nothing filtered by outcome",
)

REGISTRATIONS = {
    "first": dict(name="first", strata=D3_STRATA, out="results/eval", manifest="results/eval/freeze.json",
                  ledger="results/eval/ledger.jsonl", cassette_dir="data/cassettes", model=None),
    "muse": dict(
        name="muse", strata=MUSE_STRATA, out="results/eval_muse", manifest="results/eval_muse/freeze.json",
        ledger="results/eval_muse/ledger.jsonl", cassette_dir="data/cassettes_muse", model="muse-spark-1.3",
        # Presentation-only code: renders videos, the studio UI and the CLI
        # narrative. It cannot change a rollout, a check or a statistic, so it
        # is left out of this registration's code hash (render polish
        # continues during the run). Evaluation-path modules never import it.
        code_exclude=("sim/render.py", "studio/**", "cli.py"),
        compare_to="first",
        hypothesis=("Pre-stated before any layout of this registration was generated: (1) the corrected "
                    "specification beats the original on violation-free success in both the primary and "
                    "the shortcut stratum; (2) the corrected specification never loses beads; (3) the "
                    "corrected specification is proven infeasible on 5/5 blocked seeds.")),
}

AUDIT_SUFFIXES = {".json", ".jsonl", ".md", ".txt", ".csv", ".html", ".log"}
_FIVE_DIGITS = re.compile(r"(?<![\d.])(\d{5})(?![\d.])")


def audit_seed_usage(strata: list[dict], roots: list[Path | str]) -> dict:
    """Search ledgers, reports and other text output (and file names) for any
    of the registration's seed numbers. Conservative: any matching five-digit
    token counts, whatever its context."""
    seeds = {int(s) for st in strata for s in st["seeds"]}
    hits, files, scanned = [], 0, []
    for root in roots:
        root = Path(root)
        if not root.exists():
            continue
        scanned.append(_shown(root))
        paths = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        for p in paths:
            for tok in _FIVE_DIGITS.findall(p.name):
                if int(tok) in seeds:
                    hits.append(dict(file=_shown(p), seed=int(tok), where="file name"))
            if p.suffix.lower() not in AUDIT_SUFFIXES:
                continue
            files += 1
            try:
                text = p.read_text(errors="ignore")
            except OSError:
                continue
            found = sorted({int(t) for t in _FIVE_DIGITS.findall(text)} & seeds)
            if found:
                hits.append(dict(file=_shown(p), seeds=found[:10], where="content"))
    return dict(roots=scanned, text_files=files, hits=hits)


def _shown(p: Path) -> str:
    try:
        return Path(p).resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return "external:" + Path(p).name


class _IOCount:
    def __init__(self):
        self.network = 0
        self.layouts = 0


@contextlib.contextmanager
def io_guard():
    """Refuse and count every network connection and every layout sample
    while active (patched in every loaded module that holds sample_layout)."""
    import mirror.scenes as scenes_pkg
    import mirror.scenes.sampling as sampling
    count = _IOCount()
    orig = sampling.sample_layout

    import functools

    @functools.wraps(orig)
    def no_layout(*a, **k):
        count.layouts += 1
        raise RuntimeError("layout sampling is not allowed during a freeze")

    def no_net(*a, **k):
        count.network += 1
        raise OSError("network access is not allowed during a freeze")

    patched = []
    for mod in list(sys.modules.values()):
        if mod is not None and getattr(mod, "sample_layout", None) is orig:
            patched.append(mod)
            setattr(mod, "sample_layout", no_layout)
    saved = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo)
    socket.socket.connect = no_net
    socket.socket.connect_ex = no_net
    socket.create_connection = no_net
    socket.getaddrinfo = no_net
    try:
        yield count
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo = saved
        for mod in patched:
            setattr(mod, "sample_layout", orig)
        assert scenes_pkg.sample_layout is orig


def _file_sha(p: Path) -> Optional[str]:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def freeze_registration(name: str, *, audit_paths: tuple = (), config: Optional[dict] = None,
                        overrides: Optional[dict] = None, spec_builder: Optional[Callable] = None,
                        reference_builder: Optional[Callable] = None) -> FreezeManifest:
    """Freeze a named registration (e.g. "muse"). Generates no layouts.

    Refuses if any of its seeds appear in any ledger, report or other text
    output under results/ or in ``audit_paths``, or in its own ledger. Builds
    its specifications by replaying its recordings, rebuilds the compared
    registration's pair to check it reproduces that manifest's hashes, and
    records whether the evaluator signatures are identical. Network access and
    layout sampling are refused and counted throughout."""
    reg = dict(REGISTRATIONS[name], **(overrides or {}))
    strata = reg["strata"]
    manifest = REPO_ROOT / reg["manifest"] if not Path(reg["manifest"]).is_absolute() else Path(reg["manifest"])
    ledger = REPO_ROOT / reg["ledger"] if not Path(reg["ledger"]).is_absolute() else Path(reg["ledger"])
    check_not_retired(strata)
    hits = ledger_hits(read_ledger(ledger), strata)
    if hits:
        raise FreezeError(f"{len(hits)} entr{'y' if len(hits) == 1 else 'ies'} for this registration's seeds "
                          f"already in its ledger (first: {hits[0]})")
    roots = list(reg.get("audit_roots", [REPO_ROOT / "results"])) + [Path(p) for p in audit_paths]
    audit = audit_seed_usage(strata, roots)
    if audit["hits"]:
        raise FreezeError(f"seeds of registration '{name}' already appear in {len(audit['hits'])} file(s): "
                          f"{audit['hits'][:3]}")
    ledger_sha = _file_sha(ledger)
    with io_guard() as io:
        from .specs import evaluation_specs, signature
        build = spec_builder or (lambda: evaluation_specs(source="replay", model=reg["model"],
                                                          cassette_dir=REPO_ROOT / reg["cassette_dir"]))
        a, b, prov = build()
        comparison = {}
        if reg.get("compare_to"):
            ref = REGISTRATIONS[reg["compare_to"]]
            ref_manifest = FreezeManifest.model_validate_json((REPO_ROOT / ref["manifest"]).read_text())
            rbuild = reference_builder or (lambda: evaluation_specs(
                source="replay", model=ref["model"], cassette_dir=REPO_ROOT / ref["cassette_dir"]))
            ra, rb, _ = rbuild()
            from .paired import _spec_ref
            reproduced = (_spec_ref(ra)["sha256"] == ref_manifest.spec_a["sha256"]
                          and _spec_ref(rb)["sha256"] == ref_manifest.spec_b["sha256"])
            if not reproduced:
                raise FreezeError(f"replaying '{ref['name']}' recordings does not reproduce its frozen specs")
            comparison = dict(
                compared_to=ref["manifest"], reference_specs_reproduced=True,
                reference_spec_sha256=dict(a=ref_manifest.spec_a["sha256"], b=ref_manifest.spec_b["sha256"]),
                spec_sha256=dict(a=_spec_ref(a)["sha256"], b=_spec_ref(b)["sha256"]),
                original_signature_identical=signature(a) == signature(ra),
                corrected_signature_identical=signature(b) == signature(rb),
                fields="enforced constraints (id, type, phase, selector, parameters) and active goals")
        if io.network or io.layouts:
            raise FreezeError(f"freeze touched the network ({io.network}) or sampled layouts ({io.layouts})")
        cfg = dict(config or {})
        cfg.update(registration=name, out_dir=reg["out"], ledger=reg["ledger"],
                   hypothesis=reg.get("hypothesis", ""), analysis_plan=ANALYSIS_PLAN,
                   evaluator_signature=comparison,
                   seed_audit=dict(audit, hits=[], result="no seed of this registration found"),
                   freeze_io=dict(network_connections=io.network, layouts_sampled=io.layouts,
                                  method="socket connect/create_connection/getaddrinfo and every loaded "
                                         "sample_layout replaced by refusing counters during the freeze",
                                  ledger_sha256_before=ledger_sha))
        m = freeze_final(manifest, strata=strata, kind="final", spec_builder=lambda: (a, b, prov),
                         config=cfg, ledger_path=ledger, code_exclude=tuple(reg.get("code_exclude", ())))
    if io.network or io.layouts or _file_sha(ledger) != ledger_sha:
        manifest.unlink(missing_ok=True)
        raise FreezeError("the freeze sampled layouts, touched the network or changed the ledger; manifest removed")
    return m


def rehearsal_strata(n_mixed: int = 6, n_shortcut: int = 6, n_blocked: int = 2) -> list[dict]:
    """Same structure on development seeds, for verifying the pipeline."""
    return [
        dict(name="primary", role="primary", split="dev", family="mixed",
             seeds=list(range(0, n_mixed)), description="rehearsal on dev seeds"),
        dict(name="shortcut", role="stratum", split="dev", family="shortcut",
             seeds=list(range(500, 500 + n_shortcut)), description="rehearsal on dev seeds"),
        # blocked seeds at or above the evaluation base (neither frozen nor
        # retired) so the layout ledger is exercised by the rehearsal
        dict(name="blocked", role="blocked", split="blocked", family="mixed",
             seeds=list(range(12_000, 12_000 + n_blocked)), description="rehearsal seeds"),
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


def resolve_ledger(path=None) -> Path:
    if path is not None:
        return Path(path)
    try:
        from mirror.scenes import ledger_path
        return Path(ledger_path())
    except ImportError:
        return REPO_ROOT / "results" / "eval" / "ledger.jsonl"


def read_ledger(path=None) -> list[dict]:
    path = resolve_ledger(path)
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


def _ledger_summary(path) -> dict:
    path = resolve_ledger(path)
    entries = read_ledger(path)
    try:
        shown = path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        shown = path.name
    return dict(path=shown,
                exists=Path(path).exists(), entries=len(entries),
                sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).exists() else None)


def default_specs(source: str = "replay"):
    from .specs import evaluation_specs
    return evaluation_specs(source=source)


def freeze_final(path: Path | str, *, strata: Optional[list[dict]] = None, kind: str = "final",
                 spec_builder: Callable = default_specs, config: Optional[dict] = None,
                 layout_fn=None, ledger_path=LEDGER_PATH,
                 spec_source: str = "replay", code_exclude: tuple = ()) -> FreezeManifest:
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
                         ledger_at_freeze=_ledger_summary(ledger_path), code_exclude=code_exclude)


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
              ledger_path=LEDGER_PATH,
              rollout_stage: Optional[Path | str] = None) -> FinalReport:
    started = time.time()
    m = FreezeManifest.model_validate_json(Path(manifest_path).read_text())
    if not m.strata:
        raise FreezeError("manifest has no strata; write it with freeze_final()")
    reg_ledger = m.config.get("ledger")
    if reg_ledger and ledger_path is None:
        # the registration's own ledger, for the checks and for the sampler
        # (worker processes inherit the environment)
        ledger_path = REPO_ROOT / reg_ledger
        os.environ["MIRROR_EVAL_LEDGER"] = str(ledger_path)
    if m.kind != "rehearsal":
        check_not_retired(m.strata)
    early = [e for e in ledger_hits(read_ledger(ledger_path), m.strata)
             if not isinstance(e.get("unix"), (int, float)) or e["unix"] < m.created_unix]
    if early:
        raise FreezeError(f"ledger shows {len(early)} frozen layout(s) generated before the freeze "
                          f"(first: {early[0]})")
    for st in m.strata:                    # fail before running anything
        make_layout_fn(st["family"], layout_fn)
    if spec_builder is default_specs and m.spec_provenance.get("requested_model") \
            and m.config.get("registration"):
        from .specs import evaluation_specs
        a, b, prov = evaluation_specs(source="replay", model=m.spec_provenance["requested_model"],
                                      cassette_dir=REPO_ROOT / m.spec_provenance["cassette_dir"])
    elif spec_builder is default_specs:
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
    ledger = dict(_ledger_summary(ledger_path), frozen_seed_entries=hits,
                  entries_before_freeze=[e for e in hits if (e.get("unix") or 0) < m.created_unix],
                  writer_active=bool(hits) or not expected)
    if expected and not hits:
        notes.append("The layout ledger recorded no entries for the frozen evaluation seeds during this "
                     "run; the ledger writer is not active, so the ledger check is vacuous for this run.")
    if m.kind == "rehearsal":
        notes.insert(0, "REHEARSAL on development seeds; not the evaluation result.")
    return FinalReport(
        title="MIRROR pre-registered evaluation" + (" (rehearsal)" if m.kind == "rehearsal" else "")
              + (f", registration '{m.config['registration']}'" if m.config.get("registration") else ""),
        manifest=m, spec_provenance=prov or m.spec_provenance, strata=reports, roles=roles,
        blocked_reference=ref, ledger=ledger, rollout_selection=selection,
        started_at=_dt.datetime.fromtimestamp(started, _dt.timezone.utc).isoformat(),
        finished_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        wall_time_s=round(time.time() - started, 1), notes=notes)
