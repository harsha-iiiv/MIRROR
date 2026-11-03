#!/usr/bin/env python
"""Record Muse Spark responses for correction prompt revision 4.

Writes NEW cassette directories only (data/cassettes_muse_rev4/<set>/) and a
report under results/dev_v2/prompt_rev4/. Existing recordings, benchmark
results and frozen files are never touched; the script refuses to write into a
non-empty cassette directory unless --resume is given.

Without --approved it is a dry run: no network, it lists every planned call,
builds each revision-4 request and estimates its cost from the measured
revision-3 Muse Spark correction recordings (data/cassettes_muse/
corrections_benchmark: 220 calls) and the list prices in mirror/evaluate/costs.py.

With --approved it records, under a hard spend cap: before every model request
it checks that the spend so far plus a reserve for one worst-case request stays
within --cap-usd, and stops otherwise. Spend is computed from the API's usage
fields at list price.

    python scripts/record_prompt_rev4.py                      # dry run
    python scripts/record_prompt_rev4.py --approved --cap-usd 4.00
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODEL = "muse-spark-1.3"
CASSETTES = ROOT / "data" / "cassettes_muse_rev4"
MEASURED = ROOT / "data" / "cassettes_muse" / "corrections_benchmark"
CHARS_PER_TOKEN_FALLBACK = 3.06      # measured on revision-3 requests (6726 chars -> 2197 prompt tokens)
RESERVE_USD = 0.10                   # one request with the longest recorded output (10097 tokens) plus a repair round


def planned_sets():
    """[(set name, [(base spec, text, context, prior turns)])] in recording order."""
    from mirror.interpret.benchmark.score import load_base_spec, load_cases
    from mirror.scenes import reference_scene
    from tests.interpret.paraphrases import DEV_PARAPHRASES, HELDOUT_PARAPHRASES
    from tests.interpret.test_capabilities import capability_base_spec
    from tests.interpret.understanding_matrix import MATRIX
    scene = reference_scene("shortcut")
    base = load_base_spec()
    out = []
    for name, f in (("benchmark", "cases.json"), ("benchmark_holdout", "cases_holdout.json")):
        cases = load_cases(ROOT / "mirror" / "interpret" / "benchmark" / f)
        out.append((name, [(base, c["text"], {"scene": scene}, []) for c in cases["cases"]]))
    cap = capability_base_spec()
    out.append(("paraphrases_dev", [(cap, t, {}, []) for t, _ in DEV_PARAPHRASES]))
    out.append(("paraphrases_heldout", [(cap, t, {}, []) for t, _ in HELDOUT_PARAPHRASES]))
    out.append(("understanding", [(cap, c["text"], dict(c.get("context") or {}), list(c.get("turns") or []))
                                  for c in MATRIX]))
    return out


def measured_rev3():
    v = [json.loads(p.read_text()) for p in sorted(MEASURED.glob("*.json"))]
    pin = [x["response"]["usage"]["prompt_tokens"] for x in v]
    pout = sorted(x["response"]["usage"]["completion_tokens"] for x in v)
    chars = [sum(len(m["content"]) for m in x["request"]["messages"]) for x in v]
    return {"calls": len(v), "prompt_tokens_mean": statistics.mean(pin),
            "completion_tokens_mean": statistics.mean(pout),
            "completion_tokens_p95": pout[int(0.95 * (len(pout) - 1))], "completion_tokens_max": pout[-1],
            "chars_per_token": sum(chars) / sum(pin), "repairs": sum(1 for x in v if x.get("attempts", 1) > 1)}


def estimate(sets, meas):
    from mirror.evaluate.costs import PRICES
    from mirror.interpret.correct import _model_context
    from mirror.interpret.prompts import correct_messages_v4
    p = PRICES[MODEL]
    rows, total_mean, total_p95 = [], 0.0, 0.0
    for name, items in sets:
        n_req, tok = 0, []
        for spec, text, ctx, turns in items:
            for t in list(turns) + [text]:
                if t.lower().startswith(("undo", "go back")):
                    continue                     # reverts are handled without a model call
                scene = ctx.get("scene")
                kinds = sorted({o.kind for o in scene.objects}) if scene is not None else None
                msgs = correct_messages_v4(spec, t, kinds, _model_context(ctx))
                tok.append(sum(len(m["content"]) for m in msgs) / meas["chars_per_token"])
                n_req += 1
        mean = sum(x * p["input"] + meas["completion_tokens_mean"] * p["output"] for x in tok) / 1e6
        p95 = sum(x * p["input"] + meas["completion_tokens_p95"] * p["output"] for x in tok) / 1e6
        rows.append({"set": name, "sentences": len(items), "model_calls": n_req,
                     "prompt_tokens_est_mean": round(statistics.mean(tok)), "usd_mean": round(mean, 3),
                     "usd_if_every_output_at_p95": round(p95, 3)})
        total_mean += mean
        total_p95 += p95
    calls = sum(r["model_calls"] for r in rows)
    return {"rows": rows, "model_calls": calls, "max_requests_with_one_repair_each": 2 * calls,
            "usd_mean": round(total_mean, 2), "usd_if_every_output_at_p95": round(total_p95, 2),
            "basis": {"model": MODEL, "prices_per_1m": p, "measured_rev3": meas}}


def record(sets, cap_usd: float, out: Path, resume: bool, client_factory=None, cassettes: Path = CASSETTES) -> dict:
    from mirror.evaluate.costs import record_cost
    from mirror.interpret import LLMClient
    from mirror.interpret.benchmark.score import run_case
    from mirror.interpret.llm import LLMError
    from mirror.interpret.outcome import interpret
    report = {"model": MODEL, "prompt_revision": "4", "cap_usd": cap_usd, "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "sets": {}, "stopped": None}
    spent = lambda c: sum(record_cost(u) for u in c.usage)
    total = 0.0
    for name, items in sets:
        cdir = cassettes / name
        if cdir.exists() and any(cdir.iterdir()) and not resume:
            raise SystemExit(f"{cdir} is not empty; refusing to overwrite recordings (use --resume to add missing ones)")
        client = (client_factory(cdir) if client_factory else
                  LLMClient.from_env(model=MODEL, mode="record", cassette_dir=cdir, usage_log_path=out / "usage.jsonl"))
        orig = client.complete_json

        def guarded(*a, _orig=orig, _c=client, **k):
            if total + spent(_c) + RESERVE_USD > cap_usd:
                raise LLMError(f"spend cap reached: {total + spent(_c):.3f} of {cap_usd:.2f} USD")
            return _orig(*a, **k)
        client.complete_json = guarded
        rows = []
        for spec, text, ctx, turns in items:
            versions = [spec]
            for t in turns:
                o, new = interpret(versions, t, client=client, context=ctx, prompt_revision="4")
                if new is not None:
                    versions.append(new)
            o, _ = interpret(versions, text, client=client, context=ctx, prompt_revision="4")
            rows.append({"text": text, "turns": turns, "outcome": o.model_dump(mode="json")})
            if "spend cap reached" in o.message:
                report["stopped"] = o.message
                break
        report["sets"][name] = {"rows": rows, "usd": round(spent(client), 4), "calls": len(client.usage)}
        total += spent(client)
        if report["stopped"]:
            break
    report["usd_total"] = round(total, 4)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--approved", action="store_true", help="make paid model calls (needs the owner's approval)")
    ap.add_argument("--cap-usd", type=float, default=4.00)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "results" / "dev_v2" / "prompt_rev4"))
    args = ap.parse_args(argv)
    sets = planned_sets()
    est = estimate(sets, measured_rev3())
    out = Path(args.out)
    if not args.approved:
        print(json.dumps(est, indent=1))
        print(f"\nDry run: {est['model_calls']} model calls (at most {est['max_requests_with_one_repair_each']} "
              f"requests with one repair each), about ${est['usd_mean']} expected; hard cap ${args.cap_usd:.2f}. "
              "No request was made.")
        return 0
    out.mkdir(parents=True, exist_ok=True)
    (out / "estimate.json").write_text(json.dumps(est, indent=1) + "\n")
    rep = record(sets, args.cap_usd, out, args.resume)
    (out / "recording.json").write_text(json.dumps(rep, indent=1) + "\n")
    print(f"recorded {sum(s['calls'] for s in rep['sets'].values())} calls, ${rep['usd_total']:.3f}"
          + (f"; stopped: {rep['stopped']}" if rep["stopped"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
