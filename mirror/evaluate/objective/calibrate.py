"""Route time calibration on DEVELOPMENT layouts only.

For every development layout (split "dev"; never an evaluation seed) and
every feasible transport route of the cup under the original specification,
the plan that uses that route is executed in the simulator with the standard
controller, at two rotation prices (0.05 and 1.0, which change the empty
gripper's transits). Each executed route segment contributes one sample:

  transport  lift + transport + place of one object (what the planner costs
             as one transport candidate), measured as the summed duration of
             those executor phases;
  transit    the empty gripper's move to the next object (move phase).

Features are computed from the plan's pose sequence exactly as the planner's
route cost reads them: translation length L (m) and geodesic rotation R
(rad) between consecutive waypoints, plus the segment count N.

Models (ordinary least squares, per segment kind):
  A  T = c0 + a*L + b*R            -> rotation price lambda = b / a (m/rad)
  B  T = c0 + a*L + b*R + c*N      (diagnostic: per-segment acceleration overhead)

Objective of record (stated before any fit was seen): lambda from model A on
TRANSPORT segments, because only the transport route decides whether a held
object is tilted. The planner has one rotation price for transports and
transits; the transit fit is reported beside it, and the sweep values bracket
both. Also reported: leave-one-layout-out prediction error, a bootstrap over
layouts for lambda, and a decision check (on layouts with several cup routes,
how often the price-weighted cost picks the route that executed fastest,
and the seconds lost when it does not).
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from .common import (CUP, config_with, lam_key, phase_durations, plan_sha, route_segments, scene_sha,
                     spec_pair, transport_note)

DEV_SEEDS = {"mixed": list(range(8000, 8040)), "shortcut": list(range(8000, 8040))}
CAL_LAMS = (0.05, 1.0)
CHECK_LAMS = (0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 1.0)
N_BOOT = 2000


def _layout_job(args):
    seed, family = args
    from mirror.plan import plan
    from mirror.scenes import sample_layout
    from mirror.sim import SimEnv
    a, _, _ = spec_pair()
    sc = sample_layout(seed, "dev", family=family)
    base = plan(a, sc, config=config_with(0.05))
    labels = sorted({r.label for r in base.routes if r.object_id == CUP and r.feasible
                     and not r.label.startswith("transit")})
    runs, seen = [], {}
    for lam in CAL_LAMS:
        for lab in labels:
            pr = plan(a, sc, prefer=f"{CUP}:{lab}", config=config_with(lam))
            if pr.status != "ok":
                runs.append(dict(lam=lam, forced=lab, status=f"plan_{pr.status}", reason=pr.reason[:200]))
                continue
            h = plan_sha(pr)
            note = transport_note(pr, CUP)
            if h in seen:
                runs.append(dict(lam=lam, forced=lab, plan_sha256=h, duplicate_of=seen[h]))
                continue
            seen[h] = len(runs)
            t0 = time.perf_counter()
            res = SimEnv(sc, seed=0).execute(pr, frame_hz=1.0, max_sim_time=120.0)
            ph = phase_durations(res.log)
            segs = route_segments(pr, sc)
            for s in segs:
                s["measured_s"] = round(sum(ph.get(p, 0.0) for p in s["phases"]), 4)
            runs.append(dict(lam=lam, forced=lab, plan_sha256=h, cup_route=note["route"], cup_mode=note["mode"],
                             status=res.status, reason=res.reason, exec_s=round(float(res.sim_time), 4),
                             wall_s=round(time.perf_counter() - t0, 2), phases={k: round(v, 4) for k, v in ph.items()},
                             segments=segs))
    return dict(seed=seed, family=family, scene_sha256=scene_sha(sc), cup_labels=labels, runs=runs)


def _samples(layouts: list[dict]) -> list[dict]:
    """One sample per distinct executed route segment of a completed run."""
    out, seen = [], set()
    for lay in layouts:
        for r in lay["runs"]:
            if r.get("status") != "completed":
                continue
            for s in r["segments"]:
                key = (lay["family"], lay["seed"], s["kind"], s["object_id"], s["label"],
                       round(s["length_m"], 6), round(s["rotation_rad"], 6), s["segments"])
                if key in seen:
                    continue
                seen.add(key)
                out.append(dict(layout=f'{lay["family"]}:{lay["seed"]}', kind=s["kind"], object_id=s["object_id"],
                                label=s["label"], L=s["length_m"], R=s["rotation_rad"], N=s["segments"],
                                T=s["measured_s"]))
    return out


def _ols(X, y):
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return beta


def _design(rows, model):
    L = np.array([r["L"] for r in rows]); R = np.array([r["R"] for r in rows]); N = np.array([r["N"] for r in rows])
    cols = [np.ones_like(L), L, R] + ([N.astype(float)] if model == "B" else [])
    return np.column_stack(cols)


def fit(rows: list[dict], model: str, *, n_boot: int = N_BOOT, seed: int = 0) -> dict:
    X = _design(rows, model)
    y = np.array([r["T"] for r in rows])
    beta = _ols(X, y)
    res = y - X @ beta
    ss_tot = float(((y - y.mean()) ** 2).sum())
    names = ["c0_s", "a_s_per_m", "b_s_per_rad"] + (["c_s_per_segment"] if model == "B" else [])
    lays = sorted({r["layout"] for r in rows})
    # leave one layout out
    cv = []
    for lay in lays:
        tr = [i for i, r in enumerate(rows) if r["layout"] != lay]
        te = [i for i, r in enumerate(rows) if r["layout"] == lay]
        bt = _ols(X[tr], y[tr])
        cv += list(y[te] - X[te] @ bt)
    cv = np.array(cv)
    # bootstrap over layouts for lambda = b / a
    rng = np.random.default_rng(seed)
    idx_by = {lay: [i for i, r in enumerate(rows) if r["layout"] == lay] for lay in lays}
    lams = []
    for _ in range(n_boot):
        pick = rng.choice(len(lays), size=len(lays), replace=True)
        ii = [i for k in pick for i in idx_by[lays[k]]]
        bt = _ols(X[ii], y[ii])
        if bt[1] > 0:
            lams.append(bt[2] / bt[1])
    q = lambda v, p: float(np.quantile(v, p))
    return dict(model=model, n=len(rows), layouts=len(lays),
                coef={k: float(v) for k, v in zip(names, beta)},
                rotation_price_m_per_rad=float(beta[2] / beta[1]),
                rotation_price_ci95_bootstrap_layouts=[q(lams, 0.025), q(lams, 0.975)] if lams else None,
                r2=1 - float((res ** 2).sum()) / ss_tot if ss_tot else None,
                rmse_s=float(np.sqrt((res ** 2).mean())), max_abs_residual_s=float(np.abs(res).max()),
                residual_quantiles_s={p: q(res, float(p)) for p in ("0.05", "0.25", "0.5", "0.75", "0.95")},
                loo_layout_rmse_s=float(np.sqrt((cv ** 2).mean())),
                loo_layout_max_abs_s=float(np.abs(cv).max()),
                largest_residuals=[dict(rows[i], residual_s=float(res[i]))
                                   for i in np.argsort(-np.abs(res))[:5]])


def decision_check(layouts: list[dict], lams=CHECK_LAMS) -> dict:
    """On layouts with two or more executed cup routes at lambda 0.05 (same
    transits for every cup route), which cup route minimises the measured
    whole-kit execution time, and which one the cost L + lambda*R picks."""
    cases = []
    for lay in layouts:
        alts = [r for r in lay["runs"] if r.get("lam") == 0.05 and r.get("status") == "completed"]
        if len({r["cup_route"] for r in alts}) < 2:
            continue
        rows = []
        for r in alts:
            seg = next(s for s in r["segments"] if s["kind"] == "transport" and s["object_id"] == CUP)
            rows.append(dict(route=r["cup_route"], mode=r["cup_mode"], L=seg["length_m"], R=seg["rotation_rad"],
                             exec_s=r["exec_s"], cup_route_s=seg["measured_s"]))
        fastest = min(rows, key=lambda x: (x["exec_s"], x["route"]))
        case = dict(layout=f'{lay["family"]}:{lay["seed"]}', alternatives=rows, fastest=fastest["route"],
                    fastest_tilted=fastest["mode"] != "upright", picks={})
        for lam in lams:
            pick = min(rows, key=lambda x: (x["L"] + lam * x["R"], x["route"]))
            case["picks"][lam_key(lam)] = dict(route=pick["route"], tilted=pick["mode"] != "upright",
                                              regret_s=round(pick["exec_s"] - fastest["exec_s"], 4))
        cases.append(case)
    summ = {}
    for lam in lams:
        k = lam_key(lam)
        summ[k] = dict(agree=sum(c["picks"][k]["route"] == c["fastest"] for c in cases), cases=len(cases),
                       picks_tilted=sum(c["picks"][k]["tilted"] for c in cases),
                       total_regret_s=round(sum(c["picks"][k]["regret_s"] for c in cases), 3),
                       max_regret_s=round(max([c["picks"][k]["regret_s"] for c in cases] or [0.0]), 3))
    return dict(cases=cases, by_lambda=summ, fastest_is_tilted=sum(c["fastest_tilted"] for c in cases),
                note="regret = whole-kit execution time of the picked cup route minus the fastest executed one")


def run(out_dir: Path, workers: int = 1) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = [(s, fam) for fam, seeds in DEV_SEEDS.items() for s in seeds]
    t0 = time.time()
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            layouts = list(ex.map(_layout_job, jobs))
    else:
        layouts = [_layout_job(j) for j in jobs]
    samples = _samples(layouts)
    fits = {}
    for kind in ("transport", "transit"):
        rows = [r for r in samples if r["kind"] == kind]
        fits[kind] = {m: fit(rows, m) for m in ("A", "B")}
    lam_rec = fits["transport"]["A"]["rotation_price_m_per_rad"]
    dec = decision_check(layouts, CHECK_LAMS + (round(lam_rec, 4),))
    failed = [dict(layout=f'{l["family"]}:{l["seed"]}', lam=r.get("lam"), forced=r.get("forced"),
                   status=r.get("status"), reason=r.get("reason", "")) for l in layouts for r in l["runs"]
              if r.get("status") not in ("completed", None)]
    res = dict(
        objective_of_record=dict(
            rotation_penalty_m_per_rad=round(lam_rec, 4),
            source="model A on transport segments: rotation_penalty = b / a",
            ci95_bootstrap_layouts=fits["transport"]["A"]["rotation_price_ci95_bootstrap_layouts"],
            transit_model_A_price_m_per_rad=round(fits["transit"]["A"]["rotation_price_m_per_rad"], 4)),
        dev_layouts=dict(seeds=DEV_SEEDS, split="dev", count=len(layouts)),
        executed_runs=sum(1 for l in layouts for r in l["runs"] if "status" in r and "segments" in r),
        failed_runs=failed, samples=dict(transport=sum(r["kind"] == "transport" for r in samples),
                                         transit=sum(r["kind"] == "transit" for r in samples)),
        fits=fits, decision_check={k: v for k, v in dec.items() if k != "cases"},
        controller="mirror.sim.SimEnv seed 0, standard executor (trapezoidal per-segment timing)",
        wall_s=round(time.time() - t0, 1))
    (out_dir / "layouts.json").write_text(json.dumps(layouts, indent=1))
    (out_dir / "samples.json").write_text(json.dumps(samples, indent=1))
    (out_dir / "decision_cases.json").write_text(json.dumps(dec["cases"], indent=1))
    (out_dir / "calibration.json").write_text(json.dumps(res, indent=1))
    return res


def posthoc(samples: list[dict]) -> dict:
    """POST HOC diagnostics, added after the calibration output was seen; they
    do not change the objective of record. Model A's largest residuals were
    the marker's routes (slow, long place descents), so: (1) model A with one
    intercept per object (candidates compete only within an object), and
    (2) model A on the cup's transports alone."""
    tr = [r for r in samples if r["kind"] == "transport"]
    objs = sorted({r["object_id"] for r in tr})
    X = np.column_stack([[float(r["object_id"] == o) for r in tr] for o in objs]
                        + [[r["L"] for r in tr], [r["R"] for r in tr]])
    y = np.array([r["T"] for r in tr])
    beta = _ols(X, y)
    res = y - X @ beta
    a, b = beta[len(objs)], beta[len(objs) + 1]
    fe = dict(intercepts_s={o: float(v) for o, v in zip(objs, beta[:len(objs)])}, a_s_per_m=float(a),
              b_s_per_rad=float(b), rotation_price_m_per_rad=float(b / a),
              r2=1 - float((res ** 2).sum()) / float(((y - y.mean()) ** 2).sum()),
              rmse_s=float(np.sqrt((res ** 2).mean())), max_abs_residual_s=float(np.abs(res).max()))
    cup = fit([r for r in tr if r["object_id"] == CUP], "A")
    cup.pop("largest_residuals", None)
    return dict(label="post hoc; not the objective of record", object_fixed_effects=fe, cup_only_model_A=cup)
