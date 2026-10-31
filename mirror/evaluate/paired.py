"""Paired evaluation of two frozen specification versions on identical scenes."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Callable, Optional, Sequence

import numpy as np

from mirror.contracts import Constraint, SceneState, TaskSpec
from mirror.geometry import DEFAULT_UPRIGHT_TOL_DEG

from . import checker as _chk
from .checker import spec_hash
from .feasibility import classify_planning_failure, feasibility
from .grounding import select
from .models import ConditionMetrics, EvalReport, FreezeManifest, PairedTrial, TrialRecord
from .stats import paired_binary, paired_continuous
from .trial import execute_trial, tilt_trace

PACKAGE_ROOT = Path(__file__).resolve().parents[1]   # mirror/
MAX_WORKERS = 2

SIMPLIFICATIONS = [
    "Completion uses the object's centre: inside the tray's inner footprint (minus inside_margin_m) and above its floor.",
    "Settled means linear speed below tolerances.settle_speed_mps over the last 0.3 s of the log; angular speed is not checked.",
    "Transport phase boundaries come from the executor's per-step held-object label (finger-contact detection).",
    "Bead retention counts bead centres inside the cup interior box (2 mm tolerance) at the final step.",
    "keep_clearance uses a bounding-sphere distance to obstacle boxes (conservative).",
    "Upright-route feasibility witness is a clearance condition, not a constructed trajectory; scenes with box obstacles are 'unknown'.",
    "Infeasibility proofs consider only the carried object and assume the tool centre lies within its bounding sphere.",
]


# ------------------------------------------------------------ freezing ---

def code_hash(root: Path = PACKAGE_ROOT) -> tuple[str, int]:
    h = hashlib.sha256()
    files = sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    for p in files:
        h.update(p.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest(), len(files)


def _spec_ref(spec: TaskSpec) -> dict:
    return dict(spec_id=spec.spec_id, version=spec.version, sha256=spec_hash(spec))


def freeze(spec_a: TaskSpec, spec_b: TaskSpec, seeds: Sequence[int], split: str,
           path: Path | str, config: Optional[dict] = None) -> FreezeManifest:
    """Write the freeze manifest. Must happen before any evaluation layout is
    generated; ``paired_eval`` refuses the eval split without it."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"{path} exists; a freeze manifest is written once")
    digest, n = code_hash()
    now = time.time()
    m = FreezeManifest(created_at=_dt.datetime.fromtimestamp(now, _dt.timezone.utc).isoformat(),
                       created_unix=now, split=split, seeds=[int(s) for s in seeds],
                       spec_a=_spec_ref(spec_a), spec_b=_spec_ref(spec_b),
                       code_sha256=digest, code_files=n, config=dict(config or {}))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(m.model_dump_json(indent=1))
    return m


class FreezeError(RuntimeError):
    pass


def verify_manifest(path: Path | str, spec_a: TaskSpec, spec_b: TaskSpec,
                    seeds: Sequence[int], split: str) -> FreezeManifest:
    path = Path(path)
    if not path.exists():
        raise FreezeError(f"no freeze manifest at {path}; run freeze() before generating {split} layouts")
    m = FreezeManifest.model_validate_json(path.read_text())
    problems = []
    if m.split != split:
        problems.append(f"manifest split {m.split!r} != {split!r}")
    if sorted(m.seeds) != sorted(int(s) for s in seeds):
        problems.append("seeds differ from the manifest")
    if m.spec_a != _spec_ref(spec_a):
        problems.append("specification A differs from the frozen version")
    if m.spec_b != _spec_ref(spec_b):
        problems.append("specification B differs from the frozen version")
    digest, _ = code_hash()
    if digest != m.code_sha256:
        problems.append("code under mirror/ changed since the freeze")
    if problems:
        raise FreezeError("; ".join(problems))
    return m


# ---------------------------------------------------------- comparison ---

