# Claims and limitations

MIRROR is a simulation study. This page states what it shows, what it does
not show, and every simplification behind the results.

## What the project claims, where demonstrated

In a supported MuJoCo kit-packing task, a human correction becomes an
explicit, scoped task constraint that is checked from execution evidence. Old
runs are re-judged under it, objects the correction does not cover keep their
freedom, and where no compliant motion exists the planner refuses with a proof.
How much the correction changes behaviour on new layouts depends on the
planner's objective: under a rotation price of 0.05 m/rad compliance rose from
27/60 and 33/60 to 60/60 (and from 4/30 and 3/30 to 30/30 on a shortcut family);
under a time-calibrated price (0.2445 m/rad) the uncorrected specification
already plans upright routes and the gain is 0.000, and the correction's
measurable effect is to turn spills on blocked layouts into proven refusals
(10/10). Evaluation numbers are in [EVALUATION.md](EVALUATION.md) and
`results/`.

## What the evaluation numbers measure

* **The two layout evaluations test one fixed specification pair.** In each
  of the first two registrations the demonstration was interpreted once and the
  reference correction compiled once, before the freeze. The resulting original
  and corrected specifications were then run on 60 fresh layouts and 30 shortcut
  layouts (33/60 → 60/60 and 3/30 → 30/30 with the Muse Spark pair), with 5
  blocked layouts handled separately. The layouts are new; the language input is
  not. These are not 90 new-video interpretations and not 90 independent
  language-learning trials.
* **The Muse Spark correction benchmark is not blind.** Its rows (44/44 first
  set, 27/28 held-out) swap the model after development, on case sets that had
  already been seen, with a prompt revision tuned on the development model.
  Reused development cases are not a fresh external holdout. The benchmark as a
  whole is not pre-registered (below).
