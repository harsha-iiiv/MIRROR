"""Perception robustness: counterfactual frames from an image-editing model,
a geometry-preservation check against simulator ground truth, and SAM 3.1
re-evaluation per edit family."""
from .instructions import (FAMILIES, INSTRUCTIONS, SAM_PROMPTS, EditInstruction, by_id,
                           instructions_sha256)
from .geometry import GeometryReport, ObjectGeometry, check_geometry
from .counterfactuals import Counterfactual, EditBudgetExceeded, make_counterfactuals
from .evaluate import COLOUR_FREE_PROMPTS, CHOOSER, DEFAULT_CONDITIONS, evaluate_robustness, iou, score_sam
from .frames import FramesNotVisible, GTFrame, frames_for_seeds

__all__ = ["FAMILIES", "INSTRUCTIONS", "SAM_PROMPTS", "EditInstruction", "by_id", "instructions_sha256",
           "GeometryReport", "ObjectGeometry", "check_geometry", "Counterfactual", "EditBudgetExceeded",
           "make_counterfactuals", "evaluate_robustness", "iou", "score_sam", "COLOUR_FREE_PROMPTS",
           "CHOOSER", "DEFAULT_CONDITIONS", "FramesNotVisible", "GTFrame", "frames_for_seeds"]
