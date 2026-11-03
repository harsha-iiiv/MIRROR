#!/usr/bin/env python
"""Record the studio's Challenge-mode tour: a fixed, labelled recording that plays
without any model call or simulation when a visitor watches it.

    MUJOCO_GL=osmesa python scripts/record_challenge_tour.py --out data/challenge_tour/v1

What it does, all on this machine and all reported in the record:
  * loads the reference demonstration and interprets it with the OFFLINE rule-based
    interpreter (no model call is made by this script),
  * builds a development challenge layout (never a frozen evaluation seed),
  * executes the current specification there (the prior behaviour),
  * compiles the correction, accepts the reviewed scope, re-checks the prior rollout
    from its step log, executes the revised specification,
  * optionally executes both versions on further development layouts (transfer),
  * writes record.json and the rollout payloads it names.

Every number in the record comes from these runs. Re-running it writes a new
version directory; existing tour versions are never overwritten.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ["MIRROR_LLM_MODE"] = "offline"          # the tour never calls a model

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mirror.lineage import canonical_json  # noqa: E402
from mirror.studio.records import record_id_of  # noqa: E402
from mirror.studio.session_adapter import REFERENCE_CORRECTION, StudioService  # noqa: E402


def code_version() -> str:
    """Commit of the code that produced the tour; "+uncommitted" when mirror/ or scripts/ had local changes."""
    def git(*a):
        return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True, timeout=10,
                              stdin=subprocess.DEVNULL).stdout.strip()
    try:
        head = git("rev-parse", "--short", "HEAD") or "unknown"
        dirty = git("status", "--porcelain", "--", "mirror", "scripts")
        return head + ("+uncommitted" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def wait_ok(svc, job, what):
    svc.wait(job, timeout=1800)
    if job.status != "done":
        raise SystemExit(f"{what} failed: {job.error}")
    return job.result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True, help="new tour version directory, e.g. data/challenge_tour/v1")
    ap.add_argument("--family", default="shortcut")
    ap.add_argument("--seed", type=int, default=4242)
    ap.add_argument("--correction", default=REFERENCE_CORRECTION)
    ap.add_argument("--transfer-seeds", type=int, nargs="*", default=[])
    args = ap.parse_args()
    out = Path(args.out)
    if out.exists():
        raise SystemExit(f"{out} exists: tour versions are never overwritten; choose a new version")
    t0 = time.time()
    svc = StudioService(backend="live", session_id="tour", private_runtime=True)
    try:
        cid = wait_ok(svc, svc.challenge_create(args.family, args.seed, edits=[]), "challenge")["challenge_id"]
        prop = wait_ok(svc, svc.challenge_correction(cid, args.correction), "correction")
        if not prop.get("changed"):
            raise SystemExit(f"the correction did not change the specification: {prop}")
        svc.challenge_review(cid, True, "recorded tour")
        wait_ok(svc, svc.challenge_execute(cid), "execution")
        record = svc.challenge_record(cid)
        ch = svc.challenges[cid]
        payloads = {r: svc.rollout_payload(r) for r in (ch.before["rollout_id"], ch.after["rollout_id"])}
        transfer = []
        for seed in args.transfer_seeds:
            from mirror.studio.challenge import challenge_scene
            scene, origin = challenge_scene(args.family, seed, [])
            svc.session.set_scene(scene)
            row = {"seed": seed, "scene_id": scene.scene_id, "scene_sha256": origin["content_sha256"]}
            for key, sv in (("base", ch.base_spec_version_id), ("revised", ch.revised_spec_version_id)):
                version = int(sv.rpartition(".v")[2])
                res = wait_ok(svc, svc.run(spec_version=version, condition=f"tour transfer {seed} {key}"), "transfer")
                e = svc.session.rollouts[res["rollout_id"]]
                row[key] = svc._rollout_brief(e)
                payloads[res["rollout_id"]] = svc.rollout_payload(res["rollout_id"])
            # The independent checker on the prior behaviour's step log, under the revised rule.
            base_e = svc.session.rollouts[row["base"]["rollout_id"]]
            rep = svc.session.recheck(base_e.rollout_id, int(ch.revised_spec_version_id.rpartition(".v")[2]))
            row["base_under_revised"] = {**svc._rollout_brief(base_e, rep), "spec_version_id": ch.revised_spec_version_id}
            transfer.append(row)
        record["transfer"] = transfer
        record["provenance"] = {**record["provenance"], "recorded": True,
                                "recorded_with": {"interpreter": "offline rule-based interpreter (no model call)",
                                                  "simulation": "MuJoCo, executed when the tour was recorded",
                                                  "code_version": code_version(),
                                                  "command": " ".join(["scripts/record_challenge_tour.py"] + sys.argv[1:])},
                                "recorded_wall_s": round(time.time() - t0, 1)}
        record["label"] = ("Recorded tour: these runs were executed once when the tour was recorded, with the "
                           "offline rule-based interpreter. Watching it makes no model call and no simulation.")
        record.pop("record_id", None)
        record = json.loads(canonical_json(record))
        record["record_id"] = record_id_of(record)
        (out / "rollouts").mkdir(parents=True)
        for rid, p in payloads.items():
            (out / "rollouts" / f"{rid}.json").write_text(json.dumps(p, separators=(",", ":")))
        (out / "record.json").write_text(json.dumps(record, indent=1))
        print(json.dumps({"record_id": record["record_id"], "before": record["rollouts"]["before"]["status"],
                          "recheck_before": [r["status"] for r in record["rechecks"] if r.get("is_before")],
                          "after": record["rollouts"]["after"]["status"],
                          "transfer": [(t["seed"], t["base"]["status"], t["base_under_revised"]["status"],
                                        t["revised"]["status"]) for t in transfer],
                          "wall_s": record["provenance"]["recorded_wall_s"]}, indent=1))
    finally:
        svc.close("tour recorded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
