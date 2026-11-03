# Perception: from video to a demonstration trace, and what it can claim

MIRROR's interpreter reasons over a demonstration trace: one gripper pose and
one pose per object per frame, plus grasp and release events. A trace can
come from the simulator (every value is the simulator's state) or from video
(every value was reconstructed from pixels, and some were never seen). This
document describes how video becomes a trace and how the trace records what
it actually observed.

## Evidence kinds

| `evidence.kind` | What it is | What may be assumed |
|---|---|---|
| `simulator_ground_truth` | State logged by the physics simulator (teleoperation, scripted, replay). | Everything is measured. |
| `rendered_video_reconstruction` | Video rendered by MIRROR's simulator, reconstructed from its pixels alone. Not footage of a person. | The rendered cameras' intrinsics, the kit table size and kit object sizes. |
| `camera_video_reconstruction` | Real camera footage, calibrated (docs/CAMERA_CAPTURE.md), identities confirmed by a person. | Only what was calibrated, measured and confirmed. |
| `unverified_video_reconstruction` | Video of unknown origin reconstructed as if rendered. | Nothing: an `abstain` review item blocks it until a person confirms the footage or calibrates. |
| `legacy_video_unannotated` | A video-derived trace written before evidence annotations existed (derived, never stored). | Tilt is not evidence; positions are at best inferred. |
| `interface_fixture` | Key-framed motion for interface development. | Not a physics simulation. |

A trace without `evidence` is treated as simulator ground truth only when its
`source` is a simulator source. A video trace without it is never treated as
ground truth.

## Observability of each value

`evidence.objects[id].position` and `.tilt` (and `evidence.gripper_position`)
are runs of frames with one status each:

- `measured`: observed directly. Positions: two views triangulated, or the
  top-view ray at a resting object's known height. Cup tilt: side-view
  silhouette fit (only the in-image component is observable).
- `inferred`: derived under a stated assumption. A carried object hidden
  under the gripper moves with it; one view at an assumed height; a resting
  object standing or lying, from its top-view outline (small tilts under
  about 15 degrees cannot be resolved, upside-down looks like upright, a cube
  looks the same on every face and is never inferred).
- `interpolated`: across a gap of at most 1 s (positions) or 0.6 s within one
  carry (tilt), between measured values.
- `unobserved`: not seen. The stored value is a placeholder for display (the
  nearest known position, an identity orientation) and must never be used as
  evidence.

Each run states its basis and, where known, a one-sigma uncertainty.

## What the summary does with it

`mirror.interpret.summarize_demo` reads the evidence:

- An unobserved tilt is reported as `None`, never as 0 degrees.
- "The cup stayed within N degrees of upright for the whole time it was
  carried" requires the tilt to be measured (or interpolated) on at least 80%
  of the carried frames; the statement then says on how many. A measured tilt
  above the tolerance is reported even with partial coverage.
- Positions held for display are not used for routes, clearances or path
  length; an unobserved final position does not count as ending in the tray.
- The summary's notes (which reach the model) state the evidence kind and
  every coverage gap; `summary.review` lists the review items; each object
  carries an `observability` block.
- For simulator traces the summary, and therefore every recorded model
  request, is byte-identical to earlier versions.

## Review and abstention

Reconstruction raises review items: `calibration_assumed` (abstain: intrinsics
and sizes taken from the rendered set-up without confirmation),
`object_not_found` (abstain), `identity_ambiguous`, `low_coverage`,
`tilt_unobserved`, `single_view`, `unknown_object`, `legacy_unannotated`.
`abstain` items must be resolved by a person before the trace supports a
specification; `review` items are shown with the proposal.

## Provenance of model outputs

`evidence.models` lists the models the masks depend on (grounding,
segmentation) with their origin: `fresh_model_call`, `recorded_replay`,
`offline_rules`, `oracle_ground_truth`. Masks read back from a saved tracks
file are a recording, whatever the original calls were.

## Pipeline

`mirror.perception.trace_from_video(top, side, footage=...)`:

1. Track (Muse Spark grounding boxes, SAM 3.1 box-prompted video tracking,
   re-seeding of lost tracks).
2. Calibrate: rendered footage from the table outline and the kit table size;
   camera footage from a calibration board (no table size assumed).
3. Estimate positions, carries, grasp and release, cup tilt, resting
   orientation, and per-frame observability.
4. Build the scene from the kit catalogue and the measured positions.
5. Build the trace with its evidence.

Grasp and release are timed at the gripper's lowest point around each carry
(its smallest top-view outline), `event_timing="gripper_lowest"`; the earlier
motion-onset timing remains available as `event_timing="motion"` and
reproduces the frozen video benchmark's numbers from its recorded tracks.

## Analysis

`mirror.perception.analysis` reconstructs recorded tracks with the current
code and compares events, carry-tilt support and offline-rule proposals with
the ground-truth trace, without model calls. Results and their status are in
results/perception_v2/.
