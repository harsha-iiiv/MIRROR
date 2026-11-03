# Filming a demonstration with real cameras

MIRROR can reconstruct a demonstration from real camera footage of the
supported kit (cup with beads, block, marker, tray, optional wall). It does
not assume anything about your cameras, your table or your objects: you
calibrate the cameras, measure where the calibration board sits, and confirm
which tracked object is which. Until all of that is done, the footage is
refused rather than guessed.

Status: the workflow is implemented and tested on synthetic data only
(projected board corners, drawn board images, and simulator segmentation
masks standing in for tracked footage). No real footage has been recorded
or reconstructed yet.

## What you need

- The kit objects. Their sizes must match the kit catalogue within 15%
  (cup 6 x 6 x 10 cm, block 4 cm cube, marker 2.4 cm diameter x 12 cm, tray
  25.6 x 19.6 cm outside, 3 cm walls). Other objects are not supported.
- One camera looking down at the table (`top`, required) and optionally one
  at a shallow angle from the side (`side`). Without the side camera the cup's
  tilt cannot be measured, and MIRROR says so instead of assuming it stayed
  upright.
- Fixed exposure and focus if the camera allows it; no zoom changes.
- A printed checkerboard on a flat, rigid card. Default pattern 10 x 7
  squares (9 x 6 inner corners). Print at 100% and measure one square with a
  ruler; enter that value, not the nominal one.

## 1. Intrinsics (once per camera and zoom setting)

Film or photograph the board held at 12 or more different positions and
angles, filling the corners of the image as well as the centre. Each view
must show the whole board. MIRROR detects the inner corners (needs the
`camera` extra: `pip install 'mirror[camera]'`, or click the four outermost
inner corners when detection fails) and solves the intrinsics with two radial
distortion terms.

Accepted only when: at least 6 views, reprojection error under 1 px, and the
views together cover at least a quarter of the image. If the lens distortion
is not negligible, the videos are undistorted with these intrinsics before
tracking.

If you know the camera maker's intrinsics for this exact mode, you may enter
them instead; they are recorded as `user_supplied`.

## 2. Camera poses (every time a camera moves)

Lay the board flat on the table where the demonstration will happen, film a
few seconds with every camera without moving anything, then remove it.
Accepted only when the board's reprojection error is under 1.5 px.

Measure where the board's first inner corner is in the robot workspace (the
centre of the table area the robot can reach, x to the right as seen from the
operator, y away from the operator) and the angle of the board's x axis.
Enter these values; the table plane is the board plane. The table's size is
never assumed.

## 3. Recording the demonstration

- Start with every object resting and visible to the top camera.
- Move one object at a time; set it down fully before the next one.
- Keep your hand from covering the object more than necessary; the cup's tilt
  is measured only while the side camera sees it.
- End with every object resting for at least two seconds.

## 4. Correspondence review

Tracking proposes labels; you confirm them. For every track, choose cup,
block, marker, tray, wall or ignore. Tracks you do not confirm are not used.
Every confirmation is stored with the trace as a manual correction.

## What you get back

A demonstration trace whose evidence says, frame by frame, which positions and
tilts were measured, inferred, interpolated or not observed, and which review
items remain. Values that were not observed are placeholders for display and
never count as evidence: for example, if the cup is hidden from the side
camera while carried, MIRROR does not report that it stayed upright and asks
instead.
