#!/usr/bin/env python
"""Write SYNTHETIC trial-export records (mirror-trial/1) to study/pilot/fixtures/.

Every record has "synthetic": true and a SYN participant (or DEV for the
smoke-test example). Behaviours are invented to exercise pilotkit.py: a
wrong-object change, a wrong-phase change, a clarification, an unresolved
failure in each condition, a mistaken recorded/fresh answer. Timings are the
same in both conditions so no fixture suggests a difference between them.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pilotkit as pk  # noqa: E402

OUT = pk.sk.STUDY / "pilot" / "fixtures"


def _state_with(start: dict, extra: dict) -> dict:
    s = json.loads(json.dumps(start))
    s["enforced"].update(extra)
    s["enforced"] = dict(sorted(s["enforced"].items()))
    return s


def trials(pid: str, plan: list[tuple[str, str, str]], context: str = "usability_pilot") -> list[dict]:
    cards = pk.load_cards()
    start = pk.sk.base_state()
    out, t, n = [], 0.0, 0
    for task, cond, beh in plan:
        card = cards[task]
        acc = pk.acceptance_state(card, start)
        t0 = t
        steps = []
        if beh == "wrong_object":
            steps.append(("apply", _state_with(acc, {"c_upright_transport_block": {"max_tilt_deg": 15.0}}), None))
        if beh == "wrong_phase":
            want = sorted(set(acc["enforced"]) - set(start["enforced"]))[0]          # e.g. c_upright_transport_marker
            other = want.replace("_transport_", "_tmp_").replace("_final_", "_transport_").replace("_tmp_", "_final_")
            steps.append(("apply", _state_with(start, {other: acc["enforced"][want]}), None))
        if beh == "clarify" or (card["expected_first"] == "asks" and card.get("acceptance") is not None):
            steps.append(("asks", start, {"question": "Which requirement matters?", "answered": True, "useful": 4}))
        if beh == "unresolved":
            steps.append(("apply", _state_with(start, {"c_upright_transport_marker": {"max_tilt_deg": 15.0}}), None))
        else:
            first = card["expected_first"]
            if first == "asks" and card.get("acceptance") is None:
                steps.append(("asks", start, None))                 # absent object: ask, change nothing
            elif first == "rejects":
                steps.append(("rejects", start, None))
            else:
                steps.append(("apply", acc, None))
        prev = start
        for k, (outcome, after, clar) in enumerate(steps, 1):
            n += 1
            t += 40.0
            exe = None
            if outcome == "apply" and card.get("acceptance") is not None:
                if card.get("expected_execution") == "proven_infeasible" and after == acc:
                    exe = {"rollout_id": f"syn.{n}", "status": "planning_failed", "violations": [],
                           "proven_infeasible": True, "provenance": "simulated"}
                else:
                    exe = {"rollout_id": f"syn.{n}", "status": "success", "violations": [], "proven_infeasible": False,
                           "provenance": "simulated"}
            last = k == len(steps)
            rec = {"format": pk.FORMAT, "trial_id": f"{pid}-{task}-{k}", "participant": pid, "synthetic": True,
                   "context": context, "task_origin": "prepared", "task": task, "condition": cond, "attempt": k,
                   "started_at": t - 30.0, "ended_at": t, "task_started_at": t0,
                   "versions": {"code_commit": "synthetic", "server_version": "synthetic", "prompt_revision": "3",
                                "model": "muse-spark-1.3" if cond == "mirror" else None,
                                "interpreter": "offline-rules" if cond == "mirror" else "manual"},
                   "scene": {"family": card["scene"].get("family"), "split": "dev", "seed": card["scene"].get("seed"),
                             "edits": [], "scene_id": f"fixture_{task}", "content_sha256": "0" * 16},
                   "lineage": {"generation_id": f"gsyn{pid}", "demo_id": "demo_ref_demo_reference",
                               "demo_sha256": "0" * 16, "base_spec_version_id": f"gsyn{pid}.v{n}",
                               "revised_spec_version_id": f"gsyn{pid}.v{n + 1}" if outcome == "apply" else None},
                   "input": {"kind": "typed" if cond == "mirror" else "manual_form", "chars": 52},
                   "proposal": {"outcome": outcome if outcome in pk.OUTCOMES else "apply", "ops": [], "scope": {},
                                "question": (clar or {}).get("question"), "rejected": None,
                                "validation": {"passed": True, "errors": []},
                                "provenance": "offline_interpreter" if cond == "mirror" else None,
                                "fallback_reason": None, "latency_s": 0.01, "cost_usd": 0.0},
                   "clarification": clar, "review": {"accepted": outcome == "apply", "edited_before_accept": False},
                   "spec_state_before": prev, "spec_state_after": after,
                   "recheck": None, "execution": exe,
                   "failure_reason": "participant gave up" if (beh == "unresolved" and last) else None,
                   "comprehension": ({"answer": "fresh" if beh != "misread" else "recorded", "truth": "simulated"}
                                     if k == 1 and task in ("C1", "C4") else None)}
            out.append(rec)
            prev = after
    return out


PLANS = {
    "SYN01": [("C1", "mirror", "ok"), ("C2", "mirror", "clarify"), ("C3", "mirror", "ok"),
              ("C4", "manual", "ok"), ("C5", "manual", "unresolved"), ("C6", "manual", "ok"),
              ("C7", "mirror", "ok"), ("C8", "mirror", "ok"), ("C9", "mirror", "ok"), ("C10", "mirror", "ok")],
    "SYN02": [("C4", "manual", "wrong_phase"), ("C5", "manual", "ok"), ("C6", "manual", "ok"),
              ("C1", "mirror", "misread"), ("C2", "mirror", "ok"), ("C3", "mirror", "unresolved"),
              ("C8", "mirror", "ok"), ("C9", "mirror", "ok"), ("C10", "mirror", "ok"), ("C7", "mirror", "ok")],
    "SYN03": [("C4", "mirror", "wrong_object"), ("C5", "mirror", "ok"), ("C6", "mirror", "ok"),
              ("C1", "manual", "ok"), ("C2", "manual", "ok"), ("C3", "manual", "wrong_phase"),
              ("C9", "mirror", "ok"), ("C10", "mirror", "ok"), ("C7", "mirror", "ok"), ("C8", "mirror", "ok")],
}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for pid, plan in PLANS.items():
        (OUT / f"synthetic_{pid}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in trials(pid, plan)))
    dev = trials("DEV", [("C1", "mirror", "ok")], context="dev_smoke")
    (OUT / "synthetic_dev_smoke.jsonl").write_text("".join(json.dumps(r) + "\n" for r in dev))
    (OUT / "README.md").write_text("# Synthetic pilot fixtures\n\nWritten by `tools/make_pilot_fixtures.py`. Every "
                                   "record is `\"synthetic\": true`. Not pilot data; no person took part.\n")
    print(f"wrote {len(PLANS)} synthetic participants and one synthetic smoke-test log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
