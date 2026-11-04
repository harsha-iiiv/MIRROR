# Twin: the robot on your table, seen from your phone

`mirror.twin` draws a simulated MIRROR run inside the person's own footage. It is display only:
it makes no measurement, calls no model and uses no network.

```
python scripts/twin_render.py --pipeline OUT_DIR --only main      # after mirror.capture.real_run
python scripts/twin_render.py --pipeline OUT_DIR --only before_after   # when OUT has a correction
python scripts/twin_render.py --pipeline OUT_DIR --arm            # same task on the UR10e arm backend -> OUT/twin_arm
python scripts/twin_render.py --session S.json --video TAKE.MOV --tracks TRACKS.json.gz \
    --rollout before=R.json [--rollout after=R2.json --correction-json C.json] \
    [--placement P.json] --label "Development clip D1, never scored" --out DIR
```

## Outputs

| file | what it shows |
|---|---|
| `overlay.mp4` (540x960) | your table, emptied, with the simulated gripper and proxies doing the task, from your phone's own viewpoint |
| `side_by_side.mp4` (780x720) | your hands / the robot on your table, synchronised on the pick and place events |
| `twin_3panel.mp4` (1280x720) | your hands / the robot on your table / a top-down "table twin" |
| `before_after.mp4` (1280x720) | two rollouts (before and after a correction) on the same table, with the re-check of the old run |
| `stills/`, `plate.png`, `topdown_plate.png` | key frames, the clean plate, the rectified table |
| `twin.json` | inputs, labels, placement, plate choice, registration checks, time map, timings |

## How

1. **Camera.** The capture's intrinsics and board-at-rest table pose give the phone in the table
   frame; the scene placement (`table_to_sim.json`, or a fit of the rollout's first frame to the
   capture's start positions) moves it into the simulator. MuJoCo renders a centred pinhole wide
   enough for every ray of the phone image; `cv2.remap` resamples it through the real `K` and lens
   distortion, so a rendered point lands on the pixel the phone would have seen
   (`tests/twin/test_twin_render.py` checks centroids within 1 px).
2. **Clean plate.** The phone does not move. The frame before the first touch and a frame after the
   last placement are chosen automatically (best agreement outside the objects: no hands, no
   calibration sheets); each moved object's SAM region in the start frame is replaced by the end
   frame, exposure-matched and feathered.
3. **Composite.** Only the gripper and proxies are drawn. Table, tray and walls are holdout: where
   the simulated wall is in front, the real divider shows. Inside the real divider's SAM mask, a
   simulated part on the far side of the wall's plane is hidden. The robot's shadow darkens the plate
   by the ratio of the shaded holdout surface to an empty-scene render (one overhead light).
4. **Colours.** Each proxy takes the dominant colour of its real object in the start frame.
5. **Time.** The simulated run plays at a fixed speed (shown on screen); your video is re-timed
   between matching events (robot grasp = your pick, robot release = your place) and its rate is
   shown on screen. Anchors whose order disagrees are dropped.
6. **Table twin.** A plane homography of the undistorted plate onto a virtual top-down camera.

## D1 results (development clip, never scored)

Storage `mirror/capture/D1/twin/` (README there). From the pipeline's replayed D1 run with the tray placed from the
footage: simulated tray vs real tray 0.1 cm / 0.2 deg (it was 3.3 cm / 3.6 deg when the tray was assumed aligned with
the board; the twin's registration check found it). Arm backend on the same scene: the checker flags one cup tilt of
16.2 deg (rule 15) for 1.3 s, shown in the film.

## Limits (say them on screen, they are in the caption)

* One camera and whatever calibration labels the session carries (D1: calibration reuse flagged).
  Registration is reported in image space (`twin.json` → `registration`): D1 sim tray vs SAM tray
  IoU 0.82; object bases 9-11 px at 540x960. Not a 3-D accuracy figure.
* Proxies are simulator stand-ins sized from the person's measurements, not their objects
  (D1: a 4 cm cube for an 11 cm carton, an upright marker for a lying one).
* The robot is a simulated free-floating gripper (or, with `--arm`, a UR10e on a simulated ceiling mount drawn with its
  collision shapes). It never touched the table.
* The top-down view is exact only on the table plane; anything taller is smeared.
* The occluder test is per geom (centre side of the wall plane), not per pixel depth.
