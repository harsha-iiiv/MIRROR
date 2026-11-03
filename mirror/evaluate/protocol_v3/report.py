"""Markdown report of a protocol v3 run (rebuilt from results and ledger)."""
from __future__ import annotations

import json
from pathlib import Path

from .metrics import load, paired, per_condition

ROWS = [("Attempts by status", lambda s: ", ".join(f"{k} {v}" for k, v in sorted(s["attempt_status"].items())) or "-"),
        ("Turns exact (not measured)", lambda s: f"{s['turn_exact']}/{s['turns']} ({s['not_measured_turns']})"),
        ("Apply turns exact", lambda s: f"{s['apply_exact']}/{s['apply_turns']}"),
        ("Refusal turns exact (any refusal)", lambda s: f"{s['refusal_exact']}/{s['refusal_turns']} ({s['refused_any_type']})"),
        ("False accepts", lambda s: str(s["false_accepts"])),
        ("False refusals", lambda s: str(s["false_refusals"])),
        ("Wrong-scope turns", lambda s: str(s["wrong_scope_turns"])),
        ("Clarification turns", lambda s: str(s["clarification_turns"])),
        ("Invalid or error turns", lambda s: str(s["invalid_or_error_turns"])),
        ("Final specification exact (cases measured)", lambda s: f"{s['final_state_exact']}/{s['cases_measured']}"),
        ("End-to-end success (known; unknown)", lambda s: f"{s['end_to_end_success']}/{s['end_to_end_known']}; {s['end_to_end_unknown']}"),
        ("Executed / violations under gold / proven-infeasible stops",
         lambda s: f"{s['executed']} / {s['gold_violations']} / {s['proven_infeasible_stops']}"),
        ("Median turn latency (s)", lambda s: str(s["turn_latency_s_median"])),
        ("Median planning / sim time (s)", lambda s: f"{s['planning_time_s_median']} / {s['sim_time_s_median']}"),
        ("Median path length (m)", lambda s: str(s["path_length_m_median"])),
        ("Network calls / cost (USD)", lambda s: f"{s['network_calls']} / {s['cost_usd']}")]


def markdown(out: Path, *, title: str, label: str, n_cases: int) -> str:
    res, led = load(out)
    summ = per_condition(res, led, n_cases)
    conds = list(summ)
    lines = [f"# {title}", "", label, "",
             "| | " + " | ".join(conds) + " |", "|---|" + "---|" * len(conds)]
    for name, f in ROWS:
        lines.append(f"| {name} | " + " | ".join(f(summ[c]) for c in conds) + " |")
    lines += ["", "Paired end-to-end success (exact McNemar; unknown cases excluded and counted):", ""]
    for a, b in (("mirror_model", "offline_rules"), ("mirror_model", "manual_structured"),
                 ("offline_rules", "manual_structured")):
        if a in summ and b in summ:
            p = paired(res, a, b)
            lines.append(f"- {a} vs {b}: both {p['both']}, only {a} {p['only_a']}, only {b} {p['only_b']}, "
                         f"neither {p['neither']}, unknown {p['unknown_or_missing']}, p = {p['mcnemar_p']}")
    lines += ["", "By category (cases, end-to-end success, final spec exact):", ""]
    for c in conds:
        cats = summ[c]["by_category"]
        lines.append(f"- {c}: " + "; ".join(f"{k} {v.get('cases', 0)}/{v.get('end_to_end_success', 0)}/"
                                              f"{v.get('final_state_exact', 0)}" for k, v in cats.items()))
    lines += ["", "Failures (every non-exact turn):", ""]
    for r in sorted(res, key=lambda r: (r["condition"], r["case_id"])):
        for i, t in enumerate(r["turns"]):
            if not t["score"]["exact"] and t["outcome"] != "not_measured":
                tag = "refused (other type)" if t["score"]["refused_any"] else "failed"
                lines.append(f"- {tag}: {r['condition']} {r['case_id']} turn {i + 1}: got {t['outcome']}, gold "
                             f"{t['score']['gold_outcome']}" + (f", wrong scope {t['score']['wrong_scope_kinds']}"
                                                                if t["score"]["wrong_scope_kinds"] else "")
                             + (f" ({t['detail'][:160]})" if t.get("detail") else ""))
    nm = sorted({(r["condition"], r["case_id"]) for r in res for t in r["turns"] if t["outcome"] == "not_measured"})
    lines += ["", f"Not measured (replay had no recording for the request; excluded, never replaced): {len(nm)} case(s)"
              + (": " + ", ".join(f"{c} {i}" for c, i in nm) if nm else "")]
    old = out / "old_runs.json"
    if old.exists():
        rows = json.loads(old.read_text())
        ch = [x for x in rows if x.get("gold_changes_spec")]
        lines += ["", "Old behaviour re-checked under the gold specification (base-spec rollout on the same scene):",
                  "", f"- cases whose gold changes the enforced specification: {len(ch)}; base rollout passed under "
                      f"its own spec and fails under gold: {sum(1 for x in ch if x.get('rejected'))}; "
                      f"passes under both (the change did not matter in this scene): "
                      f"{sum(1 for x in ch if x.get('rejected') is False and x.get('under_gold') == 'success')}"]
        lines += [f"- {x['case_id']}: base {x.get('base_status')}, under gold {x.get('under_gold')}"
                  + (f", violated {x['violations']}" if x.get("violations") else "") for x in ch]
    bad = [e for e in led if e["event"] == "end" and e["status"] != "completed"]
    lines += ["", f"Attempts that did not complete: {len(bad)}", ""]
    lines += [f"- attempt {e['attempt']} {e['condition']} {e['case_id']}: {e['status']}"
              + (f" ({str(e.get('detail', ''))[:200]})" if e.get("detail") else "") for e in bad]
    return "\n".join(lines) + "\n"


def write(out: Path, **kw) -> Path:
    p = out / "report.md"
    p.write_text(markdown(out, **kw))
    res, led = load(out)
    (out / "summary.json").write_text(json.dumps(per_condition(res, led, kw.get("n_cases")), indent=2) + "\n")
    return p
