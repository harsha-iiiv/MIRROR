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
`--redo STAGE` forgets that stage and every later one.

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
| `record` | live Meta models (Muse Spark, SAM 3.1); every response saved as a cassette in `--cassettes` (default `OUT/cassettes`) | charged to `OUT/spend.jsonl`; before each call the worst case is checked against 95% of `--cap-usd` (minus `--prior-usd`), and the run stops before crossing it |
| `replay` | cassettes only; both clients get a transport that raises on any network request, and `MODEL_API_KEY` is removed from the environment | $0 |
| `offline` | none; stops before grounding (perception needs a model or a recording) | $0 |

A typical 50 s take costs about $0.33 (3 Muse Spark grounding calls, 2 SAM video requests,
1 SAM image request), plus about $0.02-0.05 for interpretation and one correction.

## Stages (`OUT/real_run.json` keeps status and wall time of each)

| stage | what | outputs |
|---|---|---|
| probe | ffprobe both clips, compare recorded camera settings | `probe.json` |
| lens | ChArUco intrinsics (or `--intrinsics`) | `lens/intrinsics.json` |
| scan | sync card events, board visible / at rest windows, end-board placement | `scan.json` |
| seed | clean frame after the setup, least image motion (`--seed-t` overrides) | `seed.json` |
| ground | Muse Spark: full frame, table crop, zoom refine | `grounding.json`, `evidence/grounding_boxes.jpg` |
| track | 540x960 proxy, SAM 3.1 forward + backward, re-seeding lost objects; walls segmented once and held | `tracks_proxy.json.gz`, `tracks_full.json.gz` |
| session | `build_session` (single view, calibration checks, events) | `session.json`, `review.md` |
| report | 2D timeline, overlay video, evidence crops, questions | `timeline.json/png`, `overlay.mp4`, `questions.json`, `page.json` |
| answers | apply the person's answers through the capture API, rebuild | `session_answered.json`, `review_answered.md`, `intent.json` |
| task | `CaptureSession.gate()`. Passed: Muse Spark proposal (`questions_spec.json` for the person's review of proposed rules). Refused: refusal recorded; the person's stated task is used and labelled | `spec.json` |
| scene | real table frame (board centre at the sim table centre); software shift with a label only if out of reach; wall yaw from its mask | `scene/scene.json`, `table_to_sim.json` |
| run | plan + MuJoCo execution + independent checker | `run/report.json`, `run/rollout.json` |
| render | overview video, 2x | `run/sim_run.mp4` |
| correct | the person's correction (`answers.correction`) or `--rehearse-correction ID` (labelled rehearsal); replan, re-check the first run under the new rule | `correct/*`, `before_after.mp4` |
| publish | summary | `summary.md`, `page.json`, `manifest.json` |

## Answers

`answers.json` (or `--answers`) has the same shape the D1 page writes:
`{"<question id>": {"answer": "...", "by": "person", "at": <epoch ms>, "seconds_spent": s}}`.
Only entries with `by: "person"` are applied. Object answers confirm or reject mappings;
event answers confirm (or move) the capture's own pick event; `camera_settings` is recorded
but never resolves the calibration flag; `intent` is the stated task; `correction` picks a
candidate (id or label) or gives free text; `spec_review` = `{"answer": {"confirm": [...], "reject": [...]}}`.

## What it never does

Relax a threshold, bypass the gate, set `confirmed_by_person` without a person's answer,
call a model in replay mode, or present the person's stated task as MIRROR's inference.
Every output carries its labels (real footage, one camera, calibration flagged when it is,
development clip when `--dev-clip` is given, replayed model outputs in replay mode).


## Tomorrow's take (runbook)

1. Film the lens clip and the task with the same phone on the same stand, same lens (1x), same video mode,
   AE/AF locked; board flat at the start of the task for at least half a second, sync card shown and hidden.
2. Write `m.json` (copy D1's and change names/sizes if the objects changed).
3. Watch (record mode; the cap is what is left of the $10, e.g. 9.67 after D1's $0.33):

```
MODEL_API_KEY=... python -m mirror.capture.real_run --lens IMG_LENS.MOV --take IMG_TASK.MOV \
    --measurements m.json --out runs/D2 --mode record --cap-usd 9.67 --dev-clip D2 \
    --publish-dir /cloudspace/mirror/capture/D2/pipeline
```

   It stops at `waiting: answers` with `questions.json` (and `page.json`, `overlay.mp4`, `timeline.png`, evidence).
4. The person answers (page or file). From a Firebase answers node: add `--answers-firebase <path>`; from a file:
   `--answers answers.json`. Run the same command again: answers are applied, the gate decides.
   * Gate passed: Muse Spark proposes the task (recorded); it stops at `waiting: task review` with
     `questions_spec.json`; add `spec_review` to the answers and run again.
   * Gate refused: the refusal is recorded; with an `intent` answer the stated task runs, labelled.
5. The robot run and render follow. The correction runs once `correction` is answered (or rehearse one with
   `--rehearse-correction <id>`, labelled as a rehearsal).

Wall time on this sandbox (4 shared cores, two other agents running, D1 replay): watch stages about 6 minutes
of compute, robot stages under 1 minute plus about 2 minutes per rendered video; record mode adds the model
latency (about 3 minutes of SAM video and 40 s of grounding for a 50 s take). Time spent waiting for the shared
render lock is reported separately (`lock_wait_s`).
