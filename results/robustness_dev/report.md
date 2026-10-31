# Perception robustness: SAM 3.1 on Muse Image counterfactuals

Split: dev. Frames: 3. Instructions: 11 (sha256 fd517bf09b4879f3). SAM prompts: cup = "white box", block = "blue block", marker = "orange marker".

| family | rows | found | IoU | ΔIoU vs original | centroid err (px) | IoU, geometry kept | geometry verdicts |
|---|---|---|---|---|---|---|---|
| original | 9 | 1.0 | 0.9651 | None | 0.2011 | 0.9651 | original 9 |
| lighting | 27 | 0.6296 | 0.5802 | -0.3849 | 1.0218 | 0.6222 | kept 21, moved 1, untested 3, weakened 2 |
| background | 18 | 0.8889 | 0.789 | -0.1761 | 2.545 | 0.7972 | kept 15, missing 1, untested 2 |
| distractor | 18 | 1.0 | 0.9333 | -0.0318 | 0.8422 | 0.9383 | kept 15, moved 1, untested 2 |
| material | 9 | 0.6667 | 0.616 | -0.3491 | 1.0517 | 0.78 | kept 6, resized 1, untested 1, weakened 1 |
| sensor | 27 | 0.8889 | 0.8461 | -0.119 | 0.445 | 0.8461 | preserved 27 |

Model edits: 24 (ok 24, refused 0, errors 0); geometry changed in 4, reframed in 0. Latency per edit (as recorded): mean 7.8047 s (min 5.35, max 12.69). Output sizes: [(2048, 1152)].

SAM: 108 calls (0 over the network), mean latency 1.159 s.

Notes:
- Ground truth: visible-pixel masks from a segmentation render of the same simulator frame.
- Edited frames are resampled to the source size before SAM and before scoring.
- iou and centroid error use SAM's top-confidence match; iou_best (chosen with the ground truth) is diagnostic only.
- Local sensor corruptions preserve geometry by construction; the geometry check is still run and reported.
- Edits whose geometry changed stay in every table; their scores use a ground truth that no longer fits.
