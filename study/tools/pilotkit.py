#!/usr/bin/env python
"""Usability pilot tools over the trial export (schema mirror-trial/1).

  pilotkit.py assign --participants 4 [--seed 0]
  pilotkit.py validate TRIALS.jsonl [...]
  pilotkit.py analyze TRIALS.jsonl [...] [--out report.md] [--context usability_pilot]
  pilotkit.py export-csv TRIALS.jsonl [...] --out trials.csv [--keep-text]

Developer smoke tests (context dev_smoke), pilot sessions (usability_pilot)
and benchmark runs (frozen_benchmark) are never combined: analyze reads one
context. Synthetic fixtures are never combined with real records, and every
report built from them says so in its first line.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import studykit as sk  # noqa: E402

CARDS = sk.STUDY / "pilot" / "task_cards.json"
FORMAT = "mirror-trial/1"
CONTEXTS = ("dev_smoke", "usability_pilot", "frozen_benchmark")
ORIGINS = ("prepared", "developer_written", "independently_authored")
OUTCOMES = ("apply", "asks", "rejects", "error")
REQUIRED = ("format", "trial_id", "participant", "synthetic", "context", "task_origin", "task", "condition",
            "attempt", "started_at", "ended_at", "task_started_at", "versions", "scene", "lineage", "input",
            "proposal", "review", "spec_state_before", "spec_state_after")
SYNTHETIC_BANNER = "SYNTHETIC FIXTURE DATA: NOT PILOT RESULTS. No participant took part."
LABEL = {"dev_smoke": "Developer smoke test: not independent user validation.",
         "usability_pilot": "Usability pilot with 3-5 people: preliminary usability evidence only, not proof of "
                            "demand or of a general productivity gain.",
         "frozen_benchmark": "Frozen benchmark run."}


def load_cards(path: Path = CARDS) -> dict[str, dict]:
    return {c["id"]: c for c in json.loads(Path(path).read_text())["cards"]}


def acceptance_state(card: dict, start: dict) -> dict:
    """Semantic state the card asks for, applied to the task's starting specification."""
    if card.get("acceptance") is None:
        return start
    from mirror.evaluate.protocol_v3.edits import apply_edit, semantic_state
    from mirror.evaluate.protocol_v3.runner import base_spec
    from mirror.evaluate.protocol_v3.schema import StructuredEdit
    base = base_spec()
    spec, _ = apply_edit(base, StructuredEdit.model_validate(sk.state_to_edit(start, semantic_state(base))),
                         text="start")
    spec, _ = apply_edit(spec, StructuredEdit.model_validate(card["acceptance"]), text=card["id"])
    return semantic_state(spec)


# ------------------------------------------------------------ assignment ---

ORDERS = [(("mirror", "A"), ("manual", "B")), (("manual", "B"), ("mirror", "A")),
          (("mirror", "B"), ("manual", "A")), (("manual", "A"), ("mirror", "B"))]
SETS = {"A": ["C1", "C2", "C3"], "B": ["C4", "C5", "C6"]}
PROBES = ["C7", "C8", "C9", "C10"]


def assign(n: int, seed: int = 0) -> list[dict]:
    if not 1 <= n <= 12:
        raise ValueError("between 1 and 12 participants")
    out = []
    for i in range(n):
        k = (i + seed) % 4
        blocks = [{"condition": c, "set": s, "cards": SETS[s][(i % 3):] + SETS[s][:(i % 3)]} for c, s in ORDERS[k]]
        probes = PROBES[(i + seed) % 4:] + PROBES[:(i + seed) % 4]
        out.append({"participant": f"P{i + 1:02d}", "practice": ["C0"], "blocks": blocks,
                    "probes_mirror_only": probes,
                    "comprehension_checks": ["after the recorded tour", "after the first fresh run"]})
    return out


# ------------------------------------------------------------ validation ---

def read(paths: Iterable[Path]) -> list[dict]:
    recs = []
    for p in paths:
        recs += [json.loads(x) for x in Path(p).read_text().splitlines() if x.strip()]
    return recs


