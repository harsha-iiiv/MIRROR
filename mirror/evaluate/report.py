"""Write evaluation reports: JSON, Markdown and figures."""
from __future__ import annotations

from pathlib import Path

from .checker import GROUND_TRUTH_USE
from .models import EvalReport


def write_report(report: EvalReport, out_dir: Path | str, *, write_json: bool = True) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {"markdown": out / "report.md"}
    if write_json:
        paths["json"] = out / "report.json"
        paths["json"].write_text(report.model_dump_json())
    (out / "manifest.json").write_text(report.manifest.model_dump_json(indent=1))
    figs = _figures(report, out)
    paths.update(figs)
    paths["markdown"].write_text(markdown(report, figs))
    return paths


def _fmt_ci(ci) -> str:
    lo, hi = ci
    if lo != lo:  # nan
        return "n/a"
    return f"[{lo:.2f}, {hi:.2f}]"


def markdown(r: EvalReport, figs: dict | None = None) -> str:
    figs = figs or {}
    ca, cb = r.config["conditions"]
    ma, mb = r.metrics[ca], r.metrics[cb]
    L = [f"# {r.title}", ""]
    L += [f"Split: **{r.split}**, {len(r.trials)} paired scenes, {2 * len(r.trials)} trials. "
          f"Every attempt is listed below.", ""]
    m = r.manifest
    L += ["## Freeze", "",
          f"- Manifest created {m.created_at}" + (" (written before layouts were generated)" if r.split == "eval" else ""),
          f"- Spec A `{m.spec_a['spec_id']}` v{m.spec_a['version']} sha256 `{m.spec_a['sha256'][:16]}`",
          f"- Spec B `{m.spec_b['spec_id']}` v{m.spec_b['version']} sha256 `{m.spec_b['sha256'][:16]}`",
          f"- Code sha256 `{m.code_sha256[:16]}` over {m.code_files} source files",
          f"- Seeds: {', '.join(map(str, m.seeds))}", ""]
    L += ["## Corrected rules (enforced in B, not in A)", ""]
    if r.corrected_rules:
        for c in r.corrected_rules:
            L.append(f"- `{c['id']}`: {c['type']} on {_sel(c['applies_to'])} during {c['phase']}, "
                     f"params {c['params']}")
    else:
        L.append("- none")
    L.append("")
    def f(x, spec="{:.1f}"):
        return "n/a" if x is None else spec.format(x)

    rows = [
        ("Trials executed in simulation", f"{ma.executed}/{ma.n}", f"{mb.executed}/{mb.n}"),
        ("Task completion", f"{ma.completion}/{ma.n}", f"{mb.completion}/{mb.n}"),
        ("Violation-free success", f"{ma.violation_free_success}/{ma.n}", f"{mb.violation_free_success}/{mb.n}"),
        ("Trials violating corrected rules", f"{ma.corrected_rule_violations}", f"{mb.corrected_rule_violations}"),
        ("Max tilt of corrected objects, mean / max (deg)", f"{f(ma.max_tilt_deg_mean)} / {f(ma.max_tilt_deg_max)}",
         f"{f(mb.max_tilt_deg_mean)} / {f(mb.max_tilt_deg_max)}"),
        ("Time over tolerance, mean (s)", f(ma.over_tol_s_mean, "{:.2f}"), f(mb.over_tol_s_mean, "{:.2f}")),
        ("Beads lost, total (mean per executed trial)", f"{ma.beads_lost_total} ({f(ma.beads_lost_mean)})",
         f"{mb.beads_lost_total} ({f(mb.beads_lost_mean)})"),
        ("Trials with forbidden contacts", f"{ma.forbidden_contact_trials}", f"{mb.forbidden_contact_trials}"),
        ("Trials with any obstacle contact (spec-independent)", f"{ma.obstacle_contact_trials}",
         f"{mb.obstacle_contact_trials}"),
        ("Planning failures", f"{ma.planning_failures}", f"{mb.planning_failures}"),
        ("Execution failures", f"{ma.execution_failures}", f"{mb.execution_failures}"),
        ("Timeouts", f"{ma.timeouts}", f"{mb.timeouts}"),
        ("Planning time, mean (s)", f"{ma.planning_time_s_mean:.3f}", f"{mb.planning_time_s_mean:.3f}"),
        ("Execution time, mean simulated (s)", f(ma.execution_time_s_mean), f(mb.execution_time_s_mean)),
        ("Human interventions", f"{ma.interventions}", f"{mb.interventions}"),
        ("Feasible scenes wrongly rejected (of known feasible)",
         f"{ma.feasible_scenes_wrongly_rejected} of {ma.feasible_scenes_known}",
         f"{mb.feasible_scenes_wrongly_rejected} of {mb.feasible_scenes_known}"),
    ]
    L += ["## Metrics (reported separately, not combined)", "",
          f"| Metric | A: {ca} | B: {cb} |", "|---|---|---|"]
    L += [f"| {a} | {b} | {c} |" for a, b, c in rows]
    L.append("")
    p = r.paired
    v = p["violation_free_success"]
    ce = p.get("correction_exercised", {})
    L += ["## Paired statistics", "",
          f"The correction was exercised in {ce.get('n', 0)} of {ce.get('of', len(r.trials))} scenes "
          f"(the original specification's rollout violated a corrected rule"
          + (f": seeds {', '.join(map(str, ce['seeds']))}" if ce.get("seeds") else "") + "). "
          "In the other scenes both versions may legitimately behave the same.", "",
          f"Violation-free success: A {v['rate_a']:.2f} (95% Wilson {_fmt_ci(v['rate_a_ci95'])}), "
          f"B {v['rate_b']:.2f} ({_fmt_ci(v['rate_b_ci95'])}). Difference B - A {v['diff']:+.2f}, "
          f"bootstrap 95% CI {_fmt_ci(v['diff_ci95_bootstrap'])}.", "",
          f"Pairs: both {v['both']}, only A {v['only_a']}, only B {v['only_b']}, neither {v['neither']}. "
          f"Exact McNemar p = {v['mcnemar_p_exact']:.4g}. {v['power_note']}.", ""]
    c = p["corrected_rule_compliance"]
    L += [f"Corrected-rule compliance (executed and no violation): A {c['rate_a']:.2f}, B {c['rate_b']:.2f}, "
          f"McNemar p = {c['mcnemar_p_exact']:.4g}.", ""]
    b = p["beads_lost"]
    L += [f"Beads lost per scene: mean difference B - A {b['mean_diff']:+.2f}, "
          f"bootstrap 95% CI {_fmt_ci(b['mean_diff_ci95_bootstrap'])}.", ""]
    k = p["previously_successful_kept"]
    L += [f"Previously successful scenes kept: {k['kept']} of {k['a_success']} scenes where A "
          f"succeeded without violating the corrected rules; {k['own_success_kept']} of "
          f"{k['a_own_spec_success']} scenes where A succeeded under its own specification also "
          f"succeeded under B.", ""]
    L += ["## Scope regression", "", r.scope["verdict"] + ".", ""]
    if r.scope["cases"]:
        L += ["| seed | object | max tilt A | max tilt B | still tilted |", "|---|---|---|---|---|"]
        L += [f"| {x['seed']} | {x['object_id']} | {x['tilt_a']:.1f} | {x['tilt_b']:.1f} | {x['still_tilted']} |"
              for x in r.scope["cases"]]
        L.append("")
    if r.blocked:
        L += ["## Blocked case", "", "| condition | status | classification | independent analysis |",
              "|---|---|---|---|"]
        for x in r.blocked:
            L.append(f"| {x['condition']} | {x['status']} | {x['failure_class'] or '-'} | "
                     f"{x['independent']['verdict']} |")
        for x in r.blocked:
            if x["independent"]["verdict"] == "proven_infeasible":
                L += ["", "Independent argument: " + x["independent"]["detail"]["argument"] + "."]
                break
        L.append("")
    L += ["## Every attempt", "",
          "| seed | scene | A status | A tilt | A beads lost | B status | B tilt | B beads lost | notes |",
          "|---|---|---|---|---|---|---|---|---|"]
    for t in r.trials:
        note = "; ".join(x for x in (t.a.failure_class, t.b.failure_class) if x)
        L.append(f"| {t.seed} | {t.scene_id} | {t.a.report.status} | {t.a.corrected_max_tilt_deg:.0f} | "
                 f"{t.a.beads_lost} | {t.b.report.status} | {t.b.corrected_max_tilt_deg:.0f} | "
                 f"{t.b.beads_lost} | {note} |")
    L.append("")
    if figs:
        L += ["## Figures", ""]
        for name, path in figs.items():
            L.append(f"![{name}]({Path(path).name})")
        L.append("")
    L += ["## Where simulator ground truth is used", ""] + [f"- {g}" for g in GROUND_TRUTH_USE] + [""]
    L += ["## Simplifications", ""] + [f"- {s}" for s in r.simplifications] + [""]
    if r.notes:
        L += ["## Notes", ""] + [f"- {n}" for n in r.notes] + [""]
    L += ["Finite simulated trials; not formal verification and not evidence of physical deployment.", ""]
    return "\n".join(L)


