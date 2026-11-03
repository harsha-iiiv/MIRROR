# Spec agreement with clarifying questions (perception_v2b)

**Post-hoc development analysis on recorded tracks. The registered video2 results are unchanged. No model calls.**

Code: 4cd949f (perception a7b43b2 + offline proposer that asks about unobserved tilt, semantics 8a0627a). Interpreter: offline rules (deterministic) on the video-derived and the ground-truth trace.

Scripted answers: each clarifying question is answered from the ground-truth offline specification: 'required' if it has the same rule type, phase and object, otherwise 'incidental'. No person answered; this measures what correct answers would recover, not how people answer.

## Reading

- With the questions answered from the ground truth (scripted, no person), the held-out specification matches on 9/16 layouts: the same 9 layouts the old code matched, now without asserting unmeasured upright evidence. The old 9/16 rested on stated-but-unmeasured upright tilt; here those items are asked instead.
- Nothing matches without answers (0/16, 0/12): the video does not show the upright rules, so every layout needs questions (73 on 16 held-out layouts, 1 to 6 per layout; 63 on 12 development layouts).
- The 7 held-out layouts that still differ after answers have three causes, all perception-side: an object not shown ending in the tray (5 objects, so the object and its rules are left out of the task), a carry with no detected grasp/release (4 objects, so no transport question is asked; the offline proposer only asks when a carry interval exists), and one false tilt measurement (71007: cup measured at 30 degrees while carried, true tilt about 0).
- These numbers count question items, not people's effort: a real person may answer differently, and asking six questions per demonstration is a usability cost the pilot should measure.

## held-out: recorded video2 tracks 71000-71015 (post-hoc)

| | old code (v2 base) | perception_v2 (no questions) | v2b: equal, no answers | v2b: questions asked | v2b: equal after scripted answers |
|---|---|---|---|---|---|
| spec equal to ground truth | 9/16 | 0/16 | 0/16 | 73 in 16 seeds | 9/16 |

Residual causes after scripted answers (count of object-level causes): false tilt measurement while carried: 1; no carry interval detected: 4; object not shown ending in the tray: 5

| seed | old code equal | v2 equal | equal without answers | questions | equal after scripted answers | still missing after answers | why (from the video trace) | extra after answers |
|---|---|---|---|---|---|---|---|---|
| 71000 | yes | no | no | 6 | yes | - | - | - |
| 71001 | no | no | no | 5 | no | upright transport: marker {"max_tilt_deg": 15.0} | marker: no carry interval detected (grasp/release missed), so no transport question was asked | - |
| 71002 | no | no | no | 4 | no | goal inside: block; upright final: block {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0} | block: not shown ending in the tray by the video, so it is left out of the task with its rules | - |
| 71003 | yes | no | no | 6 | yes | - | - | - |
| 71004 | yes | no | no | 5 | yes | - | - | - |
| 71005 | yes | no | no | 6 | yes | - | - | - |
| 71006 | yes | no | no | 5 | yes | - | - | - |
| 71007 | no | no | no | 4 | no | upright transport: cup {"max_tilt_deg": 15.0} | cup: tilt measured at 30 deg while carried (truth about 0): false tilt measurement | - |
| 71008 | yes | no | no | 6 | yes | - | - | - |
| 71009 | no | no | no | 1 | no | goal inside: block; goal inside: cup; upright final: block {"max_tilt_deg": 15.0}; upright final: cup {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0} | block: not shown ending in the tray by the video, so it is left out of the task with its rules; cup: not shown ending in the tray by the video, so it is left out of the task with its rules | - |
| 71010 | no | no | no | 2 | no | goal inside: cup; upright final: cup {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0}; upright transport: cup {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | block: no carry interval detected (grasp/release missed), so no transport question was asked; cup: not shown ending in the tray by the video, so it is left out of the task with its rules; marker: no carry interval detected (grasp/release missed), so no transport question was asked | - |
| 71011 | no | no | no | 4 | no | goal inside: block; upright final: block {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0} | block: not shown ending in the tray by the video, so it is left out of the task with its rules | - |
| 71012 | yes | no | no | 5 | yes | - | - | - |
| 71013 | no | no | no | 4 | no | upright transport: marker {"max_tilt_deg": 15.0} | marker: no carry interval detected (grasp/release missed), so no transport question was asked | - |
| 71014 | yes | no | no | 5 | yes | - | - | - |
| 71015 | yes | no | no | 5 | yes | - | - | - |

## development: attempt-1 tracks 70000-70011

| | old code (v2 base) | perception_v2 (no questions) | v2b: equal, no answers | v2b: questions asked | v2b: equal after scripted answers |
|---|---|---|---|---|---|
| spec equal to ground truth | n/a | 0/12 | 0/12 | 63 in 12 seeds | 9/12 |

Residual causes after scripted answers (count of object-level causes): no carry interval detected: 1; object not shown ending in the tray: 2

| seed | old code equal | v2 equal | equal without answers | questions | equal after scripted answers | still missing after answers | why (from the video trace) | extra after answers |
|---|---|---|---|---|---|---|---|---|
| 70000 | - | no | no | 4 | no | goal inside: block; upright final: block {"max_tilt_deg": 15.0}; upright transport: block {"max_tilt_deg": 15.0} | block: not shown ending in the tray by the video, so it is left out of the task with its rules | - |
| 70001 | - | no | no | 6 | yes | - | - | - |
| 70002 | - | no | no | 6 | yes | - | - | - |
| 70003 | - | no | no | 6 | yes | - | - | - |
| 70004 | - | no | no | 6 | yes | - | - | - |
| 70005 | - | no | no | 5 | yes | - | - | - |
| 70006 | - | no | no | 6 | yes | - | - | - |
| 70007 | - | no | no | 4 | no | goal inside: marker; upright final: marker {"max_tilt_deg": 15.0}; upright transport: marker {"max_tilt_deg": 15.0} | marker: not shown ending in the tray by the video, so it is left out of the task with its rules | - |
| 70008 | - | no | no | 6 | yes | - | - | - |
| 70009 | - | no | no | 4 | no | upright transport: marker {"max_tilt_deg": 15.0} | marker: no carry interval detected (grasp/release missed), so no transport question was asked | - |
| 70010 | - | no | no | 6 | yes | - | - | - |
| 70011 | - | no | no | 4 | yes | - | - | - |

