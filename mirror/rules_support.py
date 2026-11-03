"""Rule-support matrix: which goals, constraints, phases and orderings the
compiler may produce, how the planner honours each one and how the checker
monitors it.

This table is the single source of truth. ``unsupported_reason`` is used by
the specification validator and the correction compiler, so a rule that the
planner or the checker cannot honour is never accepted; the web interface
reads ``matrix()`` (or ``python -m mirror.rules_support --json``) to show the
same semantics. docs/RULES.md embeds ``markdown()`` and a test keeps them in
sync.

Support levels
  planner: "enforced"         the planner restricts its search so plans satisfy
                              the rule, or reports a proven infeasibility
           "by_construction"  every plan the planner can produce satisfies it
           "none"             the planner cannot honour it
  checker: "monitored"        evaluated at every step of the dense step log
                              (and at playback frames when re-checking old
                              rollouts, with the limits noted)
           "monitored_dense"  needs the dense step log (contacts); unknown on
                              playback frames
           "none"
  ui:      filled in by the interface ("pending" until it states support)
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Optional, get_args

from mirror.contracts import Constraint, Goal, OrderingConstraint, OrderingEvent, Phase, Status

CONTACT_GROUPS = ("obstacle", "table_outside_tray", "other_objects")
MATRIX_VERSION = "1"


@dataclass(frozen=True)
class RuleSupport:
    category: str                 # "goal" | "constraint" | "ordering" | "phase"
    op: str                       # goal/constraint type, "ordering", or the phase name
    phase: Optional[str] = None
    variant: Optional[str] = None  # contact group, or "<before_event>/<after_event>"
    compiler: bool = False         # the correction compiler may produce it
    planner: str = "none"
    checker: str = "none"
    ui: str = "pending"
    planner_how: str = ""
    checker_how: str = ""
    notes: str = ""

    @property
    def key(self) -> str:
        return ":".join(x for x in (self.category, self.op, self.phase or "", self.variant or "") if x)

    @property
    def supported(self) -> bool:
        return self.planner != "none" and self.checker != "none"


PHASE_TEXT = {
    "transport": "from the first step the object is held (finger contact, or lifted off its support "
                 "between held steps) until it is released",
    "final": "the last 0.3 s of the rollout, after the object's last release",
    "always": "every recorded step of the rollout, including before the grasp",
}


def _rows() -> list[RuleSupport]:
    R: list[RuleSupport] = []
    for ph in get_args(Phase):
        R.append(RuleSupport("phase", ph, compiler=True, planner="enforced", checker="monitored",
                             checker_how=PHASE_TEXT[ph],
                             notes="Scope words: 'while carrying', 'once placed', 'at all times'."))
    R.append(RuleSupport(
        "goal", "inside", compiler=True, planner="enforced", checker="monitored",
        planner_how="every object matched by an active goal is packed into the destination tray",
        checker_how="object centre inside the tray footprint at the end, released and settled",
        notes="Proposed goals are pursued and required (a fresh proposal must be executable and "
              "judged); rejected goals are ignored. This is the one place a proposed item gates success."))
    # upright
    up_planner = {"transport": ("enforced", "transport modes tilted beyond max_tilt_deg are removed"),
                  "always": ("enforced", "as transport; objects only rest upright and are placed upright"),
                  "final": ("by_construction", "objects are always placed upright")}
    for ph in get_args(Phase):
        lvl, how = up_planner[ph]
        R.append(RuleSupport("constraint", "upright", ph, compiler=True, planner=lvl, checker="monitored",
                             planner_how=how, checker_how=f"tilt <= max_tilt_deg during {ph}",
                             notes="max_tilt_deg in (0, 90] from the compiler, (0, 180] in a spec"))
    # forbidden contact
    for ph in get_args(Phase):
        for grp in CONTACT_GROUPS:
            if grp == "table_outside_tray" and ph in ("transport", "always"):
                R.append(RuleSupport(
                    "constraint", "forbidden_contact", ph, grp, compiler=False, planner="none",
                    checker="monitored_dense",
                    notes=("Unsatisfiable as defined: the object rests on the table when its grasp begins "
                           "(and before it), so the checker would always report a contact. The compiler "
                           "asks whether the user means 'must not end up on the table' (final).")))
                continue
            how = {"obstacle": "motions are collision-checked with the obstacle inflated by the safety margin; "
                               "rest poses are collision-free",
                   "other_objects": "motions keep the other objects' boxes clear by the safety margin; tray "
                                    "spots keep a gap to placed objects",
                   "table_outside_tray": "objects are placed inside the tray"}[grp]
            R.append(RuleSupport("constraint", "forbidden_contact", ph, grp, compiler=True,
                                 planner="by_construction" if grp != "obstacle" or ph == "final" else "enforced",
                                 checker="monitored_dense", planner_how=how,
                                 checker_how=f"no simulator contact with {grp} during {ph}",
                                 notes="Contacts are only in the dense step log; on playback frames the "
                                       "verdict is unknown."))
    # clearance
    cl = {"transport": "free-space transport uses min_m as the obstacle margin; a start pose closer than "
                       "min_m is a proven infeasibility; the tray spot is checked",
          "always": "as transport (start pose proof, margin, tray spot)",
          "final": "the chosen tray spot is checked; no alternative spot is searched (planning failure, "
                   "not a proof)"}
    for ph in get_args(Phase):
        R.append(RuleSupport("constraint", "keep_clearance", ph, "obstacle", compiler=True, planner="enforced",
                             checker="monitored", planner_how=cl[ph],
                             checker_how="exact distance between the object's solid shape (box; capsule for "
                                         "the marker) and every obstacle box >= min_m",
                             notes="Clearance is measured to obstacles (walls, boxes) only. Clearance from "
                                   "another object or an arbitrary region is not supported."))
    for variant in ("other_objects", "region"):
        R.append(RuleSupport("constraint", "keep_clearance", None, variant, compiler=False,
                             notes="Not supported: the planner does not keep a distance from these. The "
                                   "compiler asks a clarifying question instead."))
    # ordering
    ev = {("placement_complete", "grasp_start"): "pack every 'before' object before touching any 'after' object (default)",
          ("grasp_start", "grasp_start"): "start with the 'before' objects",
          ("placement_complete", "placement_complete"): "the 'before' objects are in the tray first",
          ("grasp_start", "placement_complete"): "start the 'before' objects before the 'after' ones are placed"}
    for (be, ae), text in ev.items():
        R.append(RuleSupport(
            "ordering", "ordering", None, f"{be}/{ae}", compiler=(be, ae) != ("grasp_start", "placement_complete"),
            planner="by_construction" if (be, ae) != ("placement_complete", "grasp_start") else "enforced",
            checker="monitored",
            planner_how="confirmed orderings become precedence edges of the object order; objects are "
                        "packed one at a time, so an earlier object is placed before the next is grasped",
            checker_how="events from the evidence: grasp start (held label or lift-off), placement "
                        "completion (settled inside the tray after the last release); ties within two "
                        "samples are unknown",
            notes=text + ". A confirmed cycle is an invalid specification."))
    return R


ROWS: tuple[RuleSupport, ...] = tuple(_rows())
BY_KEY = {r.key: r for r in ROWS}


def lookup(category: str, op: str, phase: Optional[str] = None, variant: Optional[str] = None) -> Optional[RuleSupport]:
    return BY_KEY.get(RuleSupport(category, op, phase, variant).key)


def constraint_rows(c: Constraint) -> list[Optional[RuleSupport]]:
    if c.type == "forbidden_contact":
        groups = c.params.get("with") or []
        return [lookup("constraint", c.type, c.phase, g) for g in groups] or [None]
    if c.type == "keep_clearance":
        return [lookup("constraint", c.type, c.phase, c.params.get("from", "obstacle"))]
    return [lookup("constraint", c.type, c.phase)]


def ordering_row(o: OrderingConstraint) -> Optional[RuleSupport]:
    return lookup("ordering", "ordering", None, f"{o.before_event}/{o.after_event}")


def unsupported_reason(item: Any, *, for_compiler: bool = False) -> Optional[str]:
    """None when the planner and checker both honour ``item`` (and, with
    ``for_compiler``, the compiler may produce it); otherwise why not."""
    if isinstance(item, Constraint):
        rows = constraint_rows(item)
        where = f"{item.type} during {item.phase}"
    elif isinstance(item, OrderingConstraint):
        rows = [ordering_row(item)]
        where = f"ordering {item.before_event} before {item.after_event}"
    elif isinstance(item, Goal):
        rows = [lookup("goal", item.type)]
        where = f"goal {item.type}"
    else:
        raise TypeError(f"not a rule: {type(item).__name__}")
    for r in rows:
        if r is None:
            return f"{where}: not in the rule-support matrix"
        if not r.supported:
            return f"{where} ({r.variant or ''}): {r.notes}".replace(" ()", "")
        if for_compiler and not r.compiler:
            return f"{where} ({r.variant or ''}): the correction compiler does not produce this form".replace(" ()", "")
    return None


def matrix() -> dict:
    return {"version": MATRIX_VERSION, "rows": [dict(asdict(r), key=r.key, supported=r.supported) for r in ROWS]}


def markdown() -> str:
    out = ["| Rule | Phase | Variant | Compiler | Planner | Checker | Notes |",
           "|---|---|---|---|---|---|---|"]
    for r in ROWS:
        if r.category == "phase":
            continue
        name = r.op if r.category != "goal" else f"goal: {r.op}"
        out.append(f"| {name} | {r.phase or '-'} | {r.variant or '-'} | {'yes' if r.compiler else 'no'} | "
                   f"{r.planner} | {r.checker} | {r.notes} |")
    return "\n".join(out)


if __name__ == "__main__":  # pragma: no cover
    if "--json" in sys.argv:
        print(json.dumps(matrix(), indent=1))
    else:
        print(markdown())