* **The demo film and the studio site show different registrations.** The film's
  side-by-side and grid clips use first-registration seeds (30000+, development
  model specifications); the site's evidence step loads the second registration
  (50000+ and 60000+, Muse Spark specifications). See
  [Evidence provenance](EVALUATION.md#evidence-provenance).
* **The agreement check is a regression check, not evidence about Muse.** Each
  registration requires the replayed model specifications to equal the
  offline-rule specifications before it runs (`mirror/evaluate/specs.py`). This
  deterministic regression check guarantees the recorded responses still
  produce the expected rules. Because of it, the offline rules alone would give
  the same layout results, so the evaluations do not show that Muse Spark caused
  the improvement. Language understanding has its own prepared protocol
  ([protocols/v3_language.md](protocols/v3_language.md)), not yet run.
* **The earlier gains measure the correction against a 0.05 m/rad objective.**
  The first two registrations planned with `PlannerConfig.rotation_penalty` =
  0.05 m/rad, which makes slow tilted slot routes look cheap. Registration
  objective_v1 ([report](../results/eval_objective_v1/attempt_1/report.md)),
  frozen before its layouts, calibrated the price to execution time on
  development layouts (0.2445 m/rad). At that price the original specification
  never tilts the cup on 120 new layouts (0/80 primary, 0/40 shortcut), its plans
  equal the corrected specification's on all 120, and the retained benefit is
  0.000. At 0.05 on the same layouts the earlier effect replicates (49/80 → 80/80,
  p = 9.3e-10; 0/40 → 40/40, p = 1.8e-12), and the 0.05 objective is also slower.
  Do not describe 27/60 → 60/60 or 33/60 → 60/60 as the effect of the correction
  alone. The rule's remaining measurable effect is on layouts where only a tilted
  route exists: on 10 blocked layouts it turns 10/10 spilling runs (38 beads) into
  proven refusals. Timing is simulated under one controller, and the calibrated
  price values time only, not spill risk.
* **Outcome-level reproduction, not bit-exact.** Network-blocked replays
  (`results/reanalysis_v1/replay/`) reproduce the first registration in all 95
  layouts, outcomes and bead counts, and the Muse registration in all 95 layouts
  and outcomes on Linux x86_64 with one bead count different (shortcut seed
  60002, original specification: 6 stored, 2 replayed). On Apple arm64
  (`results/reanalysis_v1/replay_cross_platform/`) all 95 outcomes agree, 0 of 95
  layout hashes are byte-identical (layouts agree to about 1e-16) and bead counts
  in spilling runs differ by up to 2. The sim golden rollout (recorded on Linux
  x86_64) re-run on macOS arm64 gives the same status, steps, events, phases,
  contact pairs and beads in the cup, with object positions up to 8.4e-5 m and bead
  positions up to 3.3 mm apart. Registered evaluations were run on Linux x86_64
  only. The run notes' "the simulator is deterministic"
  (`results/eval_muse/RUN_NOTES.md`, kept unchanged) is superseded by this.
* **The video benchmark's numbers are conditional, and it cannot measure tilt.**
  0.72 cm is the median among estimates within 5 cm of the truth; over every
  visible object-frame 88.8% are within 5 cm, the median of all estimates is
  0.99 cm, the 90th percentile 6.4 cm, and 26 cm while an object is carried. Seed
  71010 failed as a whole (camera calibration). A colour-threshold baseline scores
  a lower gated median (0.54 cm) with only 60% of its estimates within 5 cm. True
  tilt is at most 0.29° in every clip, so a constant-zero estimate (0.01°) beats
  the reported 2.0°: no tilt-accuracy claim follows
  (`results/reanalysis_v1/video/report.md`). A new video benchmark with
  substantial tilt is designed, not run.
* **Robustness denominators.** Do not claim that image edits "barely matter" for
  perception. The near-zero median IoU change included 90 of 218 rows for
  never-prompted objects (0 before and after). On prompted objects the median
  change is −0.039, with large losses in the tail; found at IoU ≥ 0.5 is 0.445
  (A) and 0.372 (B); the carried cup was never correctly segmented
  (`results/reanalysis_v1/robustness/report.json`).
* **The correction benchmark is not pre-registered.** Its 44 first-set and 28
  held-out cases were committed together with their results after the runs
  (06:32 PDT, 31 October), and the `frozen_at` fields in the case files are
  hand-entered and inaccurate ([protocols/chronology.md](protocols/chronology.md)).
  What the record shows: the case-file hashes equal the committed files for
  every result, and by recorded response times the first model run on the
  held-out cases began after that file's final save. The Muse Spark rows are a
  post-hoc model swap.
* **Every registration has an immutable manifest** (`results/manifests_v1/`):
  freeze and result commits, frozen code hash recomputed from the freeze commit's
  own tree (all six reproduce), model ids, prompt hashes, scene and outcome
  digests. Commit times are the committer's clock, not an external timestamp.
* **No fresh authoring evaluation and no user study has been run.** The v3
  protocol ([protocols/v3_authoring.md](protocols/v3_authoring.md), with its
  [test matrix](protocols/v3_test_matrix.md)), the impact study kit (`study/`)
  and a 3-5 person usability pilot (`study/pilot/`) are prepared, not run, and
  tested only on development and clearly synthetic fixtures. The builder's own
  testing is a developer smoke test, not independent user validation; a 3-5
  person pilot, when run, is preliminary usability evidence only, not proof of
  demand or of a general productivity gain.

## What it does not claim

* No physical robot, and no evidence of simulation-to-reality transfer.
* No understanding of human video. The demonstration is a simulated
  teleoperation trace. The video path reads rendered simulator video, not
  camera footage of people (see "Video, voice and image" below). A real-phone
  capture path exists (`mirror/capture`), but no real phone footage has been
  processed or validated.
* No general task learning from one example. One task family is supported.
* No new foundation model or learned robot policy.
* No formal verification or safety guarantee. Finite simulated trials are
  evidence, not proof. The only proofs are the narrow geometric
  infeasibility certificates described below.
* Not the first language-conditioned manipulation system. Prior work such as
  VoxPoser derives constraints from language. The contribution here is the
  authoring, clarification and regression-testing workflow and its evidence.

## Simplifications

### Robot and physics
* The robot is a free-flying parallel-jaw gripper on three prismatic and
  three revolute actuated joints, with no arm body. Only the gripper collides.
  Every evaluation result uses this baseline.
* The optional arm backend (simulated UR10e/UR5e, `mirror/arm`) has a
  development report only (`results/arm/v1`: reference scenes and development
  layouts 300-309 on one platform, Darwin arm64 with MuJoCo 3.17.0). It never
  takes slot routes, so in those layouts its uncorrected runs did not spill and
  the correction changed nothing; the spill-then-correct story is a gripper
  baseline result. Its mount and tool were tuned on development seeds 100-107,
  excluded from that report. Servo gains are chosen, gravity compensation is
  exact, and nothing is claimed about a physical arm.
* Its workspace is a box (0.45 m reach in height). The wall is taller than
  this, so no route goes over it.
* Grasps are contact-based (the jaw closes at 12 N per finger; friction
  holds the object), with no attachment constraint. Gravity compensation is
  applied to the robot's bodies only, not to the held object.
* The executor stops at every waypoint, with speed and acceleration limits.
  Targets outside the workspace are never clamped; they fail the rollout.
  Anything that falls off the table comes to rest on a floor plane.
  Physics timestep 0.004 s.
* Bead contacts are not logged; bead loss is measured from bead positions.
* Layout validity at sampling time uses a conservative 2-D route search at
  carrying height, not a motion plan.

### Demonstration and interpretation
* The reference demonstration is driven by a scripted operator through the
  same velocity-command interface a person uses in the studio. It is
  labelled `teleop_scripted`.
* In the first two evaluations the interpreter reads simulator trace data
  (poses, grasp and release events), not video. The trace rebuilt from video
  is a separate path, measured in its own registration. Privileged simulator state is used for summarizing the
  demo, for planning and for checking, and is never presented as perception.
* Recorded model responses in `data/cassettes/` were produced with a
  development model (`gpt-5.5`, through an OpenAI-compatible endpoint), not
  Muse Spark. With a Meta Model API key, `MIRROR_LLM_MODE=record` re-records
  them with `muse-spark-1.3`.
* Rules apply per object kind; a rule covers every object in the scene that
  matches its selector.
* The reference correction confirms a rule that the interpreter had already
  proposed (as an open question) from the demonstration. The correction's
  role is to settle that question and scope it, not to invent the rule from
  nothing.
* The offline rules lean toward "likely required" for the cup's upright
  carry because the scene describes the cup as holding beads. That is scene
  knowledge, not evidence from the demonstration.
* In `mirror story` and in `mirror run` without `--spec`, the review step is
  scripted: it confirms the goals and "cup upright at the destination".
* When a correction names no phase, an upright rule applies while the object
  is carried.
* Tilt shown in a clarification is computed from planned waypoints, assuming
  a rigid grasp; executed tilt is measured by the checker.

### Correction compiler and its benchmark
* Natural-language corrections are compiled by the model into intent-level
  operations, then checked by deterministic code (schema, specification
  consistency and scope) before anything is applied.
* After the correction benchmark showed the model applying corrections about
  objects that are not in the scene ("bowl", "jar") to the cup, a
  deterministic validation layer was added. It runs after the model and asks
  which scene object was meant whenever a correction names a known object
  noun that matches nothing in the scene. Because it was added after those
  failures were seen, the benchmark reports interpreter output alone and
  interpreter plus validation layer as separate rows, computed from the same
  recorded model outputs. The layer recognises only nouns in its fixed
  lexicon, so it narrows but does not remove the risk of a correction being
  applied to the wrong object.
* Benchmark cases are single sentences applied to one base specification in
  one scene. Gold answers follow the documented policies (for example, a rule
  with no phase applies while the object is carried). The prompt was revised
  twice after results were seen; the first-revision numbers (offline rules and
  the development model, the first runs on each case set) remain the headline
  and later rows are labelled post-change
  (`results/corrections_benchmark_summary.md`; its row labels predate the
  chronology analysis and are superseded by it).
* The Muse Spark rows of that benchmark are a post-hoc model swap: both case
  sets had been seen and the prompt was tuned on the development model. They
  are not a blind or held-out measurement of Muse Spark.
* The held-out set was written by the same people who wrote the first set and
  the compiler, after seeing the first-set failures. It is held out from the
  interpreters, not independent of their authors.

### Planning
* Routes are enumerated (direct, around each wall end, through each slot in
  each orientation mode), with a sampling fallback for extra obstacles.
  Collision checking uses a conservative geometric envelope of the gripper
  plus the held object.
* Only top grasps of upright objects are supported, and the grasp is
  treated as rigid while planning. The marker's collision shape is a square
  prism around its cylinder (conservative).
* Orientation modes are a fixed set (upright, ±30°, ±45°, ±60°, ±90° about the
  jaw axis). A slot is crossed in a straight horizontal pass at a fixed
  orientation; the gripper rotates only while stationary. With a tilt limit
  between modes the planner may find no route where a partly tilted path
  exists, and it says so.
* If an unmoved object blocks a route, the planner retries other object
  orders.
* "No plan" means the planner found none. A proof (`proven: true`) is
  attached only when a geometric bound shows no motion within the workspace
  can carry the object across under the enforced rules, under stated
  assumptions (rigid top grasp within a depth range). A `planner_model`
  record (`proven: false`) covers only the planner's own way of crossing a
  slot and is not a proof.
* Forbidden contacts are avoided through safety margins (10 mm in open space,
  3 mm near contact) rather than a separate check during planning.
* The empty gripper may also tilt through a slot on its way back; rules
  apply to objects.

### Checking
* Completion uses the object's centre (inside the tray footprint, above its
  floor), release, and settling (linear speed below tolerance for the last
  0.3 s; angular speed is not checked).
