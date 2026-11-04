"""M2 understanding test matrix (author-written development cases).

Every case was written by the developer of the offline rules; none is an
independent or held-out measurement. Categories follow milestone M2.
"jar" and "bowl" informed the absent-object guard during development and are
kept only as REGRESSION cases. The other absent-object words ("thermos",
"goblet", "coffee") were checked against the lexicons before use: none appears
in offline.KIND_WORDS, offline.UNKNOWN_OBJECTS or guard.ABSENT_NOUNS, so they
exercise the general unknown-word path, not a list entry.

Expected: outcome (apply | clarify | refuse | revert), optional reason_code,
and for apply/revert optional enforce/not_enforced/order checks as in
paraphrases.py. ``context`` is passed to the interpreter (focus_kind = object
selected in the interface). ``turns`` (optional) are applied first.
"""
UP_T, UP_F = "c_upright_transport_cup", "c_upright_final_cup"

MATRIX = [
    # 1. unprompted paraphrases of a supported requirement
    dict(cat="paraphrase", text="Make sure nothing spills out of the cup on the way over.",
         expect=dict(outcome="apply", enforce={UP_T: None})),
    dict(cat="paraphrase", text="No tipping the cup while the robot carries it.",
         expect=dict(outcome="apply", enforce={UP_T: None})),
    # 2. object-specific vs all-object scope
    dict(cat="scope", text="Only the cup must stay upright while carried; the block can tilt.",
         expect=dict(outcome="apply", enforce={UP_T: None}, not_enforced=["c_upright_transport_block"])),
    dict(cat="scope", text="Every object must stay upright while it is carried.",
         expect=dict(outcome="apply", enforce={"c_upright_transport_all": None})),
    # 3. transport vs final placement
    dict(cat="phase", text="The cup only needs to be upright once it is in the tray; it may tilt while moving.",
         expect=dict(outcome="apply", enforce={UP_F: None}, not_enforced=[UP_T])),
    dict(cat="phase", text="The marker has to stand upright once it is placed.",
         expect=dict(outcome="apply", enforce={"c_upright_final_marker": None})),
    # 4. ordering
    dict(cat="ordering", text="Pack the block before the cup.",
         expect=dict(outcome="apply", order={"o_block_before_cup": ("placement_complete", "grasp_start")})),
    dict(cat="ordering", text="Pick up the marker before you pick up the block.",
         expect=dict(outcome="apply", order={"o_marker_started_before_block": ("grasp_start", "grasp_start")})),
    # 5. clearance
    dict(cat="clearance", text="Keep the block at least 4 cm away from the wall.",
         expect=dict(outcome="apply", enforce={"c_keep_clearance_transport_block": {"min_m": 0.04}})),
    dict(cat="clearance", text="Keep the cup away from the wall.", expect=dict(outcome="clarify", reason_code="needs_value")),
    # 6. multi-turn changes and reversals
    dict(cat="multi_turn", turns=["The cup can tilt up to 20 degrees while it moves, no more."],
         text="make that 30 degrees", expect=dict(outcome="apply", enforce={UP_T: {"max_tilt_deg": 30.0}})),
    dict(cat="multi_turn", turns=["Keep the cup at least 5 cm away from the wall."], text="Undo that.",
         expect=dict(outcome="revert", not_enforced=["c_keep_clearance_transport_cup"])),
    dict(cat="multi_turn", turns=["Pack the block before the cup.", "Keep the cup at least 5 cm away from the wall."],
         text="Go back to version 1.", expect=dict(outcome="revert", not_enforced=["o_block_before_cup"])),
    # 7. vague instructions: ask which measurable requirement matters, never assume "upright"
    dict(cat="vague", text="Be careful with the cup.", expect=dict(outcome="clarify", reason_code="vague")),
    dict(cat="vague", text="Handle the marker gently.", expect=dict(outcome="clarify", reason_code="vague")),
    # 8. absent objects vs aliases
    dict(cat="absent", text="Put the jar in the tray.", regression=True,
         expect=dict(outcome="clarify", reason_code="absent_object")),
    dict(cat="absent", text="Keep the bowl upright.", regression=True,
         expect=dict(outcome="clarify", reason_code="absent_object")),
    dict(cat="absent", text="Keep the thermos upright while carrying it.",
         expect=dict(outcome="clarify", reason_code="unknown_object")),
    dict(cat="absent", text="Don't let the goblet hit the wall.",
         expect=dict(outcome="clarify", reason_code="unknown_object")),
    dict(cat="alias", text="Don't tip the coffee.", expect=dict(outcome="clarify", reason_code="unknown_object")),
    dict(cat="alias", text="Don't tip the coffee.", context={"focus_kind": "cup"},
         expect=dict(outcome="apply", enforce={UP_T: None}, alias="cup")),
    dict(cat="alias", text="Keep the mug level while you carry it.",
         expect=dict(outcome="apply", enforce={UP_T: None})),
    # 9. unsupported requirements: honest refusal naming what is supported, or a supported alternative
    dict(cat="unsupported", text="Move faster.", expect=dict(outcome="refuse", reason_code="not_supported")),
    dict(cat="unsupported", text="Keep the cup 5 cm away from the marker.",
         expect=dict(outcome="clarify", reason_code="unsupported_rule")),
    dict(cat="unsupported", text="Do not let the cup touch the table while you carry it.",
         expect=dict(outcome="clarify", reason_code="unsupported_rule")),
]

# 10. Infeasible supported combinations: judged by the planner (see test_understanding.py).
INFEASIBLE = [
    dict(name="clearance larger than the start distance", scene="demo",
         text="Keep the cup at least 30 cm away from the wall.", expect="proven_infeasible"),
    dict(name="upright cup through a wall whose only opening is the low slot", scene="blocked",
         text="Keep the cup upright the whole time it is carried.", expect="proven_infeasible"),
    dict(name="tray spot too close to the wall (no proof)", scene="demo",
         text="Once placed, the cup must stay at least 30 cm from the wall.", expect="no_route_found"),
]
