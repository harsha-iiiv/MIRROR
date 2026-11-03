#!/usr/bin/env python
"""Write SYNTHETIC event logs to study/fixtures/ for testing studykit.py.

Every record has "synthetic": true and a SYN participant id. The timings and
behaviours are invented to exercise the tools, with identical timings in both
conditions so that no fixture suggests a difference between them (a time-limit failure, a
wrong-scope edit, clarifications, an invalid manual edit, a facilitator
intervention). They are not observations of anyone.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import studykit as sk  # noqa: E402

OUT = sk.STUDY / "fixtures"


def _edit_state(task_id: str, *, extra_kind: str | None = None) -> dict:
    t = sk.load_tasks()[task_id]
    st = sk.acceptance_state(t)
    if extra_kind:
        st = json.loads(json.dumps(st))
        st["enforced"][f"c_upright_transport_{extra_kind}"] = {"max_tilt_deg": 15.0}
        st["enforced"] = dict(sorted(st["enforced"].items()))
    return st


def session(pid: str, plan: list[tuple[str, str, str]]) -> list[dict]:
    """plan: (task, condition, behaviour) with behaviour in
    ok | wrong_scope_then_fix | ask_then_ok | invalid_then_ok | time_limit | intervention."""
    base = sk.base_state()
    ev, seq, t = [], 0, 1000.0
    sid = f"syn-{pid.lower()}"

    def put(event, **kw):
        nonlocal seq
        seq += 1
        ev.append({"schema": sk.SCHEMA, "synthetic": True, "participant": pid, "session": sid, "seq": seq,
                   "t": round(t, 2), "event": event, **kw})
    put("session_start", task=None, condition=None, app_commit="synthetic", task_packet_sha256=sk.packet_sha256(),
        consent={"text": False, "audio": False, "quote": False})
    v = 0
    for task, cond, beh in plan:
        put("task_start", task=task, condition=cond, scene_key=f"fixture_{task}", spec_version_id=f"g1:v{v}",
            spec_state=base)
        step = 45.0          # identical in both conditions: fixtures must not suggest an effect

        def new_version(state, source):
            nonlocal v, t
            v += 1
            t += step
            put("spec_version_created", task=task, condition=cond, spec_version_id=f"g1:v{v}", spec_state=state,
                source=source)

        def submit():
            nonlocal t
            t += 9.0
            if cond == "mirror":
                put("correction_submitted", task=task, condition=cond, input="text", chars=58)
                put("interpretation_returned", task=task, condition=cond, outcome="apply",
                    provenance="offline_interpreter", latency_s=0.01)
            else:
                put("manual_edit_saved", task=task, condition=cond, valid=True, errors=0)
        if beh == "time_limit":
            t += 480.0
            put("task_end", task=task, condition=cond, reason="time_limit")
            continue
        if beh == "wrong_scope_then_fix":
            submit()
            new_version(_edit_state(task, extra_kind="marker" if task != "T2" else "block"), "correction")
        if beh == "ask_then_ok":
            t += 7.0
            put("clarification_shown", task=task, condition=cond, question_chars=64)
            t += 12.0
            put("clarification_answered", task=task, condition=cond, chars=21)
        if beh == "invalid_then_ok":
            t += 30.0
            put("manual_edit_saved", task=task, condition=cond, valid=False, errors=1)
        if beh == "intervention":
            put("facilitator_note", task=task, condition=cond, kind="explanation")
        submit()
        new_version(_edit_state(task), "correction" if cond == "mirror" else "manual")
        t += 20.0
        put("execution_run", task=task, condition=cond, rollout_id=f"syn-{task}-{v}", verdict="success",
            scene_key=f"fixture_{task}")
        put("task_end", task=task, condition=cond, reason="participant_done")
        put("rating", task=task, condition=cond, confidence=5, effort=3)
    put("session_end", task=None, condition=None)
    return ev


PLANS = {
    "SYN01": [("T1", "mirror", "ok"), ("T2", "mirror", "ask_then_ok"), ("T3", "mirror", "ok"),
              ("T4", "manual", "invalid_then_ok"), ("T5", "manual", "ok"), ("T6", "manual", "ok")],
    "SYN02": [("T4", "manual", "ok"), ("T5", "manual", "time_limit"), ("T6", "manual", "ok"),
              ("T1", "mirror", "wrong_scope_then_fix"), ("T2", "mirror", "ok"), ("T3", "mirror", "ok")],
    "SYN03": [("T4", "mirror", "ok"), ("T5", "mirror", "ok"), ("T6", "mirror", "intervention"),
              ("T1", "manual", "ok"), ("T2", "manual", "invalid_then_ok"), ("T3", "manual", "ok")],
    "SYN04": [("T1", "manual", "ok"), ("T2", "manual", "ok"), ("T3", "manual", "ok"),
              ("T4", "mirror", "ask_then_ok"), ("T5", "mirror", "time_limit"), ("T6", "mirror", "ok")],
}


def main() -> int:
    OUT.mkdir(exist_ok=True)
    for pid, plan in PLANS.items():
        p = OUT / f"synthetic_{pid}.jsonl"
        p.write_text("".join(json.dumps(e) + "\n" for e in session(pid, plan)))
    (OUT / "README.md").write_text("# Synthetic fixtures\n\nWritten by `tools/make_fixtures.py`. Every record is "
                                   "`\"synthetic\": true` with an invented SYN participant. Not study data; no "
                                   "person took part.\n")
    print(f"wrote {len(PLANS)} synthetic logs to {OUT.name}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