* The transport phase comes from the per-step two-finger hold signal; gaps of
  up to 0.5 s are bridged.
* Beads count as retained when their centres are inside the cup's interior
  at the last step (2 mm tolerance). Bead loss is a reported metric, not a
  rule.
* Clearance is measured exactly against the solid shapes (an earlier bounding-sphere
  estimate over-reported violations; no published result enforced a clearance
  rule).
* Ordering rules are checked independently from execution evidence (grasp start,
  placement completion). A confirmed rule the evidence cannot settle gives the
  status `unverified`, never success ([RULES.md](RULES.md)). Historical rollouts
  re-check from their playback frames to their recorded status (266/266).
* An upright route is labelled feasible by a clearance condition, not a
  constructed path; scenes with box obstacles are marked unknown.

### Studio and static site
* Live runs in the studio use development showcase layouts (dev seeds
  9000–9099) and are labelled as such. Pre-registered evaluation results are
  only loaded, never regenerated, by the studio.
* Maps of loaded evaluation trials are sketches rebuilt from each rollout's
  recorded geometry. The tilt chart uses about 200 samples per run.
* The static site stores poses at 1 mm and 0.001 quaternion resolution.
  Bodies that have not moved are omitted from playback frames, and long
  rollouts may be thinned for playback; every thinning step is noted in the
  report. Metrics always come from the full-rate step log.
