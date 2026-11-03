"""Prompt text for the model-backed interpreter.

The prompts carry the task vocabulary and the honesty rules. Output format is
enforced by the JSON schema (structured output), not by the prompt.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from mirror.contracts import SceneState, TaskSpec
from mirror.interpret.types import DemoSummary

VOCABULARY = """\
Task family: packing a kit of rigid objects into a destination tray on a table.
Objects: cup (open-top vessel that may hold loose beads), block, marker (standing cylinder).
Goal type: an object ends at rest inside the destination tray.
Rule types:
  upright: the object's axis stays within max_tilt_deg of vertical (default 15).
  forbidden_contact: the object must not touch contact_with groups
    (obstacle, table_outside_tray, other_objects).
  keep_clearance: the object stays at least min_clearance_m from obstacles.
Phases: final (once placed and settled), transport (from grasp until release of that object),
  always (every moment of the task).
Ordering: one object kind must be packed before another."""

PROPOSE_SYSTEM = VOCABULARY + """

You interpret one demonstration of this task for a simulated robot. You receive
deterministic measurements of the demonstration and the scene inventory, never
raw video. Propose a task specification that a human will review.

Rules:
1. Separate evidence from inference. shown_by_demo cites measured values from the
   input; assumption states what you are inferring beyond them.
2. A consistent behaviour is not automatically a requirement. It may be incidental,
   for example forced by the layout or merely convenient.
3. Every regularity (R1, R2, ...) must either appear in regularity_ids of an object,
   rule or ordering, or be listed in dismissed with a reason.
4. Propose only rules the demonstration itself satisfies, and only where the
   behaviour plausibly matters for the objects involved. Do not turn an observed
   margin into a rule; dismiss it with a reason instead.
5. Each proposed rule and ordering gets one short, concrete question the human can
   answer with "required" or "incidental", and your lean. If the lean rests on the
   scene inventory (what an object contains) rather than on the demonstration, say
   so in assumption.
6. include_in_task is true only for objects that ended inside the tray.
7. Do not invent objects, coordinates, routes or robot commands. Use null for
   parameters that do not apply to a rule type.
8. summary is one plain sentence describing the task."""

CORRECT_SYSTEM = VOCABULARY + """

You compile a person's correction of a robot task specification into edit
operations. A deterministic compiler applies them, assigns identifiers and
statuses, and checks them, so describe intent precisely.

Actions:
  require_rule  the rule must hold (creates it, or confirms and updates an existing one).
                Needs rule.type, rule.object_kind and rule.phase.
  relax_rule    the rule is not required (rejects matching rules; phase null means
                every phase of that rule type for that object).
  confirm_item / reject_item  confirm or reject an existing goal, rule or ordering by target_id.
  require_order / relax_order  before_kind must be packed before after_kind
                (relax_order with both kinds null relaxes every ordering).
  add_goal / remove_goal  whether goal_kind must end in the tray.

Rules:
1. Scope: apply each change only to the objects the person names. Use
   object_kind null (every object) only if they explicitly say all or every.
2. Resolve each description to an object kind in the scene by its meaning. If a
   pronoun or description could mean more than one object in the task and the
   context does not settle it, use outcome "clarify" with a question and options.
3. If the person names an object that is not in the scene (a different object, not
   another description of one that is), use outcome "clarify" and ask which object
   they meant; never substitute a scene object for it.
4. If the correction contradicts itself, use outcome "clarify".
5. If it is not about goals, orientation, contact, clearance or ordering, use
   outcome "reject" and explain in interpretation.
6. Keep existing rules unless the person changes them. A correction that adds a
   phase keeps the rules already in place for other phases.
