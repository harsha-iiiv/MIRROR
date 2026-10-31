# Perception robustness

Does perception of the kit survive appearance changes that leave its
geometry alone? This package edits ground-truth frames with an image-editing
model (Muse Image) and local sensor corruptions, checks at pixel level that
each edit kept every object where it was, and re-runs SAM 3.1 on every edited
frame, scoring it against simulator ground truth.

## Pieces

| Module | Role |
|---|---|
| `instructions.py` | Frozen edit list (`INSTRUCTIONS_SHA256` checked by the tests): lighting (dim, warm, harsh shadow), background (walnut table, cluttered lab bench), distractors (second mug, paper), material (glossy cup) as model edits; Gaussian noise, defocus and motion blur as local operations. Also the frozen SAM prompts. |
| `frames.py` | Ground-truth frames: executed rollouts on development layouts rendered by the simulator's renderer (top and oblique cameras; start, carry, end), with visible-pixel masks from a segmentation render of the same pose. |
| `counterfactuals.py` | `make_counterfactuals(frame_png, instructions, client)`: Muse Image edits through the shared Meta client (recorded, replayable), local corruptions in code. Refusals and errors are returned, not raised. A live edit is refused once the usage log shows 150 network edits. |
| `geometry.py` | `check_geometry(original, edited, masks)`: global registration (one similarity transform, chosen by the median fit over objects, gripper and background) undoes the editor's whole-frame drift; then each object's edge-supported outline is matched against the edited frame's edges over shifts and scales. Verdicts: kept, weakened, moved, resized, missing; frames that cannot be registered are reframed. |
| `controls.py` | Synthetic positive and negative controls and `calibrate(frames)`. |
| `evaluate.py` | `evaluate_robustness(frames, instructions, client)`: SAM on originals and edits, IoU and centroid error against ground truth per family and class, every edit kept in the tables with its geometry verdict. |
| `sheets.py` | Contact sheets with ground-truth outlines. |
| `run.py` | `python -m mirror.robustness.run dev|calibrate|frames`. |

## Rules

* Development frames only (`sample_layout(seed, "dev")`) until a benchmark
  list is pre-registered.
* Scores use SAM's top-confidence match; the best-IoU match is chosen with the
  ground truth and reported as a diagnostic only.
* Edits whose geometry changed are reported, never dropped.
* Local corruptions preserve geometry by construction; the check is still run.

## Known limits of the geometry check (measured on controls)

See `results/robustness_dev/calibration.json`. Shifts of 8 px and removals are
detected almost always; 4 px shifts and 12 % rescaling in most cases.
Appearance-only controls pass, except that strong blur sometimes reads as a
small outline change. Changes of shape that keep the outline edges in place
(for example a box redrawn as a cylinder of similar footprint) may be reported
as weakened rather than changed.
