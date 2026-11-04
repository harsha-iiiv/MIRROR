"""One recorded Muse Spark compile of the film's correction sentence, compared with the offline rule interpreter.

Record mode: an existing cassette is served (no call); otherwise exactly one API call is made and saved as a cassette.
Before a live call, the spend ledger (--ledger, default MIRROR_PROJECT_LEDGER, else OUT.spend.jsonl) is checked
under a lock against --cap-usd (default $8) and the call is appended to it. The API key is read from MODEL_API_KEY in
the process environment only.

    MODEL_API_KEY=... python film/take/spark_compile.py --phase2 DIR --cassettes DIR --out spark.json
"""
from __future__ import annotations
import argparse, fcntl, json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from mirror.contracts import SceneState, TaskSpec  # noqa: E402
from mirror.interpret import LLMClient  # noqa: E402
from mirror.interpret.correct import compile_correction  # noqa: E402

DEFAULT_CAP = 8.0
TEXT = "Keep the mug at least 6 cm away from the divider while carrying it."
IN_USD, OUT_USD = 1.25e-6, 4.25e-6   # per token, as in the D1 ledger's muse-spark-1.3 estimates


def scoped(ops):
    out = []
    for o in ops:
        v = o.get("value") or {}
        out.append(dict(op=o["op"], type=v.get("type"), applies_to=(v.get("applies_to") or {}).get("kind"), phase=v.get("phase"),
                        params=v.get("params")))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase2", required=True)
    ap.add_argument("--cassettes", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ledger", default=os.environ.get("MIRROR_PROJECT_LEDGER") or None,
                    help="append-only spend ledger (default MIRROR_PROJECT_LEDGER, else OUT.spend.jsonl)")
    ap.add_argument("--cap-usd", type=float, default=DEFAULT_CAP)
    a = ap.parse_args(argv)
    LEDGER = Path(a.ledger) if a.ledger and a.ledger.lower() != "none" else Path(a.out).with_suffix(".spend.jsonl")
    LOCK = LEDGER.with_suffix(".lock")
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    p2 = Path(a.phase2)
    scene = SceneState.model_validate_json((p2 / "scene.json").read_text())
    spec = TaskSpec.model_validate_json((p2 / "spec_intent.json").read_text())
    usage_log = Path(a.out).with_suffix(".usage.jsonl")
    client = LLMClient.from_env(mode="record", cassette_dir=Path(a.cassettes), usage_log_path=usage_log)
    with open(LOCK, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        spent = sum(json.loads(l).get("est_cost_usd", 0) for l in LEDGER.read_text().splitlines() if l.strip()) if LEDGER.exists() else 0.0
        if spent + 0.05 > a.cap_usd:
            raise SystemExit(f"spend {spent:.3f} would exceed the ${a.cap_usd:.2f} cap")
        t0 = time.time()
        new, rec = compile_correction(spec, TEXT, client, context={"scene": scene})
        off, roff = compile_correction(spec, TEXT, LLMClient.offline_client(), context={"scene": scene})
        calls = [json.loads(l) for l in usage_log.read_text().splitlines()] if usage_log.exists() else []
        live = [c for c in calls if not c.get("from_cassette", c.get("cassette", False))]
        cost = 0.0
        for c in live:
            cost += (c.get("prompt_tokens") or 0) * IN_USD + (c.get("completion_tokens") or 0) * OUT_USD
        if live:
            with open(LEDGER, "a") as f:
                f.write(json.dumps(dict(time=time.strftime("%Y-%m-%dT%H:%M:%S%z"), track="film", model=client.model, endpoint="/responses",
                                        est_cost_usd=round(cost, 6), what="compile film correction sentence (record mode)",
                                        calls=len(live))) + "\n")
    ops_m = [o.model_dump(mode="json") for o in rec.diff]
    ops_o = [o.model_dump(mode="json") for o in roff.diff]
    res = dict(text=TEXT, model=client.model, record_model=getattr(rec, "model", None), from_cassette=not live, live_calls=len(live),
               est_cost_usd=round(cost, 6), wall_s=round(time.time() - t0, 1), ops_model=ops_m, ops_offline=ops_o,
               scoped_model=scoped(ops_m), scoped_offline=scoped(ops_o), same_scoped_rule=scoped(ops_m) == scoped(ops_o),
               usage=calls)
    Path(a.out).write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(dict(same=res["same_scoped_rule"], live=len(live), cost=res["est_cost_usd"], model=scoped(ops_m), offline=scoped(ops_o))))


if __name__ == "__main__":
    main()