7. If an orientation or contact requirement states no phase, use transport.
8. Never output coordinates or robot commands. Use null for fields that do not apply.
9. interpretation restates, in one sentence, what you understood."""


# Illustrative phrases that appear in the prompts above. The correction benchmark
# tags cases that share wording with them (scripts/bench_corrections.py).
EXAMPLE_PHRASES = (
    "open-top vessel that may hold loose beads",
    "forced by the layout", "merely convenient", "an observed margin",
    "what an object contains", "a correction that adds a phase",
    "if an orientation or contact requirement states no phase, use transport",
)

# 1: original prompts; 2: neutral examples, contact rules default to transport;
# 3: revision 2 with the object-resolution rule reworded (revision 2 led the model to
#    map objects absent from the scene onto the cup).
PROMPT_REVISION = "3"


def scene_inventory(scene: SceneState) -> dict[str, Any]:
    return {
        "objects": [{"id": o.id, "kind": o.kind, "beads_inside": o.beads} for o in scene.objects],
        "trays": [t.id for t in scene.trays],
        "obstacles": [{"id": ob.id, "kind": ob.kind, "slots": len(ob.slots)} for ob in scene.obstacles],
    }


def summary_payload(summary: DemoSummary) -> dict[str, Any]:
    objs = []
    for o in summary.objects:
        objs.append({
            "id": o.object_id, "kind": o.kind, "moved": o.moved,
            "grasp_times_s": o.grasp_times, "release_times_s": o.release_times,
            "start_tilt_deg": o.start_tilt_deg, "end_tilt_deg": o.end_tilt_deg,
            "ended_in_tray": o.ended_in_tray,
            "carry_duration_s": o.carry_duration_s,
            "carry_max_tilt_deg": o.carry_max_tilt_deg, "carry_mean_tilt_deg": o.carry_mean_tilt_deg,
            "carry_path_length_m": o.carry_path_length_m, "carry_max_height_m": o.carry_max_height_m,
            "min_obstacle_distance_m": o.min_obstacle_distance_m, "nearest_obstacle": o.nearest_obstacle,
            "wall_crossings": o.wall_crossings,
        })
    return {"demo_id": summary.demo_id, "source": summary.source, "duration_s": summary.duration_s,
            "pick_order": summary.order, "objects": objs,
            "regularities": [{"id": r.id, "kind": r.kind, "object_kind": r.object_kind,
                              "statement": r.statement} for r in summary.regularities],
            "notes": summary.notes}


KEYFRAMES_NOTE = """

The input also includes a few images. They are frames rendered by the simulator
from the recorded demonstration, not real video or camera footage. Use them to
understand the scene layout; the measurements are authoritative, and every
statement in shown_by_demo must be supported by the measurements."""


OBSERVATIONS_NOTE = """

The input also includes video_observations: what a video model reported after
watching a video of this demonstration. They can be wrong. Treat them as
secondary evidence: a care cue may support proposing a rule (cite it in
shown_by_demo with its times), but never propose a rule that the measurements
contradict, and do not dismiss a measured regularity because of them."""


def propose_messages(summary: DemoSummary, scene: SceneState,
                     keyframes: Optional[list] = None,
                     observations: Optional[dict] = None) -> list[dict[str, Any]]:
    """``observations``: optional VideoObservations.prompt_view(). When absent the
    messages are byte-identical to those of earlier versions, so existing
    recordings keep replaying."""
    payload = {"scene": scene_inventory(scene), "demonstration": summary_payload(summary)}
    if observations is not None:
        payload["video_observations"] = observations
    text = "Propose the task specification for this demonstration.\n" + json.dumps(payload, indent=1, sort_keys=True)
    system = PROPOSE_SYSTEM + (OBSERVATIONS_NOTE if observations is not None else "")
    if not keyframes:
        return [{"role": "system", "content": system}, {"role": "user", "content": text}]
    from mirror.interpret.keyframes import image_parts
    return [{"role": "system", "content": system + KEYFRAMES_NOTE},
            {"role": "user", "content": [{"type": "text", "text": text}] + image_parts(keyframes)}]


def spec_view(spec: TaskSpec) -> dict[str, Any]:
    return {
        "version": spec.version,
        "objects": [{"kind": b.kind, "role": b.role, "part_of_task": b.required} for b in spec.bindings],
        "goals": [{"id": g.id, "object_kind": g.object.kind, "status": g.status.value} for g in spec.goals],
        "rules": [{"id": c.id, "type": c.type, "object_kind": c.applies_to.kind, "phase": c.phase,
                   "params": c.params, "status": c.status.value, "description": c.description}
                  for c in spec.constraints],
        "ordering": [{"id": o.id, "before_kind": o.before.kind, "after_kind": o.after.kind,
                      "status": o.status.value} for o in spec.ordering],
        "open_questions": [{"id": u.id, "question": u.question, "about": u.related_constraint}
                           for u in spec.uncertainties if u.status == "open"],
    }


def correct_messages(spec: TaskSpec, text: str, scene_kinds: Optional[list[str]],
                     context: Optional[dict[str, Any]]) -> list[dict[str, str]]:
    payload: dict[str, Any] = {"specification": spec_view(spec)}
    if scene_kinds is not None:
        payload["objects_in_scene"] = sorted(scene_kinds)
    if context:
        payload["context"] = context
    payload["correction"] = text
    return [{"role": "system", "content": CORRECT_SYSTEM},
            {"role": "user", "content": json.dumps(payload, indent=1, sort_keys=True)}]



# --------------------------------------------------------------------------
# Revision 4 (contract 1.2). Selected with prompt_revision="4" or
# MIRROR_PROMPT_REVISION=4. Revision 3 above is unchanged, so every recorded
# response keeps replaying; revision 4 has no recordings yet.
# --------------------------------------------------------------------------

VOCABULARY_V4 = """\
Task family: packing a kit of rigid objects into a destination tray on a table.
Objects: cup (open-top vessel that may hold loose beads), block, marker (standing cylinder).
Goal type: an object ends at rest inside the destination tray.
Rule types:
  upright: the object's axis stays within max_tilt_deg of vertical (default 15).
  forbidden_contact: the object must not touch contact_with groups
    (obstacle, table_outside_tray, other_objects). Objects rest on the table until they are
    picked up, so table_outside_tray is only possible in the final phase.
  keep_clearance: the object stays at least min_clearance_m from obstacles (walls, boxes).
    Distance from another object or from an area is not supported. The person must give the
    distance; never invent one.