def validate(recs: list[dict]) -> list[str]:
    errs = []
    cards = load_cards()
    if len({bool(r.get("synthetic")) for r in recs}) > 1:
        errs.append("records mix synthetic and real trials")
    seen = set()
    for i, rec in enumerate(recs):
        w = f"record {i + 1}"
        miss = [k for k in REQUIRED if k not in rec]
        if miss:
            errs.append(f"{w}: missing {miss}")
            continue
        if rec["format"] != FORMAT:
            errs.append(f"{w}: format {rec['format']!r}")
        p = str(rec["participant"])
        if not (p == "DEV" or (p.startswith("SYN") if rec["synthetic"] else p.startswith("P")) and p[-2:].isdigit()):
            errs.append(f"{w}: participant {p!r} is not an anonymous id for this record type")
        if rec["context"] not in CONTEXTS:
            errs.append(f"{w}: context {rec['context']!r}")
        if rec["context"] == "dev_smoke" and p != "DEV" and not rec["synthetic"]:
            errs.append(f"{w}: developer smoke tests use participant DEV")
        if rec["task_origin"] not in ORIGINS:
            errs.append(f"{w}: task_origin {rec['task_origin']!r}")
        if rec["task"] is not None and rec["task"] not in cards:
            errs.append(f"{w}: unknown task card {rec['task']!r}")
        if rec["condition"] not in ("mirror", "manual"):
            errs.append(f"{w}: condition {rec['condition']!r}")
        pr = rec["proposal"]
        if pr.get("outcome") not in OUTCOMES:
            errs.append(f"{w}: proposal outcome {pr.get('outcome')!r}")
        if pr.get("provenance") not in sk.PROVENANCE and rec["condition"] == "mirror":
            errs.append(f"{w}: provenance {pr.get('provenance')!r}")
        if "text" in rec["input"] and not (rec.get("consent") or {}).get("text"):
            errs.append(f"{w}: correction text without text consent")
        if rec["trial_id"] in seen:
            errs.append(f"{w}: duplicate trial_id")
        seen.add(rec["trial_id"])
    return errs


# --------------------------------------------------------------- metrics ---

def _ids_changed(a: dict, b: dict) -> set[str]:
    ea, eb = a["enforced"], b["enforced"]
    return {c for c in set(ea) | set(eb) if ea.get(c) != eb.get(c)}


def classify_change(after: dict, start: dict, acc: dict) -> dict[str, list[str]]:
    """Wrong-object and wrong-phase constraint changes in the specification an
    attempt left, relative to the task's start state and judged against what
    the card asks for. An attempt that undoes an earlier mistake is not
    counted again."""
    want = _ids_changed(acc, start)
    want_kinds = {c.rsplit("_", 1)[1] for c in want}
    want_type_kind = {(c.split("_")[1], c.rsplit("_", 1)[1]) for c in want}
    wrong_obj, wrong_phase = [], []
    for c in sorted(_ids_changed(after, start) - want):
        kind = c.rsplit("_", 1)[1]
        if kind not in want_kinds:
            wrong_obj.append(c)
        elif (c.split("_")[1], kind) in want_type_kind:
            wrong_phase.append(c)
    return {"wrong_object": wrong_obj, "wrong_phase": wrong_phase}


