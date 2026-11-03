# MIRROR: corrections you can check

## Inspiration
Teaching a robot by demonstration usually means it copies your motion. When the room changes, it replays the wrong thing or needs a new demonstration. We wanted to show a task once, fix one misunderstanding in plain words, and then check what that fix actually changed.

## What it does
MIRROR is a task-authoring studio for a simulated gripper in MuJoCo, with an optional articulated UR10e arm. You demonstrate carrying a cup of beads, a block and a marker to a tray. MIRROR summarises the demonstration, writes down what it thinks the task is (goals, rules, open questions), and lets you confirm or correct it.

A correction becomes a scoped rule change: one rule, on named objects, in a named phase. You can check it three ways:
1. **Old runs are re-judged from execution evidence.** Every earlier run is re-checked against the new rule from the simulator's step log. A rule the evidence cannot settle is reported as "not verified", never as a pass.
2. **The corrected rule is planned and run on new layouts,** with the same planner, physics and independent checker.
3. **When no compliant route exists, MIRROR refuses with a proof** instead of breaking the rule to finish.

Vague requests ("be careful") get a question back, unsupported ones get an honest refusal, and every result says whether it came from a fresh model call, a recording or the offline rules.

## What the evidence shows
- **Refusals hold at every planner setting we tried.** On 10 blocked layouts where the only way through tilts the cup, the original specification completes all 10 runs and spills 38 beads at each of six rotation prices; the corrected specification plans nothing and returns a proven infeasibility record on all 10, at both prices it was run at.
- **What a correction changes on open layouts depends on the planner.** Planned at a rotation price of 0.05 m/rad, violation-free runs went from 27/60 and 33/60 to 60/60 (and 4/30 and 3/30 to 30/30 on a shortcut family). With the price calibrated to execution time (0.2445 m/rad), the uncorrected plan never tilted the cup on 120 new layouts that have an upright route, so the cup rule changed nothing there. The 60/60 results measure the correction against the 0.05 price, which is also slower.
- **Ordering and clearance corrections do change behaviour at the calibrated price.** On development layouts (6 per case, offline interpreter; a demonstration, not a frozen evaluation; results/demos/corrections_calibrated_v1 in the repository), at 0.2445 m/rad: "Pack the marker before the cup" is violated by the old runs on 6/6 layouts, and the new runs succeed and change the packing order on 6/6. "Keep the cup at least 6 cm away from the wall": the old runs pass 3.0 to 4.1 cm from the wall and violate it on 6/6, the new runs keep 8.0 to 9.1 cm and succeed on 6/6. On 3 blocked development layouts the corrected specification is refused with a proof. Tilt corrections change nothing at this price (0/6, against 5/6 at 0.05).

## How we used Meta's models
Each model has one job, and each job was measured. The layout, voice, video and image tests were frozen before their first model call; the correction-language benchmark was not, and we say so.
- **Muse Spark** writes the task spec, turns a correction into scoped rule changes, asks clarifying questions, and boxes the objects so SAM can track them. One specification pair it compiled was frozen and run on 90 new layouts plus 5 blocked ones. The pair also had to match our offline rules, so those runs measure the rule, not Muse Spark's added value. A study of fresh corrections from independent authors is prepared and not yet run.
- **Muse Voice Transcribe** takes spoken corrections. On 288 synthetic-speech clips: word error 3.0% clean and 2.2% with noise; corrections came out right from audio 141/144 and 140/144, against 142/144 from typed text.
- **SAM 3.1** tracks objects through top and side videos so a demonstration can be rebuilt in 3D. On 16 rendered demos, 88.8% of position estimates were within 5 cm (median 0.72 cm among those; 90th percentile 6.4 cm over all), and one seed failed on camera calibration. The cup never tilted more than 0.29 degrees in these clips, so this test could not measure tilt.
- **Muse Wonder** was tested against Muse Spark as the video observer. Spark won on every measure (event F1 0.115 vs 0.062, right objects 16 vs 14, a fifth of the cost), so the rule we wrote in advance kept Spark.
- **Muse Image** edits frames (lighting, background, clutter, material) to stress-test perception. On prompted objects, segmentation lost a median 0.04 IoU under edits; found rates at IoU 0.5 were 0.45 and 0.37, and the carried cup was never segmented.

No model moves the robot or grades a run. A planner executes, and an independent checker scores every run from the simulator.

## How we built it
Python, MuJoCo 3.17, a typed task spec with schema validation, a planner that proves infeasibility, a checker that monitors every rule from execution evidence, frozen registrations with append-only ledgers and manifests, and a web studio (replay site and a live backend with per-model budgets). It runs on Linux and macOS; replays reproduce every outcome on both, though not every bead count bit for bit.

## Challenges
Our first video benchmark failed on a harness bug, and we reported it in full. An audit then showed that our earlier headline gain depended on how the planner priced wrist rotation, that the video test could not measure tilt, and that the correction benchmark was not pre-registered: its case files and results were committed together after the runs. We re-ran with a time-calibrated planner and narrowed every claim to what the evidence shows.

## What we learned
A rule you can check is worth more than a number that depends on a planner setting. The correction held on every layout and refused safely when it could not be met; how much it changed depended on how the planner priced wrist rotation. Replays reproduce every outcome, not every bead. Video perception places objects well when it finds them (within 5 cm, median 0.72 cm), but not yet reliably whether and when things happen.

## What's next
Real phone footage (a calibrated capture path is built and waiting for clips), an outside usability pilot, fresh corrections from independent authors, and corrections that span several tasks.

## Links
- Live studio: https://mirror-robot-studio.netlify.app
- Demo video (2:40, made before these corrections; to be replaced): https://mirror-robot-studio.netlify.app/media/mirror_demo_1080p.mp4
- Code, evaluations and every ledger: https://github.com/harsha-iiiv/MIRROR
