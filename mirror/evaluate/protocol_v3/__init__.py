"""Protocol v3: a fresh, frozen evaluation of correction authoring.

New modules only. Nothing here imports or changes the frozen evaluation
runners (final, prereg, paired, bench_*); planning, simulation and checking
use the same public functions every other evaluation uses (mirror.plan.plan,
mirror.sim.SimEnv through mirror.evaluate.trial, mirror.evaluate.check).

  schema      case files: turns, gold edits, author-written manual edits
  edits       the structured-edit language of the manual baseline
  access      development vs held-out case access, commitments
  conditions  authoring conditions (Muse Spark compiler, offline rules, manual)
  execute     plan -> MuJoCo -> checker under the authored and the gold spec
  runner      attempts, append-only attempt ledger, timeouts, spend cap
  metrics     per-condition and paired summaries
  freeze      manifest of code, prompts, models, design and commitments

Protocol text: docs/protocols/v3_authoring.md. Design: design.json.
"""
PROTOCOL_ID = "mirror-authoring-v3"
PROTOCOL_VERSION = "3.0"

__all__ = ["PROTOCOL_ID", "PROTOCOL_VERSION"]
