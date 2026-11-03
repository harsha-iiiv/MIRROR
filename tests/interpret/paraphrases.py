"""Author-written paraphrase sets for the bounded correction language.

Both sets were written by the developer of the offline rules, so neither is
an independent benchmark. DEV_PARAPHRASES may be used while developing the
rules. HELDOUT_PARAPHRASES was written and committed before the rules it
exercises were implemented and is never used to tune them; its results are
reported as they come out (tests/interpret/test_capabilities.py reports, it
does not require, a held-out pass rate).

Expected outcome per sentence, against ``capability_base_spec()`` (goals for
cup, block and marker confirmed; cup final-upright confirmed; cup
transport-upright proposed; cup-before-block-before-marker orderings
proposed):
  {"outcome": "apply", "enforce": {rule_id: params or None},
   "not_enforced": [rule_id, ...], "order": {ordering_id: (before_event, after_event)}}
  {"outcome": "clarify"} / {"outcome": "reject"}
"""
from __future__ import annotations

UP_T = "c_upright_transport_cup"
UP_F = "c_upright_final_cup"

DEV_PARAPHRASES = [
    ("Keep the cup level the whole time it is being carried.",
     {"outcome": "apply", "enforce": {UP_T: {"max_tilt_deg": 15.0}}}),
    ("The cup can tilt up to 25 degrees while it moves, no more.",
     {"outcome": "apply", "enforce": {UP_T: {"max_tilt_deg": 25.0}}}),
    ("Only the cup has to stay upright while moving; the marker can be tilted.",
     {"outcome": "apply", "enforce": {UP_T: None}, "not_enforced": ["c_upright_transport_marker"]}),
    ("While carrying it the cup may lean, but once it is in the tray it must stand upright.",
     {"outcome": "apply", "enforce": {UP_F: None}, "not_enforced": [UP_T]}),
    ("Pack the marker before the cup.",
     {"outcome": "apply", "order": {"o_marker_before_cup": ("placement_complete", "grasp_start")}}),
    ("Start with the block.",
     {"outcome": "apply", "order": {"o_block_started_before_cup": ("grasp_start", "grasp_start"),
                                    "o_block_started_before_marker": ("grasp_start", "grasp_start")}}),
    ("Pick up the marker before you pick up the cup.",
     {"outcome": "apply", "order": {"o_marker_started_before_cup": ("grasp_start", "grasp_start")}}),
    ("The block has to be in the tray before the cup is placed.",
     {"outcome": "apply", "order": {"o_block_placed_before_cup_placed": ("placement_complete", "placement_complete")}}),
    ("Keep the cup at least 5 cm away from the wall.",
     {"outcome": "apply", "enforce": {"c_keep_clearance_transport_cup": {"min_m": 0.05}}}),
    ("Stay 3 cm clear of obstacles with the marker while you carry it.",
     {"outcome": "apply", "enforce": {"c_keep_clearance_transport_marker": {"min_m": 0.03}}}),
    ("Keep the cup away from the wall.", {"outcome": "clarify"}),
    ("Keep the cup 5 cm away from the marker.", {"outcome": "clarify"}),
    ("Be careful with the cup.", {"outcome": "clarify"}),
    ("Handle the marker gently.", {"outcome": "clarify"}),
    ("Do not let the cup touch the table while you carry it.", {"outcome": "clarify"}),
    ("The order does not matter.", {"outcome": "apply", "not_enforced": ["o_cup_before_block"]}),
    ("Do not let the cup hit the wall.",
     {"outcome": "apply", "enforce": {"c_forbidden_contact_transport_cup": {"with": ["obstacle"]}}}),
    ("Put the spoon in the tray.", {"outcome": "clarify"}),
    ("Make it faster.", {"outcome": "reject"}),
]

HELDOUT_PARAPHRASES = [
    ("As you move the cup across, don't let it lean over.",
     {"outcome": "apply", "enforce": {UP_T: None}}),
    ("A 30 degree lean on the cup is acceptable during the carry.",
     {"outcome": "apply", "enforce": {UP_T: {"max_tilt_deg": 30.0}}}),
    ("The marker's orientation doesn't matter, only the cup must not tip on the way.",
     {"outcome": "apply", "enforce": {UP_T: None}, "not_enforced": ["c_upright_transport_marker"]}),
    ("It's fine if the cup tilts in transit as long as it ends up standing in the tray.",
     {"outcome": "apply", "enforce": {UP_F: None}, "not_enforced": [UP_T]}),
    ("Do the marker first, then the cup.",
     {"outcome": "apply", "order": {"o_marker_before_cup": ("placement_complete", "grasp_start")}}),
    ("The cup should go in last.",
     {"outcome": "apply", "order": {"o_block_before_cup": ("placement_complete", "grasp_start"),
                                    "o_marker_before_cup": ("placement_complete", "grasp_start")}}),
    ("Grab the block before you grab the marker.",
     {"outcome": "apply", "order": {"o_block_started_before_marker": ("grasp_start", "grasp_start")}}),
    ("I want the marker already sitting in the tray by the time the block is placed.",
     {"outcome": "apply", "order": {"o_marker_placed_before_block_placed": ("placement_complete", "placement_complete")}}),
    ("Give the wall at least 6 centimetres when moving the cup.",
     {"outcome": "apply", "enforce": {"c_keep_clearance_transport_cup": {"min_m": 0.06}}}),
    ("Keep a 40 mm margin between the block and any obstacle.",
     {"outcome": "apply", "enforce": {"c_keep_clearance_transport_block": {"min_m": 0.04}}}),
    ("Don't bring the cup too close to the wall.", {"outcome": "clarify"}),
    ("Keep the block well away from the cup.", {"outcome": "clarify"}),
    ("Treat the cup with care.", {"outcome": "clarify"}),
    ("The cup should never touch the table on its way over.", {"outcome": "clarify"}),
    ("Any order is fine.", {"outcome": "apply", "not_enforced": ["o_cup_before_block"]}),
    ("Paint the block red.", {"outcome": "reject"}),
]
