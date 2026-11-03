# Filming a demonstration with phones

This is how to record the supported kitting task with one or two phones so
MIRROR can rebuild it with calibrated cameras. It is written for the person
holding the phones. Nothing here assumes the simulator's cameras, table or kit:
the cameras are calibrated from a printed target, and every real object is
mapped to a simulation proxy by you.

What it is good for: a person moving a mug, a small box and a pen into a tray
around a standing book, filmed from above and from the side. What it is not:
arbitrary activities, measured physics, or a robot. A person's hand is not a
robot gripper; MIRROR infers when each object was picked up and put down from
the object's own motion and says so.

## What you need

| Item | Notes |
|---|---|
| Mug with dry rice or plastic beads | Not hot drinks. The simulator replaces the contents with simulated beads; real spills are not measured. |
| Small box | Roughly 4 to 6 cm, a solid colour. |
| Pen or marker | A solid colour that differs from the mug and box. |
| Tray | Shallow, about 25 x 20 cm, walls a few cm high. |
| Book standing upright | The obstacle. Make sure it is stable. |
| Two phones (one is enough for single-view mode) | Any recent phone; a stand or tripod for each. |
| Printed calibration target | `assets/calibration/mirror_charuco_v1_A4.pdf` or `_Letter.pdf`, page 1 (board) and page 2 (sync card, optional object tags). |
| A ruler | For the print check and the object sizes. |
| Stiff card and glue | The board must be flat. |

Plain, matte, differently coloured objects track best. Avoid a table the same
colour as any object.

## 1. Print and check the target (once)

1. Print page 1 at **100% / Actual size** (never "Fit to page"). Print page 2
   for the sync card and, optionally, the object tags.
2. Measure the **50 mm scale bar** with a ruler. If it is off by more than 1 mm,
   fix the printer setting and reprint.
3. Measure **one board square** (nominal 30.0 mm). Write down both numbers; they
   go into `capture.json` (`print_check`). MIRROR uses the square you measured
   for metric scale and rejects the calibration if the two numbers disagree.
4. Glue the board onto stiff card. A curled board gives a wrong calibration.

The board is a 5 x 7 ChArUco board (30 mm squares, 21 mm ArUco markers,
dictionary 5x5_100, ids 0 to 16). The sync card is marker 90; tags are 95 to 97.
The PDF geometry is checked by the test suite against OpenCV's layout.

## 2. Set up the phones

- **Top phone**: 60 to 75 cm above the table, looking down at the work area but
  tilted 25 to 35 degrees from straight down (a phone exactly face-down cannot
  have its zoom checked and places the camera centre poorly).
- **Side phone**: 50 to 70 cm from the work area, 30 to 40 degrees above the
  table, seeing the whole carry path including the tray.
- Both: 1080p or 4K, 30 or 60 fps, **1x lens, do not zoom or switch lenses**,
  turn off stabilisation modes that crop (for example "action" or "cinematic"),
  lock exposure and focus if the camera app allows it (tap and hold).
- Do not touch or move a phone between its calibration and the end of the take.

## 3. Lens calibration (once per phone, lens and resolution)

Film 20 to 40 seconds per phone, in the same mode you will use for the take:
hold the board in front of the phone and move it slowly through many positions,
**tilted** at 20 to 50 degrees in different directions, near the corners and
edges of the picture as well as the centre, close and far. Keep the whole
board sharp; avoid motion blur.

MIRROR accepts the lens calibration only when (thresholds fixed in advance,
`mirror/capture/calibrate.py`):

| Check | Limit |
|---|---|
| views with at least 8 board corners | at least 10 |
| reprojection error | at most 1.0 px |
| image coverage (5 x 4 grid cells with corners) | at least 60% |
| views tilted at least 20 degrees | at least 3 |
| focal length uncertainty (1 sigma) | at most 1% |
| principal point offset from centre | at most 10% of the image |

Redo it after: changing resolution, frame rate mode, lens or zoom, or phone.

## 4. Record a take

1. Place the objects at their start positions. Measure where each one stands
   with the ruler relative to the board's printed top-left corner if you are
   recording an evaluation clip (see `docs/REAL_FOOTAGE.md`).
