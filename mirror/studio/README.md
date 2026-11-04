# MIRROR studio

The studio is the interface to one authoring session: demonstration →
interpretation → execution → changed world → correction → changed behaviour →
evidence on fresh layouts.

## Run it

```bash
python -m uvicorn mirror.studio.app:app --port 8000
# open http://127.0.0.1:8000/
```

`MIRROR_STUDIO_BACKEND` selects the backend:

| value | behaviour |
|---|---|
| `auto` (default) | live simulation when `mirror.sim`, `mirror.plan`, `mirror.interpret`, `mirror.evaluate`, `mirror.scenes` and `mirror.pipeline` import cleanly, otherwise fixtures |
| `live` | `mirror.pipeline.Session`; every rollout is executed in MuJoCo and checked from the step log |
| `fixture` | key-framed placeholder motion for interface work; labelled on every page and refused by the static exporter unless asked |

The interpreter reads `MIRROR_LLM_MODE`, `MIRROR_LLM_BASE_URL`, `MIRROR_LLM_MODEL`
and `MODEL_API_KEY` (see `mirror.interpret.LLMClient`). Paired evaluations from
the studio use `MIRROR_STUDIO_EVAL_WORKERS` processes (default 2) and keep their
freeze manifest, step logs and rollouts under `MIRROR_STUDIO_RUNTIME` (default: a
fresh temporary folder).

## Static site

```bash
python scripts/export_static.py --out site                        # reference story live + showcase run on dev seeds 9000-9007
python scripts/export_static.py --out site --evaluation results/eval/final   # load the pre-registered results instead
```

The folder works on any static host (GitHub Pages included): `config.js` switches
the frontend into recorded mode, `data/session.json` is the snapshot,
`data/rollouts/<id>.json` the playback data and `data/provenance.json` records the
backend, interpreter, code hash and every step's outcome.

## API

| method | path | purpose |
|---|---|---|
| GET | `/api/state` | session snapshot (`mirror-studio-session/1`) |
| GET | `/api/rollouts/{id}` | columnar playback payload (`mirror-studio-rollout/1`); `demo` is the demonstration |
| POST | `/api/demo/load` | load the reference demonstration (or record it) |
| POST | `/api/interpret` | job: propose a specification |
| POST | `/api/spec/decisions` | `{decisions: {item_id: confirmed\|rejected}}` → new version |
| POST | `/api/run` | job: plan → execute → check in the current layout |
| POST | `/api/scene` | `{preset}` or `{edit: {tray\|object\|slot\|obstacle\|remove_obstacle}}` |
| POST | `/api/correct` | job: compile a correction; recorded rollouts are re-checked under the new version |
| POST | `/api/recheck` | re-check one rollout under a version |
| POST | `/api/clarify`, `/api/clarify/answer` | job: two candidate behaviours, executed; answer → new version |
| POST | `/api/evaluate` | job: showcase paired run, development split, seeds 9000-9099 only (anything else is refused with 403) |
| POST | `/api/teleop/start\|finish\|cancel` | keyboard teleoperation (live only); steps over the websocket |
| WS | `/ws` | `state` revisions, `job` progress, `teleop` commands and frames |

Long operations run one at a time in a worker thread and report progress.

## Evaluation fence

The studio never generates evaluation-split layouts. Its own paired runs use the
showcase development seeds 9000-9099 and are labelled as such. Pre-registered
results (a single paired report, or the final report with one folder per
stratum) are only loaded, through `--evaluation` or `StudioService.load_evaluation`;
their maps are sketched from each recorded rollout's own geometry instead of
regenerating layouts. A rehearsal report is labelled as a rehearsal.

## What the views show, and what they do not

* The 3D view plays recorded simulator state: rollout frames from the executor, or
  frames sampled from the dense step log. The container's path is coloured by its
  tilt against the rule tolerance. After a correction, the previous behaviour in the
  same layout stays as a ghost: its path, its peak-tilt pose and where its beads
  ended up.
* A demonstration trace records gripper and object poses only, so beads are not
  drawn while a trace plays.
* Tall static walls are drawn translucent so the gripper stays visible.
* Minimaps in the evidence grid are downsampled container paths (about 90 points).
  For loaded reports the map itself is a sketch rebuilt from the rollout's geometry.
* The before/after chart plots the container's tilt from the recorded frames
  (about 200 samples per run); the numbers beside it come from the checker's report.
* The static site stores poses at 1 mm and 0.001 quaternion resolution.
* Before any rollout exists, a layout preview places objects at their initial poses
  and hides beads.
* The fixture backend's motion and bead spill are key-framed, not simulated.


## Public server

Each visitor gets a session (`POST /api/session`; the id goes in the `X-Mirror-Session`
header and as `?session=` on the websocket). Sessions, simulation jobs and model requests
are capped, origins are checked, and `GET /api/health` reports the limits; see
`mirror/studio/sessions.py`. A static export can switch to a live server when its
`config.js` names one (`apiBase`); the switch is kept in the URL as `?live=1`.
Deployment steps and every setting: `deploy/README.md`.


## "Your table": a real phone capture, start to finish

The `Your table` step (top bar, or `?step=table&ts=watch|moments|confirm|task|robot|correct|compare`) shows one
real capture as a guided story: the phone video with the tracked SAM outlines (objects light up while carried) and
event lanes, the moments MIRROR found, what the person confirmed, the task (or the capture gate's refusal, verbatim,
and the person's stated task, labelled as such), the robot run on that layout (3D, a top view over the rectified
table with the person's carry paths, the robot's paths and the phone's position, and the twin films when present),
then one correction: compiled into a scoped rule, the old run re-checked under it, and the robot run again, with a
re-check table, a before/after chart and the plan. A correction no plan can satisfy is shown as a refusal with the
planner's reason; one the compiler cannot apply as written comes back as its question.

Build the data (no model calls; corrections are compiled by the offline rules and run on this machine):

    python -m mirror.studio.real_table d1 --out data/table/D1                       # the D1 development capture
    python -m mirror.studio.real_table pipeline OUT --id <id> --out data/table/<id>  # a mirror.capture.real_run output
    python -m mirror.studio.real_table pipeline OUT --id <id> --out data/table/<id> --site site   # ... and add it to a site
    python -m mirror.studio.real_table pipeline OUT --id <id> --twin TWIN_DIR --feature --out data/table/<id>  # twin films from that run; open first

In Compare, the 3D view has two lenses: the new run (the old one as a grey ghost) and the old run judged under the new
rule (the viewer colours its frames with the checker's re-check report and marks where it broke the rule). A `broken`
verdict in the re-check table is a button: it opens the old run at that violation. Motion settles rather than snaps (one
gliding step rail, eased scroll back to the steps on phones, staggered reveals) and is off under `prefers-reduced-motion`.

Ship it with a site: `scripts/export_static.py ... --table data/table/<id>`, or add it to an existing site with
`scripts/export_static.py --add-tables-to SITE --table data/table/<id>`. `data/table/` is not committed (personal
footage). The live server serves `data/table` (or `MIRROR_TABLE_DIR`) at `/data/table` and adds
`POST /api/table/{id}/correct` (typed, or spoken through the existing push-to-talk) with `GET /api/table/{id}/live/{name}`
for the runs it makes; those use the server's interpreter (a model only when the server has one).

What it does not claim: the outlines and carries come from one camera; positions on the table are approximate; the
robot is simulated on proxies sized from the person's measurements; every label the capture carries (real footage,
one camera, calibration flagged, development clip) is shown on every step.
