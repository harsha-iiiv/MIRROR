"""Summaries per condition and paired comparisons. Pure functions of the
results and the attempt ledger, so a report can be rebuilt at any time."""
from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

from mirror.evaluate.stats import mcnemar_exact, wilson_ci


def _med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 3) if xs else None


def load(out: Path) -> tuple[list[dict], list[dict]]:
    res = [json.loads(x) for x in (out / "results.jsonl").read_text().splitlines() if x.strip()] \
        if (out / "results.jsonl").exists() else []
    led = [json.loads(x) for x in (out / "attempts.jsonl").read_text().splitlines() if x.strip()] \
        if (out / "attempts.jsonl").exists() else []
    return res, led


def latest(results: list[dict]) -> dict[tuple[str, str], dict]:
    by: dict[tuple[str, str], dict] = {}
    for r in sorted(results, key=lambda r: r.get("attempt", 0)):
        by[(r["case_id"], r["condition"])] = r
    return by


def per_condition(results: list[dict], ledger: list[dict], n_cases: Optional[int] = None,
                  track: Optional[str] = None) -> dict[str, Any]:
    """``track`` restricts to authoring or language cases (results and ledger)."""
    if track is not None:
        keep = {r["case_id"] for r in results if r.get("track", "authoring") == track}
        results = [r for r in results if r["case_id"] in keep]
        ledger = [e for e in ledger if e.get("case_id") in keep]
    by = latest(results)
    conds = sorted({c for _, c in by} | {e["condition"] for e in ledger})
    statuses: dict[str, Counter] = defaultdict(Counter)
    for e in ledger:
        if e["event"] == "end":
            statuses[e["condition"]][e["status"]] += 1
    out = {}
    for cond in conds:
        rows = [r for (cid, c), r in by.items() if c == cond]
        all_turns = [t for r in rows for t in r["turns"]]
        turns = [t for t in all_turns if t["outcome"] != "not_measured"]
        sc = [t["score"] for t in turns]
        apply_t = [s for s in sc if s["gold_outcome"] == "apply"]
        refuse_t = [s for s in sc if s["gold_outcome"] != "apply"]
        e2e = [r["end_to_end_success"] for r in rows]
        exe = [r["execution"] for r in rows if r.get("execution")]
        gold_ok = [x for x in exe if x.get("gold")]
        by_cat: dict[str, Counter] = defaultdict(Counter)
        for r in rows:
            c = by_cat[r["category"]]
            c["cases"] += 1
            c["end_to_end_success"] += int(r["end_to_end_success"] is True)
            c["final_state_exact"] += int(r["final_state_exact"])
        tags: dict[str, Counter] = defaultdict(Counter)
        for r in rows:
            for tag in r.get("tags", []):
                tags[tag]["cases"] += 1
                tags[tag]["final_state_exact"] += int(r["final_state_exact"])
                tags[tag]["turns"] += len(r["turns"])
                tags[tag]["turn_exact"] += sum(t["score"]["exact"] for t in r["turns"])
        speech: dict[str, Counter] = defaultdict(Counter)
        for t in turns:
            speech[t.get("speech", "typed")]["turns"] += 1
            speech[t.get("speech", "typed")]["exact"] += int(t["score"]["exact"])
        k_ok = sum(1 for x in e2e if x is True)
        n_known = sum(1 for x in e2e if x is not None)
        rows_m = [r for r in rows if r.get("measured", True)]
        out[cond] = {
            "attempt_status": dict(statuses.get(cond, {})),
            "cases_with_result": len(rows),
            "turns": len(turns),
            "not_measured_turns": len(all_turns) - len(turns),
            "turn_exact": sum(s["exact"] for s in sc),
            "acceptable_alternatives": sum(s.get("acceptable_alternative", False) for s in sc),
            "apply_turns": len(apply_t), "apply_exact": sum(s["exact"] for s in apply_t),
            "refusal_turns": len(refuse_t), "refusal_exact": sum(s["exact"] for s in refuse_t),
            "refused_any_type": sum(s["refused_any"] for s in refuse_t),
            "false_accepts": sum(s["false_accept"] for s in sc),
            "false_refusals": sum(s["false_refusal"] for s in sc),
            "wrong_scope_turns": sum(1 for s in sc if s["wrong_scope_kinds"]),
            "clarification_turns": sum(1 for t in turns if t["outcome"] == "asks"),
            "invalid_or_error_turns": sum(1 for t in turns if t["outcome"] in ("invalid", "error")),
            "final_state_exact": sum(r["final_state_exact"] for r in rows_m),
            "cases_measured": len(rows_m),
            "end_to_end_success": k_ok, "end_to_end_known": n_known,
            "end_to_end_unknown": sum(1 for x in e2e if x is None),
            "end_to_end_wilson95": [round(v, 3) for v in wilson_ci(k_ok, n_known)] if n_known else None,
            "executed": len(exe),
            "gold_violations": sum(1 for x in gold_ok if x["gold"]["violations"]),
            "gold_completion": sum(1 for x in gold_ok if x["gold"]["completion"]),
            "proven_infeasible_stops": sum(1 for x in exe if x["proven_infeasible"]),
            "turn_latency_s_median": _med(t["latency_s"] for t in turns),
            "planning_time_s_median": _med(x["planning_time_s"] for x in exe),
            "sim_time_s_median": _med(x["sim_time_s"] for x in exe),
            "path_length_m_median": _med(x["path_length_m"] for x in exe),
            "cost_usd": round(sum(r.get("cost_usd", 0.0) for r in rows), 4),
            "network_calls": sum(t["network_calls"] for t in turns),
            "by_category": {k: dict(v) for k, v in sorted(by_cat.items())},
            "by_input": {k: dict(v) for k, v in sorted(speech.items())},
            "by_tag": {k: dict(v) for k, v in sorted(tags.items())},
        }
    if n_cases is not None:
        for v in out.values():
            v["cases_planned"] = n_cases
    return out


def paired(results: list[dict], a: str, b: str) -> dict[str, Any]:
    """Exact McNemar on case-level end-to-end success; cases unknown in either
    condition are counted and excluded from the test, never imputed."""
    by = latest(results)
    ids = sorted({cid for cid, _ in by})
    only_a = only_b = both = neither = unknown = 0
    for cid in ids:
        ra, rb = by.get((cid, a)), by.get((cid, b))
        va = ra["end_to_end_success"] if ra else None
        vb = rb["end_to_end_success"] if rb else None
        if va is None or vb is None:
            unknown += 1
        elif va and vb:
            both += 1
        elif va:
            only_a += 1
        elif vb:
            only_b += 1
        else:
            neither += 1
    return {"a": a, "b": b, "both": both, "only_a": only_a, "only_b": only_b, "neither": neither,
            "unknown_or_missing": unknown,
            "mcnemar_p": mcnemar_exact(only_a, only_b) if (only_a + only_b) else None}
