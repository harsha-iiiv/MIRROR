# Perception robustness: SAM 3.1 on Muse Image counterfactuals

Split: eval. Frames: 8. Instructions: 11 (sha256 fd517bf09b4879f3).

Prompt conditions: A = Muse-to-SAM chooser per original frame; B = {"cup": "open box", "block": "small cube", "marker": "cylinder"}.

Chooser phrases (A): eval80000_top_carry: cup "None", block "blue cube", marker "orange cylinder"; eval80001_oblique_carry: cup "None", block "blue cube", marker "red cylinder"; eval80002_top_carry: cup "None", block "blue cube", marker "None"; eval80003_oblique_carry: cup "None", block "blue cube", marker "red cylinder"; eval80004_top_carry: cup "tan cube", block "blue cube", marker "brown cylinder"; eval80005_oblique_carry: cup "None", block "blue cube", marker "red cylinder"; eval80006_top_carry: cup "None", block "None", marker "None"; eval80007_oblique_carry: cup "None", block "blue cube", marker "red cylinder"

## A/corrected

| family | rows | found | IoU | ΔIoU | centroid px | kept: found / median ΔIoU / median centroid px | geometry |
|---|---|---|---|---|---|---|---|
| original | 24 | 0.5833 | 0.5307 | None | 11.9057 | 0.5833 / None / 0.15 | original 24 |
| lighting | 69 | 0.5217 | 0.4475 | -0.0779 | 6.4525 | 0.5 / -0.0046 / 1.38 | kept 54, missing 1, moved 13, weakened 1 |
| background | 48 | 0.4375 | 0.3452 | -0.1855 | 17.5738 | 0.4706 / -0.0422 / 1.245 | kept 34, missing 1, moved 7, reframed 6 |
| distractor | 48 | 0.5417 | 0.4347 | -0.096 | 14.6685 | 0.5 / -0.0016 / 0.88 | kept 40, moved 8 |
| material | 24 | 0.4583 | 0.3928 | -0.1379 | 2.5836 | 0.4444 / -0.0149 / 1.3 | kept 18, missing 1, moved 5 |
| sensor | 72 | 0.4444 | 0.4022 | -0.1285 | 10.5003 | 0.4444 / 0.0 / 0.225 | preserved 72 |
| all_edits | 261 | 0.4828 | 0.4088 | -0.1205 | 10.6917 | 0.4725 / -0.0001 / 0.67 | kept 146, missing 3, moved 33, preserved 72, reframed 6, weakened 1 |

## A/uncorrected

| family | rows | found | IoU | ΔIoU | centroid px | kept: found / median ΔIoU / median centroid px | geometry |
|---|---|---|---|---|---|---|---|
| original | 24 | 0.5833 | 0.5307 | None | 11.9057 | 0.5833 / None / 0.15 | original 24 |
| lighting | 69 | 0.5217 | 0.3834 | -0.142 | 7.7289 | 0.5 / -0.0365 / 2.65 | kept 54, missing 1, moved 13, weakened 1 |
| background | 48 | 0.4583 | 0.2874 | -0.2433 | 18.8209 | 0.5 / -0.1709 / 3.55 | kept 34, missing 1, moved 7, reframed 6 |
| distractor | 48 | 0.5417 | 0.3696 | -0.1611 | 16.0108 | 0.5 / -0.003 / 3.565 | kept 40, moved 8 |
| material | 24 | 0.5 | 0.388 | -0.1427 | 3.2017 | 0.5 / -0.0528 / 2.37 | kept 18, missing 1, moved 5 |
| sensor | 72 | 0.4444 | 0.4022 | -0.1285 | 10.5003 | 0.4444 / 0.0 / 0.225 | preserved 72 |
| all_edits | 261 | 0.4904 | 0.3688 | -0.1605 | 11.586 | 0.4817 / -0.0007 / 1.93 | kept 146, missing 3, moved 33, preserved 72, reframed 6, weakened 1 |

