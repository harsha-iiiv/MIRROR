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
python scripts/export_static.py --out site --seeds 8              # runs the reference story live
python scripts/export_static.py --out site --evaluation results/final   # reuse a saved evaluation report
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
| POST | `/api/evaluate` | job: freeze, then paired evaluation on fresh layouts |
| POST | `/api/teleop/start\|finish\|cancel` | keyboard teleoperation (live only); steps over the websocket |
| WS | `/ws` | `state` revisions, `job` progress, `teleop` commands and frames |

Long operations run one at a time in a worker thread and report progress.

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
* Before any rollout exists, a layout preview places objects at their initial poses
  and hides beads.
* The fixture backend's motion and bead spill are key-framed, not simulated.
