# Perception robustness: SAM 3.1 on Muse Image counterfactuals

Split: dev. Frames: 2. Instructions: 6 (sha256 fd517bf09b4879f3).

Prompt conditions: A = Muse-to-SAM chooser per original frame; B = {"cup": "open box", "block": "small cube", "marker": "cylinder"}.

Chooser phrases (A): dev3_top_carry: cup "None", block "blue cube", marker "None"; dev8_oblique_carry: cup "white box", block "blue cube", marker "red cylinder"

## A/corrected

| family | rows | found | IoU | ΔIoU | centroid px | kept: found / median ΔIoU / median centroid px | geometry |
|---|---|---|---|---|---|---|---|
| original | 6 | 0.6667 | 0.652 | None | 0.1525 | 0.6667 / None / 0.165 | original 6 |
| lighting | 12 | 0.5 | 0.4534 | -0.1986 | 1.5267 | 0.5 / 0.0 / 0.745 | kept 8, moved 1, untested 2, weakened 1 |
| background | 6 | 0.5 | 0.4565 | -0.1956 | 1.45 | 0.4 / -0.0627 / 1.465 | kept 5, untested 1 |
| distractor | 6 | 0.6667 | 0.6274 | -0.0246 | 0.825 | 0.75 / -0.0299 / 0.68 | kept 4, moved 1, untested 1 |
| material | 6 | 0.5 | 0.4543 | -0.1977 | 1.3233 | 0.5 / -0.003 / 0.655 | kept 4, resized 1, untested 1 |
| sensor | 6 | 0.6667 | 0.6476 | -0.0044 | 0.25 | 0.6667 / -0.0001 / 0.15 | preserved 6 |
| all_edits | 36 | 0.5556 | 0.5154 | -0.1366 | 1.089 | 0.5556 / -0.0001 / 0.65 | kept 21, moved 2, preserved 6, resized 1, untested 5, weakened 1 |

## A/uncorrected

| family | rows | found | IoU | ΔIoU | centroid px | kept: found / median ΔIoU / median centroid px | geometry |
|---|---|---|---|---|---|---|---|
| original | 6 | 0.6667 | 0.652 | None | 0.1525 | 0.6667 / None / 0.165 | original 6 |
| lighting | 12 | 0.5 | 0.2791 | -0.3729 | 7.3317 | 0.5 / -0.0466 / 5.72 | kept 8, moved 1, untested 2, weakened 1 |
| background | 6 | 0.5 | 0.2665 | -0.3856 | 10.7033 | 0.4 / -0.2636 / 9.15 | kept 5, untested 1 |
| distractor | 6 | 0.6667 | 0.3979 | -0.2541 | 6.4125 | 0.75 / -0.2503 / 8.49 | kept 4, moved 1, untested 1 |
| material | 6 | 0.5 | 0.3494 | -0.3026 | 4.1167 | 0.5 / -0.0217 / 3.69 | kept 4, resized 1, untested 1 |
| sensor | 6 | 0.6667 | 0.6476 | -0.0044 | 0.25 | 0.6667 / -0.0001 / 0.15 | preserved 6 |
| all_edits | 36 | 0.5556 | 0.3699 | -0.2821 | 5.755 | 0.5556 / -0.0008 / 2.04 | kept 21, moved 2, preserved 6, resized 1, untested 5, weakened 1 |

## B/corrected

| family | rows | found | IoU | ΔIoU | centroid px | kept: found / median ΔIoU / median centroid px | geometry |
|---|---|---|---|---|---|---|---|
| original | 6 | 0.6667 | 0.4906 | None | 37.8875 | 0.6667 / None / 0.17 | original 6 |
| lighting | 12 | 0.75 | 0.4518 | -0.0388 | 64.08 | 0.625 / 0.0 / 0.94 | kept 8, moved 1, untested 2, weakened 1 |
| background | 6 | 0.5 | 0.3034 | -0.1872 | 51.11 | 0.4 / -0.061 / 1.445 | kept 5, untested 1 |
| distractor | 6 | 0.6667 | 0.4675 | -0.0231 | 38.595 | 0.75 / -0.0311 / 0.68 | kept 4, moved 1, untested 1 |
| material | 6 | 0.5 | 0.307 | -0.1836 | 52.32 | 0.25 / -0.0017 / 0.21 | kept 4, resized 1, untested 1 |
| sensor | 6 | 0.5 | 0.3286 | -0.162 | 50.4233 | 0.5 / 0.0 / 0.08 | preserved 6 |
| all_edits | 36 | 0.6111 | 0.385 | -0.1056 | 54.2118 | 0.5185 / 0.0 / 0.845 | kept 21, moved 2, preserved 6, resized 1, untested 5, weakened 1 |

Model edits: 10 (ok 10, refused 0, errors 0); geometry changed in 3, reframed in 0. Latency per edit (as recorded): mean 7.2768 s (min 6.06, max 9.59). Output sizes: [(2048, 1152)]. Whole-frame correction: median shift 3.9241 px, max 15.13 px, scales [0.98, 0.99, 1.0].

| frame | edit | dx | dy | scale | fit px |
|---|---|---|---|---|---|
| dev3_top_carry | light_dim | 1 | 1 | 1.0 | 0.507 |
| dev3_top_carry | light_warm | 0 | 1 | 1.0 | 0.658 |
| dev3_top_carry | bg_wood | 3 | 2 | 1.0 | 0.958 |
| dev3_top_carry | distractor_mug | 0 | 3 | 0.99 | 0.267 |
| dev3_top_carry | material_glossy_cup | 0 | 1 | 1.0 | 0.587 |
| dev3_top_carry | noise_gauss | 0 | 0 | 1.0 | 0.087 |
| dev8_oblique_carry | light_dim | -6 | -5 | 0.98 | 0.594 |
| dev8_oblique_carry | light_warm | -8 | 0 | 0.99 | 0.764 |
| dev8_oblique_carry | bg_wood | -15 | -2 | 1.0 | 1.999 |
| dev8_oblique_carry | distractor_mug | -7 | -2 | 0.99 | 0.441 |
| dev8_oblique_carry | material_glossy_cup | -3 | -3 | 0.99 | 0.878 |
| dev8_oblique_carry | noise_gauss | 0 | 0 | 1.0 | 0.158 |

SAM: 90 calls (23 over the network), mean latency 1.0376 s.

Notes:
- Ground truth: visible-pixel masks from a segmentation render of the same simulator frame.
- Edited frames are resampled to the source size. 'corrected' also undoes one whole-frame similarity transform per edit (the editor's drift; listed in 'corrections'); 'uncorrected' does not.
- Condition A uses the Muse-to-SAM prompt chooser on each original frame and reuses its phrases for that frame's edits; condition B uses fixed colour-free phrases.
- iou and centroid error use SAM's top-confidence match; iou_best (chosen with the ground truth) is diagnostic only.
- Local sensor corruptions preserve geometry by construction; the geometry check is still run.
- Edits whose geometry changed stay in every table; their scores use a ground truth that no longer fits.