def _same_rule(a: Constraint, b: Constraint) -> bool:
    return (a.type == b.type and a.phase == b.phase and a.params == b.params
            and a.applies_to.model_dump() == b.applies_to.model_dump())


def corrected_rules(spec_a: TaskSpec, spec_b: TaskSpec) -> list[Constraint]:
    """Rules enforced in B with no identical enforced rule in A."""
    ea = spec_a.enforced_constraints()
    return [c for c in spec_b.enforced_constraints() if not any(_same_rule(c, x) for x in ea)]


def _scene_hash(scene: SceneState) -> str:
    return hashlib.sha256(scene.model_dump_json().encode()).hexdigest()


def _record(condition, rollout, log, plan, spec, scene, rules, interventions=0) -> TrialRecord:
    rep = rollout.report
    corr = []
    max_tilt, over = 0.0, 0.0
    ruled_ids: set[str] = set()
    trace = {}
    if log is not None and log.n:
        ctx = _chk._context(log, spec, scene)
        corr, _ = _chk.evaluate_rules(ctx, rules)
        for c in rules:
            for oid in select(c.applies_to, spec, scene):
                if oid not in log.object_ids:
                    continue
                ruled_ids.add(oid)
                if c.type == "upright":
                    k = log.index(oid)
                    m = _chk._phase_mask(ctx, k, c.phase)
                    if m.any():
                        max_tilt = max(max_tilt, float(ctx.tilt[m, k].max()))
                trace.setdefault(oid, tilt_trace(log, oid))
        over = float(sum(v.t_end - v.t_start + log.dt for v in corr))
    beads_total = sum(o.beads_total for o in rep.outcomes)
    beads_in = sum(o.beads_in_container for o in rep.outcomes)
    scope = {}
    if log is not None:
        for o in rep.outcomes:
            if o.object_id not in ruled_ids and o.object_id in log.object_ids:
                scope[o.object_id] = round(o.max_transport_tilt_deg, 2)
    fclass = ""
    feas = feasibility(spec, scene)["verdict"]
    if rep.status == "planning_failed":
        fclass = classify_planning_failure(plan.infeasibility, feas)
    return TrialRecord(
        condition=condition, report=rep,
        violation_free_success=(rep.status == "success" and not corr),
        corrected_violations=corr, corrected_max_tilt_deg=round(max_tilt, 3),
        corrected_over_tol_s=round(over, 4), beads_total=beads_total,
        beads_lost=beads_total - beads_in if log is not None else 0,
        forbidden_contacts=len(rep.forbidden_contacts), obstacle_contacts=obstacle_contacts(log),
        failure_class=fclass, feasibility=feas,
        scope_objects_tilted=scope, interventions=interventions, tilt_trace=trace)


def obstacle_contacts(log) -> list[dict]:
    """Every interval in which the gripper or an object touches a static
    obstacle, whatever the specification says. Planning is meant to avoid
    these, so they reveal envelope or tracking mismatches."""
    if log is None:
        return []
    by: dict[tuple[str, str], list[tuple[int, float]]] = {}
    for c in log.contacts:
        if c.other.startswith("obstacle:"):
            by.setdefault((c.object_id, c.other), []).append((c.step, c.depth))
    out = []
    for (body, other), lst in sorted(by.items()):
        mask = np.zeros(log.n, bool)
        depth = np.zeros(log.n)
        for s, d in lst:
            if 0 <= s < log.n:
                mask[s] = True
                depth[s] = max(depth[s], d)
        for s, e in _chk._intervals(mask):
            out.append(dict(body=body, other=other, t_start=round(float(log.t[s]), 3),
                            t_end=round(float(log.t[e]), 3), max_depth=float(depth[s:e + 1].max()),
                            phase=log.phase[s] if s < len(log.phase) else ""))
    return out


