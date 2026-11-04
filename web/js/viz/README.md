# 3D workspace (web/js/viewer.js + web/js/viz/)

The studio's rollout viewer, rebuilt as a workspace for people who work with robots. Everything drawn
comes from data the studio already exports (VisualScene, columnar frames, the checker's report, the
spec) or from a real capture's calibration. Nothing is decorative and every number can be inspected.

| Layer | What it shows | Source |
|---|---|---|
| Look | PBR materials, neutral studio light probe, soft key-light shadows, a measured tabletop (1 cm grid, 10 cm rulers in metres, world frame triad) | `viz/look.js` |
| Paths | a lit tube per moved object, coloured per playback frame by the checker's intervals (OK / advisory / violated); the traced object thick, the rest thin; revealed to the playhead, the remainder faint | `viz/ribbon.js`, `viz/verdict.js` |
| Rules | `upright`: cone of the tolerance about world z + the object's own z axis as a needle, only while carried; `keep_clearance`: each obstacle box grown by `min_m` (box ⊕ ball, the exact region the object surface must not enter); tray goal region; markers where each violation interval peaks (pulse while the playhead is inside; static under reduced motion); proposed rules drawn muted as "proposed, not enforced" | `viz/rules.js` |
| Ghost | the previous run's bodies, translucent, at the same playback time (plus the old path and its spilled beads) | `viz/ghost.js` |
| Re-check | `loadRollout(p, {report})` judges a run's frames with another report, e.g. the old run under the new rule | `viewer.js` |
| Readouts | hover any body: tilt (vs the rule's limit), speed (frame difference), height, exact distance from the object's solid shape to the nearest obstacle box with the closest-point segment drawn; a persistent clearance line when a clearance rule exists | `viz/geom.js` is a line-by-line port of `mirror/evaluate/geom.py` (tested against it) |
| Timeline | phases per object, grasp/release, violation intervals, playhead; scrub; frame steps | `viz/timeline.js` |
| Phone | for real captures: the calibrated phone as a frustum with its undistorted frame, the rectified table photo as the table texture, and a glide into the phone's own viewpoint where the simulation is drawn over the real frame with the calibrated pinhole (approximate; calibration reuse may be flagged) | `viz/phone.js`, `mirror/viz/capture_view.py` |
| Arm | UR10e/UR5e display meshes decimated from MuJoCo Menagerie (BSD-3, `assets/NOTICE`); C toggles the simulation's collision capsules | `viz/arm.js`, `scripts/viz_build_arm_meshes.py` |
| Fallback | no WebGL: a top-down 2D view with the same API | `viz/fallback2d.js` |

Camera presets glide (sine in-out, about 0.9 s, log-space radius) and settle; no zooms or shake;
`prefers-reduced-motion` makes every move instant. The renderer draws only when something changed
and only while on screen; on software rasterisers (SwiftShader, llvmpipe) it drops the light probe and
renders at pixel ratio 1.

Keys (viewer focused): `, .` frame, `[ ]` event, `1-5` follow/3D/top/side/phone, `R` rules, `G` ghost,
`P` paths, `M` measure, `L` labels, `C` collision, `X` real table, `?` sheet. Space and arrows stay with
the host page.

Honesty: the checker evaluates every physics step; the viewer samples its intervals and recomputes
readouts on playback frames (said on the card). Capture views are approximate (one camera, flagged
calibration, table plane taken as the board plane, placement chosen by software) and carry their
labels; proxies are simulation stand-ins, not measured models.

Standalone page: `web/js/viz/lab.html?run=r003` (static export data), `?run=r002&recheck=3` (old run
under the new rule), `?pack=../../data/viz/d1/&run=after` (capture pack, `5` or "Phone view").
Packs: `python -m mirror.viz.capture_view ...` and `python scripts/viz_export_capture.py ...`.