## B/corrected

| family | rows | found | IoU | ΔIoU | centroid px | kept: found / median ΔIoU / median centroid px | geometry |
|---|---|---|---|---|---|---|---|
| original | 24 | 0.6667 | 0.449 | None | 78.1169 | 0.6667 / None / 0.205 | original 24 |
| lighting | 69 | 0.7391 | 0.4093 | -0.045 | 91.2878 | 0.7037 / 0.0 / 1.655 | kept 54, missing 1, moved 13, weakened 1 |
| background | 48 | 0.5833 | 0.347 | -0.102 | 78.2636 | 0.6176 / -0.011 / 1.51 | kept 34, missing 1, moved 7, reframed 6 |
| distractor | 48 | 0.6458 | 0.4345 | -0.0145 | 53.1616 | 0.625 / 0.0 / 0.99 | kept 40, moved 8 |
| material | 24 | 0.5833 | 0.2754 | -0.1736 | 102.6786 | 0.6111 / 0.0 / 150.88 | kept 18, missing 1, moved 5 |
| sensor | 72 | 0.5139 | 0.2696 | -0.1794 | 105.8403 | 0.5139 / 0.0 / 0.46 | preserved 72 |
| all_edits | 261 | 0.6169 | 0.3516 | -0.0988 | 86.0165 | 0.6055 / 0.0 / 1.495 | kept 146, missing 3, moved 33, preserved 72, reframed 6, weakened 1 |

Model edits: 64 (ok 63, refused 1, errors 0); geometry changed in 25, reframed in 2. Latency per edit (as recorded): mean 7.3849 s (min 5.02, max 11.58). Output sizes: [(2048, 1152)]. Whole-frame correction: median shift 3.0 px, max 17.8 px, scales [0.97, 0.98, 0.99, 1.0, 1.01].

