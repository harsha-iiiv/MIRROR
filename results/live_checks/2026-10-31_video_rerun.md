# Live re-run of the bundled reference video (31 October 2026)

A measurement-only check of the deployed live server (code 351ea19), not a pre-registered result.

The bundled reference demonstration (top and side views, 82 s) was processed live once: Muse Spark grounding (8 calls including re-seeds), SAM 3.1 tracking (4 requests), Muse Spark video observations and the Muse Spark proposal. The result was scored against the simulator's ground truth for that video with `mirror.perception.perception_metrics`.

| | Recorded run (published site) | Live re-run |
|---|---|---|
| objects found (share of visible frames) | 0.999 | 0.998 |
| mean position error | 0.88 cm | 1.90 cm |
| grasp/release events matched | 6 of 6 | 4 of 6 |
| mean event timing error | 0.44 s | 0.70 s |
| cup tilt error | 1.8° | not measured (no side-view tilt frames) |

Timings on the live server: grounding 56 s, SAM tracking 262 s, video observations 34 s, proposal 29 s; about 7.5 minutes end to end.

The difference comes from model grounding: the boxes Muse Spark returns vary between calls, so SAM starts from different seeds. The pre-registered video benchmark (`results/eval_video`) is the reference measurement.
