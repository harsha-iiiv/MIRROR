"""Reference interpretation scenarios, run with any LLMClient.

Recording (writes data/cassettes/):
    MIRROR_LLM_MODE=record MIRROR_LLM_BASE_URL=... MIRROR_LLM_MODEL=... MODEL_API_KEY=... \
        python -m tests.interpret.scenarios

The replay tests run the same scenarios against the recorded cassettes.
When data/demos/reference_demo.json exists, the simulator's reference
demonstration is used as well as the synthetic fixture.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from mirror.contracts import DemoTrace
from mirror.interpret import (CorrectionRejected, LLMClient, NeedsClarification, apply_answer,
                              compile_correction, confirm_items, make_clarification, propose_spec_detailed)
from tests.interpret.fixtures import make_candidates, make_demo

REPO = Path(__file__).resolve().parents[2]
REFERENCE_DEMO = REPO / "data" / "demos" / "reference_demo.json"
# The replay tests use a pinned copy of the reference demonstration so that
# regenerating the simulator's demo does not invalidate the recordings.
PINNED_DEMO = REPO / "data" / "cassettes" / "inputs" / "reference_demo.json.gz"

CORRECTIONS = [
    "This container must remain upright throughout transport, not just at the destination.",
    "The marker can tilt, only the cup matters.",
    "It must stay upright the whole time.",
    "The bowl must stay upright.",
    "The cup doesn't need to stay upright while it is carried.",
    "Go faster please.",
]
ANSWERS = ["No: the beads would spill. Keep the cup upright whenever it is being carried."]


def demos(source: str = "pinned") -> list[DemoTrace]:
    """The fixture demo plus the reference demo: ``pinned`` reads the snapshot
    stored next to the cassettes, ``current`` reads data/demos."""
    out = [make_demo()]
    if source == "current" and REFERENCE_DEMO.exists():
        out.append(DemoTrace.model_validate_json(REFERENCE_DEMO.read_text()))
    elif source == "pinned" and PINNED_DEMO.exists():
        out.append(DemoTrace.model_validate_json(gzip.decompress(PINNED_DEMO.read_bytes())))
    return out


def pin_reference_demo() -> str:
    raw = REFERENCE_DEMO.read_bytes()
    PINNED_DEMO.parent.mkdir(parents=True, exist_ok=True)
    PINNED_DEMO.write_bytes(gzip.compress(raw, mtime=0))
    return hashlib.sha256(raw).hexdigest()


def _outcome(fn) -> dict[str, Any]:
    try:
        spec, rec = fn()
    except NeedsClarification as e:
        return {"outcome": "clarify", "question": e.question}
    except CorrectionRejected as e:
        return {"outcome": "reject", "reason": str(e)}
    return {"outcome": "apply", "version": spec.version,
            "ops": [(o.op, o.target_id, json.dumps(o.value, sort_keys=True)) for o in rec.diff],
            "statuses": {c.id: c.status.value for c in spec.constraints}, "model": rec.model}


def run(client: LLMClient, trace: DemoTrace) -> dict[str, Any]:
    it = propose_spec_detailed(trace, client)
    spec = it.spec
    confirm = [g.id for g in spec.goals] + [c.id for c in spec.constraints if c.id == "c_upright_final_cup"]
    base, _ = confirm_items(spec, confirm=confirm)
    result: dict[str, Any] = {
        "demo_id": trace.demo_id, "interpreter": spec.interpreter,
        "proposal": json.loads(spec.model_dump_json()),
        "corrections": {t: _outcome(lambda t=t: compile_correction(base, t, client, {"scene": trace.scene}))
                        for t in CORRECTIONS},
    }
    if trace.demo_id == "fixture_demo":
        scene, cands = make_candidates()
        clar = make_clarification(base, scene, cands)
        result["answers"] = {a: _outcome(lambda a=a: apply_answer(base, clar, a, client)) for a in ANSWERS}
    return result


def main() -> int:
    client = LLMClient.from_env()
    if client.mode not in ("record", "replay", "live"):
        print("set MIRROR_LLM_MODE to record, replay or live", file=sys.stderr)
        return 2
    if client.mode == "record" and REFERENCE_DEMO.exists():
        print(f"pinned reference demo sha256 {pin_reference_demo()}", file=sys.stderr)
    for tr in demos("pinned"):
        res = run(client, tr)
        print(json.dumps({k: v for k, v in res.items() if k != "proposal"}, indent=1))
    if client.mode == "record" and "--prune" in sys.argv:
        used = {u["key"] for u in client.usage}
        for p in client.cassette_dir.glob("*.json"):
            if p.name[:16] not in used:
                p.unlink()
                print(f"removed unused cassette {p.name}", file=sys.stderr)
    print(json.dumps(client.usage, indent=1), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
