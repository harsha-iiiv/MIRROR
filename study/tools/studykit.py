#!/usr/bin/env python
"""Impact study tools: assignment, log validation, export, analysis, transfer check.

  studykit.py assign --participants 8 [--seed 7]
  studykit.py validate LOG.jsonl [...]
  studykit.py export LOG.jsonl [...] --out table.json [--drop-text]
  studykit.py analyze table.json [--out report.md]
  studykit.py transfer table.json --out transfer.json   (runs the simulator; slow)

Synthetic fixture logs (fixtures/) carry "synthetic": true on every record.
The tools refuse to mix synthetic and real records, and every report built
from synthetic records says so in its first line.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional

HERE = Path(__file__).resolve().parent
STUDY = HERE.parent
REPO = STUDY.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

SCHEMA = "mirror-study-events/1"
TASKS = STUDY / "task_packet" / "tasks.json"
CONDITIONS = ("mirror", "manual", "reteach")
EVENT_FIELDS = {
    "session_start": {"app_commit", "task_packet_sha256", "consent"},
    "task_start": {"scene_key", "spec_version_id", "spec_state"},
    "correction_submitted": {"input", "chars"},
    "interpretation_returned": {"outcome", "provenance", "latency_s"},
    "clarification_shown": {"question_chars"},
    "clarification_answered": {"chars"},
    "manual_edit_saved": {"valid", "errors"},
    "spec_version_created": {"spec_version_id", "spec_state", "source"},
    "demo_recorded": {"duration_s"},
    "recheck_run": {"rollout_id", "verdict"},
    "execution_run": {"rollout_id", "verdict", "scene_key"},
    "task_end": {"reason"},
    "rating": {"confidence", "effort"},
    "facilitator_note": {"kind"},
    "session_end": set(),
}
PROVENANCE = {"recorded", "fresh model call", "offline interpreter", "replayed cassette"}
SYNTHETIC_BANNER = "SYNTHETIC FIXTURE DATA: NOT STUDY RESULTS. No participant took part."


# ------------------------------------------------------------------ tasks ---

def load_tasks(path: Path = TASKS) -> dict[str, dict]:
    return {t["id"]: t for t in json.loads(Path(path).read_text())["tasks"]}


def packet_sha256(path: Path = TASKS) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def acceptance_state(task: dict) -> dict:
    from mirror.evaluate.protocol_v3.edits import apply_edit, semantic_state
    from mirror.evaluate.protocol_v3.runner import base_spec
    from mirror.evaluate.protocol_v3.schema import StructuredEdit
    spec, _ = apply_edit(base_spec(), StructuredEdit.model_validate(task["acceptance"]), text=task["id"])
    return semantic_state(spec)


def base_state() -> dict:
    from mirror.evaluate.protocol_v3.edits import semantic_state
    from mirror.evaluate.protocol_v3.runner import base_spec
    return semantic_state(base_spec())


# ------------------------------------------------------------- assignment ---

ORDERS = [(("mirror", "A"), ("manual", "B")), (("manual", "B"), ("mirror", "A")),
          (("mirror", "B"), ("manual", "A")), (("manual", "A"), ("mirror", "B"))]
LATIN = [(0, 1, 2), (1, 2, 0), (2, 0, 1)]
SETS = {"A": ["T1", "T2", "T3"], "B": ["T4", "T5", "T6"]}


def assign(n: int, seed: int = 0) -> list[dict]:
    """Counterbalanced plan: condition/set order rotates over 4 orders, task
    order within a set over a 3x3 Latin square, reteach order alternates."""
    if not 1 <= n <= 24:
        raise ValueError("between 1 and 24 participants")
    rows = []
    for i in range(n):
        k = (i + seed) % 4
        blocks = []
        for j, (cond, s) in enumerate(ORDERS[k]):
            sq = LATIN[(i + j + seed) % 3]
            blocks.append({"condition": cond, "set": s, "tasks": [SETS[s][x] for x in sq]})
        reteach = ["reteach", "mirror"] if (i + seed) % 2 == 0 else ["mirror", "reteach"]
        rows.append({"participant": f"P{i + 1:02d}", "blocks": blocks, "T7_order": reteach})
    return rows


# ------------------------------------------------------------- validation ---

def read_log(path: Path) -> list[dict]:
    return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]


def validate(events: list[dict]) -> list[str]:
    errs: list[str] = []
    synth = {bool(e.get("synthetic")) for e in events}
    if len(synth) > 1:
        errs.append("log mixes synthetic and real records")
    last: dict[str, int] = {}
    consent: dict[str, dict] = {}
    for i, e in enumerate(events):
        where = f"line {i + 1}"
        if e.get("schema") != SCHEMA:
            errs.append(f"{where}: schema {e.get('schema')!r}")
        for k in ("participant", "session", "seq", "t", "event"):
            if k not in e:
                errs.append(f"{where}: missing {k}")
        p = str(e.get("participant", ""))
        ok_id = (p.startswith("SYN") if e.get("synthetic") else p.startswith("P")) and p[-2:].isdigit()
        if not ok_id:
            errs.append(f"{where}: participant id {p!r} is not pseudonymous for this record type")
        ev = e.get("event")
        if ev not in EVENT_FIELDS:
            errs.append(f"{where}: unknown event {ev!r}")
            continue
        miss = EVENT_FIELDS[ev] - set(e)
        if miss:
            errs.append(f"{where}: {ev} missing {sorted(miss)}")
        s = e.get("session", "")
        if s in last and e.get("seq", -1) <= last[s]:
            errs.append(f"{where}: seq not increasing in session {s}")
        last[s] = e.get("seq", -1)
        if e.get("condition") not in (None,) + CONDITIONS:
            errs.append(f"{where}: condition {e.get('condition')!r}")
        if ev == "session_start":
            consent[s] = dict(e.get("consent") or {})
        c = consent.get(s, {})
        if ev == "correction_submitted" and "text" in e and not c.get("text"):
            errs.append(f"{where}: correction text recorded without text consent")
        if ev == "rating" and e.get("comment") and not c.get("quote"):
            errs.append(f"{where}: comment recorded without quote consent")
        if ev == "interpretation_returned" and e.get("provenance") not in PROVENANCE:
            errs.append(f"{where}: provenance {e.get('provenance')!r}")
    return errs


# ----------------------------------------------------------------- export ---

def _changed(a: dict, b: dict) -> set[str]:
    from mirror.evaluate.protocol_v3.edits import changed_kinds
    return changed_kinds(a, b)


def task_metrics(events: list[dict], tasks: dict[str, dict], limit_s: float) -> list[dict]:
    """One row per participant x task x condition."""
    rows, cur = [], None
    for e in events:
        ev = e["event"]
        if ev == "task_start":
            t = tasks[e["task"]]
            acc = acceptance_state(t)
            cur = {"participant": e["participant"], "task": e["task"], "condition": e["condition"],
                   "set": t["set"], "synthetic": bool(e.get("synthetic")), "t0": e["t"],
                   "start_state": e["spec_state"], "acceptance": acc,
                   "accept_kinds": sorted(_changed(acc, e["spec_state"])), "prev_state": e["spec_state"],
                   "time_to_valid_s": None, "iterations": 0, "wrong_scope_edits": 0, "clarifications": 0,
                   "invalid_edits": 0, "errors": 0, "corrections": 0, "provenance": set(),
                   "interventions": 0, "final_state": e["spec_state"], "end_reason": None, "texts": []}
        elif cur is None:
            continue
        elif ev == "spec_version_created":
            cur["iterations"] += 1
            st = e["spec_state"]
            if set(_changed(st, cur["start_state"])) - set(cur["accept_kinds"]):
                cur["wrong_scope_edits"] += 1
            cur["prev_state"] = cur["final_state"] = st
            if cur["time_to_valid_s"] is None and st == cur["acceptance"]:
                cur["time_to_valid_s"] = round(e["t"] - cur["t0"], 3)
        elif ev == "clarification_shown":
            cur["clarifications"] += 1
        elif ev == "manual_edit_saved" and not e["valid"]:
            cur["invalid_edits"] += 1
        elif ev == "interpretation_returned":
            cur["provenance"].add(e["provenance"])
            if e["outcome"] == "error":
                cur["errors"] += 1
        elif ev == "correction_submitted":
            cur["corrections"] += 1
            if "text" in e:
                cur["texts"].append(e["text"])
        elif ev == "facilitator_note":
            cur["interventions"] += 1
        elif ev == "task_end":
            cur["end_reason"] = e["reason"]
            cur["duration_s"] = round(e["t"] - cur["t0"], 3)
            cur["reached_valid"] = cur["time_to_valid_s"] is not None
            cur["final_exact"] = cur["final_state"] == cur["acceptance"]
            cur["time_to_valid_or_limit_s"] = cur["time_to_valid_s"] if cur["reached_valid"] else limit_s
            cur["provenance"] = sorted(cur["provenance"])
            for k in ("prev_state", "t0"):
                cur.pop(k)
            rows.append(cur)
            cur = None
    return rows


def export(paths: Iterable[Path], *, drop_text: bool = True) -> dict[str, Any]:
    tasks = load_tasks()
    limit = float(json.loads(TASKS.read_text())["time_limit_s"])
    all_rows, synth, problems = [], set(), []
    for p in paths:
        ev = read_log(p)
        errs = validate(ev)
        if errs:
            problems.append({"log": Path(p).name, "errors": errs[:20]})
            continue
        synth |= {bool(e.get("synthetic")) for e in ev}
        all_rows += task_metrics(ev, tasks, limit)
    if len(synth) > 1:
        raise ValueError("refusing to combine synthetic fixtures with real study logs")
    if drop_text:
        for r in all_rows:
            r.pop("texts", None)
    return {"synthetic": synth == {True}, "task_packet_sha256": packet_sha256(), "rows": all_rows,
            "invalid_logs": problems}


# ---------------------------------------------------------------- analyze ---

def _med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 1) if xs else None


def analyze(table: dict) -> str:
    rows = table["rows"]
    lines = ([SYNTHETIC_BANNER, ""] if table.get("synthetic") else []) + ["# Impact pilot results", ""]
    lines += [f"Task packet sha256 `{table['task_packet_sha256'][:16]}`; {len({r['participant'] for r in rows})} "
              f"participants; {len(rows)} participant x task rows.", ""]
    if table.get("invalid_logs"):
        lines += [f"Logs rejected by validation: {len(table['invalid_logs'])} (listed at the end).", ""]
    main = [r for r in rows if r["set"] in ("A", "B")]
    lines += ["## Per condition (main tasks)", "",
              "| condition | rows | reached valid spec | median time to valid (s; limit counted) | completed (final exact) "
              "| wrong-scope edits | clarifications | iterations (median) | invalid edits | errors | gave up / limit |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in ("mirror", "manual"):
        rs = [r for r in main if r["condition"] == c]
        if not rs:
            continue
        lines.append(f"| {c} | {len(rs)} | {sum(r['reached_valid'] for r in rs)} | "
                     f"{_med(r['time_to_valid_or_limit_s'] for r in rs)} | {sum(r['final_exact'] for r in rs)} | "
                     f"{sum(r['wrong_scope_edits'] for r in rs)} | {sum(r['clarifications'] for r in rs)} | "
                     f"{_med(r['iterations'] for r in rs)} | {sum(r['invalid_edits'] for r in rs)} | "
                     f"{sum(r['errors'] for r in rs)} | "
                     f"{sum(r['end_reason'] in ('gave_up', 'time_limit') for r in rs)} |")
    per: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for r in main:
        per[r["participant"]][r["condition"]].append(r["time_to_valid_or_limit_s"])
    pairs = [(sum(v["mirror"]), sum(v["manual"])) for v in per.values() if v["mirror"] and v["manual"]]
    lines += ["", "## Paired time to valid specification (sum over a participant's three tasks)", ""]
    if len(pairs) >= 2:
        from scipy.stats import wilcoxon
        d = [a - b for a, b in pairs]
        try:
            w = wilcoxon([a for a, _ in pairs], [b for _, b in pairs], method="exact")
            p = round(float(w.pvalue), 4)
        except ValueError:
            p = None
        lines.append(f"{len(pairs)} pairs; median difference MIRROR - manual {_med(d)} s; exact Wilcoxon p = {p}. "
                     f"With this few people the test is descriptive.")
    else:
        lines.append("fewer than two complete pairs; no test")
    lines += ["", "## Per participant and task (every row, including failures)", "",
              "| participant | task | condition | time to valid (s) | final exact | wrong-scope | clarifications "
              "| iterations | end | provenance | interventions |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (r["participant"], r["task"], r["condition"])):
        lines.append(f"| {r['participant']} | {r['task']} | {r['condition']} | {r['time_to_valid_s']} | "
                     f"{r['final_exact']} | {r['wrong_scope_edits']} | {r['clarifications']} | {r['iterations']} | "
                     f"{r['end_reason']} | {', '.join(r['provenance']) or '-'} | {r['interventions']} |")
    fails = [r for r in rows if not r["final_exact"] or r["end_reason"] != "participant_done"]
    lines += ["", f"## Failures ({len(fails)})", ""]
    lines += [f"- {r['participant']} {r['task']} {r['condition']}: end {r['end_reason']}, final exact {r['final_exact']}, "
              f"wrong-scope edits {r['wrong_scope_edits']}" for r in fails] or ["none"]
    for bad in table.get("invalid_logs", []):
        lines.append(f"- rejected log {bad['log']}: {bad['errors'][:3]}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------- transfer ---

def state_to_edit(state: dict, base: dict) -> dict:
    """A StructuredEdit (dict) that turns the base specification into ``state``."""
    rules = []
    for cid, p in state["enforced"].items():
        if base["enforced"].get(cid) != p:
            _, typ_phase_kind = cid.split("_", 1)
            parts = typ_phase_kind.split("_")
            kind, phase, typ = parts[-1], parts[-2], "_".join(parts[:-2])
            r = {"type": typ, "object": kind, "phase": phase}
            if "max_tilt_deg" in p:
                r["max_tilt_deg"] = p["max_tilt_deg"]
            if "with" in p:
                r["with_groups"] = p["with"]
            if "min_m" in p:
                r["min_m"] = p["min_m"]
            rules.append(r)
    for cid in base["enforced"]:
        if cid not in state["enforced"]:
            _, tpk = cid.split("_", 1)
            parts = tpk.split("_")
            rules.append({"type": "_".join(parts[:-2]), "object": parts[-1], "phase": parts[-2], "status": "rejected"})
    orders = [{"before": o.split("_")[1], "after": o.split("_")[3]} for o in state["ordering"] if o not in base["ordering"]]
    goals = [{"object": g.rsplit("_", 1)[1], "status": "rejected"} for g in state["inactive_goals"]
             if g not in base["inactive_goals"]]
    return {"rules": rules, "orderings": orders, "goals": goals}


def transfer(table: dict) -> list[dict]:
    """Execute each row's final specification on the task's held-back layouts
    and check it under the acceptance specification."""
    from mirror.evaluate.protocol_v3.edits import apply_edit
    from mirror.evaluate.protocol_v3.execute import Executor
    from mirror.evaluate.protocol_v3.runner import base_spec
    from mirror.evaluate.protocol_v3.schema import SceneRef, StructuredEdit
    from mirror.evaluate.protocol_v3.scenes import resolve
    tasks, ex, out = load_tasks(), Executor(), []
    b = base_spec()
    bstate = base_state()
    for r in table["rows"]:
        t = tasks[r["task"]]
        spec, _ = apply_edit(b, StructuredEdit.model_validate(state_to_edit(r["final_state"], bstate)), text="final")
        gold, _ = apply_edit(b, StructuredEdit.model_validate(t["acceptance"]), text="acceptance")
        res = []
        for seed in t["transfer_seeds"]:
            scene = resolve(SceneRef(kind="layout", split="dev", family="mixed", seed=seed))
            e = ex.execute(scene, spec, gold)
            res.append({"seed": seed, "gold_status": (e.gold or {}).get("status"), "plan_status": e.plan_status,
                        "unverified": (e.gold or {}).get("unverified", [])})
        out.append({"participant": r["participant"], "task": r["task"], "condition": r["condition"],
                    "synthetic": r["synthetic"], "layouts": res,
                    "passed": sum(1 for x in res if x["gold_status"] == "success" and not x["unverified"])})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("assign")
    a.add_argument("--participants", type=int, required=True)
    a.add_argument("--seed", type=int, default=0)
    v = sub.add_parser("validate")
    v.add_argument("logs", nargs="+")
    e = sub.add_parser("export")
    e.add_argument("logs", nargs="+")
    e.add_argument("--out", required=True)
    e.add_argument("--keep-text", action="store_true", help="keep consented correction text (private store only)")
    z = sub.add_parser("analyze")
    z.add_argument("table")
    z.add_argument("--out")
    t = sub.add_parser("transfer")
    t.add_argument("table")
    t.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "assign":
        print(json.dumps(assign(args.participants, args.seed), indent=1))
    elif args.cmd == "validate":
        bad = 0
        for p in args.logs:
            errs = validate(read_log(Path(p)))
            bad += bool(errs)
            print(f"{p}: {'ok' if not errs else str(len(errs)) + ' problem(s)'}")
            for x in errs[:20]:
                print("  " + x)
        return 1 if bad else 0
    elif args.cmd == "export":
        tbl = export([Path(p) for p in args.logs], drop_text=not args.keep_text)
        Path(args.out).write_text(json.dumps(tbl, indent=1) + "\n")
        print(f"{len(tbl['rows'])} rows, synthetic={tbl['synthetic']}, invalid logs {len(tbl['invalid_logs'])}")
    elif args.cmd == "analyze":
        md = analyze(json.loads(Path(args.table).read_text()))
        if args.out:
            Path(args.out).write_text(md)
        print(md)
    elif args.cmd == "transfer":
        res = transfer(json.loads(Path(args.table).read_text()))
        Path(args.out).write_text(json.dumps(res, indent=1) + "\n")
        print(f"{len(res)} rows checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