Phases: final (once placed and settled), transport (from grasp until release of that object),
  always (every moment of the task).
Ordering: objects of one kind before objects of another, with order_event:
  finish_before_start   the earlier kind is packed before the later kind is touched (default,
                        e.g. "pack the marker before the cup", "do the marker first")
  start_before_start    the earlier kind is picked up first ("start with the block",
                        "pick up the marker before the cup")
  placed_before_placed  the earlier kind is in the tray before the later kind is placed
                        ("the block must be in the tray before the cup is placed")"""

CORRECT_SYSTEM_V4 = CORRECT_SYSTEM.replace(VOCABULARY, VOCABULARY_V4).replace(
    "  require_order / relax_order  before_kind must be packed before after_kind\n"
    "                (relax_order with both kinds null relaxes every ordering).",
    "  require_order / relax_order  before_kind before after_kind, with order_event\n"
    "                (relax_order with both kinds null relaxes every ordering; order_event null).").replace(
    "9. interpretation restates, in one sentence, what you understood.",
    "9. interpretation restates, in one sentence, what you understood.\n"
    "10. If the person asks for care without naming what should hold (\"be careful with it\"),\n"
    "    use outcome \"clarify\" and offer the supported changes as options.\n"
    "11. If the request needs something the rule types cannot express (a distance from another\n"
    "    object, a clearance with no distance, no table contact while carrying), use outcome\n"
    "    \"clarify\" and ask for a supported alternative; never approximate it silently.\n"
    "12. order_event is null for every action except require_order.\n"
    "13. A word that is not an object kind in the scene (\"the coffee\", \"the thermos\") names an object\n"
    "    only if context.focus_kind says which object the person selected; otherwise use outcome\n"
    "    \"clarify\" and ask which object they mean or whether it is absent. Never substitute silently.")
PROMPT_REVISION_V4 = "4"


def spec_view_v4(spec: TaskSpec) -> dict[str, Any]:
    v = spec_view(spec)
    v["ordering"] = [{"id": o.id, "before_kind": o.before.kind, "after_kind": o.after.kind,
                      "before_event": o.before_event, "after_event": o.after_event,
                      "status": o.status.value} for o in spec.ordering]
    return v


def correct_messages_v4(spec: TaskSpec, text: str, scene_kinds: Optional[list[str]],
                        context: Optional[dict[str, Any]]) -> list[dict[str, str]]:
    payload: dict[str, Any] = {"specification": spec_view_v4(spec)}
    if scene_kinds is not None:
        payload["objects_in_scene"] = sorted(scene_kinds)
    if context:
        payload["context"] = context
    payload["correction"] = text
    return [{"role": "system", "content": CORRECT_SYSTEM_V4},
            {"role": "user", "content": json.dumps(payload, indent=1, sort_keys=True)}]