def _sel(s: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in s.items() if v is not None) or "any object"


def _figures(r: EvalReport, out: Path) -> dict[str, Path]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return {}
    ca, cb = r.config["conditions"]
    figs: dict[str, Path] = {}
    ink, accent, mute = "#27272a", "#c2410c", "#a1a1aa"
    plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False})
    tol = min([float(c["params"].get("max_tilt_deg", 15.0)) for c in r.corrected_rules
               if c["type"] == "upright"] or [15.0])

    seeds = [t.seed for t in r.trials]
    if seeds:
        fig, ax = plt.subplots(figsize=(max(4.0, 0.5 * len(seeds) + 2), 3.0))
        for i, t in enumerate(r.trials):
            ax.plot([i - 0.15, i + 0.15], [t.a.corrected_max_tilt_deg, t.b.corrected_max_tilt_deg],
                    color=mute, lw=0.8)
            ax.scatter([i - 0.15], [t.a.corrected_max_tilt_deg], color=ink, s=18, zorder=3,
                       label=ca if i == 0 else None)
            ax.scatter([i + 0.15], [t.b.corrected_max_tilt_deg], color=accent, s=18, zorder=3,
                       label=cb if i == 0 else None)
        ax.axhline(tol, color=accent, lw=0.8, ls="--")
        ax.set_xticks(range(len(seeds)), [str(s) for s in seeds], rotation=45 if len(seeds) > 10 else 0)
        ax.set_xlabel("seed")
        ax.set_ylabel("max tilt while carried (deg)")
        ax.legend(frameon=False, loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=2)
        fig.tight_layout()
        figs["paired_tilt"] = out / "paired_tilt.png"
        fig.savefig(figs["paired_tilt"], dpi=150)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(max(4.0, 0.5 * len(seeds) + 2), 3.0))
        x = range(len(seeds))
        ax.bar([i - 0.2 for i in x], [t.a.beads_lost for t in r.trials], width=0.4, color=ink, label=ca)
        ax.bar([i + 0.2 for i in x], [t.b.beads_lost for t in r.trials], width=0.4, color=accent, label=cb)
        ax.set_xticks(list(x), [str(s) for s in seeds], rotation=45 if len(seeds) > 10 else 0)
        ax.set_xlabel("seed")
        ax.set_ylabel("beads lost")
        ax.legend(frameon=False)
        fig.tight_layout()
        figs["beads_lost"] = out / "beads_lost.png"
        fig.savefig(figs["beads_lost"], dpi=150)
        plt.close(fig)

    statuses = ["success", "constraint_violated", "task_incomplete", "planning_failed",
                "execution_failed", "timeout"]
    colors = ["#16a34a", accent, "#eab308", "#71717a", "#dc2626", "#7c3aed"]
    fig, ax = plt.subplots(figsize=(4.5, 2.4))
    for row, cond in enumerate((ca, cb)):
        left = 0
        for s, col in zip(statuses, colors):
            n = r.metrics[cond].status_counts.get(s, 0)
            if n:
                ax.barh(row, n, left=left, color=col, label=s if s not in ax.get_legend_handles_labels()[1] else None)
                left += n
    ax.set_yticks([0, 1], [ca, cb])
    ax.set_xlabel("trials")
    ax.legend(frameon=False, fontsize=7, loc="upper center", bbox_to_anchor=(0.5, -0.35), ncol=3)
    fig.tight_layout()
    figs["outcomes"] = out / "outcomes.png"
    fig.savefig(figs["outcomes"], dpi=150)
    plt.close(fig)

    traced = [t for t in r.trials if t.a.tilt_trace or t.b.tilt_trace]
    # scenes where the correction mattered first, then the rest, up to 8 panels
    traced = sorted(traced, key=lambda t: (not t.a.corrected_violations and not t.b.corrected_violations,
                                           t.seed))[:8]
    if traced:
        cols = min(4, len(traced))
        rows_n = (len(traced) + cols - 1) // cols
        fig, axes = plt.subplots(rows_n, cols, figsize=(2.6 * cols, 1.9 * rows_n), squeeze=False,
                                 sharey=True)
        for ax, t in zip(axes.flat, traced):
            for rec, col in ((t.a, ink), (t.b, accent)):
                for oid, pts in rec.tilt_trace.items():
                    if pts:
                        ax.plot([p[0] for p in pts], [p[1] for p in pts], color=col, lw=0.9)
            ax.axhline(tol, color=accent, lw=0.6, ls="--")
            ax.set_title(f"seed {t.seed}", fontsize=8)
        for ax in list(axes.flat)[len(traced):]:
            ax.axis("off")
        fig.supxlabel("time (s)", fontsize=8)
        fig.supylabel("tilt (deg)", fontsize=8)
        fig.tight_layout()
        figs["tilt_traces"] = out / "tilt_traces.png"
        fig.savefig(figs["tilt_traces"], dpi=150)
        plt.close(fig)
    return figs


