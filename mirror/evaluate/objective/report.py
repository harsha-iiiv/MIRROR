"""Report for registration objective_v1, computed from an attempt's records only."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from mirror.evaluate.stats import bootstrap_ci, paired_binary, paired_continuous

N_BOOT = 10_000


def load(out: Path, attempt: str) -> tuple[dict, list[dict], dict]:
    manifest = json.loads((out / "freeze.json").read_text())
    adir = out / attempt
    recs = [json.loads(l) for l in (adir / "records.jsonl").read_text().splitlines() if l.strip()]
    meta = json.loads((adir / "attempt.json").read_text())
    return manifest, recs, meta


def _t(r):
    return r["trial"]


def _vfs(r):
    return bool(_t(r)["violation_free_success"])


def _done(r):
    return bool(_t(r)["report"]["completion"])


def _exec(r):
    return float(_t(r)["report"]["execution_time_s"]) if _t(r)["report"]["physics_steps"] else None


def condition_metrics(rs: list[dict]) -> dict:
    n = len(rs)
    st = {}
    for r in rs:
        k = _t(r)["report"]["status"]
        st[k] = st.get(k, 0) + 1
    ex = [r for r in rs if _exec(r) is not None]
    comp = [r for r in rs if _done(r)]
    planned = [r for r in rs if r["plan_status"] == "ok"]
    mean = lambda xs: round(float(np.mean(xs)), 4) if xs else None
    return dict(
        n=n, completion=len(comp), success=sum(_t(r)["report"]["status"] == "success" for r in rs),
        violation_free_success=sum(_vfs(r) for r in rs),
        cup_tilted_planned=sum(r["cup_tilted_planned"] for r in rs),
        corrected_rule_violation_trials=sum(bool(_t(r)["corrected_violations"]) for r in rs),
        cup_max_transport_tilt_deg_mean=mean([_t(r)["corrected_max_tilt_deg"] for r in ex]),
        beads_total=sum(_t(r)["beads_total"] for r in rs), beads_lost=sum(_t(r)["beads_lost"] for r in rs),
        beads_retained=sum(r["beads_retained"] for r in rs),
        trials_losing_beads=sum(_t(r)["beads_lost"] > 0 for r in rs),
        planning_failures=sum(r["plan_status"] != "ok" for r in rs),
        planning_failures_proven=sum(r["proven_infeasible"] for r in rs),
        status_counts=st,
        exec_time_s_mean_completed=mean([_exec(r) for r in comp]),
        path_length_m_mean=mean([r["path_length_m"] for r in planned]),
        path_rotation_rad_mean=mean([r["path_rotation_rad"] for r in planned]),
        path_cost_calibrated_mean=mean([r["path_cost_calibrated"] for r in planned]),
        obstacle_contact_trials=sum(bool(_t(r)["obstacle_contacts"]) for r in rs))


def _by(recs, stratum):
    d = {}
    for r in recs:
        if r["stratum"] == stratum:
            d.setdefault(r["condition"], {})[r["seed"]] = r
    return d


def _paired_time(A: dict, B: dict) -> dict:
    """Execution time B - A on seeds where both completed."""
    seeds = sorted(s for s in A if s in B and _done(A[s]) and _done(B[s]))
    if not seeds:
        return dict(n=0)
    out = paired_continuous([_exec(A[s]) for s in seeds], [_exec(B[s]) for s in seeds], n_boot=N_BOOT)
    d = [_exec(B[s]) - _exec(A[s]) for s in seeds]
    out.update(b_faster=sum(x < -1e-9 for x in d), equal=sum(abs(x) <= 1e-9 for x in d), b_slower=sum(x > 1e-9 for x in d),
               note="B minus A, seeds where both completed")
    return out


def _paired_cost(A: dict, B: dict) -> dict:
    seeds = sorted(s for s in A if s in B and A[s]["plan_status"] == "ok" and B[s]["plan_status"] == "ok")
    if not seeds:
        return dict(n=0)
    return paired_continuous([A[s]["path_cost_calibrated"] for s in seeds],
                             [B[s]["path_cost_calibrated"] for s in seeds], n_boot=N_BOOT)


def _binary(A: dict, B: dict, f=_vfs) -> dict:
    seeds = sorted(set(A) & set(B))
    return paired_binary([f(A[s]) for s in seeds], [f(B[s]) for s in seeds], n_boot=N_BOOT)


def retained_benefit(by: dict, cal_key: str) -> dict:
    oc, cc = by[f"original@{cal_key}"], by[f"corrected@{cal_key}"]
    o5, c5 = by["original@0.05"], by["corrected@0.05"]
    seeds = sorted(set(oc) & set(cc) & set(o5) & set(c5))
    num = np.array([_vfs(cc[s]) - _vfs(oc[s]) for s in seeds], float)
    den = np.array([_vfs(c5[s]) - _vfs(o5[s]) for s in seeds], float)
    if den.sum() == 0:
        return dict(n=len(seeds), gap_cal=float(num.mean()), gap_005=0.0, retained=None,
                    note="no benefit at 0.05 in this stratum; ratio undefined")
    rng = np.random.default_rng(0)
    idx = rng.integers(0, len(seeds), size=(N_BOOT, len(seeds)))
    dens = den[idx].sum(1)
    ratios = num[idx].sum(1)[dens > 0] / dens[dens > 0]
    return dict(n=len(seeds), gap_cal=float(num.mean()), gap_005=float(den.mean()),
                retained=float(num.sum() / den.sum()),
                retained_ci95_bootstrap=[float(np.quantile(ratios, 0.025)), float(np.quantile(ratios, 0.975))],
                note="(VFS corrected@cal - VFS original@cal) / (VFS corrected@0.05 - VFS original@0.05)")


def tradeoffs(by: dict, a: str, b: str) -> dict:
    A, B = by[a], by[b]
    seeds = sorted(set(A) & set(B))
    return dict(a=a, b=b, only_a_completes=[s for s in seeds if _done(A[s]) and not _done(B[s])],
                only_b_completes=[s for s in seeds if _done(B[s]) and not _done(A[s])],
                only_a_vfs=[s for s in seeds if _vfs(A[s]) and not _vfs(B[s])],
                only_b_vfs=[s for s in seeds if _vfs(B[s]) and not _vfs(A[s])])


def analyse(manifest: dict, recs: list[dict]) -> dict:
    cal = manifest["calibrated_key"]
    conds = manifest["conditions"]
    out = dict(calibrated_rotation_penalty=manifest["calibrated_rotation_penalty"], calibrated_key=cal, strata={})
    for st in manifest["strata"]:
        by = _by(recs, st["name"])
        missing = {c: sorted(set(st["seeds"]) - set(by.get(c, {}))) for c in conds}
        S = dict(seeds=len(st["seeds"]), missing={c: m for c, m in missing.items() if m},
                 metrics={c: condition_metrics(list(by.get(c, {}).values())) for c in conds})
        if all(by.get(c) for c in conds):
            oc, cc, nt = f"original@{cal}", f"corrected@{cal}", f"never_tilt@{cal}"
            S["vfs"] = {"corrected@cal vs original@cal": _binary(by[oc], by[cc]),
                        "corrected@0.05 vs original@0.05": _binary(by["original@0.05"], by["corrected@0.05"])}
            for c in conds:
                if c.startswith("original@") and c != oc:
                    S["vfs"][f"corrected@cal vs {c}"] = _binary(by[c], by[cc])
            S["vfs"]["never_tilt@cal vs original@cal"] = _binary(by[oc], by[nt])
            S["completion"] = {f"{c} vs corrected@cal": _binary(by[cc], by[c], _done) for c in conds if c != cc}
            S["retained_benefit"] = retained_benefit(by, cal)
            S["exec_time"] = {"corrected@cal - original@cal": _paired_time(by[oc], by[cc]),
                              "never_tilt@cal - corrected@cal": _paired_time(by[cc], by[nt]),
                              "never_tilt@cal - original@cal": _paired_time(by[oc], by[nt]),
                              "corrected@0.05 - original@0.05": _paired_time(by["original@0.05"], by["corrected@0.05"]),
                              "corrected@cal - corrected@0.05": _paired_time(by["corrected@0.05"], by[cc])}
            S["path_cost_calibrated"] = {"corrected@cal - original@cal": _paired_cost(by[oc], by[cc]),
                                         "never_tilt@cal - corrected@cal": _paired_cost(by[cc], by[nt]),
                                         "original@0.05 - original@cal": _paired_cost(by[oc], by["original@0.05"])}
            S["tradeoffs"] = [tradeoffs(by, cc, nt), tradeoffs(by, oc, nt), tradeoffs(by, oc, cc),
                              tradeoffs(by, "original@1.0", nt)]
            # identical plans across conditions (the executor is deterministic, so identical plans
            # give identical outcomes; reported so readers can see which conditions coincide)
            S["identical_plans"] = {f"{x} == {y}": sum(by[x][s]["plan_sha256"] is not None and
                                                       by[x][s]["plan_sha256"] == by[y][s]["plan_sha256"]
                                                       for s in by[x] if s in by[y])
                                    for x, y in [(oc, "original@0.05"), (oc, "original@0.10"), (oc, "original@0.20"),
                                                 (oc, cc), (cc, "corrected@0.05"), (cc, nt), ("original@1.0", nt)]}
        out["strata"][st["name"]] = S
    return out


def _fmt(x, nd=2):
    return "–" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def markdown(manifest: dict, meta: dict, res: dict, conclusions: str = "") -> str:
    cal = res["calibrated_key"]
    L = [f"# Registration objective_v1: planner objective and baselines ({meta['attempt']})", ""]
    if conclusions:
        L += [conclusions.strip(), ""]
    L += [f"Frozen {manifest['created_at']} (commit of `freeze.json` precedes every layout); run from "
          f"`{meta.get('git_head', '')[:12]}`, attempt `{meta['attempt']}`, status {meta['status']}.",
          f"Calibrated rotation price (objective of record): **{manifest['calibrated_rotation_penalty']} m/rad** "
          f"(from development layouts only; see calibration/calibration.json).", ""]
    for name, S in res["strata"].items():
        L += [f"## Stratum: {name} ({S['seeds']} layouts)", ""]
        if S.get("missing"):
            L += [f"Missing records: {S['missing']}", ""]
        L += ["| condition | completed | violation-free | cup tilted (planned) | beads lost (trials) | "
              "planning failures (proven) | exec time s (completed) | path cost @cal |",
              "|---|---|---|---|---|---|---|---|"]
        for c, m in S["metrics"].items():
            L.append(f"| {c} | {m['completion']}/{m['n']} | {m['violation_free_success']}/{m['n']} | "
                     f"{m['cup_tilted_planned']} | {m['beads_lost']} ({m['trials_losing_beads']}) | "
                     f"{m['planning_failures']} ({m['planning_failures_proven']}) | "
                     f"{_fmt(m['exec_time_s_mean_completed'])} | {_fmt(m['path_cost_calibrated_mean'], 3)} |")
        L.append("")
        if "vfs" in S:
            L += ["Violation-free success, paired (A vs B; only_a / only_b; exact McNemar p):", ""]
            for k, v in S["vfs"].items():
                L.append(f"- {k}: {v['only_a']} / {v['only_b']}, diff {v['diff']:+.3f} "
                         f"[{v['diff_ci95_bootstrap'][0]:+.3f}, {v['diff_ci95_bootstrap'][1]:+.3f}], p = {v['mcnemar_p_exact']:.3g}")
            rb = S["retained_benefit"]
            L += ["", f"Retained benefit at the calibrated price: "
                      + (f"{rb['retained']:.3f} [{rb['retained_ci95_bootstrap'][0]:.3f}, {rb['retained_ci95_bootstrap'][1]:.3f}]"
                         if rb.get("retained") is not None else rb["note"])
                      + f" (gap {rb['gap_cal']:+.3f} at cal vs {rb['gap_005']:+.3f} at 0.05)", ""]
            L += ["Execution time, paired (seeds where both completed):", ""]
            for k, v in S["exec_time"].items():
                if v.get("n"):
                    L.append(f"- {k}: mean {v['mean_diff']:+.2f} s [{v['mean_diff_ci95_bootstrap'][0]:+.2f}, "
                             f"{v['mean_diff_ci95_bootstrap'][1]:+.2f}] over {v['n']} (faster {v['b_faster']}, "
                             f"equal {v['equal']}, slower {v['b_slower']})")
            L += ["", "Trade-offs (seeds):", ""]
            for t in S["tradeoffs"]:
                L.append(f"- {t['a']} vs {t['b']}: only {t['a']} completes {t['only_a_completes']}; "
                         f"only {t['b']} completes {t['only_b_completes']}")
            L += ["", f"Identical plans: {S['identical_plans']}", ""]
    return "\n".join(L) + "\n"


def write(out: Path, *, attempt: str) -> dict:
    out = Path(out)
    manifest, recs, meta = load(out, attempt)
    res = analyse(manifest, recs)
    res["attempt"] = meta
    concl = out / attempt / "conclusions.md"
    (out / attempt / "report.json").write_text(json.dumps(res, indent=1))
    (out / attempt / "report.md").write_text(markdown(manifest, meta, res, concl.read_text() if concl.exists() else ""))
    return res