| frame | edit | dx | dy | scale | fit px |
|---|---|---|---|---|---|
| eval80000_top_carry | light_dim | -3 | -1 | 1.0 | 0.339 |
| eval80000_top_carry | light_warm | 0 | 0 | 0.99 | 0.618 |
| eval80000_top_carry | bg_wood | -5 | 0 | 0.99 | 0.771 |
| eval80000_top_carry | bg_lab_bench | -4 | -3 | 1.0 | 0.597 |
| eval80000_top_carry | distractor_mug | -8 | 0 | 0.99 | 0.318 |
| eval80000_top_carry | distractor_paper | -2 | -1 | 0.99 | 0.449 |
| eval80000_top_carry | material_glossy_cup | -2 | 0 | 1.0 | 0.616 |
| eval80000_top_carry | noise_gauss | 0 | 0 | 1.0 | 0.058 |
| eval80000_top_carry | blur_gauss | 0 | 0 | 1.0 | 0.591 |
| eval80000_top_carry | blur_motion | 0 | 0 | 1.0 | 0.7 |
| eval80001_oblique_carry | light_dim | -3 | -2 | 0.99 | 0.543 |
| eval80001_oblique_carry | light_warm | 2 | 1 | 1.01 | 1.659 |
| eval80001_oblique_carry | light_harsh | -2 | 2 | 1.01 | 2.132 |
| eval80001_oblique_carry | bg_wood | -4 | 1 | 1.01 | 1.845 |
| eval80001_oblique_carry | bg_lab_bench | -2 | -1 | 1.0 | 0.893 |
| eval80001_oblique_carry | distractor_mug | -1 | -1 | 1.0 | 0.457 |
| eval80001_oblique_carry | distractor_paper | -1 | 0 | 1.0 | 0.425 |
| eval80001_oblique_carry | material_glossy_cup | -1 | 1 | 1.0 | 1.093 |
| eval80001_oblique_carry | noise_gauss | 0 | 0 | 1.0 | 0.148 |
| eval80001_oblique_carry | blur_gauss | 0 | 0 | 1.0 | 0.485 |
| eval80001_oblique_carry | blur_motion | 0 | 0 | 1.0 | 0.455 |
| eval80002_top_carry | light_dim | 0 | -1 | 1.0 | 0.344 |
| eval80002_top_carry | light_warm | 2 | 1 | 1.0 | 1.213 |
| eval80002_top_carry | light_harsh | 1 | -2 | 0.99 | 0.752 |
| eval80002_top_carry | bg_wood | 4 | -1 | 0.99 | 0.634 |
| eval80002_top_carry | bg_lab_bench | 0 | 1 | 0.98 | 0.489 |
| eval80002_top_carry | distractor_mug | 0 | -1 | 1.0 | 0.385 |
| eval80002_top_carry | distractor_paper | 0 | -2 | 0.99 | 0.483 |
| eval80002_top_carry | material_glossy_cup | 0 | 0 | 0.99 | 1.478 |
| eval80002_top_carry | noise_gauss | 0 | 0 | 1.0 | 0.069 |
| eval80002_top_carry | blur_gauss | 0 | 0 | 1.0 | 0.543 |
| eval80002_top_carry | blur_motion | 0 | 0 | 1.0 | 0.658 |
| eval80003_oblique_carry | light_dim | -6 | 0 | 1.0 | 0.623 |
| eval80003_oblique_carry | light_warm | -8 | 2 | 1.01 | 0.661 |
| eval80003_oblique_carry | light_harsh | -9 | 1 | 1.01 | 1.357 |
| eval80003_oblique_carry | bg_wood | -9 | -1 | 1.0 | 1.601 |
| eval80003_oblique_carry | bg_lab_bench | -8 | -2 | 1.01 | 1.739 |
| eval80003_oblique_carry | distractor_mug | -4 | 2 | 1.01 | 0.302 |
| eval80003_oblique_carry | distractor_paper | -8 | 0 | 1.0 | 0.464 |
| eval80003_oblique_carry | material_glossy_cup | -3 | 3 | 1.01 | 0.908 |
| eval80003_oblique_carry | noise_gauss | 0 | 0 | 1.0 | 0.122 |
| eval80003_oblique_carry | blur_gauss | 0 | 0 | 1.0 | 0.463 |
| eval80003_oblique_carry | blur_motion | 0 | 0 | 1.0 | 0.73 |
| eval80004_top_carry | light_dim | 0 | 1 | 0.99 | 0.287 |
| eval80004_top_carry | light_warm | 2 | 1 | 0.99 | 0.677 |
| eval80004_top_carry | light_harsh | 1 | 0 | 0.99 | 1.375 |
| eval80004_top_carry | bg_wood | 1 | 0 | 0.99 | 0.708 |
| eval80004_top_carry | bg_lab_bench | 4 | 7 | 0.97 | 2.658 |
| eval80004_top_carry | distractor_mug | 0 | 0 | 1.0 | 0.286 |
| eval80004_top_carry | distractor_paper | 1 | -1 | 1.0 | 0.33 |
| eval80004_top_carry | material_glossy_cup | -1 | 0 | 0.99 | 0.766 |
| eval80004_top_carry | noise_gauss | 0 | 0 | 1.0 | 0.068 |
| eval80004_top_carry | blur_gauss | 0 | 0 | 1.0 | 0.448 |
| eval80004_top_carry | blur_motion | 0 | 0 | 1.0 | 0.649 |
| eval80005_oblique_carry | light_dim | -4 | 0 | 0.99 | 0.781 |
| eval80005_oblique_carry | light_warm | -1 | 0 | 1.0 | 0.792 |
| eval80005_oblique_carry | light_harsh | -7 | 0 | 1.0 | 0.576 |
| eval80005_oblique_carry | bg_wood | -3 | 0 | 1.0 | 1.519 |
| eval80005_oblique_carry | bg_lab_bench | -7 | 1 | 1.0 | 0.476 |
| eval80005_oblique_carry | distractor_mug | -9 | 2 | 1.01 | 0.459 |
| eval80005_oblique_carry | distractor_paper | -4 | -1 | 1.0 | 0.312 |
| eval80005_oblique_carry | material_glossy_cup | -5 | 0 | 1.0 | 0.589 |
| eval80005_oblique_carry | noise_gauss | 0 | 0 | 1.0 | 0.206 |
| eval80005_oblique_carry | blur_gauss | 0 | 0 | 1.0 | 0.624 |
| eval80005_oblique_carry | blur_motion | 0 | 0 | 1.0 | 0.706 |
| eval80006_top_carry | light_dim | -7 | 0 | 0.99 | 0.705 |
| eval80006_top_carry | light_warm | -7 | -3 | 0.98 | 0.926 |
| eval80006_top_carry | light_harsh | -4 | 0 | 0.99 | 0.862 |
| eval80006_top_carry | bg_wood | -2 | 0 | 1.0 | 0.76 |
| eval80006_top_carry | bg_lab_bench | -14 | 11 | 0.98 | 2.762 |
| eval80006_top_carry | distractor_mug | -3 | -1 | 0.98 | 0.402 |
| eval80006_top_carry | distractor_paper | -5 | -2 | 0.99 | 0.455 |
| eval80006_top_carry | material_glossy_cup | -4 | -2 | 0.99 | 0.525 |
| eval80006_top_carry | noise_gauss | 0 | 0 | 1.0 | 0.116 |
| eval80006_top_carry | blur_gauss | 0 | 0 | 1.0 | 0.615 |
| eval80006_top_carry | blur_motion | 0 | 0 | 1.0 | 0.582 |
| eval80007_oblique_carry | light_dim | 1 | -1 | 0.99 | 0.768 |
| eval80007_oblique_carry | light_warm | -2 | -2 | 1.0 | 1.194 |
| eval80007_oblique_carry | light_harsh | 0 | 0 | 0.99 | 1.584 |
| eval80007_oblique_carry | bg_wood | 1 | -2 | 1.0 | 1.716 |
| eval80007_oblique_carry | bg_lab_bench | -3 | -1 | 1.0 | 1.959 |
| eval80007_oblique_carry | distractor_mug | 1 | -1 | 1.0 | 0.684 |
| eval80007_oblique_carry | distractor_paper | -3 | 0 | 1.0 | 0.46 |
| eval80007_oblique_carry | material_glossy_cup | 3 | -2 | 1.0 | 1.017 |
| eval80007_oblique_carry | noise_gauss | 0 | 0 | 1.0 | 0.198 |
| eval80007_oblique_carry | blur_gauss | 0 | 0 | 1.0 | 0.838 |
| eval80007_oblique_carry | blur_motion | 0 | 0 | 1.0 | 0.61 |

SAM: 558 calls (544 over the network), mean latency 0.9887 s.

Notes:
- Ground truth: visible-pixel masks from a segmentation render of the same simulator frame.
- Edited frames are resampled to the source size. 'corrected' also undoes one whole-frame similarity transform per edit (the editor's drift; listed in 'corrections'); 'uncorrected' does not.
- Condition A uses the Muse-to-SAM prompt chooser on each original frame and reuses its phrases for that frame's edits; condition B uses fixed colour-free phrases.
- iou and centroid error use SAM's top-confidence match; iou_best (chosen with the ground truth) is diagnostic only.
- Local sensor corruptions preserve geometry by construction; the geometry check is still run.
- Edits whose geometry changed stay in every table; their scores use a ground truth that no longer fits.

Refusals and errors:
- eval80000_top_carry / light_harsh: refused: MetaError: HTTP 400: {"error":{"code":"content_policy_violation","message":"The response was filtered due to the prompt triggering our content management policy.","param":"prompt","type":"invalid_request_error"}}