def _rng(seeds) -> str:
    seeds = sorted(seeds)
    if seeds and seeds == list(range(seeds[0], seeds[-1] + 1)) and len(seeds) > 2:
        return f"{seeds[0]}-{seeds[-1]}"
    return ", ".join(map(str, seeds))


def write_final_report(final, out_dir: Path | str) -> dict[str, Path]:
    """Combined JSON + Markdown, plus a per-stratum Markdown report with figures."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {"json": out / "report.json", "markdown": out / "report.md"}
    paths["json"].write_text(final.model_dump_json())
    (out / "manifest.json").write_text(final.manifest.model_dump_json(indent=1))
    for name, rep in final.strata.items():
        write_report(rep, out / name, write_json=False)
    paths["markdown"].write_text(final_markdown(final))
    return paths


def final_markdown(f) -> str:
    m = f.manifest
    L = [f"# {f.title}", ""] + [f"> {n}" for n in f.notes] + [""]
    L += ["## Pre-registration", "",
          f"- Freeze manifest written {m.created_at}, before any of these layouts was generated.",
          f"- Code sha256 `{m.code_sha256[:16]}` over {m.code_files} source files.",
          f"- Spec A (original) `{m.spec_a['spec_id']}` v{m.spec_a['version']} `{m.spec_a['sha256'][:16]}`; "
          f"spec B (corrected) v{m.spec_b['version']} `{m.spec_b['sha256'][:16]}`.", ""]
    p = f.spec_provenance or {}
    if p:
        L += [f"- Specifications: {p.get('source', '')}; interpreter `{p.get('interpreter')}`; "
              f"offline interpreter agrees: {p.get('offline_agrees')}.",
              f"- Correction text: \"{p.get('correction_text', '')}\"", ""]
    L += ["| stratum | role | split | family | seeds | n |", "|---|---|---|---|---|---|"]
    for st in m.strata:
        L.append(f"| {st['name']} | {st['role']} | {st['split']} | {st['family']} | {_rng(st['seeds'])} | "
                 f"{len(st['seeds'])} |")
    L.append("")
    names = list(f.strata)
    L += ["## Results side by side", "",
          "| Metric | " + " | ".join(f"{n}: A / B" for n in names) + " |",
          "|---|" + "---|" * len(names)]

    def row(label, fn):
        cells = []
        for n in names:
            r = f.strata[n]
            ca, cb = r.config["conditions"]
            cells.append(f"{fn(r.metrics[ca], r)} / {fn(r.metrics[cb], r)}")
        L.append(f"| {label} | " + " | ".join(cells) + " |")

    nz = lambda x, s="{:.1f}": "n/a" if x is None else s.format(x)
    row("Scenes", lambda c, r: str(c.n))
    row("Executed in simulation", lambda c, r: str(c.executed))
    row("Task completion", lambda c, r: str(c.completion))
    row("Violation-free success", lambda c, r: str(c.violation_free_success))
    row("Trials violating corrected rule", lambda c, r: str(c.corrected_rule_violations))
    row("Max corrected-object tilt (deg)", lambda c, r: nz(c.max_tilt_deg_max))
    row("Beads lost (total)", lambda c, r: str(c.beads_lost_total))
    row("Obstacle-contact trials", lambda c, r: str(c.obstacle_contact_trials))
    row("Planning failures", lambda c, r: str(c.planning_failures))
    row("Execution failures", lambda c, r: str(c.execution_failures))
    row("Timeouts", lambda c, r: str(c.timeouts))
    row("Feasible scenes wrongly rejected", lambda c, r: f"{c.feasible_scenes_wrongly_rejected}/{c.feasible_scenes_known}")
    L.append("")
    L += ["## Paired statistics per stratum", ""]
    for n in names:
        r = f.strata[n]
        v = r.paired["violation_free_success"]
        ce = r.paired.get("correction_exercised", {})
        L += [f"**{n}** ({f.roles.get(n, '')}): correction exercised in {ce.get('n', 0)}/{ce.get('of', 0)} "
              f"scenes. Violation-free success A {v['rate_a']:.2f}, B {v['rate_b']:.2f}; "
              f"difference {v['diff']:+.2f} (bootstrap 95% CI {_fmt_ci(v['diff_ci95_bootstrap'])}); "
              f"pairs only-A {v['only_a']}, only-B {v['only_b']}; exact McNemar p = {v['mcnemar_p_exact']:.4g}. "
              f"{v['power_note']}. Scope: {r.scope['verdict']}. [Details]({n}/report.md)", ""]
    blk = [n for n in names if f.roles.get(n) == "blocked"]
    for n in blk:
        r = f.strata[n]
        L += [f"## Blocked stratum ({n})", "",
              "| seed | A status | B status | B classification | independent analysis (B) |",
              "|---|---|---|---|---|"]
        for t in r.trials:
            L.append(f"| {t.seed} | {t.a.report.status} | {t.b.report.status} | {t.b.failure_class or '-'} | "
                     f"{t.b.feasibility} |")
        L.append("")
    if f.blocked_reference:
        L += ["## Blocked reference scene", "", "| condition | status | classification | independent |",
              "|---|---|---|---|"]
        for x in f.blocked_reference:
            L.append(f"| {x['condition']} | {x['status']} | {x['failure_class'] or '-'} | "
                     f"{x['independent']['verdict']} |")
        L.append("")
    L += [f"Wall time {f.wall_time_s:.0f} s. Finite simulated trials; not formal verification and not "
          f"evidence of physical deployment.", ""]
    return "\n".join(L)
