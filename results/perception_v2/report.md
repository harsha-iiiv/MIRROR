# Perception v2: post-hoc development analysis of video reconstruction (not a pre-registered evaluation)

No model calls. Recorded tracks were reconstructed again with the base code and with the new code; every number below is computed against simulator ground truth. The interpreter is the offline rules on both sides, so the specification comparison measures what the reconstruction gives the interpreter, not Muse Spark's behaviour.

Status: post-hoc development analysis. The held-out seeds belong to the second video registration, whose verdicts (results/eval_video2) are unchanged and remain the registered result. Parameters were chosen on the development seeds before the held-out run.

| run | seeds | events within 1 s (F1, median) | grasp bias s (median) | release bias s (median) | cup carry frames | tilt measured | presented upright without a measurement | presented upright while truly tilted >15° | offline spec = ground-truth offline spec | corrections a person must make | seeds with an abstain item (other than calibration_assumed) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| new code, dev seeds, both cameras | 12/12 | 0.83 | 0.15 | -0.03 | 889 | 237 | 0 | 0 | 0 | 70 | 0 |
| base code, held-out seeds, both cameras | 16/16 | 0.60 | 0.59 | 0.89 | 1213 | 304 | 909 | 0 | 9 | 20 | 0 |
| new code, held-out seeds, both cameras | 16/16 | 0.86 | 0.16 | -0.04 | 1213 | 382 | 0 | 0 | 0 | 93 | 1 |

Columns: *presented upright without a measurement* counts carried frames whose trace orientation is upright and that the trace offers as data (base code: every frame; new code: frames not marked unobserved) although the side view gave no tilt measurement. *Corrections a person must make*: constraints and goals in the ground-truth offline proposal that are missing from the video one, plus those in the video one that are not in the ground-truth one.

## new code, dev seeds, both cameras

