# Perception robustness: muse-image-1.0 counterfactual edits (plus local sensor edits), sam-3.1 under two prompt conditions, simulator ground-truth masks

Status: completed. Design `170ac8748af2c5ea`, inputs `cd2c842833ba8b8d`, code `f466fbebcb1e7e20`. 8 frames x 11 instructions, two prompt conditions.

## What was measured

Each ground-truth frame was edited once per instruction: by the image model (lighting, background, distractors, material) or by local code (camera noise and blur). SAM 3.1 segmented the cup, block and marker in the original and every edited frame, with phrases a model chose on the original frame (condition A) and with fixed colour-free phrases (condition B), and every mask was compared with the simulator's true mask. The image model shifts and rescales the whole frame slightly; that is measured per edit and undone for the corrected (primary) numbers. A geometry check decides per object whether an edit kept it where it was; objects that moved, resized, weakened or vanished are reported separately, never dropped.

The thresholds were written in the first draft of this design (31 October 2026, before the plan track's development run) and are unchanged. That development run (3 development frames x 11 instructions, results/robustness_dev) and a one-frame rehearsal of this harness were seen before the design was finalised; the frames, prompt conditions and correction rule were chosen after seeing them, the thresholds were not.

## A/corrected

| edits | objects | found | kept objects | kept: found | kept: median IoU change | kept: median centroid error | weakened | changed |
|---|---|---|---|---|---|---|---|---|
| original | 24 | 0.583 | 24 | 0.583 | n/a | 0.15 px | 0 | 0 |
| lighting | 69 | 0.522 | 54 | 0.500 | -0.005 | 1.38 px | 1 | 14 |
| background | 48 | 0.438 | 34 | 0.471 | -0.042 | 1.25 px | 0 | 14 |
| distractor | 48 | 0.542 | 40 | 0.500 | -0.002 | 0.88 px | 0 | 8 |
| material | 24 | 0.458 | 18 | 0.444 | -0.015 | 1.30 px | 0 | 6 |
| sensor | 72 | 0.444 | 72 | 0.444 | 0.000 | 0.23 px | 0 | 0 |
| all_edits | 261 | 0.483 | 218 | 0.472 | -0.000 | 0.67 px | 1 | 42 |

Pre-stated checks (all edits, geometry kept):

- median IoU change >= -0.05 (geometry kept): met
- found rate >= 0.9 (geometry kept): not met
- median centroid error <= 4.0 px (geometry kept): met

## A/uncorrected

| edits | objects | found | kept objects | kept: found | kept: median IoU change | kept: median centroid error | weakened | changed |
|---|---|---|---|---|---|---|---|---|
| original | 24 | 0.583 | 24 | 0.583 | n/a | 0.15 px | 0 | 0 |
| lighting | 69 | 0.522 | 54 | 0.500 | -0.036 | 2.65 px | 1 | 14 |
| background | 48 | 0.458 | 34 | 0.500 | -0.171 | 3.55 px | 0 | 14 |
| distractor | 48 | 0.542 | 40 | 0.500 | -0.003 | 3.56 px | 0 | 8 |
| material | 24 | 0.500 | 18 | 0.500 | -0.053 | 2.37 px | 0 | 6 |
| sensor | 72 | 0.444 | 72 | 0.444 | 0.000 | 0.23 px | 0 | 0 |
| all_edits | 261 | 0.490 | 218 | 0.482 | -0.001 | 1.93 px | 1 | 42 |

## B/corrected

| edits | objects | found | kept objects | kept: found | kept: median IoU change | kept: median centroid error | weakened | changed |
|---|---|---|---|---|---|---|---|---|
| original | 24 | 0.667 | 24 | 0.667 | n/a | 0.20 px | 0 | 0 |
| lighting | 69 | 0.739 | 54 | 0.704 | 0.000 | 1.66 px | 1 | 14 |
| background | 48 | 0.583 | 34 | 0.618 | -0.011 | 1.51 px | 0 | 14 |
| distractor | 48 | 0.646 | 40 | 0.625 | 0.000 | 0.99 px | 0 | 8 |
| material | 24 | 0.583 | 18 | 0.611 | 0.000 | 150.88 px | 0 | 6 |
| sensor | 72 | 0.514 | 72 | 0.514 | 0.000 | 0.46 px | 0 | 0 |
| all_edits | 261 | 0.617 | 218 | 0.606 | 0.000 | 1.50 px | 1 | 42 |

Pre-stated checks (all edits, geometry kept):

- median IoU change >= -0.05 (geometry kept): met
- found rate >= 0.9 (geometry kept): not met
- median centroid error <= 4.0 px (geometry kept): met

## Chosen phrases (condition A)

| frame | cup | block | marker |
|---|---|---|---|
| eval80000_top_carry | (none) | blue cube | orange cylinder |
| eval80001_oblique_carry | (none) | blue cube | red cylinder |
| eval80002_top_carry | (none) | blue cube | (none) |
| eval80003_oblique_carry | (none) | blue cube | red cylinder |
| eval80004_top_carry | tan cube | blue cube | brown cylinder |
| eval80005_oblique_carry | (none) | blue cube | red cylinder |
| eval80006_top_carry | (none) | (none) | (none) |
| eval80007_oblique_carry | (none) | blue cube | red cylinder |

Fixed phrases (condition B): {"cup": "open box", "block": "small cube", "marker": "cylinder"}.

## Spend

| model | network calls | replayed | median latency | cost |
|---|---|---|---|---|
| muse-spark-1.3 | 14 | 0 | 9.6 s | $0.152 |
| sam-3.1 | 575 | 14 | 0.8 s | $1.438 |
| muse-image-1.0 | 63 | 0 | 7.1 s | $0.630 |

Total $2.220 at list prices (dev.meta.ai/docs/pricing-rate-limits and dev.meta.ai/products/meta-model-api, read 2026-10-31).

## Every edit

| frame | instruction | applied by | status | geometry | objects | correction (scale, dx, dy) |
|---|---|---|---|---|---|---|
| eval80000_top_carry | light_dim | image model | ok | preserved | cup kept, block kept, marker kept | 1.000, -3, -1 |
| eval80000_top_carry | light_warm | image model | ok | preserved | cup kept, block kept, marker kept | 0.990, 0, 0 |
| eval80000_top_carry | light_harsh | image model | refused (MetaError: HTTP 400: {"error":{"code":"content_policy_violation","message":"The ) | None | - | - |
| eval80000_top_carry | bg_wood | image model | ok | preserved | cup kept, block kept, marker kept | 0.990, -5, 0 |
| eval80000_top_carry | bg_lab_bench | image model | ok | preserved | cup kept, block kept, marker kept | 1.000, -4, -3 |
| eval80000_top_carry | distractor_mug | image model | ok | preserved | cup kept, block kept, marker kept | 0.990, -8, 0 |
| eval80000_top_carry | distractor_paper | image model | ok | preserved | cup kept, block kept, marker kept | 0.990, -2, -1 |
| eval80000_top_carry | material_glossy_cup | image model | ok | preserved | cup kept, block kept, marker kept | 1.000, -2, 0 |
| eval80000_top_carry | noise_gauss | local code | ok | kept by construction | cup kept, block kept, marker kept | 1.000, 0, 0 |
| eval80000_top_carry | blur_gauss | local code | ok | kept by construction | cup kept, block kept, marker kept | 1.000, 0, 0 |
| eval80000_top_carry | blur_motion | local code | ok | kept by construction (check said changed, not used) | cup moved, block kept, marker kept | 1.000, 0, 0 |
| eval80001_oblique_carry | light_dim | image model | ok | changed | cup kept, block moved, marker moved | 0.990, -3, -2 |
| eval80001_oblique_carry | light_warm | image model | ok | changed | cup moved, block kept, marker moved | 1.010, 2, 1 |
| eval80001_oblique_carry | light_harsh | image model | ok | changed | cup moved, block moved, marker moved | 1.010, -2, 2 |
| eval80001_oblique_carry | bg_wood | image model | ok | preserved | cup kept, block kept, marker kept | 1.010, -4, 1 |
| eval80001_oblique_carry | bg_lab_bench | image model | ok | preserved | cup kept, block kept, marker kept | 1.000, -2, -1 |
| eval80001_oblique_carry | distractor_mug | image model | ok | changed | cup kept, block moved, marker moved | 1.000, -1, -1 |
| eval80001_oblique_carry | distractor_paper | image model | ok | changed | cup kept, block moved, marker moved | 1.000, -1, 0 |
| eval80001_oblique_carry | material_glossy_cup | image model | ok | changed | cup kept, block moved, marker moved | 1.000, -1, 1 |
| eval80001_oblique_carry | noise_gauss | local code | ok | kept by construction | cup kept, block kept, marker kept | 1.000, 0, 0 |
| eval80001_oblique_carry | blur_gauss | local code | ok | kept by construction (check said changed, not used) | cup moved, block kept, marker kept | 1.000, 0, 0 |
| eval80001_oblique_carry | blur_motion | local code | ok | kept by construction | cup kept, block kept, marker kept | 1.000, 0, 0 |
| eval80002_top_carry | light_dim | image model | ok | preserved | cup kept, block kept, marker kept | 1.000, 0, -1 |
| eval80002_top_carry | light_warm | image model | ok | preserved | cup kept, block kept, marker weakened | 1.000, 2, 1 |
| eval80002_top_carry | light_harsh | image model | ok | changed | cup kept, block moved, marker kept | 0.990, 1, -2 |
| eval80002_top_carry | bg_wood | image model | ok | changed | cup kept, block moved, marker kept | 0.990, 4, -1 |
| eval80002_top_carry | bg_lab_bench | image model | ok | changed | cup moved, block kept, marker kept | 0.980, 0, 1 |
| eval80002_top_carry | distractor_mug | image model | ok | preserved | cup kept, block kept, marker kept | 1.000, 0, -1 |
| eval80002_top_carry | distractor_paper | image model | ok | changed | cup moved, block kept, marker kept | 0.990, 0, -2 |
| eval80002_top_carry | material_glossy_cup | image model | ok | changed | cup missing, block kept, marker moved | 0.990, 0, 0 |
| eval80002_top_carry | noise_gauss | local code | ok | kept by construction | cup kept, block kept, marker kept | 1.000, 0, 0 |
| eval80002_top_carry | blur_gauss | local code | ok | kept by construction (check said changed, not used) | cup moved, block kept, marker kept | 1.000, 0, 0 |
| eval80002_top_carry | blur_motion | local code | ok | kept by construction (check said changed, not used) | cup moved, block kept, marker kept | 1.000, 0, 0 |
| eval80003_oblique_carry | light_dim | image model | ok | changed | cup kept, block moved, marker kept | 1.000, -6, 0 |
| eval80003_oblique_carry | light_warm | image model | ok | changed | cup kept, block moved, marker kept | 1.010, -8, 2 |
| eval80003_oblique_carry | light_harsh | image model | ok | changed | cup kept, block moved, marker kept | 1.010, -9, 1 |
| eval80003_oblique_carry | bg_wood | image model | ok | changed | cup moved, block moved, marker kept | 1.000, -9, -1 |
| eval80003_oblique_carry | bg_lab_bench | image model | ok | changed | cup moved, block moved, marker kept | 1.010, -8, -2 |
| eval80003_oblique_carry | distractor_mug | image model | ok | changed | cup kept, block moved, marker kept | 1.010, -4, 2 |
| eval80003_oblique_carry | distractor_paper | image model | ok | changed | cup kept, block moved, marker kept | 1.000, -8, 0 |
| eval80003_oblique_carry | material_glossy_cup | image model | ok | changed | cup kept, block moved, marker kept | 1.010, -3, 3 |
| eval80003_oblique_carry | noise_gauss | local code | ok | kept by construction | cup kept, block kept, marker kept | 1.000, 0, 0 |
| eval80003_oblique_carry | blur_gauss | local code | ok | kept by construction | cup kept, block kept, marker kept | 1.000, 0, 0 |
| eval80003_oblique_carry | blur_motion | local code | ok | kept by construction (check said changed, not used) | cup moved, block kept, marker kept | 1.000, 0, 0 |
| eval80004_top_carry | light_dim | image model | ok | preserved | cup kept, block kept, marker kept | 0.990, 0, 1 |
| eval80004_top_carry | light_warm | image model | ok | preserved | cup kept, block kept, marker kept | 0.990, 2, 1 |
| eval80004_top_carry | light_harsh | image model | ok | preserved | cup kept, block kept, marker kept | 0.990, 1, 0 |
| eval80004_top_carry | bg_wood | image model | ok | preserved | cup kept, block kept, marker kept | 0.990, 1, 0 |
| eval80004_top_carry | bg_lab_bench | image model | ok | reframed | cup untested, block untested, marker untested | 0.970, 4, 7 |
| eval80004_top_carry | distractor_mug | image model | ok | preserved | cup kept, block kept, marker kept | 1.000, 0, 0 |
| eval80004_top_carry | distractor_paper | image model | ok | preserved | cup kept, block kept, marker kept | 1.000, 1, -1 |
| eval80004_top_carry | material_glossy_cup | image model | ok | preserved | cup kept, block kept, marker kept | 0.990, -1, 0 |
| eval80004_top_carry | noise_gauss | local code | ok | kept by construction | cup kept, block kept, marker kept | 1.000, 0, 0 |
| eval80004_top_carry | blur_gauss | local code | ok | kept by construction | cup kept, block kept, marker kept | 1.000, 0, 0 |
| eval80004_top_carry | blur_motion | local code | ok | kept by construction | cup kept, block kept, marker kept | 1.000, 0, 0 |
| eval80005_oblique_carry | light_dim | image model | ok | preserved | cup kept, block kept, marker kept | 0.990, -4, 0 |
| eval80005_oblique_carry | light_warm | image model | ok | preserved | cup kept, block kept, marker kept | 1.000, -1, 0 |
| eval80005_oblique_carry | light_harsh | image model | ok | preserved | cup kept, block kept, marker kept | 1.000, -7, 0 |
| eval80005_oblique_carry | bg_wood | image model | ok | preserved | cup kept, block kept, marker kept | 1.000, -3, 0 |
| eval80005_oblique_carry | bg_lab_bench | image model | ok | preserved | cup kept, block kept, marker kept | 1.000, -7, 1 |
| eval80005_oblique_carry | distractor_mug | image model | ok | preserved | cup kept, block kept, marker kept | 1.010, -9, 2 |
| eval80005_oblique_carry | distractor_paper | image model | ok | preserved | cup kept, block kept, marker kept | 1.000, -4, -1 |
| eval80005_oblique_carry | material_glossy_cup | image model | ok | preserved | cup kept, block kept, marker kept | 1.000, -5, 0 |
| eval80005_oblique_carry | noise_gauss | local code | ok | kept by construction | cup kept, block kept, marker kept | 1.000, 0, 0 |
| eval80005_oblique_carry | blur_gauss | local code | ok | kept by construction | cup weakened, block kept, marker kept | 1.000, 0, 0 |
| eval80005_oblique_carry | blur_motion | local code | ok | kept by construction | cup kept, block kept, marker kept | 1.000, 0, 0 |
| eval80006_top_carry | light_dim | image model | ok | preserved | cup kept, block kept, marker kept | 0.990, -7, 0 |
| eval80006_top_carry | light_warm | image model | ok | changed | cup kept, block moved, marker moved | 0.980, -7, -3 |
| eval80006_top_carry | light_harsh | image model | ok | preserved | cup kept, block kept, marker kept | 0.990, -4, 0 |
| eval80006_top_carry | bg_wood | image model | ok | changed | cup kept, block moved, marker kept | 1.000, -2, 0 |
| eval80006_top_carry | bg_lab_bench | image model | ok | reframed | cup untested, block untested, marker untested | 0.980, -14, 11 |
| eval80006_top_carry | distractor_mug | image model | ok | changed | cup kept, block kept, marker moved | 0.980, -3, -1 |
| eval80006_top_carry | distractor_paper | image model | ok | preserved | cup kept, block kept, marker kept | 0.990, -5, -2 |
| eval80006_top_carry | material_glossy_cup | image model | ok | changed | cup moved, block kept, marker kept | 0.990, -4, -2 |
| eval80006_top_carry | noise_gauss | local code | ok | kept by construction | cup kept, block kept, marker kept | 1.000, 0, 0 |
| eval80006_top_carry | blur_gauss | local code | ok | kept by construction | cup kept, block kept, marker kept | 1.000, 0, 0 |
| eval80006_top_carry | blur_motion | local code | ok | kept by construction (check said changed, not used) | cup moved, block kept, marker kept | 1.000, 0, 0 |
| eval80007_oblique_carry | light_dim | image model | ok | preserved | cup kept, block kept, marker kept | 0.990, 1, -1 |
| eval80007_oblique_carry | light_warm | image model | ok | changed | cup missing, block kept, marker kept | 1.000, -2, -2 |
| eval80007_oblique_carry | light_harsh | image model | ok | preserved | cup kept, block kept, marker kept | 0.990, 0, 0 |
| eval80007_oblique_carry | bg_wood | image model | ok | preserved | cup kept, block kept, marker kept | 1.000, 1, -2 |
| eval80007_oblique_carry | bg_lab_bench | image model | ok | changed | cup missing, block kept, marker kept | 1.000, -3, -1 |
| eval80007_oblique_carry | distractor_mug | image model | ok | preserved | cup kept, block kept, marker kept | 1.000, 1, -1 |
| eval80007_oblique_carry | distractor_paper | image model | ok | preserved | cup kept, block kept, marker kept | 1.000, -3, 0 |
| eval80007_oblique_carry | material_glossy_cup | image model | ok | preserved | cup kept, block kept, marker kept | 1.000, 3, -2 |
| eval80007_oblique_carry | noise_gauss | local code | ok | kept by construction | cup kept, block kept, marker kept | 1.000, 0, 0 |
| eval80007_oblique_carry | blur_gauss | local code | ok | kept by construction | cup kept, block kept, marker kept | 1.000, 0, 0 |
| eval80007_oblique_carry | blur_motion | local code | ok | kept by construction | cup kept, block kept, marker kept | 1.000, 0, 0 |

Edit failures: 1 of 88. Every SAM request with its prompt, match count, IoU and centroid error is in plan_report/report.json (rows). Budget lines: {"muse-image-1.0": {"used": 64, "cap": 80}, "sam-3.1": {"used": 589, "cap": 850}, "muse-spark-1.3:chooser": {"used": 14, "cap": 40}}. Every attempt is in ledger.jsonl (100 entries).
