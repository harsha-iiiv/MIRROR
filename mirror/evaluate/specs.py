"""Specification pairs for evaluation runs.

``evaluation_specs`` builds the pre-registered pair from the pinned reference
demonstration: the interpreter's proposal with every goal and the cup's
final-upright rule confirmed (original), plus the reference correction
(corrected). It uses recorded model responses (replay) and checks that the
offline interpreter yields the same enforced rules and goals.

``reference_specs`` is a hand-written equivalent kept for tests.
"""
from __future__ import annotations

from mirror.contracts import (Constraint, Goal, ObjectBinding, ObjectSelector, Provenance,
                              Status, TaskSpec)
from mirror.geometry import DEFAULT_UPRIGHT_TOL_DEG


def reference_specs() -> tuple[TaskSpec, TaskSpec]:
    bindings = [ObjectBinding(role="container", kind="cup"),
                ObjectBinding(role="part", kind="block"),
                ObjectBinding(role="tool", kind="marker")]
    goals = [Goal(id=f"g_{role}_inside", type="inside", object=ObjectSelector(role=role),
                  provenance=Provenance(source="manual", evidence="reference task"))
             for role in ("container", "part", "tool")]
    final = Constraint(id="c_container_upright_final", type="upright",
                       applies_to=ObjectSelector(role="container"), phase="final",
                       params={"max_tilt_deg": DEFAULT_UPRIGHT_TOL_DEG}, status=Status.confirmed,
                       provenance=Provenance(source="manual", evidence="reference task"),
                       description="The container ends upright in the tray.")
    original = TaskSpec(spec_id="kit_packing_reference", version=1, bindings=bindings, goals=goals,
                        constraints=[final], summary="Pack the cup, block and marker into the tray; "
                        "the cup ends upright.", interpreter="manual")
    transport = Constraint(id="c_container_upright_transport", type="upright",
                           applies_to=ObjectSelector(role="container"), phase="transport",
                           params={"max_tilt_deg": DEFAULT_UPRIGHT_TOL_DEG}, status=Status.confirmed,
                           provenance=Provenance(source="user_correction", version_introduced=2,
                                                 evidence="This container must remain upright "
                                                          "throughout transport, not just at the destination."),
                           description="The container stays upright while it is carried.")
    corrected = original.model_copy(deep=True)
    corrected.version = 2
    corrected.parent_version = 1
    corrected.constraints = [final, transport]
    corrected.summary = original.summary + " The cup stays upright while carried."
    return original, corrected


# ----------------------------------------------------------------------------
# Pre-registered pair from the reference demonstration
# ----------------------------------------------------------------------------

import gzip
import hashlib
import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PINNED_DEMO = REPO_ROOT / "data" / "cassettes" / "inputs" / "reference_demo.json.gz"
CASSETTE_DIR = REPO_ROOT / "data" / "cassettes"
REFERENCE_CORRECTION = ("This container must remain upright throughout transport, "
                        "not just at the destination.")
CONFIRM_RULES = ("c_upright_final_cup",)


class SpecAgreementError(RuntimeError):
    pass


def _build(trace, client):
    from mirror.interpret import compile_correction, confirm_items, propose_spec
    proposal = propose_spec(trace, client)
    confirm = [g.id for g in proposal.goals] + [c.id for c in proposal.constraints if c.id in CONFIRM_RULES]
    missing = [r for r in CONFIRM_RULES if r not in {c.id for c in proposal.constraints}]
    if missing:
        raise SpecAgreementError(f"{client.label}: proposal lacks {missing}")
    original, _ = confirm_items(proposal, confirm=confirm)
    corrected, record = compile_correction(original, REFERENCE_CORRECTION, client,
                                           {"scene": trace.scene})
    return original, corrected, record


def _signature(spec: TaskSpec) -> dict:
    return dict(
        enforced=sorted((c.id, c.type, c.phase, json.dumps(c.applies_to.model_dump(exclude_none=True), sort_keys=True),
                         json.dumps(c.params, sort_keys=True)) for c in spec.enforced_constraints()),
        goals=sorted((g.id, json.dumps(g.object.model_dump(exclude_none=True), sort_keys=True), g.target)
                     for g in spec.active_goals()))


def _recorded_models(cassette_dir: Path) -> list[str]:
    models: list[str] = []
    for p in sorted(cassette_dir.glob("*.json")):
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        m = (d.get("request") or {}).get("model")
        if d.get("name") == "propose_spec" and m and m not in models:
            models.append(m)
    return models


def evaluation_specs(*, cassette_dir: Path | None = None) -> tuple[TaskSpec, TaskSpec, dict]:
    """(original, corrected, provenance) built by replaying recorded model
    responses on the pinned reference demonstration. Raises
    SpecAgreementError unless the offline interpreter produces identical
    enforced constraints and active goals for both versions."""
    from mirror.contracts import DemoTrace
    from mirror.interpret import LLMClient
    cassette_dir = Path(cassette_dir or os.environ.get("MIRROR_CASSETTE_DIR") or CASSETTE_DIR)
    raw = gzip.decompress(PINNED_DEMO.read_bytes())
    trace = DemoTrace.model_validate_json(raw)
    models = [os.environ["MIRROR_LLM_MODEL"]] if os.environ.get("MIRROR_LLM_MODEL") else []
    models += [m for m in _recorded_models(cassette_dir) if m not in models]
    replay, errors = None, []
    for m in models:
        try:
            replay = _build(trace, LLMClient(mode="replay", model=m, cassette_dir=cassette_dir))
            break
        except Exception as exc:  # cassette miss for this model id
            errors.append(f"{m}: {type(exc).__name__}: {exc}")
    if replay is None:
        raise SpecAgreementError("no recorded responses replay for the reference demonstration: "
                                 + "; ".join(errors or ["no cassettes"]))
    offline = _build(trace, LLMClient(mode="offline"))
    for name, r, o in (("original", replay[0], offline[0]), ("corrected", replay[1], offline[1])):
        if _signature(r) != _signature(o):
            raise SpecAgreementError(f"{name}: replayed and offline interpretations differ: "
                                     f"{_signature(r)} vs {_signature(o)}")
    original, corrected, record = replay
    prov = dict(
        source="replay of recorded model responses on the pinned reference demonstration",
        interpreter=original.interpreter, demo_id=trace.demo_id,
        demo_sha256=hashlib.sha256(raw).hexdigest(), correction_text=REFERENCE_CORRECTION,
        confirmed_rules=list(CONFIRM_RULES), correction_ops=[o.op for o in record.diff],
        offline_agrees=True, offline_interpreter=offline[0].interpreter,
        enforced_original=[c.id for c in original.enforced_constraints()],
        enforced_corrected=[c.id for c in corrected.enforced_constraints()])
    return original, corrected, prov