def _job(args):
    (seed, condition, scene, spec, rules, planner, env_factory, log_dir, max_sim_time) = args
    rollout, log, plan = execute_trial(scene, spec, condition=condition, planner=planner,
                                       env_factory=env_factory, max_sim_time=max_sim_time,
                                       extra_rules=rules)
    rollout.frames = []  # playback frames are not kept in evaluation records
    interventions = int(log.meta.get("interventions", 0)) if log is not None else 0
    rec = _record(condition, rollout, log, plan, spec, scene, rules, interventions)
    if log_dir is not None and log is not None:
        p = Path(log_dir) / f"seed{seed}_{condition}.npz"
        p.parent.mkdir(parents=True, exist_ok=True)
        log.save(p)
        rec.log_path = p.name
    return seed, condition, rec


def condition_metrics(recs: list[TrialRecord], condition: str) -> ConditionMetrics:
    n = len(recs)
    st = {}
    for r in recs:
        st[r.report.status] = st.get(r.report.status, 0) + 1
    tilts = [r.corrected_max_tilt_deg for r in recs if r.report.physics_steps]
    known = [r for r in recs if r.feasibility == "feasible_witness"]
    mean = lambda xs: float(np.mean(xs)) if xs else 0.0
    executed = [r for r in recs if r.report.physics_steps]
    ex = bool(executed)
    return ConditionMetrics(
        condition=condition, n=n, executed=len(executed),
        completion=sum(r.report.completion for r in recs),
        violation_free_success=sum(r.violation_free_success for r in recs),
        corrected_rule_violations=sum(bool(r.corrected_violations) for r in recs),
        max_tilt_deg_mean=round(mean(tilts), 3) if ex else None,
        max_tilt_deg_max=round(max(tilts), 3) if ex else None,
        over_tol_s_mean=round(mean([r.corrected_over_tol_s for r in executed]), 4) if ex else None,
        beads_lost_total=sum(r.beads_lost for r in recs),
        beads_lost_mean=round(mean([r.beads_lost for r in executed]), 3) if ex else None,
        forbidden_contact_trials=sum(r.forbidden_contacts > 0 for r in recs),
        obstacle_contact_trials=sum(bool(r.obstacle_contacts) for r in recs),
        status_counts=st, planning_failures=st.get("planning_failed", 0),
        execution_failures=st.get("execution_failed", 0), timeouts=st.get("timeout", 0),
        planning_time_s_mean=round(mean([r.report.planning_time_s for r in recs]), 4),
        execution_time_s_mean=round(mean([r.report.execution_time_s for r in executed]), 3) if ex else None,
        interventions=sum(r.interventions for r in recs),
        feasible_scenes_wrongly_rejected=sum(r.report.status == "planning_failed" for r in known),
        feasible_scenes_known=len(known))


def scope_analysis(trials: list[PairedTrial], tol: float = DEFAULT_UPRIGHT_TOL_DEG) -> dict:
    """For objects not covered by the corrected rules: when the original run
    tilted them beyond tolerance (a tilted route), did the corrected run still
    do so? Unchanged tilting means the correction was not over-applied."""
    rows, preserved, removed = [], 0, 0
    for t in trials:
        for oid, ta in t.a.scope_objects_tilted.items():
            tb = t.b.scope_objects_tilted.get(oid)
            if tb is None or ta <= tol:
                continue
            kept = tb > tol
            preserved += kept
            removed += not kept
            rows.append(dict(seed=t.seed, object_id=oid, tilt_a=ta, tilt_b=tb, still_tilted=kept))
    verdict = ("no unrelated object took a tilted route in the original runs; scope not exercised"
               if not rows else
               f"{preserved}/{len(rows)} unrelated-object tilted routes kept under the corrected spec")
    return dict(tolerance_deg=tol, cases=rows, preserved=preserved, removed=removed, verdict=verdict)