def task_rows(recs: list[dict]) -> list[dict]:
    cards = load_cards()
    by: dict[tuple, list[dict]] = defaultdict(list)
    for r in recs:
        if r["task"] and cards[r["task"]]["set"] != "practice":
            by[(r["participant"], r["task"], r["condition"])].append(r)
    rows = []
    for (pid, task, cond), atts in sorted(by.items()):
        atts.sort(key=lambda r: r["attempt"])
        card = cards[task]
        start = atts[0]["spec_state_before"]
        acc = acceptance_state(card, start)
        first = atts[0]
        exp = card["expected_first"]
        first_ok = (first["proposal"]["outcome"] == exp and
                    (first["spec_state_after"] == acc if exp == "apply" else first["spec_state_after"] == start))
        if cond == "manual" and exp == "apply":
            first_ok = first["spec_state_after"] == acc and first["proposal"]["validation"].get("passed", True)
        valid_t = next((r["ended_at"] - r["task_started_at"] for r in atts
                        if r["spec_state_after"] == acc and r["review"].get("accepted", cond == "manual")), None)
        wo = wp = 0
        for r in atts:
            c = classify_change(r["spec_state_after"], start, acc)
            wo += bool(c["wrong_object"])
            wp += bool(c["wrong_phase"])
        clar = [r["clarification"] for r in atts if r.get("clarification")]
        clar_next_ok = 0
        for i, r in enumerate(atts):
            if r.get("clarification") and i + 1 < len(atts) and atts[i + 1]["spec_state_after"] == acc:
                clar_next_ok += 1
        last = atts[-1]
        final_ok = last["spec_state_after"] == acc
        exe = last.get("execution") or {}
        if card.get("expected_execution") == "proven_infeasible":
            completed = final_ok and exe.get("status") == "planning_failed" and exe.get("proven_infeasible", False)
        elif card.get("acceptance") is None:
            completed = final_ok
        else:
            completed = final_ok and exe.get("status") == "success"
        comp = [r["comprehension"] for r in atts if r.get("comprehension")]
        rows.append({
            "participant": pid, "task": task, "set": card["set"], "condition": cond, "attempts": len(atts),
            "first_attempt_correct": first_ok, "time_to_valid_s": None if valid_t is None else round(valid_t, 1),
            "attempts_with_wrong_object": wo, "attempts_with_wrong_phase": wp,
            "clarifications": len(clar), "clarification_useful_ratings": [c.get("useful") for c in clar if c.get("useful")],
            "clarification_followed_by_correct": clar_next_ok, "completed": completed,
            "unresolved_failure": None if completed else (last.get("failure_reason") or "did not reach the card's outcome"),
            "provenance": sorted({r["proposal"].get("provenance") for r in atts if r["proposal"].get("provenance")}),
            "comprehension_correct": sum(1 for c in comp if c["answer"] == ("recorded" if c["truth"] in ("recorded", "replayed_cassette") else "fresh")),
            "comprehension_asked": len(comp), "synthetic": bool(first.get("synthetic")), "context": first["context"],
            "task_origin": first["task_origin"]})
    return rows


def _med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 1) if xs else None


def analyze(recs: list[dict], context: Optional[str] = None) -> str:
    ctxs = sorted({r["context"] for r in recs})
    if context is None:
        if len(ctxs) > 1:
            raise ValueError(f"records from several contexts {ctxs}; choose one with --context")
        context = ctxs[0] if ctxs else "usability_pilot"
    recs = [r for r in recs if r["context"] == context]
    synth = {bool(r["synthetic"]) for r in recs}
    if len(synth) > 1:
        raise ValueError("refusing to combine synthetic fixtures with real trials")
    rows = task_rows(recs)
    lines = ([SYNTHETIC_BANNER, ""] if synth == {True} else []) + ["# MIRROR usability pilot", "", LABEL[context], ""]
    lines += [f"{len({r['participant'] for r in rows})} participants, {len(rows)} participant x task rows, "
              f"{len(recs)} attempts. Task origins: {sorted({r['task_origin'] for r in rows})}.", ""]
    lines += ["| condition | rows | first attempt correct | median time to valid spec (s) | rows with wrong-object change "
              "| rows with wrong-phase change | clarifications (followed by correct) | completed | unresolved |",
              "|---|---|---|---|---|---|---|---|---|"]
    for cond in ("mirror", "manual"):
        rs = [r for r in rows if r["condition"] == cond]
        if rs:
            lines.append(f"| {cond} | {len(rs)} | {sum(r['first_attempt_correct'] for r in rs)} | "
                         f"{_med(r['time_to_valid_s'] for r in rs)} | {sum(bool(r['attempts_with_wrong_object']) for r in rs)} | "
                         f"{sum(bool(r['attempts_with_wrong_phase']) for r in rs)} | {sum(r['clarifications'] for r in rs)} "
                         f"({sum(r['clarification_followed_by_correct'] for r in rs)}) | {sum(r['completed'] for r in rs)} | "
                         f"{sum(1 for r in rs if r['unresolved_failure'])} |")
    ratings = [x for r in rows for x in r["clarification_useful_ratings"]]
    ca = sum(r["comprehension_asked"] for r in rows)
    lines += ["", f"Clarification usefulness (participant rating 1-5): {len(ratings)} ratings, median {_med(ratings)}.",
              f"Recorded vs fresh understood: {sum(r['comprehension_correct'] for r in rows)} of {ca} checks.", "",
              "## Per participant and task (every row)", "",
              "| participant | task | condition | attempts | first correct | time to valid (s) | wrong object | wrong phase "
              "| clarifications | completed | unresolved failure |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['participant']} | {r['task']} | {r['condition']} | {r['attempts']} | {r['first_attempt_correct']} | "
                     f"{r['time_to_valid_s']} | {r['attempts_with_wrong_object']} | {r['attempts_with_wrong_phase']} | "
                     f"{r['clarifications']} | {r['completed']} | {r['unresolved_failure'] or ''} |")
    return "\n".join(lines) + "\n"


