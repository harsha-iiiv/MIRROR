# Real run: your phone clips to a checked robot run, in one command

`mirror.capture.real_run` runs the whole real-footage path that the D1 phases did by hand:
watch the clip, ask the person what only they can answer, then (gate permitting)
interpret, build the scene on the real table frame, plan, execute, render and apply one
correction with a before/after re-check.

```
python -m mirror.capture.real_run \
    --lens LENS.MOV --take TASK.MOV --measurements m.json --out runs/take1 \
    --mode record --cap-usd 9.60
# same as: python -m mirror.cli real-run -- --lens ... (arguments are passed through)
```

Run it again with the same `--out` after the person answers: it resumes where it stopped.
`--redo STAGE` forgets that stage and every later one and moves their outputs (session, questions, intent, spec,
scene, run, correct/...) to `OUT/stale/<timestamp>/`, so nothing stale can re-enter the manifest or a later stage.

## Inputs

* **Lens clip**: the printed ChArUco board waved in front of the same phone, same lens,
  same video mode, focus and exposure locked if possible (AE/AF lock), 20-40 s with tilted
  views. Calibrated from every 15th frame; rejected calibrations stay rejected
  (`calibration_invalid` blocks the gate). `--intrinsics` reuses an accepted calibration
  instead (the focal check on the take still runs).
* **Take**: the board laid flat at the start (at rest for at least half a second), the sync
  card shown and hidden, then the task. Board-at-rest windows, sync events and an
  end-of-take board laid down differently are found automatically (`scan.json`).
* **Measurements** (`m.json`):

```json
{"print_check": {"measured_bar_mm": 50.0, "measured_square_mm": 30.0, "measured_by": "person"},
 "table_name": "the dark wooden table",
 "objects": [
  {"label": "mug", "name": "the white patterned mug", "short": "mug", "proxy": "cup",
   "real_identity": "white patterned mug, black inside and handle", "real_contents": "dry rice",
   "dims": {"height": 0.12, "width": {"value_m": 0.08, "note": "outside diameter"}, "depth": 0.08},
   "unsupported_features": ["handle", "round body"]},
  {"label": "red_box", "name": "the red box", "short": "red box", "proxy": "block", "dims": {...}},
  {"label": "marker", "name": "the blue and white marker pen", "proxy": "marker", "dims": {...}},
  {"label": "tray", "name": "the grey tray", "proxy": "tray", "dims": {"width": 0.355, "depth": 0.23, "height": 0.02}},
  {"label": "divider", "name": "the upright wooden divider", "proxy": "wall", "dims": {"width": 0.40, "depth": 0.025, "height": 0.29}}]}
```

`name` is what Muse Spark is asked to find; object order is prompt order. A bare number is
a ruler measurement in metres. D1's file: `/workspace/mirror-team/v2/night/pipeline/D1/measurements.json`.

## Modes and money

| mode | model calls | spend |
|---|---|---|
| `record` | live Meta models (Muse Spark, SAM 3.1); every response saved as a cassette in `--cassettes` (default: one directory per take, `/workspace/mirror-team/v2/cassettes/take_<sha16>`, so a second output directory for the same take replays instead of paying again) | charged to `OUT/spend.jsonl` AND the project ledger `/workspace/mirror-team/v2/spend_project.jsonl` (`MIRROR_PROJECT_LEDGER`); before every call the worst case is checked against 95% of `--cap-usd` (plus `--prior-usd`, persisted in `real_run.json`) and against 95% of the $30 project cap (`MIRROR_PROJECT_CAP_USD`); retried or failed attempts whose usage was never logged are charged at worst case |
| `replay` | cassettes only; both clients get a transport that raises on any network request, and `MODEL_API_KEY` is removed from the environment | $0 |
| `offline` | none; stops before grounding (perception needs a model or a recording) | $0 |

A typical 50 s take costs about $0.33 (3 Muse Spark grounding calls, 2 SAM video requests,
1 SAM image request), plus about $0.02-0.05 for interpretation and one correction.

## Stages (`OUT/real_run.json` keeps status and wall time of each)