| seed | found | F1 1 s | carry frames | tilt measured | unsupported upright | spec equal | missing | extra | review |
|---|---|---|---|---|---|---|---|---|---|
| 70000 | 0.90 | 0.67 | 62 | 24 | 0 | no | goal inside: block; upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright final: marker {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, tilt_unobserved |
| 70001 | 0.96 | 0.83 | 75 | 28 | 0 | no | upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright final: marker {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, tilt_unobserved |
| 70002 | 0.98 | 0.83 | 77 | 34 | 0 | no | upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright final: marker {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, tilt_unobserved |
| 70003 | 0.94 | 0.83 | 79 | 13 | 0 | no | upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright final: marker {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, tilt_unobserved |
| 70004 | 0.92 | 1.00 | 64 | 0 | 0 | no | upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright final: marker {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, single_view, tilt_unobserved |
| 70005 | 0.98 | 1.00 | 84 | 22 | 0 | no | upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, tilt_unobserved |
| 70006 | 0.97 | 0.86 | 76 | 32 | 0 | no | upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright final: marker {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, tilt_unobserved |
| 70007 | 0.94 | 0.83 | 77 | 20 | 0 | no | goal inside: marker; upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright final: marker {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, tilt_unobserved |
| 70008 | 0.94 | 0.71 | 77 | 0 | 0 | no | upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright final: marker {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, single_view, tilt_unobserved |
| 70009 | 0.86 | 0.60 | 72 | 0 | 0 | no | upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, single_view, tilt_unobserved |
| 70010 | 0.95 | 0.33 | 73 | 19 | 0 | no | upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright final: marker {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, tilt_unobserved |
| 70011 | 0.96 | 0.83 | 73 | 45 | 0 | no | upright final: block {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, tilt_unobserved |

## base code, held-out seeds, both cameras

| seed | found | F1 1 s | carry frames | tilt measured | unsupported upright | spec equal | missing | extra | review |
|---|---|---|---|---|---|---|---|---|---|
| 71000 | 0.98 | 0.86 | 68 | 18 | 50 | yes | - | - | - |
| 71001 | 0.93 | 0.50 | 81 | 31 | 50 | no | upright transport: marker {"max_tilt_deg": 15.0} | - | - |
| 71002 | 0.70 | 0.09 | 76 | 0 | 76 | no | goal inside: block; upright final: block {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0} | - | - |
| 71003 | 0.96 | 0.50 | 73 | 39 | 34 | yes | - | - | - |
| 71004 | 0.93 | 0.33 | 78 | 0 | 78 | yes | - | - | - |
| 71005 | 0.97 | 0.83 | 67 | 27 | 40 | yes | - | - | - |
| 71006 | 0.99 | 0.83 | 84 | 21 | 63 | yes | - | - | - |
| 71007 | 0.98 | 0.62 | 80 | 17 | 63 | no | upright transport: cup {"max_tilt_deg": 15.0} | - | - |
| 71008 | 0.96 | 0.57 | 82 | 27 | 55 | yes | - | - | - |
| 71009 | 0.98 | 0.57 | 82 | 25 | 57 | no | goal inside: block; goal inside: cup; upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0} | - | - |
| 71010 | 0.00 | 0.00 | 76 | 0 | 76 | no | goal inside: cup; upright final: cup {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | - |
| 71011 | 0.98 | 0.71 | 68 | 30 | 38 | no | goal inside: block; upright final: block {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0} | - | - |
| 71012 | 0.99 | 0.83 | 78 | 13 | 65 | yes | - | - | - |
| 71013 | 0.84 | 0.40 | 68 | 0 | 68 | no | upright transport: marker {"max_tilt_deg": 15.0} | - | - |
| 71014 | 0.99 | 0.83 | 76 | 36 | 40 | yes | - | - | - |
| 71015 | 0.99 | 0.71 | 76 | 20 | 56 | yes | - | - | - |

## new code, held-out seeds, both cameras

| seed | found | F1 1 s | carry frames | tilt measured | unsupported upright | spec equal | missing | extra | review |
|---|---|---|---|---|---|---|---|---|---|
| 71000 | 0.98 | 0.86 | 68 | 23 | 0 | no | upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright final: marker {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, tilt_unobserved |
| 71001 | 0.93 | 0.67 | 81 | 36 | 0 | no | upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright final: marker {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, tilt_unobserved |
| 71002 | 0.70 | 0.09 | 76 | 0 | 0 | no | goal inside: block; upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright final: marker {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, single_view, tilt_unobserved |
| 71003 | 0.96 | 0.75 | 73 | 48 | 0 | no | upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright final: marker {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, tilt_unobserved |
| 71004 | 0.99 | 1.00 | 78 | 22 | 0 | no | upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, tilt_unobserved |
| 71005 | 0.97 | 1.00 | 67 | 28 | 0 | no | upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright final: marker {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, tilt_unobserved |
| 71006 | 0.99 | 1.00 | 84 | 22 | 0 | no | upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, tilt_unobserved |
| 71007 | 0.98 | 0.75 | 80 | 28 | 0 | no | upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, tilt_unobserved |
| 71008 | 0.96 | 0.86 | 82 | 28 | 0 | no | upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright final: marker {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, tilt_unobserved |
| 71009 | 0.98 | 0.86 | 82 | 36 | 0 | no | goal inside: block; goal inside: cup; upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, tilt_unobserved |
| 71010 | 0.00 | 0.00 | 76 | 6 | 0 | no | goal inside: cup; upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright final: marker {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, calibration_invalid, scale_mismatch |
| 71011 | 0.98 | 0.86 | 68 | 30 | 0 | no | goal inside: block; upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright final: marker {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, tilt_unobserved |
| 71012 | 0.99 | 1.00 | 78 | 18 | 0 | no | upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, tilt_unobserved |
| 71013 | 0.84 | 0.60 | 68 | 0 | 0 | no | upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, single_view, tilt_unobserved |
| 71014 | 0.99 | 1.00 | 76 | 36 | 0 | no | upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, tilt_unobserved |
| 71015 | 0.99 | 0.86 | 76 | 21 | 0 | no | upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | - | calibration_assumed, tilt_unobserved |

