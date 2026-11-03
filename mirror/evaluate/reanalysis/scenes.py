"""Dump the frozen layouts of a paired registration as plain numbers (derived, development only).

Samples each frozen seed with the registration's own sampler (accesses go to a
scratch ledger, never the registration's) and writes object, tray and obstacle
poses and sizes, so layouts can be compared numerically across platforms when
their exact-byte hashes differ.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


def dump(reg_dir: Path) -> dict:
    os.environ["MIRROR_EVAL_LEDGER"] = str(Path(tempfile.mkdtemp(prefix="scenes_")) / "ledger.jsonl")
    from mirror.evaluate.paired import _scene_hash, make_layout_fn
    man = json.loads((reg_dir / "manifest.json").read_text())
    out = {}
    for st in man["strata"]:
        layout = make_layout_fn(st["family"], None)
        for s in st["seeds"]:
            sc = layout(s, st["split"])
            out[f"{st['name']}:{s}"] = dict(scene_sha256=_scene_hash(sc), scene=json.loads(sc.model_dump_json()))
    return out


def numbers(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from numbers(v, f"{prefix}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from numbers(v, f"{prefix}[{i}]")
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        yield prefix, float(obj)


def compare(a: dict, b: dict) -> dict:
    max_d, where, struct = 0.0, None, 0
    for k in sorted(set(a) & set(b)):
        na, nb = dict(numbers(a[k]["scene"])), dict(numbers(b[k]["scene"]))
        if set(na) != set(nb):
            struct += 1
            continue
        for p in na:
            d = abs(na[p] - nb[p])
            if d > max_d:
                max_d, where = d, f"{k}{p}"
    return dict(layouts=len(set(a) & set(b)), hash_equal=sum(a[k]["scene_sha256"] == b[k]["scene_sha256"] for k in set(a) & set(b)),
                structure_differs=struct, max_abs_numeric_difference=max_d, at=where)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump"); d.add_argument("registration"); d.add_argument("--out", required=True)
    c = sub.add_parser("compare"); c.add_argument("first"); c.add_argument("second"); c.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "dump":
        res = dump(Path(a.registration))
    else:
        res = compare(json.loads(Path(a.first).read_text()), json.loads(Path(a.second).read_text()))
        print(json.dumps(res))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
