"""Cross-platform agreement of replays (derived, development evidence only).

Compares two replay reports written by ``mirror.evaluate.reanalysis.replay``
(or a replay and the stored registration report) trial by trial: scene hash,
outcome and detail digests, and the largest differences in beads kept, tilts,
executed time and physics steps. A replay on another platform is a
development check, never a registered result.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .replay import stored_digests


def _per_trial_from_replay(rep: dict) -> dict:
    out = {}
    for name, st in rep["strata"].items():
        for seed, v in (st.get("per_trial") or {}).items():
            out[(name, int(seed))] = v
    return out


def _per_trial_from_stored(report: dict) -> dict:
    return {(name, t["seed"]): {s: dict(detail=t[s]["detail"], numeric=t[s]["numeric"], scene=t["scene_sha256"],
                                        outcome=t[s]["outcome"]) for s in ("a", "b")}
            for name, ts in stored_digests(report).items() for t in ts}


def _beads(d):
    return {k: v[0] for k, v in (d.get("beads") or {}).items()}


def compare(a: dict, b: dict) -> dict:
    keys = sorted(set(a) & set(b))
    res = dict(trials_compared=len(keys), only_in_first=len(set(a) - set(b)), only_in_second=len(set(b) - set(a)),
               outcome_equal=0, detail_equal=0, routes_equal=0, beads_equal=0,
               max_abs_bead_difference=0, bead_differences=[], max_abs_final_tilt_deg=0.0,
               max_abs_max_transport_tilt_deg=0.0, max_abs_execution_time_s=0.0, max_abs_physics_steps=0)
    for k in keys:
        oe = de = re_ = be = True
        for s in ("a", "b"):
            da, db = a[k][s]["detail"], b[k][s]["detail"]
            core = lambda d: {x: d[x] for x in d if x not in ("beads", "routes")}  # noqa: E731
            oe &= core(da) == core(db)
            de &= da == db
            re_ &= da.get("routes") == db.get("routes")
            ba, bb = _beads(da), _beads(db)
            be &= ba == bb
            for obj in set(ba) | set(bb):
                diff = abs((ba.get(obj) or 0) - (bb.get(obj) or 0))
                if diff:
                    res["bead_differences"].append(dict(stratum=k[0], seed=k[1], side=s, object=obj,
                                                        first=ba.get(obj), second=bb.get(obj)))
                res["max_abs_bead_difference"] = max(res["max_abs_bead_difference"], diff)
            na, nb = a[k][s]["numeric"], b[k][s]["numeric"]
            for f in ("execution_time_s", "physics_steps"):
                if na.get(f) is not None and nb.get(f) is not None:
                    key = "max_abs_" + f
                    res[key] = max(res[key], abs(na[f] - nb[f]))
            for obj, va in na["objects"].items():
                vb = nb["objects"].get(obj, {})
                for f in ("final_tilt_deg", "max_transport_tilt_deg"):
                    if va.get(f) is not None and vb.get(f) is not None:
                        res["max_abs_" + f] = max(res["max_abs_" + f], abs(va[f] - vb[f]))
        res["outcome_equal"] += oe
        res["detail_equal"] += de
        res["routes_equal"] += re_
        res["beads_equal"] += be
    for f in ("max_abs_final_tilt_deg", "max_abs_max_transport_tilt_deg", "max_abs_execution_time_s"):
        res[f] = round(float(res[f]), 6)
    return res


def load(path: Path) -> tuple[str, dict, dict]:
    d = json.loads(path.read_text())
    if "strata" in d and d.get("version") == "reanalysis_v1/replay":
        meta = {k: d.get(k) for k in ("code_state_label", "runtime", "platform", "replay_code_sha256",
                                      "code_identical_to_freeze", "network_calls_refused", "spec_pair_identical")}
        return "replay", _per_trial_from_replay(d), dict(meta, scene_counts={n: s["scene"] for n, s in d["strata"].items()},
                                                         outcome_counts={n: s["outcome"] for n, s in d["strata"].items()})
    return "stored", _per_trial_from_stored(d), dict(manifest_versions=d["manifest"].get("versions"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("first")
    ap.add_argument("second")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    ka, pa, ma = load(Path(a.first))
    kb, pb, mb = load(Path(a.second))
    rep = dict(version="reanalysis_v1/cross_platform", purpose="development check, not a registered result",
               first=dict(kind=ka, source=Path(a.first).name, **ma), second=dict(kind=kb, source=Path(a.second).name, **mb),
               comparison=compare(pa, pb))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(rep, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps({k: v for k, v in rep["comparison"].items() if k != "bead_differences"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
