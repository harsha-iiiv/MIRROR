# Validating the phone-capture path on real footage

Status: **awaiting footage.** No real phone footage has been recorded or
processed. Everything in `mirror/capture` is tested on synthetic renders only,
and those tests are not evidence about real footage.

The machine-readable design is `mirror/capture/validation_design_v1.json`
(version 1). It was written before any real footage existed; its SHA-256 is
stored in every score report. Changing a label definition, threshold or metric
after seeing evaluation results means a new design version; earlier results are
kept.

## Clips

| Id | Split | Condition | Expected outcome |
|---|---|---|---|
| D1 | development | two phones, upright carry | used to build and debug; never scored as evidence |
| E1 | evaluation | two phones, upright carry | reconstruct |
| E2 | evaluation | two phones, mug tilted about 30 degrees during the carry (tag on the mug) | reconstruct; tilt measured |
| E3 | evaluation | two phones, hand covers the mug from above for most of the carry | reconstruct with stated uncertainty |
| E4 | evaluation | two phones, one dim lamp | reconstruct or abstain |
| E5 | evaluation | invalid calibration: zoom changed or top phone moved after the board view | **reject** |
| E6 | evaluation | top phone only | reduced single-view reconstruction |
| E7a, E7b | evaluation | two repeats of E1 by the same person | proposals identical to E1 (test-retest) |

Evaluation clips are recorded after the design is committed, following
`docs/CAMERA_CAPTURE.md`, and are not used for development. If evaluation
clips are later used to fix the pipeline, they become development clips and
new evaluation clips are recorded.

## Labels (made by a person before looking at MIRROR's output)

- **Rest positions**: start and end position of each object's centre, measured
  with a ruler in the board frame. Independent of the video.
- **Keyframes**: object centres clicked in each view every 0.5 s and
  triangulated with the session calibration. This is a manual reference that
  shares the calibration; it is not independent physical truth.
- **Events**: the first frame each object leaves its rest and the first frame it
  is at rest again, found by stepping through frames.
- **Tilt reference**: the full tilt from the printed tag on the mug (an
  instrumented reference). The estimator under test is run without the tag.
- **Identity**: the object's name at each keyframe.
- **Intended specification**: the task card filled in before recording (which
  objects go into the tray, which must stay upright, any order).

## Metrics and thresholds (fixed in the design)

| Metric | Threshold |
|---|---|
| Lens reprojection error | at most 1.0 px |
| Table pose reprojection error | at most 1.5 px |
| Board corners triangulated from two views vs printed geometry | at most 4 mm |
| Rest position error (ruler reference) | median at most 1.5 cm, 90th percentile at most 3 cm |
| Keyframe coverage (any estimate) | at least 90% |
| Keyframes measured (not inferred or interpolated) | at least 70% |
| Outliers (error above 5 cm) | at most 5% |
| Identity errors | 0 |
| Tilt error vs tag reference (full tilt) | median at most 5 degrees |
| Tilt lower bound (side silhouette) valid | at least 95% within 3 degrees of the reference |
| Event time within 0.3 s of the label | at least 80% of events |
| Event bracket contains the label | at least 90% of events |
| End-to-end specification | equal to the intended specification (goals and constraints) |
| Test-retest | E7a and E7b proposals identical to E1 |
| Invalid calibration (E5) | rejected; a reconstruction from E5 counts as a failure |

Reporting rules:

- **Unconditional errors.** Position errors are reported over all labelled
  keyframes. A missing estimate is counted as missing and, in the
  unconditional percentiles, as larger than every threshold. Errors over found
  estimates only are shown next to them and labelled conditional.
- **Tilt.** Tilt that was not observed is an abstention, never "upright". The
  **constant-zero** baseline (always 0 degrees) is reported on every clip and on
  frames whose reference tilt is at least 5 degrees; an estimator that does not
  beat it on E2 has shown nothing.
- **Correction effort.** Number of corrections, identity confirmations and the
  seconds spent in review, per clip.
- **Failures.** Every clip is reported, including abstentions, crashes and
  rejected calibrations.

## Baselines

- Tilt: constant zero.
- Tracking: person-drawn boxes with classical colour tracking (default offline
  path); manual keyframes only (the cost is the person's time); SAM 3.1 video
  tracks from Muse Spark boxes (needs a budget; not run).
- Views: E1's top clip alone (single view).

## Running it

```
python -m mirror.capture.ingest run   E1              # per clip, after review
python -m mirror.capture.ingest score E1 labels_E1.json [--spec proposed_spec.json]
```

`mirror.capture.validation.aggregate` combines clip reports and keeps
synthetic rehearsal rows apart from evaluation rows. A single successful clip
is a labelled demonstration, not a validated perception system.

## What the old benchmarks do not show

The rendered-video benchmark's conditional 0.72 cm position error and its
nearly-all-upright tilt numbers are about simulator-rendered video; they are
not evidence of phone-video performance and are not reused here.