2. Start recording on **both** phones.
3. Lay the board **flat on the table** in the middle of the work area, film it
   for 3 seconds without moving, then take it away. This measures where each
   phone is relative to the table and fixes the table plane and scale.
4. **Sync event**: hold the sync card where both phones see it, then turn it
   face down quickly. (Alternative: switch the room light off and on.)
5. Do the task: pick up one object at a time, carry it, put it down fully before
   the next. Keep your hand from covering an object more than you need to.
6. **Second sync event**: show the sync card again and turn it face down. One
   event can always be paired with anything; two events with the same spacing
   in both clips show the clips are the same performance.
7. Optional: lay the board flat again at the end for 3 seconds (detects a phone
   that moved during the take).
8. Stop both recordings. Copy the **original** files (AirDrop/USB, "original
   quality"); messaging apps re-encode and drop timestamps.

MIRROR never triangulates two clips unless they share at least two matching
sync events; otherwise it falls back to single-view positions and says so.

### Recalibrate when

- a phone moved, was bumped, or its stand was adjusted (also detected from the
  image border and from the board if you show it again at the end);
- the zoom, lens or resolution changed (detected from metadata when the phone
  writes it, and from an oblique board view; a face-down view cannot detect a
  zoom change, so do not touch the zoom);
- the board was not seen at the start of the take.

## 5. Optional: measure the mug's tilt with a tag

From the outlines alone, a round mug's tilt is not observable: MIRROR reports
it as **unobserved** and asks instead of assuming it stayed upright. To measure
tilt, tape tag 95 flat on the side of the mug that faces the side phone,
upright (the printed arrow towards the mug's opening), and add
`"tags": {"95": "mug"}` to `capture.json`. Tilt is then measured (full tilt
relative to how the mug stood at the start) whenever the tag is visible at
less than 60 degrees from face-on. A curved mug surface bends a 20 mm tag
slightly; this is a known limitation.

## 6. One phone only

Film from the top position only. Positions are measured while an object rests
on the table (its height is known from the ruler); while it is lifted, its
height is assumed and the position carries the resulting parallax
uncertainty; tilt is not observed unless a tag is seen. MIRROR labels the
result single-view and reduced.

## 7. Process the clips (no model calls)

```
python -m mirror.capture.ingest init  my_take        # writes my_take/capture.json
# copy the clips into my_take/ and fill in capture.json: clip names, print_check,
# what each object is and its measured size, which proxy stands in for it
python -m mirror.capture.ingest frames my_take       # images of the first frame with a pixel grid
# add one box per object and camera to "prompts": {"camera", "label", "frame", "box": [x, y, w, h]}
python -m mirror.capture.ingest run    my_take       # writes session.json and review.md
```

`review.md` lists what MIRROR measured, inferred and could not observe, and
every item you must resolve before it proposes a task specification:

- confirm what each tracked object is and which proxy stands in for it
  (`objects[].confirmed_by_person`);
- confirm a track that was lost and found again is the same object
  (`identity_confirmations`);
- check pick-up and put-down moments it could only bracket (for example while
  your hand hid the object) and correct them (`corrections`).

Each confirmation and correction is stored with the capture, with the time you
spent, so the effort can be reported.

Run `python -m mirror.capture.ingest demo DIR` to see the whole flow on a
**synthetic** rendered take (labelled as such everywhere; not real footage).

## What the result means

- Positions and tilts carry a status per frame: **measured**, **inferred**
  (computed with an assumption the footage cannot check, such as the height of
  a lifted object in single view or a partly hidden object), **interpolated**
  (filled across a short gap) or **unobserved** (no value; never a default).
- Pick-up and put-down are **inferred from object motion** with a time
  bracket. They are never robot contact events.
- The simulator uses **proxies**: catalogue cup, block, marker, tray and wall
  geometry, simulated beads and assumed mass and friction. A mug handle, the
  real contents and the real mass are not modelled. Proxy fidelity is not
  measured physical accuracy.
- Bead or rice retention in MIRROR is simulated. Real spills are not measured
  unless you measure them separately.

Validation of this path on real footage is described in `docs/REAL_FOOTAGE.md`.
Its status is **awaiting footage**: everything above is tested on synthetic
renders only.
