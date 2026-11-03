# Video benchmark v3 (design, not run)

Status: freeze-ready design awaiting budget approval. Nothing has been run on
its evaluation seeds. The machine-readable design is
`mirror/perception/videobench/design_v3.json`; the harness, metrics,
baselines and tilted-demonstration generator are in
`mirror/perception/videobench/`.

## Why a new benchmark

- Every rendered clip of the earlier video registrations carried the cup
  upright (true tilt at most about 0.3 degrees). A constant-zero tilt
  estimator would have beaten the reported 2-degree tilt error, so that number
  says nothing about measuring tilt.
- The earlier position error was computed only for estimates within 5 cm of
  the truth: conditional, not overall accuracy.
- Earlier video summaries stated unobserved tilt as a measured 0 degrees. The
  perception code no longer does (docs/PERCEPTION.md); this benchmark tests it.

## Demonstrations

24 new layouts (eval seeds 72000-72023, never sampled before). The reference
operator carries the cup and, halfway, tilts it by 0, 10, 20, 35, 50 or 60
degrees (four layouts each, alternating roll and pitch) for four seconds before
returning upright. The true tilt is what the physics produced, read from the
simulator. Rendered top and side views, 960 x 540 at 15 fps, tracked at 5 fps.

Each demonstration is scored in two conditions from the same tracks:
`two_view` and `top_only` (side camera dropped; tilt cannot be seen, and the
correct answer is to abstain).

## Estimators and baselines

| Quantity | MIRROR | Baselines |
|---|---|---|
| Position | Muse Spark grounding + SAM 3.1 + reconstruction | objects never move (first estimate held); classical colour-threshold tracker from the same first-frame boxes |
| Cup tilt | side-view silhouette fit, abstains where unobserved | constant zero; principal axis of the side-view mask |

## Measures (all unconditional)

- Position: over every visible ground-truth object-frame: missing rate,
  outliers (> 5 cm), identity errors, the full error distribution, and the
  share within 1, 2 and 5 cm of all visible frames.
- Tilt: over every true carry frame: coverage, error over answered frames,
  share within 5 degrees of all carry frames (an abstention is not within),
  false-upright frames, abstentions on tilted frames; per demo, the
  carried-upright decision against the truth.
- Specification: proposals from the ground-truth trace and from each video
  trace, by the offline rules once and by Muse Spark three times per input
  (separate recordings per repeat). Differences among repeats of the same input
  are model variability; a video input whose most common proposal differs from
  the ground truth's most common proposal is a perception-induced difference.

## Rehearsal (development seeds, ground-truth masks)

Two development demonstrations (35 degrees roll, 60 degrees pitch, two-second
hold) were rendered locally and scored with simulator masks, without model
calls. Findings that changed the design or the code before freezing:

- With a two-second hold, constant-zero tilt was within 5 degrees on 85% and
  81% of all carry frames, because the cup was tilted for a minority of the
  carry. The hold is now four seconds, and the tilt check is scored on frames
  that are truly tilted.
- One demo lost its side camera to the table outline's mirror symmetry, and one
  raised a false scale warning; both fixed in the perception code.
- Where MIRROR answered, its tilt error was small (median 1.9 degrees; 60
  measured against 59.8 true), with no false-upright frames, but it answered on
  few carry frames. That is reported, not tuned.

## Pre-stated checks

H1 to H6 in the design file; each is reported met or not met, and no threshold
changes after freezing.

## Cost of running it (needs approval)

Up to 240 Muse Spark grounding calls, 192 SAM 3.1 calls and 216 Muse Spark
proposal calls: about 14 USD expected, capped at 20 USD with a stop rule; plus
rendering, about one L4 GPU-hour (or six hours of local CPU).