* Tall static walls are drawn translucent so the robot stays visible.
* Every studio step shows how it was produced (fresh model call, replayed
  recording, recorded output, offline fallback with its reason, new simulation,
  recorded playback, new check), with measured latency only for work done now.
* Every demonstration and re-interpretation is a new immutable generation; spec
  version and rollout ids are never reused, and old rollouts re-check against the
  version they ran under.
* Uploads are accepted only after the video is probed and every frame decoded
  within time, CPU and memory limits; bytes are bounded per file, session and
  server. Origin checks are not authentication; sessions are bearer capabilities,
  and the limits are controlled-demo settings, not measured public-load capacity.
* Challenge mode runs development seeds only and refuses every seed named by a
  frozen evaluation. The Challenge tour is a recording made once with the offline
  interpreter and MuJoCo.
* The correction-language paraphrase sets are author-written; the held-out 9/16
  is developer smoke evidence, not a benchmark. Correction demonstrations beyond
  the reference sentence (`scripts/demo_corrections.py`) are on development
  layouts with the offline interpreter, not an evaluation.

### Video, voice and image

* **Speech.** The spoken-correction benchmark uses synthetic speech from a
  non-Meta engine (ElevenLabs `eleven_v4_turbo`, two premade voices), clean and
  with added pink noise. It is not human recordings; results on real speakers
  may differ. The unit-test audio clip is synthesised with espeak-ng.
* **Punctuation.** In that benchmark four transcripts with a word error rate of
  0 still compiled differently from the typed sentence, because of a doubled
  full stop or a comma heard as a full stop. The correction compiler is
  sensitive to punctuation.
* **Rendered video, not camera footage.** Demonstration videos are rendered from
  the simulator, of a scripted demonstration in simulated layouts. The render
  look was adjusted for segmentation (soft wood grain, dark floor, opaque walls)
  and has no overlays. Nothing here shows perception on real camera footage of
  people.
* **Video benchmark: 16 of 20 seeds, 2 of 6 checks met.** The registered spend
  cap's reservation stopped admission after 16 of 20 seeds; a second run was
  refused by the run-once guard, and the four missing seeds cannot change any
  verdict. Replace "median position error 0.72 cm and cup tilt error 2.0° (met)"
  with: 0.72 cm median among estimates within 5 cm (88.8% of visible
  object-frames; 90th percentile of all estimates 6.4 cm); tilt not measurable on
  these clips (true tilt at most 0.29°). Objects
  found (0.886), grasp/release within 1 s (0.635), timing (0.65 s) and the
  specification check (same as from the true trace on 8 of 16 seeds) did not.
  On one seed tracking found nothing. The first attempt produced no usable
  result (a harness error, then a machine restart) and is reported in full. The
  main evaluations therefore keep using the simulator trace, not video.
