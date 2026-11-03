# Video benchmark re-analysis (derived, offline)

Rebuilt from the stored sam-3.1 masks with the registration's own reconstruction; each seed's rebuilt row was checked against the stored row first. No model call. Thresholds of the registration are not re-applied here: these are descriptive, unconditional views of the same estimates.

Miss penalty in the imputed view: hold_last (error of the object's last detection; table centre if none yet). The registration's 5 cm gate is reported, never applied, in the unconditional views.

Variants: the colour-threshold baseline and the simulator-mask oracle replace only the cup, block and marker masks; camera calibration still comes from the sam-3.1 table and tray masks, so a calibration failure affects all three variants alike.

Tilt observability: cup tilt is estimated as the in-image component in the side view only; the component along the side camera's viewing direction is unobservable, and frames without a side-view cup silhouette have no tilt estimate (marked unobservable, never scored as zero).

## eval_video2: registration 2 (reported, 16 of 20 seeds run)

Seeds: 16 (71000, 71001, 71002, 71003, 71004, 71005, 71006, 71007, 71008, 71009, 71010, 71011, 71012, 71013, 71014, 71015).

Rebuilt rows equal to the stored rows: 16 of 16 seeds.

### Position (table plane), every visible object-frame

| estimate | object-frames | within 5 cm | outliers (>5 cm) | identity errors | misses | median, hits only (registered view) | median, all estimates | p90, all estimates | max | median, misses as infinite | p90, misses as infinite | p90, misses held at last detection |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| sam-3.1 masks (registered) | 20960 | 18608 (0.888) | 2013 | 281 | 58 | 0.77 cm | 0.99 cm | 6.43 cm | 478.62 cm | 0.99 cm | 7.26 cm | 6.83 cm |
| colour threshold (classical baseline) | 20960 | 12584 (0.600) | 8321 | 6 | 49 | 0.54 cm | 1.20 cm | 42.99 cm | 128.81 cm | 1.20 cm | 43.09 cm | 43.01 cm |
| simulator masks (oracle, upper bound) | 20960 | 19324 (0.922) | 1595 | 36 | 5 | 0.44 cm | 0.54 cm | 3.58 cm | 108050.09 cm | 0.54 cm | 3.58 cm | 3.58 cm |

Static baseline (every object kept at its first sam-3.1 detection): median 42.16 cm, p90 59.37 cm.

By object kind (sam-3.1): cup: 6497/6993 within 5 cm, 496 outliers, 0 identity, 0 misses, p90 all estimates 3.56 cm; block: 5925/6974 within 5 cm, 982 outliers, 57 identity, 10 misses, p90 all estimates 18.90 cm; marker: 6186/6993 within 5 cm, 535 outliers, 224 identity, 48 misses, p90 all estimates 5.86 cm.

While an object is held (sam-3.1): 2711/3589 within 5 cm, median all estimates 2.49 cm, p90 26.42 cm.

Whole-scene failure: seed(s) 71010 (no sam-3.1 estimate within 5 cm in any frame; reconstruction notes in the frame records; 71010: side: tray mask inconsistent with the top view, not used). Without them, for reference only: sam 18608/19691 within 5 cm, p90 all estimates 3.36 cm; colour 12584/19691 within 5 cm, p90 all estimates 39.73 cm; oracle 19324/19691 within 5 cm, p90 all estimates 2.32 cm.

Registration-style aggregate (median of per-object medians over hits within 5 cm): sam 0.722 cm, colour 0.5439 cm, oracle 0.3637 cm.

### Cup tilt

- Largest true cup tilt in any frame of any seed: 0.291 deg.
- Registered measure (median over seeds of per-seed median |estimate - truth|, on frames found within 5 cm with a tilt estimate): 1.995 deg, from 610 scored frames in 12 seeds.
- Constant-zero estimator on exactly those frames: 0.0097 deg.
- Side-view sampled frames with the cup visible: 6660; with a tilt estimate: 610. The rest are unobservable for tilt, not zero.
- The true tilt never exceeds 0.291 deg, so this benchmark cannot measure tilt estimation: a constant-zero estimator beats the registered estimate, and no claim about tilt accuracy follows from it.

## eval_video_attempt_1: registration 1, attempt 1 (failed run; tracking was valid but never summarised)

Seeds: 12 (70000, 70001, 70002, 70003, 70004, 70005, 70006, 70007, 70008, 70009, 70010, 70011).

### Position (table plane), every visible object-frame

| estimate | object-frames | within 5 cm | outliers (>5 cm) | identity errors | misses | median, hits only (registered view) | median, all estimates | p90, all estimates | max | median, misses as infinite | p90, misses as infinite | p90, misses held at last detection |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| sam-3.1 masks (registered) | 15923 | 15003 (0.942) | 763 | 93 | 64 | 0.87 cm | 0.94 cm | 3.44 cm | 165.46 cm | 0.95 cm | 3.54 cm | 3.54 cm |
| colour threshold (classical baseline) | 15923 | 10152 (0.638) | 5722 | 2 | 47 | 0.60 cm | 1.15 cm | 37.56 cm | 143.61 cm | 1.16 cm | 37.61 cm | 37.57 cm |
| simulator masks (oracle, upper bound) | 15923 | 15451 (0.970) | 406 | 41 | 25 | 0.47 cm | 0.48 cm | 2.37 cm | 139.97 cm | 0.48 cm | 2.39 cm | 2.39 cm |

Static baseline (every object kept at its first sam-3.1 detection): median 37.75 cm, p90 57.36 cm.

By object kind (sam-3.1): cup: 5199/5311 within 5 cm, 112 outliers, 0 identity, 0 misses, p90 all estimates 2.53 cm; block: 5052/5301 within 5 cm, 210 outliers, 2 identity, 37 misses, p90 all estimates 2.21 cm; marker: 4752/5311 within 5 cm, 441 outliers, 91 identity, 27 misses, p90 all estimates 5.02 cm.

While an object is held (sam-3.1): 2023/2728 within 5 cm, median all estimates 2.49 cm, p90 10.98 cm.

Registration-style aggregate (median of per-object medians over hits within 5 cm): sam 0.5993 cm, colour 0.5678 cm, oracle 0.4007 cm.

### Cup tilt

- Largest true cup tilt in any frame of any seed: 0.26 deg.
- Registered measure (median over seeds of per-seed median |estimate - truth|, on frames found within 5 cm with a tilt estimate): 1.993 deg, from 558 scored frames in 9 seeds.
- Constant-zero estimator on exactly those frames: 0.009 deg.
- Side-view sampled frames with the cup visible: 4883; with a tilt estimate: 558. The rest are unobservable for tilt, not zero.
- The true tilt never exceeds 0.26 deg, so this benchmark cannot measure tilt estimation: a constant-zero estimator beats the registered estimate, and no claim about tilt accuracy follows from it.