def paired_eval(spec_a: TaskSpec, spec_b: TaskSpec, seeds: Sequence[int], split: str = "eval", *,
                manifest_path: Optional[Path | str] = None, workers: int = 1,
                log_dir: Optional[Path | str] = None,
                layout_fn: Optional[Callable[[int, str], SceneState]] = None,
                planner=None, env_factory=None, max_sim_time: float = 120.0,
                conditions: tuple[str, str] = ("original", "corrected"),
                n_boot: int = 10_000, include_blocked: bool = False) -> EvalReport:
    """Run spec A and spec B on the identical initial scene for every seed.

    For split "eval" a freeze manifest (see ``freeze``) must already exist and
    match the specifications, seeds and code; layouts are generated only after
    it is verified. Every attempt is reported."""
    started = time.time()
    workers = max(1, min(int(workers), MAX_WORKERS))
    seeds = [int(s) for s in seeds]
    notes: list[str] = []
    if split == "eval":
        if manifest_path is None:
            raise FreezeError("the eval split requires a freeze manifest (manifest_path)")
        manifest = verify_manifest(manifest_path, spec_a, spec_b, seeds, split)
    else:
        if manifest_path is not None and Path(manifest_path).exists():
            manifest = verify_manifest(manifest_path, spec_a, spec_b, seeds, split)
        else:
            digest, n = code_hash()
            manifest = FreezeManifest(created_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
                                      created_unix=time.time(), split=split, seeds=seeds,
                                      spec_a=_spec_ref(spec_a), spec_b=_spec_ref(spec_b),
                                      code_sha256=digest, code_files=n,
                                      config={"note": "development run; not frozen in advance"})
    if layout_fn is None:
        from mirror.scenes import sample_layout as layout_fn  # noqa: N813
    layouts_generated = time.time()
    if split == "eval" and layouts_generated < manifest.created_unix:
        raise FreezeError("layout generation precedes the freeze manifest")
    scenes = {s: layout_fn(s, split) for s in seeds}
    rules = corrected_rules(spec_a, spec_b)
    if not rules:
        notes.append("specification B enforces no rule beyond A; corrected-rule metrics are empty")

    jobs = []
    for s in seeds:
        for cond, spec in zip(conditions, (spec_a, spec_b)):
            jobs.append((s, cond, scenes[s], spec, rules, planner, env_factory,
                         str(log_dir) if log_dir else None, max_sim_time))
    results = {}
    if workers == 1:
        for j in jobs:
            s, c, r = _job(j)
            results[(s, c)] = r
    else:
        import multiprocessing as mp
        with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("spawn")) as ex:
            for s, c, r in ex.map(_job, jobs):
                results[(s, c)] = r

    trials = [PairedTrial(seed=s, scene_id=scenes[s].scene_id, scene_sha256=_scene_hash(scenes[s]),
                          a=results[(s, conditions[0])], b=results[(s, conditions[1])]) for s in seeds]
    ra = [t.a for t in trials]
    rb = [t.b for t in trials]
    metrics = {conditions[0]: condition_metrics(ra, conditions[0]),
               conditions[1]: condition_metrics(rb, conditions[1])}
    pairs = [dict(seed=t.seed, a=t.a.report.status, b=t.b.report.status,
                  a_vfs=t.a.violation_free_success, b_vfs=t.b.violation_free_success) for t in trials]
    exercised = [t.seed for t in trials if t.a.corrected_violations]
    paired = dict(
        outcome_pairs=pairs,
        correction_exercised=dict(
            seeds=exercised, n=len(exercised), of=len(trials),
            note="scenes where the original specification's rollout violated a corrected rule; "
                 "in the others both versions can behave identically"),
        violation_free_success=paired_binary([r.violation_free_success for r in ra],
                                             [r.violation_free_success for r in rb], n_boot=n_boot),
        completion=paired_binary([r.report.completion for r in ra], [r.report.completion for r in rb],
                                 n_boot=n_boot),
        corrected_rule_compliance=paired_binary([not r.corrected_violations and bool(r.report.physics_steps) for r in ra],
                                                [not r.corrected_violations and bool(r.report.physics_steps) for r in rb],
                                                n_boot=n_boot),
        beads_lost=paired_continuous([r.beads_lost for r in ra], [r.beads_lost for r in rb], n_boot=n_boot),
        max_tilt_deg=paired_continuous([r.corrected_max_tilt_deg for r in ra],
                                       [r.corrected_max_tilt_deg for r in rb], n_boot=n_boot),
        previously_successful_kept=dict(
            a_success=sum(r.violation_free_success for r in ra),
            kept=sum(a.violation_free_success and b.violation_free_success for a, b in zip(ra, rb)),
            a_own_spec_success=sum(a.report.status == "success" for a in ra),
            own_success_kept=sum(a.report.status == "success" and b.report.status == "success"
                                 for a, b in zip(ra, rb))),
    )
    tol = min([float(c.params.get("max_tilt_deg", DEFAULT_UPRIGHT_TOL_DEG)) for c in rules
               if c.type == "upright"] or [DEFAULT_UPRIGHT_TOL_DEG])
    scope = scope_analysis(trials, tol)
    scope_objs = sorted({o for t in trials for o in t.b.scope_objects_tilted})
    blocked = blocked_case(spec_a, spec_b, planner=planner, env_factory=env_factory,
                           rules=rules, conditions=conditions,
                           max_sim_time=max_sim_time) if include_blocked else []
    return EvalReport(
        split=split, manifest=manifest,
        corrected_rules=[c.model_dump(mode="json") for c in rules],
        scope_objects=scope_objs,
        config=dict(workers=workers, max_sim_time=max_sim_time, conditions=list(conditions),
                    planner=_name(planner, "mirror.plan.plan"),
                    executor=_name(env_factory, "mirror.sim.SimEnv(seed=0)"),
                    checker=_chk.CHECKER_ID, n_boot=n_boot,
                    layouts_generated_unix=layouts_generated),
        trials=trials, metrics=metrics, paired=paired, scope=scope, blocked=blocked,
        started_at=_dt.datetime.fromtimestamp(started, _dt.timezone.utc).isoformat(),
        finished_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        wall_time_s=round(time.time() - started, 2), notes=notes,
        simplifications=list(SIMPLIFICATIONS))