* **GPU renders.** The videos of both video-benchmark attempts were rendered on
  NVIDIA L4 GPUs with EGL (attempt 2: 8 machines in parallel). On a development demonstration, EGL frames differed
  from software (OSMesa) renders by about 1/255 on average (99th percentile
  4/255), with identical poses and events.
* **Known geometry.** Reconstruction from video assumes the camera intrinsics
  (45° vertical field of view), the table size (1.2 × 1.0 m) and the object,
  tray and wall-thickness sizes from the kit catalogue. The camera is assumed to
  be on the operator's side of the table.
* **What video cannot see.** The gripper opening is not observed (closed while
  an object is carried, open otherwise). The bead count is the number of balls
  the grounding model can see. Cup tilt comes from an in-image silhouette fit in
  the side view, only where the cup is clearly visible. Carried objects hidden
  under the gripper follow it; gripper depth comes from its apparent size.
  Static scene parts are segmented once on the first frame.
* **Prompts.** On these renders, SAM 3.1 noun-phrase prompts alone found only
  the blue cube, the table (top view) and the wall (side view), so Muse Spark
  locates each object on the first frame and SAM tracks it from that box.
* **Tuning.** Perception was tuned on the reference demonstration and
  development seeds 9001 and 9002 only. The video registration's seeds were
  never rendered or sampled by that work. On 9002 it still misses the marker's
  grasp and release.
* **Unobserved tilt was reported as upright (fixed).** Before the perception fix
  of 2 November, video-derived summaries reported unobserved tilt as measured 0°:
  all 20 recorded Muse Spark proposal requests for video-derived demonstrations
  were told, for example, that the block stayed within 0° of upright the whole
  time, although block and marker tilt are never measured from video. The video
  benchmark's "same specification 8 of 16" partly rests on these statements; its
  saved results are unchanged and remain the registered result. Unobserved tilt is
  now unknown, never upright evidence, and every trace records per frame what was
  measured, inferred, interpolated or unobserved (`mirror/observability.py`).
* **Phone capture is untested on real footage.** The phone path's tests use
  synthetic renders. Proxies (cup, block, marker, tray, wall) are catalogue
  geometry with simulated beads; a mug handle, real contents, mass and friction
  are not modelled, and simulated bead retention is not a measurement of real
  spills. Pick and place times in human demonstrations are inferred from object
  motion, not robot contact. A round mug's tilt is unobserved without a printed
  tag. Validation protocol: [REAL_FOOTAGE.md](REAL_FOOTAGE.md), awaiting footage.
  The rendered-video benchmark's results are not evidence of phone-video
  performance.
* **Run-to-run variation.** Live perception is not deterministic, and a
  benchmark number is one run per seed. On the
  reference video, the recorded run gave a 0.88 cm mean position error with
  6 of 6 grasp/release events; a live re-run on the same video gave 1.90 cm and
  4 of 6 ([record](../results/live_checks/2026-10-31_video_rerun.md)). The
  published site replays the recorded run.
* **Video models.** In development, Muse Spark and Muse Wonder reported grasps
  about 2 s early. In the video benchmark their event F1 at 0.5 s was 0.062
  (Wonder) and 0.115 (Spark). Their observations are reported and compared; they
  never set event times for the tracking thresholds and never grade a run.
* **Muse Spark kept over Muse Wonder.** By the rule stated before the test,
  Wonder would have replaced Spark as the video observer only with an event F1 at
  least 0.05 higher and at least as many correct object sets. It scored lower on
  both (0.062 vs 0.115; 14 vs 16 of 16) at about five times the cost ($3.86 vs
  $0.76) and twice the latency (79 s vs 42 s), so Spark was kept. The studio does
  not use Wonder.
* **Image edits change scenes.** Muse Image does not only restyle a frame: in
  the robustness benchmark it moved, removed or reframed objects in 42 of 261
  scored objects (16%), and it shifts and rescales the whole frame slightly.
  Both are measured and reported; objects whose geometry changed are scored
  separately, and the whole-frame drift is undone before the primary scores.
* **Robustness scope.** The robustness frames are carry moments, where the cup
  is inside the gripper. SAM rarely found the cup there even without edits, so
  the found-rate check failed under both prompt conditions. The benchmark says
  little about the cup at other moments.

