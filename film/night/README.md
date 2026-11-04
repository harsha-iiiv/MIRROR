# Night film (3-4 Nov 2026): "corrections you can check" on the owner's own table

A 74 s film and four 8 s loops built only from recorded outputs of development clip D1 (IMG_0815, the owner's phone,
one camera, calibration reuse flagged, never scored). No model was called to build them.

| Output | Size | Content |
|---|---|---|
| `mirror_night_film_v3.mp4` | 1920x1080, 30 fps, 74 s | 9 shots below |
| `loop_watch.mp4`, `loop_run.mp4`, `loop_rejudge.mp4`, `loop_sentence.mp4` | 1280x720, 8 s | README/site loops |

Shots: title over the footage (board + sync card) · SAM 3.1 outlines on the footage with the event timeline on the take's own
clock · the gate refusing task inference (recalibration_required, focal check +7.1 %) and the task as the person stated it ·
the recorded MuJoCo rollout on the D1 layout (three.js, recorded poses only, 6x replay) · the twin track's overlay of the same
run from the phone's viewpoint · one sentence compiled into one scoped rule · the old run re-judged from its step log
(closest 4.52 cm, violated) and the re-plan (closest 6.33 cm, satisfied) · every rule before/after plus three other sentences
(already satisfied / a question / refused) · end card.

## Sources (all real, all recomputable)
- `make_correction.py`: re-runs the D1 phase-2 scene and person-stated spec exactly as phase 2 did (planner default config,
  gripper backend), asserts it reproduces the phase-2 report, then compiles the four candidate sentences with the offline rule
  interpreter, re-checks the old rollout from its step log and plans/executes the new spec. 0 model calls, about 25 s.
- `export_assets.py`: footage proxy (720x1280 H.264), SAM outlines per frame from the recorded tracks, rollouts as poses,
  `data.json` (timeline, gate, spec, correction records, the numbers quoted on screen, twin registration).
- `remotion/`: the Remotion + three.js project (Remotion 4.0.540, three 0.169, @react-three/fiber 8). Proxy colours are
  neutralised in the 3-D view; geometry and poses are the recorded ones.
- `check_film.py`: contact sheet + motion check (ORB similarity scale change < 1 %/frame; text-zone flow p99 < 20 px/frame;
  loop boundary).

## One command (D1 or tomorrow's take)
```
film/night/make_film.sh --d1 --loops --out <dir> [--twin <twin>/D1] [--spark spark.json] [--gate-note "..."]
film/night/make_film.sh --pipeline <mirror.capture.real_run out dir> --loops --out <dir>     # twin picked up from <dir>/twin
```
Everything the film says that depends on the take (object names, the gate card, the spec lines, the rule table, the source
windows of the footage, the top-view fit, the 3-D camera targets) is computed by `export_assets.py` into `data.json`
(`copy`, `segments`, `events`); the Remotion code only draws it. The twin shot is dropped when no twin overlay exists.
The film's correction is the first suggested correction that adds a clearance rule and whose old run re-judges as violated
(else the first one that applies); the others appear in the rule table's side list with their real outcome.

## Rebuild (step by step)
```
python film/night/make_correction.py --phase2 <phone_real>/out/phase2 --out <corr>
python film/night/export_assets.py --capture <D1 real-demo> --phase2 <phase2> --corr <corr> --take IMG_0815.MOV \
    --twin <twin>/D1 --out <remotion>/public
cd <remotion> && npm install && bash setup_fonts.sh && npx remotion bundle src/index.ts --out-dir build
bash render.sh Film film.mp4 ; bash render.sh LoopRun loop_run.mp4 --scale=0.6667
python film/night/check_film.py film.mp4 --shots film/night/shots_film.json --out <check>
```
Render with `--gl=swangle` (no GPU in the sandbox), concurrency 2, under the shared render lock.

## What is approximate (also on screen)
One camera; calibration reuse flagged (focal check +7.1 %, the phone was moved afterwards); simulation proxies sized from ruler
measurements (a 4 cm cube stands in for the 11 cm carton, an upright cylinder for the lying marker); layout centred on the
simulator table by software; the twin overlay's simulated tray sits 3.3 cm / 3.6 deg from the real tray outline. The
correction sentences were chosen for the film from the D1 page's candidates; the owner has not chosen one.