def _name(obj, default: str) -> str:
    if obj is None:
        return default
    return getattr(obj, "__qualname__", None) or type(obj).__name__


def blocked_case(spec_a: TaskSpec, spec_b: TaskSpec, *, scene: Optional[SceneState] = None,
                 planner=None, env_factory=None, rules=None,
                 conditions=("original", "corrected"), max_sim_time: float = 120.0) -> list[dict]:
    """Run both specs on the blocked reference scene and label the outcome:
    proven infeasibility versus planner failure."""
    if scene is None:
        from mirror.scenes import reference_scene
        scene = reference_scene("blocked")
    rules = corrected_rules(spec_a, spec_b) if rules is None else rules
    out = []
    for cond, spec in zip(conditions, (spec_a, spec_b)):
        rollout, log, plan = execute_trial(scene, spec, condition=cond, planner=planner,
                                           env_factory=env_factory, max_sim_time=max_sim_time,
                                           extra_rules=rules)
        rec = _record(cond, rollout, log, plan, spec, scene, rules)
        feas = feasibility(spec, scene)
        out.append(dict(condition=cond, scene_id=scene.scene_id, status=rec.report.status,
                        failure_class=rec.failure_class, independent=feas,
                        planner_infeasibility=plan.infeasibility, plan_reason=plan.reason,
                        rejected_routes=[r.model_dump() for r in plan.routes if not r.feasible],
                        corrected_violations=len(rec.corrected_violations),
                        beads_lost=rec.beads_lost, max_tilt_deg=rec.corrected_max_tilt_deg))
    return out
