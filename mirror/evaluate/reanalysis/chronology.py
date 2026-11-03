"""Correction-benchmark chronology (derived, offline).

Collects every in-repository timestamp and hash that bears on whether the
correction cases were fixed before the interpreters were run on them:

* ``frozen_at`` written into each case file (hand-written metadata);
* ``generated_at`` in each result file: written by scripts/bench_corrections.py
  in UTC ("...Z") when the run STARTS, after the case file is read and hashed;
* ``recorded_at`` in each recorded model response (cassette): written in UTC
  when the response arrives;
* the case-file sha256 recorded in each result vs the file as committed;
* the gold answers copied into each result vs the committed case file;
* a re-score of every stored result with the committed scorer;
* the first commit that contains each file.

Nothing is re-run; no model is called.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path

BENCH = Path("mirror/interpret/benchmark")
CASSETTES = Path("data/cassettes/corrections_benchmark")
PDT = dt.timezone(dt.timedelta(hours=-7))


def _ts(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _pdt(s: str) -> str:
    return _ts(s).astimezone(PDT).isoformat()


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _first_commit(repo: Path, rel: str):
    try:
        out = subprocess.run(["git", "-C", str(repo), "log", "--diff-filter=A", "--format=%H %cI", "--", rel],
                             capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return None
    return dict(commit=out[-2], committed_at=out[-1]) if len(out) >= 2 else None


def cassette_times(repo: Path, case_files: dict) -> dict:
    """recorded_at range per cassette directory and case set, matched by the
    case text appearing in the recorded request."""
    texts = {name: {c["id"]: json.dumps(c["text"])[1:-1] for c in doc["cases"]} for name, doc in case_files.items()}
    out = {}
    for d in sorted({p.parent for p in (repo / CASSETTES).rglob("*.json")}):
        for p in sorted(d.glob("*.json")):
            c = json.loads(p.read_text())
            msg = json.dumps(c.get("request", {}).get("messages", []))
            hit = [(n, i) for n, ts in texts.items() for i, t in ts.items() if t in msg]
            key = (str(d.relative_to(repo)), hit[0][0] if len(hit) == 1 else ("ambiguous" if hit else "unmatched"))
            e = out.setdefault(key, dict(times=[], models=set(), cases=set()))
            e["times"].append(c.get("recorded_at"))
            e["models"].add(c.get("request", {}).get("model"))
            if len(hit) == 1:
                e["cases"].add(hit[0][1])
    res = {}
    for (d, n), e in out.items():
        t = sorted(x for x in e["times"] if x)
        res[f"{d}::{n}"] = dict(directory=d, case_set=n, cassettes=len(e["times"]), distinct_cases=len(e["cases"]),
                                models=sorted(m for m in e["models"] if m),
                                first_recorded_at=t[0] if t else None, last_recorded_at=t[-1] if t else None,
                                first_recorded_at_pdt=_pdt(t[0]) if t else None,
                                last_recorded_at_pdt=_pdt(t[-1]) if t else None)
    return res


def analyse(repo: Path) -> dict:
    from mirror.interpret.benchmark.score import load_cases, rescore
    case_files = {}
    for name in ("cases.json", "cases_holdout.json"):
        p = repo / BENCH / name
        case_files[name] = json.loads(p.read_text())
    files = {}
    for name, doc in case_files.items():
        p = repo / BENCH / name
        files[name] = dict(sha256=_sha(p), frozen_at=doc.get("frozen_at"), n_cases=len(doc["cases"]),
                           first_commit=_first_commit(repo, str(BENCH / name)))
    results = {}
    for p in sorted((repo / "results").glob("corrections_benchmark*.json")):
        r = json.loads(p.read_text())
        cf = r.get("cases_file", "cases.json")
        doc = case_files.get(cf)
        gold = {c["id"]: c["gold"] for c in doc["cases"]} if doc else {}
        gold_equal = all(row.get("gold") == gold.get(row["id"]) for run in r["runs"] for row in run["cases"])
        stored = [(run["interpreter"], run["summary"]) for run in r["runs"]]
        re_r = rescore(json.loads(p.read_text()), load_cases(repo / BENCH / cf))
        # the committed scorer adds fields later runs carry (prompt-overlap split); compare the stored keys
        rescored_equal = all(all(a["summary"].get(k) == v for k, v in b[1].items())
                             for a, b in zip(re_r["runs"], stored))
        added = sorted({k for a, b in zip(re_r["runs"], stored) for k in a["summary"] if k not in b[1]})
        results[p.name] = dict(
            tag=r.get("tag"), cases_file=cf, cases_sha256_at_run=r.get("cases_sha256"),
            cases_sha256_matches_committed=r.get("cases_sha256") == files.get(cf, {}).get("sha256"),
            generated_at=r.get("generated_at"),
            generated_at_pdt=_pdt(r["generated_at"]) if r.get("generated_at") else None,
            generated_at_meaning="run start (UTC), set after the case file was read and hashed",
            interpreters=[f"{run['interpreter']} ({run['mode']})" for run in r["runs"]],
            exact=[f"{run['summary']['exact']}/{run['summary']['cases']}" for run in r["runs"]],
            gold_in_result_equals_committed_cases=gold_equal,
            rescore_with_committed_scorer_reproduces_summary=rescored_equal,
            rescore_fields_added_by_later_scorer=added,
            first_commit=_first_commit(repo, f"results/{p.name}"))
    cas = cassette_times(repo, case_files)
    # ordering facts for the first held-out run
    hold = results.get("corrections_benchmark_holdout.json", {})
    v1 = cas.get(f"{CASSETTES}/prompt_v1::cases_holdout.json", {})
    frozen = files["cases_holdout.json"]["frozen_at"]
    order = dict(
        holdout_frozen_at_written=frozen,
        holdout_first_run_started=hold.get("generated_at_pdt"),
        holdout_first_model_response=v1.get("first_recorded_at_pdt"),
        holdout_last_model_response=v1.get("last_recorded_at_pdt"),
        frozen_at_is_after_run_start=bool(frozen and hold.get("generated_at") and _ts(frozen) > _ts(hold["generated_at"])),
        frozen_at_is_after_last_model_response=bool(frozen and v1.get("last_recorded_at") and _ts(frozen) > _ts(v1["last_recorded_at"])),
        minutes_frozen_at_after_run_start=round((_ts(frozen) - _ts(hold["generated_at"])).total_seconds() / 60, 2)
        if frozen and hold.get("generated_at") else None)
    fz = files["cases.json"]["frozen_at"]
    blind = results.get("corrections_benchmark.json", {})
    vf = cas.get(f"{CASSETTES}/prompt_v1::cases.json", {})
    order_frozen_set = dict(
        frozen_at_written=fz, first_run_started=blind.get("generated_at_pdt"),
        first_model_response=vf.get("first_recorded_at_pdt"), last_model_response=vf.get("last_recorded_at_pdt"),
        frozen_at_is_after_run_start=bool(fz and blind.get("generated_at") and _ts(fz) > _ts(blind["generated_at"])),
        minutes_frozen_at_after_run_start=round((_ts(fz) - _ts(blind["generated_at"])).total_seconds() / 60, 2)
        if fz and blind.get("generated_at") else None)
    return dict(version="reanalysis_v1/chronology", case_files=files, results=results, cassettes=cas,
                ordering_holdout=order, ordering_frozen_set=order_frozen_set)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--out", default="results/reanalysis_v1/chronology/report.json")
    a = ap.parse_args(argv)
    repo = Path(a.repo).resolve()
    rep = analyse(repo)
    out = repo / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=1, sort_keys=True) + "\n")
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