| stage | what | outputs |
|---|---|---|
| probe | ffprobe both clips, compare recorded camera settings; a 4K or >40 fps take gets a 1080p30-equivalent working copy (lens clip scaled the same way), decision in `probe.json/working_copy` | `probe.json` |
| lens | ChArUco intrinsics (or `--intrinsics`) | `lens/intrinsics.json` |
| scan | sync card events, board visible / at rest windows, a SUGGESTION that the end board was re-laid (never applied without the person's answer) | `scan.json` |
| seed | clean frame after the setup, least image motion (`--seed-t` overrides) | `seed.json` |
| ground | Muse Spark: full frame, table crop, zoom refine | `grounding.json`, `evidence/grounding_boxes.jpg` |
| track | 540x960 proxy, SAM 3.1 forward + backward, re-seeding lost objects; walls segmented once and held | `tracks_proxy.json.gz`, `tracks_full.json.gz` |
| session | `build_session` (single view, calibration checks, events) | `session.json`, `review.md` |
| report | 2D timeline, overlay video, evidence crops, questions | `timeline.json/png`, `overlay.mp4`, `questions.json`, `page.json` |
| answers | apply the person's answers through the capture API, rebuild | `session_answered.json`, `review_answered.md`, `intent.json` |
| task | `CaptureSession.gate()`. Passed: Muse Spark proposal (`questions_spec.json` + `page.spec_review_request` for the person's review). Refused only for `recalibration_required`: the person's stated task is used and labelled. Any other open gate item (camera_moved, pose_invalid, ...): stops with "geometry refused", no layout scene | `spec.json` |
| scene | real table frame (board centre at the sim table centre); software shift with a label only if out of reach; wall yaw from its mask | `scene/scene.json`, `table_to_sim.json` |
| run | plan + MuJoCo execution + independent checker | `run/report.json`, `run/rollout.json` |
| render | overview video, 2x | `run/sim_run.mp4` |
| correct | the person's correction (`answers.correction`) or `--rehearse-correction ID` (labelled rehearsal); replan, re-check the first run under the new rule | `correct/*`, `before_after.mp4` |
| publish | summary | `summary.md`, `page.json`, `manifest.json` |

## Answers (bound to the run)

`questions.json` carries `session_id`, `take_sha256` (of the take as filmed), `question_set_sha256` and
`created_at_ms`. Every answer must carry the same three binding fields and be written after `created_at_ms`;
anything else (an old take's answers, another session, written by anyone but `person`) is refused, listed in
`page.answers_refused` and `real_run.json`, and never applied. Shapes (the review page writes exactly these):

* `answers/<item_id>` = `{answer, by: "person", uid, at, seconds_spent, session_id, take_sha256, question_set_sha256}`.
  `answer` is the exact option string (`intent`: "As proposed" / "Order mattered too (...)" / "Something else: <text>";
  free text is recorded but never turned into a task). `end_board`: only "Yes, I laid it down differently" switches
  the end-of-take drift check off (labelled).
* `spec_review` = `{spec_sha256, session_id, take_sha256, rules: {<rule_id>: {decision: "accept"|"reject", by: "person", uid, at, seconds_spent}}}`;
  `spec_sha256` must equal MIRROR's current proposal. Unreviewed rules stay proposed.
* `correction` = `{choice_id | text, by: "person", uid, at, session_id, take_sha256}`. A person's correction after a
  rehearsal re-runs the correction (the rehearsal moves to `OUT/stale/`).

A local `--answers` file uses the same shape (`{"answers": {...}, "spec_review": {...}, "correction": {...}}`).
`--answers-firebase RUN_ID` reads them from the page and saves them to `OUT/answers_firebase.json` (the local file is
never overwritten; if both exist the local one is ignored and a note says so). D1's page answers predate the binding
fields: `--legacy-answers-d1` accepts them for IMG_0815 only (sha256 6cc4955b...) and labels every output
"Answers carried over from D1's own page (legacy, same take)".

## Firebase run node (the review page)

`--publish-firebase RUN_ID` writes, after every stage, to
`dQmURmmZYmQI8u8P7ctOVTYCLFM2/file-system/wiki/mirror/phone-capture/viewer/runs/RUN_ID`:
`page` (page.json; `storage_base` and `media` hold Storage paths such as `mirror/capture/D2/pipeline/overlay.mp4`;
no absolute sandbox paths, no token URLs), `questions` (`{session_id, take_sha256, question_set_sha256, created_at_ms,
items: {<id>: {order, kind, question, proposal, options, evidence: {time_s, image_path}}}}`) and `status`
(`{stage, updated_at_ms, message}`). Media are copied to `/cloudspace/mirror/capture/RUN_ID/pipeline` unless
`--publish-dir` says otherwise. The page (wiki/mirror/phone-capture/review) writes only `answers`, `spec_review`
and `correction`.

## What it never does

Relax a threshold, bypass the gate, set `confirmed_by_person` without a person's answer, apply an answer that is not
bound to this run, call a model in replay mode, present the person's stated task (or MIRROR's default thresholds in
it) as MIRROR's inference or the person's words, or record a rehearsed correction as the person's (provenance
`rehearsal`). Every output carries the labels that apply to THIS capture: real footage, one camera, one label per open
gate item ("Calibration reuse flagged" for recalibration_required), "End-of-take drift check not used (board re-laid)",
"Stated task (gate refused)", "Layout shifted by software (dx, dy) to fit the robot's reach" (and "still partly out of
reach" if so), legacy answers, development clip, replayed model outputs.

## This morning's take (D2 runbook)

0. **Film at 1080p, 30 fps** (iPhone: Settings > Camera > Record Video > 1080p at 30 fps). 4K or 60 fps still works
   (a 1080p30 working copy is made automatically and recorded) but costs time; SAM cost scales with frames.
1. Lens clip and task with the same phone on the same stand, 1x lens, same video mode, AE/AF locked (long-press);
   board flat at the start of the task for at least half a second, sync card shown and hidden; leave the board where
   it was at the end, or answer the end-board question honestly.
2. `m.json`: copy D1's and change names/sizes if the objects changed.
3. Watch (record mode). Cap = what is left of the owner's budget; the project ledger also enforces the $30 total:

```
cd /workspace/mirror        # or the integration worktree
MODEL_API_KEY=... MIRROR_RENDER_LOCK=/workspace/mirror-team/v2/night/render.lock MUJOCO_GL=osmesa \
python -m mirror.capture.real_run --lens IMG_LENS.MOV --take IMG_TASK.MOV --measurements m.json \
    --out /workspace/mirror-team/v2/night/pipeline/D2/out --mode record --cap-usd 9.60 --dev-clip D2 \
    --publish-firebase D2 --answers-firebase D2
```

   It stops at `waiting: answers`. The questions, overlay, timeline and evidence are on the review page
   (wiki/mirror/phone-capture/review, run D2).
4. The person answers on the page. Run the SAME command again: answers bound to D2 are applied; the gate decides.
   * Gate passed: Muse Spark proposes the task (recorded); it stops at `waiting: task review`; the person
     accepts/rejects each proposed rule on the page; run again.
   * Gate refused for `recalibration_required` only: with the `intent` answer the stated task runs, labelled.
     Any other refusal stops before the robot (fix the capture; nothing is built from refused geometry).
5. Robot run and render follow. The correction runs once the person picks one on the page (run again). For a demo
   before that, `--rehearse-correction <id>` runs one labelled as a rehearsal; the person's later choice replaces it.
6. Twin films: `MUJOCO_GL=osmesa python scripts/twin_render.py --pipeline OUT --only main` then
   `--only before_after` (under the render lock); run the pipeline once more so `page.twin` lists them.

Wall time on this sandbox (4 shared cores, D1 replay): watch stages about 6 minutes of compute, robot stages under
1 minute plus about 2 minutes per rendered video; record mode adds model latency (about 3 minutes of SAM video and
40 s of grounding for a 50 s take). Lock waits are reported separately (`lock_wait_s`).
