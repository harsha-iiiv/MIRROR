"""Immutable evidence manifests (derived, offline).

For every stored registration and benchmark this writes one manifest that
lets a reader re-verify the evidence without trusting any summary:

* the freeze and result commits (first commit adding each file) and their times;
* the frozen code hash, recomputed from the freeze commit's own tree with that
  commit's own hashing function (so the hash names an exact commit);
* run times from the stored report, ledger and model-usage log, and the
  ordering freeze -> commit -> run -> first model call;
* model ids (requested and reported), prompt revision and prompt-file hashes
  at the freeze commit, recorded-response (cassette) hashes;
* scene hashes and outcome digests per trial (paired registrations) or
  per-file input/output hashes (other registrations).

Manifests are written once per version directory and never overwritten.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Optional

from .replay import stored_digests

PAIRED = {"eval": "results/eval", "eval_muse": "results/eval_muse"}
PREREG = {"voice": "results/eval_voice", "robustness": "results/eval_robustness",
          "video": "results/eval_video", "video2": "results/eval_video2"}
PROMPT_FILES = ("mirror/interpret/prompts.py", "mirror/interpret/sam_prompts.py", "mirror/interpret/video_observe.py",
                "mirror/interpret/voice.py", "mirror/perception/track.py")


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha_file(p: Path) -> str:
    return sha_bytes(p.read_bytes())


def digest_of(items) -> str:
    """sha256 over a sorted list of (name, sha256) pairs."""
    h = hashlib.sha256()
    for name, s in sorted(items):
        h.update(f"{name}\0{s}\0".encode())
    return h.hexdigest()


def git(repo: Path, *args) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=120,
                          stdin=subprocess.DEVNULL).stdout


def first_commit(repo: Path, rel: str) -> Optional[dict]:
    out = git(repo, "log", "--diff-filter=A", "--format=%H %cI", "--", rel).split()
    return dict(commit=out[-2], committed_at=out[-1]) if len(out) >= 2 else None


def file_at(repo: Path, commit: str, rel: str) -> Optional[bytes]:
    r = subprocess.run(["git", "-C", str(repo), "show", f"{commit}:{rel}"], capture_output=True, timeout=120,
                       stdin=subprocess.DEVNULL)
    return r.stdout if r.returncode == 0 else None


def tree_code_hash(repo: Path, commit: str, kind: str, name: str, exclude) -> dict:
    """The code hash the registration's own hashing function gives on the
    commit's tree (extracted to a scratch directory; nothing in the working
    tree is touched)."""
    with tempfile.TemporaryDirectory(prefix="tree_") as td:
        arc = Path(td) / "t.tar"
        subprocess.run(["git", "-C", str(repo), "archive", "-o", str(arc), commit, "mirror", "scripts", "data"],
                       check=True, timeout=600, stdin=subprocess.DEVNULL)
        with tarfile.open(arc) as tf:
            tf.extractall(td, filter="data")
        if kind == "paired":
            code = ("import json\nfrom mirror.evaluate.paired import code_hash, file_hashes\n"
                    f"ex = tuple({list(exclude or [])!r})\n"
                    "d, n = code_hash(exclude=ex) if ex else code_hash()\n"
                    "try:\n    fh = file_hashes()\nexcept Exception:\n    fh = None\n"
                    "print(json.dumps(dict(code_sha256=d, code_files=n, file_hashes=fh)))")
        else:
            code = ("import json\nfrom mirror.evaluate.prereg import _code\nfrom mirror.evaluate.paired import file_hashes\n"
                    f"d, n, ex, files = _code({name!r})\n"
                    "print(json.dumps(dict(code_sha256=d, code_files=n, file_hashes=file_hashes())))")
        r = subprocess.run([sys.executable, "-c", code], cwd=td, capture_output=True, text=True, timeout=600,
                           env={"PYTHONPATH": td, "PATH": "/usr/bin:/bin", "MUJOCO_GL": "osmesa",
                                "MIRROR_LLM_MODE": "offline", "MIRROR_META_MODE": "replay"}, stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            return dict(error=r.stderr.strip().splitlines()[-1][:300] if r.stderr.strip() else "failed")
        return json.loads(r.stdout.strip().splitlines()[-1])


def _iso(unix: Optional[float]) -> Optional[str]:
    return dt.datetime.fromtimestamp(unix, dt.timezone.utc).isoformat() if unix else None


def _jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def prompts_at(repo: Path, commit: str) -> dict:
    out = {}
    for rel in PROMPT_FILES:
        b = file_at(repo, commit, rel)
        if b is not None:
            out[rel] = sha_bytes(b)
    rev = None
    b = file_at(repo, commit, "mirror/interpret/prompts.py")
    if b:
        for line in b.decode().splitlines():
            if line.startswith("PROMPT_REVISION"):
                rev = line.split("=", 1)[1].strip().strip('"\'')
    return dict(prompt_revision=rev, prompt_file_sha256=out)


def usage_models(rows: list[dict]) -> dict:
    out: dict = {}
    for u in rows:
        m = u.get("model") or u.get("requested_model") or "unknown"
        e = out.setdefault(m, dict(calls=0, from_cassette=0, reported_models=set()))
        e["calls"] += 1
        e["from_cassette"] += bool(u.get("from_cassette"))
        if u.get("reported_model"):
            e["reported_models"].add(u["reported_model"])
    return {k: dict(v, reported_models=sorted(v["reported_models"])) for k, v in out.items()}


def dir_hashes(d: Path, pattern: str = "*") -> dict:
    files = sorted(p for p in d.rglob(pattern) if p.is_file())
    items = [(p.relative_to(d).as_posix(), sha_file(p)) for p in files]
    return dict(files=len(items), digest=digest_of(items))


def paired_manifest(repo: Path, name: str, rel: str) -> dict:
    d = repo / rel
    man = json.loads((d / "manifest.json").read_text())
    rep = json.loads((d / "report.json").read_text())
    fz = first_commit(repo, f"{rel}/freeze.json")
    res = first_commit(repo, f"{rel}/report.json")
    frozen_file = json.loads((d / "freeze.json").read_text()) if (d / "freeze.json").exists() else None
    tree = tree_code_hash(repo, fz["commit"], "paired", name, man.get("code_exclude")) if fz else None
    prov = man.get("spec_provenance", {})
    cas = [dict(call=r.get("call"), file=r.get("cassette_file"), requested_model=r.get("requested_model"),
                reported_model=r.get("reported_model"),
                sha256=sha_file(repo / r["cassette_file"]) if r.get("cassette_file") and (repo / r["cassette_file"]).exists() else None)
           for r in prov.get("requests", [])]
    ledger = _jsonl(d / "ledger.jsonl")
    ts = sorted(e["unix"] for e in ledger if isinstance(e.get("unix"), (int, float)))
    trials = stored_digests(rep)
    run_start = dt.datetime.fromisoformat(rep["started_at"]).timestamp()
    return dict(
        registration=name, kind="paired", directory=rel,
        freeze=dict(file=f"{rel}/freeze.json", sha256=sha_file(d / "freeze.json"), created_at=man.get("created_at"),
                    first_commit=fz, manifest_sha256=sha_file(d / "manifest.json"),
                    manifest_first_commit=first_commit(repo, f"{rel}/manifest.json"),
                    freeze_equals_manifest_on_frozen_fields=bool(frozen_file) and all(
                        frozen_file.get(k) == man.get(k) for k in ("seeds", "spec_a", "spec_b", "code_sha256",
                                                                   "strata", "code_exclude"))),
        code=dict(frozen_code_sha256=man.get("code_sha256"), frozen_code_files=man.get("code_files"),
                  code_exclude=man.get("code_exclude"), freeze_commit_tree=tree,
                  freeze_commit_tree_matches=bool(tree and tree.get("code_sha256") == man.get("code_sha256")),
                  frozen_file_hashes=man.get("file_hashes"), frozen_versions=man.get("versions")),
        specs=dict(spec_a=man.get("spec_a"), spec_b=man.get("spec_b"), interpreter=prov.get("interpreter"),
                   requested_model=prov.get("requested_model"), cassette_dir=prov.get("cassette_dir"),
                   recorded_responses=cas, demo_sha256=prov.get("demo_sha256"),
                   offline_agrees=prov.get("offline_agrees"), correction_text=prov.get("correction_text")),
        prompts=prompts_at(repo, fz["commit"]) if fz else None,
        run=dict(started_at=rep.get("started_at"), finished_at=rep.get("finished_at"),
                 ledger_entries=len(ledger), ledger_first=_iso(ts[0]) if ts else None,
                 ledger_last=_iso(ts[-1]) if ts else None, results_first_commit=res,
                 report_sha256=sha_file(d / "report.json"), ledger_sha256=sha_file(d / "ledger.jsonl")),
        ordering=dict(
            freeze_created_before_commit=_before(man.get("created_unix"), fz and fz["committed_at"]),
            freeze_commit_before_run_start=_before(fz and fz["committed_at"], run_start),
            freeze_commit_before_first_layout=_before(fz and fz["committed_at"], ts[0] if ts else None),
            note="commit times are the committer's clock, not an external timestamp"),
        scenes=dict(n=sum(len(v) for v in trials.values()),
                    digest=digest_of([(f"{k}:{t['seed']}", t["scene_sha256"]) for k, v in trials.items() for t in v])),
        outcomes=dict(
            definition="outcome = status, completion, plan status, hard and advisory violations, forbidden contacts, "
                       "per object placed/released/settled; detail = outcome + beads kept + route labels",
            outcome_digest=digest_of([(f"{k}:{t['seed']}:{s}", t[s]["outcome_sha256"]) for k, v in trials.items()
                                      for t in v for s in ("a", "b")]),
            detail_digest=digest_of([(f"{k}:{t['seed']}:{s}", t[s]["detail_sha256"]) for k, v in trials.items()
                                     for t in v for s in ("a", "b")]),
            trials={k: [dict(seed=t["seed"], scene_id=t["scene_id"], scene_sha256=t["scene_sha256"],
                             **{t[s]["condition"]: dict(outcome_sha256=t[s]["outcome_sha256"],
                                                        detail_sha256=t[s]["detail_sha256"]) for s in ("a", "b")})
                        for t in v] for k, v in trials.items()}),
    )


def _ts(x) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    return dt.datetime.fromisoformat(str(x).replace("Z", "+00:00")).timestamp()


def _before(a, b) -> Optional[bool]:
    a, b = _ts(a), _ts(b)
    return None if a is None or b is None else a <= b


def prereg_manifest(repo: Path, name: str, rel: str) -> dict:
    d = repo / rel
    fz = json.loads((d / "freeze.json").read_text())
    fzc = first_commit(repo, f"{rel}/freeze.json")
    rep = json.loads((d / "report.json").read_text()) if (d / "report.json").exists() else None
    resc = first_commit(repo, f"{rel}/report.json") if rep else None
    design = json.loads((d / "design.json").read_text())
    inputs = json.loads((d / "inputs.json").read_text())
    tree = tree_code_hash(repo, fzc["commit"], "prereg", name, None) if fzc else None
    usage = []
    for p in sorted(d.rglob("*usage*.jsonl")):
        usage += [dict(u, _file=p.relative_to(d).as_posix()) for u in _jsonl(p)]
    uts = sorted(u["ts"] for u in usage if isinstance(u.get("ts"), (int, float)))
    cass = {p.relative_to(d).as_posix(): dir_hashes(p, "*.json") for p in sorted(d.glob("**/cassettes*")) if p.is_dir()}
    outs = {}
    if rep is not None:
        outs["report.json"] = sha_file(d / "report.json")
    for sub in ("seeds", "tracks", "plan_report"):
        if (d / sub).is_dir():
            outs[sub] = dir_hashes(d / sub)
    for p in sorted((d / "attempt_1").glob("*")) if (d / "attempt_1").is_dir() else []:
        outs[f"attempt_1/{p.name}"] = dir_hashes(p) if p.is_dir() else sha_file(p)
    start = rep.get("started_unix") if rep else None
    return dict(
        registration=name, kind="prereg", directory=rel, status=rep.get("status") if rep else "no report (see notes)",
        freeze=dict(file=f"{rel}/freeze.json", sha256=sha_file(d / "freeze.json"), created_at=fz.get("created_at"),
                    first_commit=fzc, design_sha256=fz.get("design_sha256"), inputs_sha256=fz.get("inputs_sha256"),
                    design_file_sha256=sha_file(d / "design.json"), inputs_file_sha256=sha_file(d / "inputs.json"),
                    design_matches=fz.get("design_sha256") == sha_file(d / "design.json"),
                    inputs_matches=fz.get("inputs_sha256") == sha_file(d / "inputs.json"),
                    amendments=fz.get("amendments")),
        code=dict(frozen_code_sha256=fz.get("code_sha256"), frozen_code_files=fz.get("code_files"),
                  code_scope=fz.get("code_scope"), freeze_commit_tree=tree,
                  freeze_commit_tree_matches=bool(tree and tree.get("code_sha256") == fz.get("code_sha256")),
                  report_freeze_code_sha256=rep.get("freeze_code_sha256") if rep else None,
                  frozen_file_hashes=fz.get("file_hashes"), frozen_versions=fz.get("versions")),
        models=dict(design=design.get("models") or design.get("observe_models"), usage=usage_models(usage)),
        prompts=prompts_at(repo, fzc["commit"]) if fzc else None,
        recorded_responses=cass,
        inputs=dict(files=len(inputs.get("files", [])),
                    digest=digest_of([(f["path"], f["sha256"]) for f in inputs.get("files", [])])),
        run=dict(started_at=_iso(start), first_model_call=_iso(uts[0]) if uts else None,
                 last_model_call=_iso(uts[-1]) if uts else None, model_calls=len(usage),
                 results_first_commit=resc, ledger_sha256=sha_file(d / "ledger.jsonl")),
        ordering=dict(
            freeze_created_before_commit=_before(fz.get("created_unix"), fzc and fzc["committed_at"]),
            freeze_commit_before_run_start=_before(fzc and fzc["committed_at"], start),
            freeze_commit_before_first_model_call=_before(fzc and fzc["committed_at"], uts[0] if uts else None),
            note="commit times are the committer's clock, not an external timestamp"),
        outputs=outs,
    )


def benchmark_manifests(repo: Path, chronology: Optional[dict]) -> dict:
    out = {}
    for p in sorted((repo / "results").glob("corrections_benchmark*.json")):
        r = json.loads(p.read_text())
        fc = first_commit(repo, f"results/{p.name}")
        runs = []
        for run in r["runs"]:
            runs.append(dict(interpreter=run["interpreter"], mode=run["mode"], models_returned=run.get("models_returned"),
                             prompt_revision=run.get("prompt_revision"),
                             exact=f"{run['summary']['exact']}/{run['summary']['cases']}",
                             cases_digest=digest_of([(c["id"], hashlib.sha256(json.dumps(
                                 c["result"], sort_keys=True).encode()).hexdigest()) for c in run["cases"]])))
        out[p.name] = dict(file=f"results/{p.name}", sha256=sha_file(p), tag=r.get("tag"),
                           cases_file=r.get("cases_file", "cases.json"), cases_sha256=r.get("cases_sha256"),
                           generated_at=r.get("generated_at"), first_commit=fc,
                           prompts=prompts_at(repo, fc["commit"]) if fc else None, runs=runs,
                           chronology=(chronology or {}).get("results", {}).get(p.name))
    cas = {}
    for d in sorted({q.parent for q in (repo / "data/cassettes/corrections_benchmark").rglob("*.json")}):
        cas[d.relative_to(repo).as_posix()] = dir_hashes(d, "*.json") | dict(
            note="each file name is the hash key of its exact request (prompt included)")
    for d in ("data/cassettes_muse", "data/cassettes_meta"):
        if (repo / d).is_dir():
            cas[d] = dir_hashes(repo / d, "*.json")
    return dict(results=out, recorded_responses=cas)


def build(repo: Path, out: Path, only=None) -> list[Path]:
    written = []
    out.mkdir(parents=True, exist_ok=True)
    chron = repo / "results/reanalysis_v1/chronology/report.json"
    chron = json.loads(chron.read_text()) if chron.exists() else None
    jobs = [(n, lambda n=n, r=r: paired_manifest(repo, n, r)) for n, r in PAIRED.items()]
    jobs += [(n, lambda n=n, r=r: prereg_manifest(repo, n, r)) for n, r in PREREG.items()]
    jobs += [("corrections_benchmark", lambda: benchmark_manifests(repo, chron))]
    head = git(repo, "rev-parse", "HEAD").strip()
    for name, fn in jobs:
        if only and name not in only:
            continue
        dst = out / f"{name}.json"
        if dst.exists():
            print(f"{dst} exists; manifests are written once (new version directory for changes)")
            continue
        m = fn()
        m["manifest_version"] = "manifests_v1"
        m["built_from_commit"] = head
        dst.write_text(json.dumps(m, indent=1, sort_keys=True) + "\n")
        written.append(dst)
        print(dst, flush=True)
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--out", default="results/manifests_v1")
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args(argv)
    repo = Path(a.repo).resolve()
    build(repo, repo / a.out, a.only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
