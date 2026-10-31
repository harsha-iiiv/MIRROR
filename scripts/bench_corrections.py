#!/usr/bin/env python
"""Correction-compiler benchmark.

  bench_corrections.py                       offline rules, plus the model if configured
  bench_corrections.py --interpreters offline
  MIRROR_LLM_MODE=record MIRROR_LLM_BASE_URL=... MIRROR_LLM_MODEL=... MODEL_API_KEY=... \
      bench_corrections.py                   records model responses
  bench_corrections.py --interpreters model  replays recorded responses when no mode is set

Cases: mirror/interpret/benchmark/cases.json (frozen). Recordings:
data/cassettes/corrections_benchmark/. Writes results/corrections_benchmark.json
and .md.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mirror.interpret import LLMClient  # noqa: E402
from mirror.interpret.auto import recorded_models  # noqa: E402
from mirror.interpret.benchmark import load_base_spec, load_cases, run_benchmark  # noqa: E402

# Benchmark recordings live in <MIRROR_CASSETTE_DIR>/corrections_benchmark
# (default data/cassettes/corrections_benchmark).
_BASE = Path(os.environ["MIRROR_CASSETTE_DIR"]) if os.environ.get("MIRROR_CASSETTE_DIR") else ROOT / "data" / "cassettes"
CASSETTES = (_BASE if _BASE.is_absolute() else ROOT / _BASE) / "corrections_benchmark"

# Errors that mean the account or quota is the problem: stop instead of
# spending more calls.
_STOP_MARKERS = ("authentication failed", "HTTP 401", "HTTP 402", "HTTP 403", "HTTP 429")


def _should_stop(result: dict) -> bool:
    return result.get("outcome") == "error" and any(m in result.get("error", "") for m in _STOP_MARKERS)


def model_client() -> tuple[LLMClient | None, str]:
    mode = os.environ.get("MIRROR_LLM_MODE")
    if mode and mode != "offline":
        return LLMClient.from_env(cassette_dir=CASSETTES), f"MIRROR_LLM_MODE={mode}"
    models = recorded_models(CASSETTES)
    wanted = os.environ.get("MIRROR_LLM_MODEL")
    if wanted:
        if wanted in models:
            return LLMClient(mode="replay", model=wanted, cassette_dir=CASSETTES), "replaying recordings"
        return None, f"no recordings from {wanted} in {CASSETTES}"
    if len(models) == 1:
        requested = next(iter(models))
        return LLMClient(mode="replay", model=requested, cassette_dir=CASSETTES), "replaying recordings"
    if len(models) > 1:
        return None, f"recordings from several models in {CASSETTES}; set MIRROR_LLM_MODEL and MIRROR_LLM_MODE=replay"
    return None, "no model configured and no recordings"


def _pct(a: int, b: int) -> str:
    return f"{a}/{b} ({100.0 * a / b:.0f}%)" if b else "-"


def markdown(report: dict) -> str:
    title = "# Correction-compiler benchmark" + (f" ({report['tag']})" if report.get("tag") else "")
    lines = [title, "",
             f"Cases: {report['n_cases']} ({report.get('cases_file', 'cases.json')} sha256 `{report['cases_sha256'][:16]}`), "
             f"written before any interpreter was run on them. Base specification: offline "
             f"proposal for the reference demonstration after review; scene: shortcut.", ""]
    lines += ["| interpreter | model id returned | exact | apply exact | refusal exact | refused (any type) "
              "| false accepts | false refusals | wrong rule | scope-error cases | errors |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for run in report["runs"]:
        s = run["summary"]
        lines.append(f"| {run['interpreter']} ({run['mode']}) | {', '.join(run['models_returned']) or '-'} | "
                     f"{_pct(s['exact'], s['cases'])} | {_pct(s['apply_exact'], s['apply_cases'])} | "
                     f"{_pct(s['refusal_exact'], s['refusal_cases'])} | "
                     f"{_pct(s['refused_any_type'], s['refusal_cases'])} | {s['false_accepts']} | "
                     f"{s['false_refusals']} | {s['wrong_rule_cases']} | {s['scope_error_cases']} | {s['errors']} |")
    for note in report.get("notes", []):
        lines += ["", f"Note: {note}"]
    for run in report["runs"]:
        s = run["summary"]
        lines += ["", f"## {run['interpreter']} ({run['mode']})", "",
                  "| category | exact |", "|---|---|"]
        for cat, c in s["by_category"].items():
            lines.append(f"| {cat} | {_pct(c['exact'], c['n'])} |")
        fails = [r for r in run["cases"] if not r["result"]["score"]["exact"]]
        lines += ["", f"Failures ({len(fails)}):", ""]
        if not fails:
            lines.append("none")
        for r in fails:
            res, sc = r["result"], r["result"]["score"]
            got = res["outcome"]
            if got == "apply":
                got += " " + json.dumps(res["state"], sort_keys=True)
            elif got == "asks":
                got += f": {res.get('question', '')}"
            elif got in ("rejects", "error"):
                got += f": {res.get('reason') or res.get('error', '')}"
            gold = r["gold"]["outcome"]
            if gold == "apply":
                gold += " " + json.dumps({"enforced": {e["id"]: e["params"] for e in r["gold"]["enforced"]},
                                          "ordering": r["gold"].get("ordering", []),
                                          "inactive_goals": r["gold"].get("inactive_goals", [])}, sort_keys=True)
            flags = [k for k in ("false_accept", "false_refusal") if sc[k]]
            if sc["scope_errors"]:
                flags.append("scope error on " + ", ".join(sc["scope_errors"]))
            lines.append(f"- **{r['id']}** ({r['category']}) \"{r['text']}\"  ")
            lines.append(f"  gold: `{gold}`  ")
            lines.append(f"  got: `{got[:600]}`" + (f"  \n  flags: {'; '.join(flags)}" if flags else ""))
    return "\n".join(lines) + "\n"


RUNS = [
    ("", "Frozen set, first run (blind), prompt revision 1"),
    ("after_fixes", "Frozen set after offline fixes informed by the first run (tuned), prompt revision 1"),
    ("holdout", "Held-out set (written after those fixes, before any run), prompt revision 1"),
    ("prompt_rev2", "Frozen set, prompt revision 2 (made after seeing results; superseded)"),
    ("holdout_prompt_rev2", "Held-out set, prompt revision 2 (superseded); offline rules include one "
                            "post-hold-out fix"),
    ("after_prompt_revision", "Frozen set, prompt revision 3 (current; made after seeing results)"),
    ("holdout_after_prompt_revision", "Held-out set, prompt revision 3 (current); offline rules include one "
                                      "post-hold-out fix"),
    ("muse", "Frozen set, Muse Spark swap (post-hoc: set seen in development, prompt revision 3 tuned on "
             "the development model)"),
    ("holdout_muse", "Held-out set, Muse Spark swap (post-hoc: set seen in development, prompt revision 3 "
                     "tuned on the development model)"),
]


def summary_markdown(out: Path) -> str:
    from mirror.interpret.benchmark.overlap import PROMPT_V1_EXAMPLES
    from mirror.interpret.benchmark.score import overlap_split, with_validation_layer
    from mirror.scenes import reference_scene
    scene = reference_scene("shortcut")
    lines = ["# Correction-compiler benchmark: summary", "",
             "Exact match = outcome and resulting enforced constraints, confirmed orderings and rejected goals all "
             "equal the gold. Per-case details are in the linked files.", "",
             "The first three sets are the headline results. Prompt revision 2 (neutral examples instead of "
             "phrases from the reference correction; contact rules without a phase default to transport) and "
             "revision 3 (revision 2 with its object-resolution rule reworded, because revision 2 made the model "
             "map objects absent from the scene onto the cup) were made after those results were seen, so their "
             "rows are post-change measurements, not independent ones. 'Prompt-overlap' cases share wording with "
             "an illustrative phrase in the prompt revision that produced the row (word bigrams, scene nouns "
             "ignored); they are scored separately.", "",
             "Each interpreter has two rows. 'Model output only' / 'rule output only' is the interpreter's "
             "output after schema and specification checks. '+ validation layer' applies the deterministic "
             "absent-object check (mirror/interpret/guard.py) to the same stored outputs, with no new model "
             "calls. That layer was added after the jar/bowl false accepts were seen, so its rows are "
             "post-hoc, not independent measurements.", "",
             "The Muse Spark rows swap the model after development: both case sets had been seen, and prompt "
             "revision 3 was written while testing the development model (gpt-5.5). They are a post-hoc model "
             "swap on unchanged prompts, not a blind benchmark of Muse Spark.", "",
             "| set | interpreter | output | model id returned | exact | prompt-overlap cases (exact) | other cases exact "
             "| false accepts | false refusals | wrong rule | scope-error cases |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for tag, label in RUNS:
        stem = "corrections_benchmark" + (f"_{tag}" if tag else "")
        f = out / f"{stem}.json"
        if not f.exists():
            continue
        rep = json.loads(f.read_text())
        for base_run in rep["runs"]:
            for layered in (False, True):
                run = with_validation_layer(base_run, scene) if layered else base_run
                s = run["summary"]
                if base_run.get("prompt_revision") is None:  # produced before revision tracking: revision 1
                    split = overlap_split(run["cases"], PROMPT_V1_EXAMPLES)
                else:
                    split = overlap_split(run["cases"])
                ovl = (f"{', '.join(split['overlap_cases'])} ({split['overlap_exact']}/{len(split['overlap_cases'])})"
                       if split["overlap_cases"] else "none")
                if run["interpreter"] == "offline-rules":
                    ovl = "n/a (no prompt)"
                    split = {**split, "non_overlap_exact": s["exact"], "non_overlap_cases": s["cases"]}
                who = f"{run['interpreter']} ({run['mode']})"
                what = ("+ validation layer (same outputs)" if layered
                        else ("rule output only" if run["interpreter"] == "offline-rules" else "model output only"))
                lines.append(f"| [{label}]({stem}.md) | {who} | {what} | "
                             f"{', '.join(run['models_returned'])} | {_pct(s['exact'], s['cases'])} | {ovl} | "
                             f"{_pct(split['non_overlap_exact'], split['non_overlap_cases'])} | "
                             f"{s['false_accepts']} | {s['false_refusals']} | {s.get('wrong_rule_cases', '-')} | "
                             f"{s['scope_error_cases']} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--interpreters", default="offline,model")
    p.add_argument("--out", default=str(ROOT / "results"))
    p.add_argument("--cases", default=None, help="cases JSON (default: the frozen benchmark cases)")
    p.add_argument("--tag", default="", help="suffix for the output files, e.g. holdout")
    p.add_argument("--summary", action="store_true", help="only rebuild corrections_benchmark_summary.md")
    p.add_argument("--rescore", action="store_true",
                   help="recompute scores of the stored results (same outputs, current scoring) and rewrite them")
    args = p.parse_args(argv)
    if args.rescore:
        from mirror.interpret.benchmark.score import rescore
        out = Path(args.out)
        for tag, _ in RUNS:
            stem = "corrections_benchmark" + (f"_{tag}" if tag else "")
            if args.tag and tag != args.tag:
                continue
            f = out / f"{stem}.json"
            if not f.exists():
                continue
            rep = json.loads(f.read_text())
            cases_path = ROOT / "mirror" / "interpret" / "benchmark" / rep.get("cases_file", "cases.json")
            rep = rescore(rep, load_cases(cases_path))
            f.write_text(json.dumps(rep, indent=1) + "\n")
            (out / f"{stem}.md").write_text(markdown(rep))
        args.summary = True
    if args.summary:
        out = Path(args.out)
        (out / "corrections_benchmark_summary.md").write_text(summary_markdown(out))
        print((out / "corrections_benchmark_summary.md").read_text())
        return 0
    from mirror.scenes import reference_scene
    scene = reference_scene("shortcut")
    cases = load_cases(Path(args.cases) if args.cases else None)
    base = load_base_spec()
    report = {"benchmark": "correction_compiler", "tag": args.tag,
              "cases_file": str(Path(args.cases).name if args.cases else "cases.json"),
              "cases_sha256": cases["sha256"],
              "n_cases": len(cases["cases"]), "base_spec": base.spec_id, "scene": scene.scene_id,
              "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "runs": [], "notes": []}
    wanted = [x.strip() for x in args.interpreters.split(",") if x.strip()]
    if "offline" in wanted:
        report["runs"].append(run_benchmark(LLMClient(mode="offline"), scene, cases, base))
    if "model" in wanted:
        client, why = model_client()
        if client is None:
            report["notes"].append(f"model run skipped: {why}")
        else:
            run = run_benchmark(client, scene, cases, base, abort_if=_should_stop)
            report["runs"].append(run)
            if run.get("aborted"):
                report["notes"].append(f"model run stopped early: {run['aborted']}")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = "corrections_benchmark" + (f"_{args.tag}" if args.tag else "")
    (out / f"{stem}.json").write_text(json.dumps(report, indent=1) + "\n")
    (out / f"{stem}.md").write_text(markdown(report))
    for run in report["runs"]:
        s = run["summary"]
        print(f"{run['interpreter']:>20} ({run['mode']}): exact {s['exact']}/{s['cases']}, "
              f"false accepts {s['false_accepts']}, false refusals {s['false_refusals']}, "
              f"scope-error cases {s['scope_error_cases']}, errors {s['errors']}")
    for n in report["notes"]:
        print("note:", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
