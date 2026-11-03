"""Attempts, the append-only attempt ledger, timeouts and the spend cap.

An attempt is one case under one condition. Every attempt is written to
``attempts.jsonl`` twice: a ``start`` line before any work and an ``end`` line
with its status (completed, timeout, error, budget_stopped). A start without
an end (machine restart) is reported as ``interrupted``; a resumed run makes a
new attempt and keeps the interrupted one in the ledger. Nothing is dropped.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from mirror.contracts import TaskSpec
from mirror.interpret.benchmark import load_base_spec

from .conditions import Condition, TurnResult
from .edits import apply_edit, changed_kinds, semantic_state, state_of
from .execute import Executor
from .schema import Case, CaseFile
from .scenes import resolve

REFUSALS = ("asks", "rejects", "abstain")


def base_spec(name: str = "reference_reviewed") -> TaskSpec:
    """The reviewed reference specification every case starts from (goals and
    the cup's final upright rule confirmed; other demonstration rules proposed)."""
    if name != "reference_reviewed":
        raise ValueError(f"unknown base {name!r}")
    return load_base_spec()


def gold_chain(case: Case, base: TaskSpec) -> list[TaskSpec]:
    """Gold spec after each turn (unchanged for asks/rejects turns)."""
    out, cur = [], base
    for i, t in enumerate(case.turns):
        if t.gold.outcome == "apply":
            cur, _ = apply_edit(cur, t.gold.edit, text=t.text)
        out.append(cur)
    return out


def score_turn(prev: dict, got: TurnResult, gold_prev: dict, gold_now: dict, gold_outcome: str) -> dict[str, Any]:
    now = semantic_state(got.spec) if got.spec is not None else prev
    s: dict[str, Any] = {"outcome": got.outcome, "gold_outcome": gold_outcome, "exact": False,
                         "refused_any": False, "false_accept": False, "false_refusal": False,
                         "wrong_scope_kinds": [], "state_matches_gold": now == gold_now}
    changed = got.outcome == "apply" and now != prev
    if gold_outcome == "apply":
        s["exact"] = got.outcome == "apply" and now == gold_now
        s["false_refusal"] = got.outcome in REFUSALS
    else:
        s["refused_any"] = got.outcome in REFUSALS
        s["exact"] = got.outcome == gold_outcome and now == prev
        s["false_accept"] = changed
    if changed:
        s["wrong_scope_kinds"] = sorted(changed_kinds(now, prev) - changed_kinds(gold_now, gold_prev))
    return s


def end_to_end(expect: str, ex: Optional[dict], final_exact: bool) -> Optional[bool]:
    if expect == "not_executed":
        return final_exact
    if ex is None:
        return None                       # not executed in this run: unknown, never success
    if expect == "compliant_completion":
        if not ex.get("gold"):
            return False
        if ex["gold"]["status"] == "success" and ex["gold"].get("unverified"):
            return None                   # the checker could not verify a required ordering
        return ex["gold"]["status"] == "success"
    if expect == "proven_infeasible":
        return ex["plan_status"] != "ok" and ex["proven_infeasible"]
    if expect == "no_plan_ok":
        return ex["plan_status"] != "ok"
    return None


def run_case(case: Case, cond: Condition, *, base_dir: Path, executor: Optional[Executor],
             allow_eval: bool = False, ledger: Optional[Path] = None) -> dict[str, Any]:
    base = base_spec(case.base)
    scene = resolve(case.scene, allow_eval=allow_eval, ledger=ledger)
    golds = gold_chain(case, base)
    spec, turns, usage = base, [], []
    gold_prev = semantic_state(base)
    for t, g in zip(case.turns, golds):
        prev = semantic_state(spec)
        res = cond.run_turn(spec, t, scene, base_dir)
        gold_now = semantic_state(g)
        sc = score_turn(prev, res, gold_prev, gold_now, t.gold.outcome)
        turns.append({**res.public(), "score": sc, "speech": t.speech.speaker if t.speech else "typed"})
        usage += res.usage
        if res.outcome == "apply" and res.spec is not None:
            spec = res.spec
        gold_prev = gold_now
    final_exact = semantic_state(spec) == semantic_state(golds[-1])
    measured = all(t["outcome"] != "not_measured" for t in turns)
    ex = None
    if executor is not None and case.expect_execution != "not_executed" and \
            all(t["outcome"] != "not_measured" for t in turns):
        ex = executor.execute(scene, spec, golds[-1]).public()
    return {"case_id": case.id, "condition": cond.name, "provenance": cond.provenance,
            "category": case.category, "scene": case.scene.key(), "turns": turns,
            "final_state_exact": final_exact, "final_spec_version": spec.version,
            "final_state": state_of(spec), "gold_final_state": state_of(golds[-1]),
            "measured": measured,
            "execution": ex,
            "end_to_end_success": end_to_end(case.expect_execution, ex, final_exact) if measured else None,
            "usage": usage}


@dataclass
class Budget:
    cap_usd: float
    reserve_per_turn_usd: float
    spent_usd: float = 0.0

    def admits(self, n_turns: int) -> bool:
        return self.spent_usd + n_turns * self.reserve_per_turn_usd <= self.cap_usd


def _cost(usage: list[dict]) -> float:
    from mirror.evaluate.costs import record_cost
    return round(sum(record_cost(u) for u in usage), 6)


def _child(conn, fn, args, kwargs):
    try:
        conn.send(("ok", fn(*args, **kwargs)))
    except Exception as e:
        conn.send(("error", f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}"))
    finally:
        conn.close()


def _isolated(fn, args, kwargs, timeout_s: float):
    """Run ``fn`` in a forked process; (status, value) with status ok|error|timeout."""
    ctx = mp.get_context("fork")
    a, b = ctx.Pipe(duplex=False)
    p = ctx.Process(target=_child, args=(b, fn, args, kwargs), daemon=True)
    p.start()
    b.close()
    if a.poll(timeout_s):
        status, value = a.recv()
        p.join(5)
        return status, value
    p.kill()
    p.join(5)
    return "timeout", f"no result within {timeout_s:.0f} s"


def _read_ledger(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def run(cases: CaseFile, conditions: list[Condition], out: Path, *, run_id: str, base_dir: Path,
        execute: bool = True, allow_eval: bool = False, budget: Optional[Budget] = None,
        timeout_s: Optional[float] = None, resume: bool = False,
        on_attempt: Optional[Callable[[dict], None]] = None) -> dict[str, Any]:
    """Run every case under every condition. ``timeout_s`` isolates each
    attempt in a child process. Results: ``out/attempts.jsonl`` (ledger) and
    ``out/results.jsonl`` (one line per finished attempt)."""
    out.mkdir(parents=True, exist_ok=True)
    ledger_p, results_p = out / "attempts.jsonl", out / "results.jsonl"
    prior = [e for e in _read_ledger(ledger_p) if e.get("run_id") == run_id]
    if prior and not resume:
        raise RuntimeError(f"run {run_id} already has {len(prior)} ledger lines in {ledger_p}; "
                           f"refusing a second run (use resume to continue after an interruption)")
    done = {(e["case_id"], e["condition"]) for e in prior if e["event"] == "end" and e["status"] == "completed"}
    ended = {e["attempt"] for e in prior if e["event"] == "end"}
    n_attempt = max([e["attempt"] for e in prior], default=0)
    layout_ledger = out / "layout_ledger.jsonl" if allow_eval else None
    executor = Executor() if execute else None

    def log(entry):
        with open(ledger_p, "a", encoding="utf-8") as f:
            f.write(json.dumps({"run_id": run_id, "unix": round(time.time(), 3), **entry}) + "\n")

    for e in prior:                                   # interrupted attempts stay visible
        if e["event"] == "start" and e["attempt"] not in ended:
            log({"event": "end", "attempt": e["attempt"], "case_id": e["case_id"],
                 "condition": e["condition"], "status": "interrupted"})
    stopped = False
    for case in cases.cases:
        for cond in conditions:
            if (case.id, cond.name) in done:
                continue
            n_attempt += 1
            log({"event": "start", "attempt": n_attempt, "case_id": case.id, "condition": cond.name})
            metered = cond.provenance == "fresh model call"
            if budget is not None and metered and (stopped or not budget.admits(len(case.turns))):
                stopped = True
                log({"event": "end", "attempt": n_attempt, "case_id": case.id, "condition": cond.name,
                     "status": "budget_stopped", "spent_usd": budget.spent_usd})
                continue
            t0 = time.time()
            kw = dict(base_dir=base_dir, executor=executor, allow_eval=allow_eval, ledger=layout_ledger)
            if timeout_s:
                status, value = _isolated(run_case, (case, cond), kw, timeout_s)
            else:
                try:
                    status, value = "ok", run_case(case, cond, **kw)
                except Exception as e:
                    status, value = "error", f"{type(e).__name__}: {e}"
            entry = {"event": "end", "attempt": n_attempt, "case_id": case.id, "condition": cond.name,
                     "wall_s": round(time.time() - t0, 3)}
            if status == "ok":
                cost = _cost(value.get("usage", []))
                if budget is not None:
                    budget.spent_usd += cost
                value.update(attempt=n_attempt, cost_usd=cost, run_id=run_id)
                with open(results_p, "a", encoding="utf-8") as f:
                    f.write(json.dumps(value, default=str) + "\n")
                log({**entry, "status": "completed", "cost_usd": cost})
                if on_attempt:
                    on_attempt(value)
            else:
                log({**entry, "status": status, "detail": str(value)[:1500]})
    return {"ledger": str(ledger_p), "results": str(results_p), "attempts": n_attempt,
            "spent_usd": budget.spent_usd if budget else None}


def old_run_checks(cases: CaseFile, *, allow_eval: bool = False, ledger: Optional[Path] = None) -> list[dict]:
    """Per executable case: does the base spec's rollout pass under the gold final spec?"""
    ex, out = Executor(), []
    for case in cases.cases:
        if case.expect_execution == "not_executed":
            continue
        scene = resolve(case.scene, allow_eval=allow_eval, ledger=ledger)
        base = base_spec(case.base)
        golds = gold_chain(case, base)
        if semantic_state(golds[-1]) == semantic_state(base):
            out.append({"case_id": case.id, "gold_changes_spec": False})
            continue
        out.append({"case_id": case.id, "gold_changes_spec": True, **ex.old_run_rejected(scene, base, golds[-1])})
    return out