def export_csv(recs: list[dict], out: Path, keep_text: bool = False) -> int:
    cols = ["trial_id", "participant", "synthetic", "context", "task_origin", "task", "condition", "attempt",
            "started_at", "ended_at", "outcome", "provenance", "fallback_reason", "latency_s", "cost_usd",
            "validation_passed", "accepted", "recheck_status", "execution_status", "failure_reason",
            "generation_id", "base_spec_version_id", "revised_spec_version_id", "scene_seed", "code_commit",
            "prompt_revision", "model", "interpreter", "input_kind", "chars"] + (["text"] if keep_text else [])
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in recs:
            pr, v, ln = r["proposal"], r["versions"], r["lineage"]
            row = {"trial_id": r["trial_id"], "participant": r["participant"], "synthetic": r["synthetic"],
                   "context": r["context"], "task_origin": r["task_origin"], "task": r["task"],
                   "condition": r["condition"], "attempt": r["attempt"], "started_at": r["started_at"],
                   "ended_at": r["ended_at"], "outcome": pr.get("outcome"), "provenance": pr.get("provenance"),
                   "fallback_reason": pr.get("fallback_reason"), "latency_s": pr.get("latency_s"),
                   "cost_usd": pr.get("cost_usd"), "validation_passed": (pr.get("validation") or {}).get("passed"),
                   "accepted": r["review"].get("accepted"), "recheck_status": (r.get("recheck") or {}).get("status"),
                   "execution_status": (r.get("execution") or {}).get("status"), "failure_reason": r.get("failure_reason"),
                   "generation_id": ln.get("generation_id"), "base_spec_version_id": ln.get("base_spec_version_id"),
                   "revised_spec_version_id": ln.get("revised_spec_version_id"), "scene_seed": r["scene"].get("seed"),
                   "code_commit": v.get("code_commit"), "prompt_revision": v.get("prompt_revision"),
                   "model": v.get("model"), "interpreter": v.get("interpreter"), "input_kind": r["input"].get("kind"),
                   "chars": r["input"].get("chars")}
            if keep_text:
                row["text"] = r["input"].get("text", "")
            w.writerow(row)
    return len(recs)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("assign")
    a.add_argument("--participants", type=int, required=True)
    a.add_argument("--seed", type=int, default=0)
    v = sub.add_parser("validate")
    v.add_argument("files", nargs="+")
    z = sub.add_parser("analyze")
    z.add_argument("files", nargs="+")
    z.add_argument("--out")
    z.add_argument("--context", choices=CONTEXTS)
    e = sub.add_parser("export-csv")
    e.add_argument("files", nargs="+")
    e.add_argument("--out", required=True)
    e.add_argument("--keep-text", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "assign":
        print(json.dumps(assign(args.participants, args.seed), indent=1))
        return 0
    recs = read([Path(f) for f in args.files])
    if args.cmd == "validate":
        errs = validate(recs)
        print("ok" if not errs else "\n".join(errs[:50]))
        return 1 if errs else 0
    errs = validate(recs)
    if errs:
        print("refusing: invalid records\n" + "\n".join(errs[:20]))
        return 1
    if args.cmd == "analyze":
        md = analyze(recs, args.context)
        if args.out:
            Path(args.out).write_text(md)
        print(md)
    else:
        print(f"{export_csv(recs, Path(args.out), args.keep_text)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
