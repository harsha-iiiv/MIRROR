"""Scoring for the correction-compiler benchmark.

A case passes (exact match) when the outcome matches the gold outcome and, for
"apply", the resulting enforced constraints (id and params), confirmed
orderings and rejected goals equal the gold exactly.

Also reported:
  scope error     an applied result changes enforced constraints, goals or
                  orderings of an object kind the text does not mention and the gold leaves alone, or
                  adds an every-object rule the gold does not have
  wrong rule      a new version was produced and it is not the gold one
  false accept    gold is "asks" or "rejects" but a new version was produced
  false refusal   gold is "apply" but the interpreter asked or rejected
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

from mirror.contracts import SceneState, Status, TaskSpec
from mirror.interpret import (CorrectionRejected, InterpretError, LLMClient, NeedsClarification,
                              compile_correction)

BENCH_DIR = Path(__file__).resolve().parent


def load_cases(path: Optional[Path] = None) -> dict[str, Any]:
    p = path or BENCH_DIR / "cases.json"
    raw = p.read_bytes()
    doc = json.loads(raw)
    doc["sha256"] = hashlib.sha256(raw).hexdigest()
    return doc


def load_base_spec(path: Optional[Path] = None) -> TaskSpec:
    return TaskSpec.model_validate_json((path or BENCH_DIR / "base_spec.json").read_text())


def _kind_of(cid: str) -> str:
    return cid.rsplit("_", 1)[1]


def state_of(spec: TaskSpec) -> dict[str, Any]:
    return {
        "enforced": {c.id: _norm(c.params) for c in spec.enforced_constraints()},
        "ordering": sorted(o.id for o in spec.ordering if o.status == Status.confirmed),
        "inactive_goals": sorted(g.id for g in spec.goals if g.status == Status.rejected),
    }


def _norm(params: dict) -> dict:
    out = {}
    for k, v in params.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = round(float(v), 6)
        elif isinstance(v, list):
            out[k] = sorted(v)
        else:
            out[k] = v
    return out


def gold_state(gold: dict) -> dict[str, Any]:
    return {"enforced": {e["id"]: _norm(e["params"]) for e in gold["enforced"]},
            "ordering": sorted(gold.get("ordering", [])),
            "inactive_goals": sorted(gold.get("inactive_goals", []))}


def _changed_kinds(state: dict, base: dict) -> set[str]:
    """Object kinds whose enforced constraints, rejected goals or confirmed
    orderings differ between two states ("all" for every-object rules)."""
    a, b = state["enforced"], base["enforced"]
    kinds = {_kind_of(cid) for cid in set(a) | set(b) if a.get(cid) != b.get(cid)}
    for key in ("inactive_goals", "ordering"):
        for item in set(state[key]) ^ set(base[key]):
            if key == "inactive_goals":
                kinds.add(_kind_of(item))
            else:
                kinds |= {item.split("_")[1], item.split("_")[3]}
    return kinds


def mentioned_kinds(text: str) -> set[str]:
    from mirror.interpret.offline import KIND_WORDS
    import re
    t = text.lower()
    return {k for k, words in KIND_WORDS.items()
            if re.search(r"\b(" + "|".join(words) + r")\b", t)}


def score_case(case: dict, result: dict, base_state: dict) -> dict[str, Any]:
    gold = case["gold"]
    out = {"exact": False, "scope_errors": [], "false_accept": False, "false_refusal": False,
           "refused_correctly": False, "wrong_rule": False}
    named = mentioned_kinds(case.get("text", ""))
    if gold["outcome"] == "apply":
        if result["outcome"] == "apply":
            g = gold_state(gold)
            out["exact"] = result["state"] == g
            out["wrong_rule"] = not out["exact"]
            out["scope_errors"] = sorted(_changed_kinds(result["state"], base_state)
                                         - _changed_kinds(g, base_state) - named)
        elif result["outcome"] in ("asks", "rejects"):
            out["false_refusal"] = True
    else:
        if result["outcome"] == "apply":
            out["false_accept"] = True
            out["wrong_rule"] = True
            out["scope_errors"] = sorted(_changed_kinds(result["state"], base_state) - named)
        elif result["outcome"] in ("asks", "rejects"):
            out["refused_correctly"] = True
            out["exact"] = result["outcome"] == gold["outcome"]
    return out


def run_case(spec: TaskSpec, scene: SceneState, text: str, client: LLMClient,
             validation_layer: bool = False) -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        new, rec = compile_correction(spec, text, client,
                                      context={"scene": scene, "validation_layer": validation_layer})
        res = {"outcome": "apply", "state": state_of(new), "model": rec.model,
               "ops": [[o.op, o.target_id] for o in rec.diff if o.op != "resolve_uncertainty"]}
    except NeedsClarification as e:
        res = {"outcome": "asks", "question": e.question}
    except CorrectionRejected as e:
        res = {"outcome": "rejects", "reason": str(e)}
    except InterpretError as e:
        res = {"outcome": "error", "error": f"{type(e).__name__}: {e}"[:400]}
    res["latency_s"] = round(time.monotonic() - t0, 3)
    return res


def run_benchmark(client: LLMClient, scene: SceneState, cases_doc: Optional[dict] = None,
                  base: Optional[TaskSpec] = None, validation_layer: bool = False,
                  abort_if=None) -> dict[str, Any]:
    """``validation_layer`` False measures interpreter output only (the default);
    True also applies the deterministic checks of mirror.interpret.guard.
    ``abort_if(result)`` returning True stops the run after that case (used to
    stop on authentication or quota errors instead of spending more calls)."""
    from mirror.interpret.benchmark.overlap import overlap
    from mirror.interpret.prompts import EXAMPLE_PHRASES, PROMPT_REVISION
    cases_doc = cases_doc or load_cases()
    base = base or load_base_spec()
    base_state = state_of(base)
    rows = []
    aborted = None
    u0 = len(client.usage)
    for case in cases_doc["cases"]:
        if aborted:
            break
        n_usage = len(client.usage)
        res = run_case(base, scene, case["text"], client, validation_layer)
        if abort_if is not None and abort_if(res):
            aborted = f"after case {case['id']}: {res.get('error', '')[:200]}"
        models = sorted({u["model"] for u in client.usage[n_usage:]})
        res["models"] = models or ([client.label] if client.offline else [])
        res["score"] = score_case(case, res, base_state)
        rows.append({"id": case["id"], "category": case["category"], "text": case["text"],
                     "gold": case["gold"], "result": res,
                     "prompt_overlap": overlap(case["text"], EXAMPLE_PHRASES)})
    calls = client.usage[u0:]
    live = [u for u in calls if not u.get("from_cassette")]
    usage = {"calls": len(calls), "network_calls": len(live),
             "calls_with_retry_or_repair": sum(1 for u in calls if (u.get("attempt") or 1) > 1),
             "prompt_tokens": sum(u.get("prompt_tokens") or 0 for u in calls),
             "completion_tokens": sum(u.get("completion_tokens") or 0 for u in calls),
             "latency_s_total": round(sum(u.get("latency_s") or 0 for u in live), 1),
             "latency_s_median": (sorted(u.get("latency_s") or 0 for u in live)[len(live) // 2] if live else None)}
    out = {"interpreter": client.label, "mode": client.mode, "prompt_revision": PROMPT_REVISION,
            "validation_layer": validation_layer, "usage": usage,
            "models_returned": sorted({m for r in rows for m in r["result"]["models"]}),
            "summary": summarize(rows), "cases": rows}
    if aborted:
        out["aborted"] = aborted
    return out


def summarize(rows: list[dict]) -> dict[str, Any]:
    n = len(rows)
    by_cat: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        c = by_cat[r["category"]]
        c["n"] += 1
        c["exact"] += int(r["result"]["score"]["exact"])
    apply_rows = [r for r in rows if r["gold"]["outcome"] == "apply"]
    refuse_rows = [r for r in rows if r["gold"]["outcome"] != "apply"]
    return {
        "cases": n,
        "exact": sum(r["result"]["score"]["exact"] for r in rows),
        "apply_cases": len(apply_rows),
        "apply_exact": sum(r["result"]["score"]["exact"] for r in apply_rows),
        "refusal_cases": len(refuse_rows),
        "refusal_exact": sum(r["result"]["score"]["exact"] for r in refuse_rows),
        "refused_any_type": sum(r["result"]["score"]["refused_correctly"] for r in refuse_rows),
        "false_accepts": sum(r["result"]["score"]["false_accept"] for r in rows),
        "false_refusals": sum(r["result"]["score"]["false_refusal"] for r in rows),
        "scope_error_cases": sum(1 for r in rows if r["result"]["score"]["scope_errors"]),
        "wrong_rule_cases": sum(1 for r in rows if r["result"]["score"]["wrong_rule"]),
        "errors": sum(1 for r in rows if r["result"]["outcome"] == "error"),
        **overlap_split(rows),
        "by_category": {k: dict(v) for k, v in sorted(by_cat.items())},
    }


def rescore(report: dict, cases_doc: dict, base: Optional[TaskSpec] = None) -> dict:
    """Recompute scores of a stored report from its stored outputs (no rerun)."""
    base_state = state_of(base or load_base_spec())
    by_id = {c["id"]: c for c in cases_doc["cases"]}
    for run in report["runs"]:
        for row in run["cases"]:
            row["result"]["score"] = score_case(by_id[row["id"]], row["result"], base_state)
        run["summary"] = summarize(run["cases"])
    return report


def overlap_split(rows: list[dict], examples=None) -> dict[str, Any]:
    """Exact matches separately for cases that share wording with the prompt's
    examples and for the rest. ``examples`` recomputes the tags (for results
    produced with an earlier prompt revision)."""
    from mirror.interpret.benchmark.overlap import overlap
    tagged = []
    for r in rows:
        ov = overlap(r["text"], examples) if examples is not None else r.get("prompt_overlap", [])
        tagged.append((r, bool(ov)))
    ov_rows = [r for r, t in tagged if t]
    rest = [r for r, t in tagged if not t]
    return {"overlap_cases": [r["id"] for r in ov_rows],
            "overlap_exact": sum(r["result"]["score"]["exact"] for r in ov_rows),
            "non_overlap_cases": len(rest),
            "non_overlap_exact": sum(r["result"]["score"]["exact"] for r in rest)}


def with_validation_layer(run: dict, scene: SceneState, base: Optional[TaskSpec] = None) -> dict:
    """Apply the validation layer to stored interpreter outputs (no new model
    calls): an applied correction the layer would stop becomes "asks"."""
    import copy
    from mirror.interpret.guard import absent_object_question
    base = base or load_base_spec()
    base_state = state_of(base)
    out = copy.deepcopy(run)
    for row in out["cases"]:
        res = row["result"]
        if res["outcome"] == "apply":
            q = absent_object_question(row["text"], base, scene)
            if q is not None:
                row["result"] = {"outcome": "asks", "question": q.question, "by": "validation layer",
                                 "models": res.get("models", []), "latency_s": res.get("latency_s", 0.0)}
        row["result"]["score"] = score_case({"gold": row["gold"], "text": row["text"]}, row["result"], base_state)
    out["summary"] = summarize(out["cases"])
    out["validation_layer"] = True
    return out
