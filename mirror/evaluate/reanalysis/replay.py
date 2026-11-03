"""Offline, network-blocked replay of a paired registration (derived).

Re-runs the frozen layouts of a stored paired registration (results/eval,
results/eval_muse) at the current code, with every network call refused,
the specification pair rebuilt from the registration's recorded model
responses, and evaluation-layout accesses written to a scratch ledger (never
to the registration's own ledger). The stored report is not touched.

Each trial is compared with the stored report at three levels:

* ``scene``: the layout's sha256;
* ``outcome``: status, completion, constraint violations (hard and advisory),
  forbidden contacts, and per object placed / released / settled;
* ``detail``: the outcome plus beads kept per container and route labels.

A match at ``outcome`` but not ``detail`` is an outcome-level reproduction,
not bit-exact determinism.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import socket
import sys
import tempfile
import time
from pathlib import Path


def _h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def outcome_view(rep: dict) -> dict:
    return dict(status=rep.get("status"), completion=rep.get("completion"), plan_status=rep.get("plan_status"),
                violations=sorted(v.get("constraint_id") for v in rep.get("violations", [])),
                advisory=sorted(v.get("constraint_id") for v in rep.get("advisory_violations", [])),
                forbidden_contacts=len(rep.get("forbidden_contacts", [])),
                objects={o["object_id"]: [o.get("in_target"), o.get("released"), o.get("settled")]
                         for o in rep.get("outcomes", [])})


def detail_view(rep: dict) -> dict:
    d = outcome_view(rep)
    d["beads"] = {o["object_id"]: [o.get("beads_in_container"), o.get("beads_total")]
                  for o in rep.get("outcomes", []) if o.get("beads_total")}
    d["routes"] = list(rep.get("route_labels", []))
    return d


def numeric_view(rep: dict) -> dict:
    """Continuous quantities (not hashed): per object tilts, plus executed time and physics steps."""
    return dict(execution_time_s=rep.get("execution_time_s"), physics_steps=rep.get("physics_steps"),
                objects={o["object_id"]: dict(final_tilt_deg=o.get("final_tilt_deg"),
                                              max_transport_tilt_deg=o.get("max_transport_tilt_deg"))
                         for o in rep.get("outcomes", [])})


def trial_digests(trial: dict) -> dict:
    out = dict(seed=trial["seed"], scene_id=trial["scene_id"], scene_sha256=trial["scene_sha256"])
    for side in ("a", "b"):
        r = trial[side]["report"]
        out[side] = dict(condition=trial[side]["condition"], outcome_sha256=_h(outcome_view(r)),
                         detail_sha256=_h(detail_view(r)), outcome=outcome_view(r), detail=detail_view(r),
                         numeric=numeric_view(r))
    return out


def stored_digests(report: dict) -> dict:
    return {name: [trial_digests(t) for t in st["trials"]] for name, st in report["strata"].items()}


@contextlib.contextmanager
def no_network():
    calls = {"n": 0}

    def refuse(*a, **k):
        calls["n"] += 1
        raise OSError("network access refused during replay")
    saved = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo)
    socket.socket.connect = socket.socket.connect_ex = refuse
    socket.create_connection = socket.getaddrinfo = refuse
    try:
        yield calls
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo = saved


def replay(reg_dir: Path, repo: Path, workers: int = 2, strata=None) -> dict:
    os.environ["MIRROR_LLM_MODE"] = "replay"
    os.environ["MIRROR_META_MODE"] = "replay"
    scratch = Path(tempfile.mkdtemp(prefix="replay_"))
    os.environ["MIRROR_EVAL_LEDGER"] = str(scratch / "ledger.jsonl")
    from mirror.evaluate.final import blocked_case, default_specs
    from mirror.evaluate.paired import _spec_ref, code_hash, corrected_rules, paired_eval
    from mirror.evaluate.specs import evaluation_specs
    man_src = reg_dir / "manifest.json"
    man = json.loads(man_src.read_text())
    stored = json.loads((reg_dir / "report.json").read_text())
    # a scratch copy marked as a replay: the frozen copy is never modified
    tmp_man = scratch / "manifest.json"
    tmp_man.write_text(json.dumps(dict(man, kind="rehearsal")))
    prov = man.get("spec_provenance", {})
    started = time.time()
    with no_network() as net:
        if prov.get("requested_model"):
            a, b, _p = evaluation_specs(source="replay", model=prov["requested_model"],
                                        cassette_dir=repo / prov["cassette_dir"])
        else:
            a, b, _p = default_specs(prov.get("spec_source", "replay"))
        spec_ok = dict(a=_spec_ref(a) == man["spec_a"], b=_spec_ref(b) == man["spec_b"])
        digest, _n = code_hash(exclude=tuple(man.get("code_exclude") or ())) if man.get("code_exclude") else code_hash()
        new = {}
        for st in man["strata"]:
            if strata and st["name"] not in strata:
                continue
            import inspect
            kw = dict(split=st["split"], family=st["family"], manifest_path=tmp_man, workers=workers, n_boot=200)
            params = inspect.signature(paired_eval).parameters
            for k, v in (("stratum", st["name"]), ("require_manifest", True), ("allow_code_drift", True)):
                if k in params:
                    kw[k] = v
            rep = paired_eval(a, b, st["seeds"], **kw)
            new[st["name"]] = [trial_digests(t) for t in json.loads(rep.model_dump_json())["trials"]]
        ref = blocked_case(a, b, rules=corrected_rules(a, b))
    old = stored_digests(stored)
    comp = {}
    for name, rows in new.items():
        o = {r["seed"]: r for r in old.get(name, [])}
        res = dict(trials=len(rows), scene=0, outcome=0, detail=0, mismatches=[])
        for r in rows:
            s = o.get(r["seed"])
            if s is None:
                res["mismatches"].append(dict(seed=r["seed"], missing_in_stored=True))
                continue
            sc = r["scene_sha256"] == s["scene_sha256"]
            oc = all(r[x]["outcome_sha256"] == s[x]["outcome_sha256"] for x in ("a", "b"))
            dc = all(r[x]["detail_sha256"] == s[x]["detail_sha256"] for x in ("a", "b"))
            res["scene"] += sc
            res["outcome"] += oc
            res["detail"] += dc
            if not (sc and oc and dc):
                res["mismatches"].append(dict(
                    seed=r["seed"], scene=sc, outcome=oc, detail=dc,
                    differences={x: {k: (s[x]["detail"].get(k), r[x]["detail"].get(k))
                                     for k in r[x]["detail"] if r[x]["detail"].get(k) != s[x]["detail"].get(k)}
                                 for x in ("a", "b")}))
        res["per_trial"] = {r["seed"]: {x: dict(detail=r[x]["detail"], numeric=r[x]["numeric"]) for x in ("a", "b")}
                            for r in rows}
        comp[name] = res
    import platform
    try:
        from mirror.evaluate.paired import runtime_versions
        runtime = runtime_versions()
    except ImportError:
        runtime = {}
    return dict(version="reanalysis_v1/replay", registration=str(reg_dir.name),
                runtime=runtime, platform=dict(machine=platform.machine(), system=platform.system()),
                frozen_versions=man.get("versions"),
                frozen_code_sha256=man.get("code_sha256"), replay_code_sha256=digest,
                code_identical_to_freeze=digest == man.get("code_sha256"),
                spec_pair_identical=spec_ok, network_calls_refused=net["n"],
                ledger="scratch copy (registration ledger untouched)", wall_time_s=round(time.time() - started, 1),
                blocked_reference=[dict(condition=r.get("condition"), status=(r.get("report") or r).get("status"))
                                   for r in ref] if isinstance(ref, list) else str(type(ref).__name__),
                strata=comp)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("registration", help="results/<dir> of a paired registration, e.g. results/eval_muse")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", default="", help="free-text label for the code state replayed (e.g. a commit)")
    a = ap.parse_args(argv)
    repo = Path(a.repo).resolve()
    rep = replay(repo / a.registration, repo, workers=a.workers)
    rep["code_state_label"] = a.label
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps({k: v for k, v in rep.items() if k != "strata"}, default=str))
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk not in ("mismatches", "per_trial")} for k, v in rep["strata"].items()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
