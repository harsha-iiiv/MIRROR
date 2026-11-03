# MIRROR studio API: sessions, lineage, provenance, Challenge mode, trials and phone capture

Status: STABLE (v0.3). Changes after this point are additive only. Every endpoint and field of
v0.2 keeps working; new fields are optional for clients.

## 1. Sessions and capability tokens

`POST /api/session` returns

```json
{"session": "<public id>", "token": "<secret>", "mode": "live|fixture", "idle_expiry_s": 1800,
 "generation_id": "g...", "token_required": true, "limits": {"uploads": {...}}}
```

* Send `X-Mirror-Session` and `X-Mirror-Token` on every session request; the websocket and media GETs
  take `?session=&token=`. The server keeps only the token's SHA-256. Never put the token in a share URL.
* `MIRROR_REQUIRE_TOKEN=0` restores the old session-id-only behaviour (not recommended).
* The Origin check only stops other web sites from using a visitor's browser. It is not authentication:
  any HTTP client can omit `Origin`. Abuse is bounded per client address (section 8) and globally.
* With `MIRROR_STUDIO_STATE=<dir>` sessions are persisted and restored after a server restart on first
  use (same id and token). Jobs queued or running at the restart come back as `status: error`,
  `error_code: interrupted`; nothing from them is kept. An idle-expired session's state is deleted.

Errors are always `{"error": code, "detail": text, ...}`:

| status | error | meaning |
|---|---|---|
| 401 | `token_required`, `token_invalid` | missing or wrong token |
| 429 | `rate_limited` | per-address admission limit; `Retry-After` set |
| 503 | `server_busy` | every session slot is busy; `Retry-After` set |
| 409 | `stale_generation` | the request named an old generation; body has `current_generation` |
| 403 | `evaluation_fenced` | seed belongs to a frozen evaluation or is outside the development range |
| 411 / 413 / 507 | `length_required` / `too_large` / `storage_full` | upload bounds |
| 422 | `invalid_video`, `invalid_study`, `invalid_capture` | validation failures (detail says why) |
| 503 | `uploads_unavailable` | ffprobe/ffmpeg missing: uploads are refused, never accepted unchecked |
| 410 | `record_expired` | a shared record was evicted from the bounded store |
| 409 | `session_busy`, `conflict`, `job_cancelled` | |

## 2. Immutable identities

A **generation** is one demonstration and everything derived from it. Loading, recording, teleoperating,
processing a video, accepting a phone capture, or re-interpreting starts a new generation; the old one is
archived unchanged and stays resolvable.

| identity | form |
|---|---|
| generation | `g<hex ms><hex4>`; never reused, also across restarts |
| demo | `demo_id` + `content_sha256` (canonical DemoTrace JSON) |
| spec version | `<generation>.v<n>` |
| rollout | `<generation>.v<n>.<scene key>.<r\|c><seq>.<hash8>`; scene key `s<seed>` (dev), `b<seed>` (blocked), `e<seed>` (eval) or `h<scene hash8>`; seq per generation; hash8 = content hash prefix |
| demo playback | `demo.<generation>.<demo hash8>`; `/api/rollouts/demo` is an alias of the current one |
| showcase evaluation rollouts | `es<hex6>-<seed><a\|b>` (new prefix per run); loaded pre-registered results keep `e<seed><a\|b>` |

Payloads under these ids never change, so caches may keep them forever. `label` (`r001`) is display only.
Rollout payloads (`/api/rollouts/{id}`) carry `generation_id`, `spec_version_id`, `content_sha256`,
`scene_sha256`, `label`, `kind`; archived rollouts resolve too.

Snapshot additions: `generation` (id, reason, parent, demo {demo_id, content_sha256, source, playback_id},
source_media [{id, view/role, sha256, bytes, duration, width, height, fps, frames, decoded_frames}],
calibration), `archive` (last 10 generations: ids, spec_version_ids, rollout_ids), `specs[].version_id`,
`specs[].content_sha256`, `rollouts[].{generation_id, spec_version_id, content_sha256, label}`,
`rechecks[].{spec_version_id, generation_id, cross_generation}`, `challenges`, `study`, `captures`.

* Mutating requests may send `X-Mirror-Generation`; a mismatch answers 409 `stale_generation` and
  changes nothing.
* Jobs carry `generation_id`. A job queued under an older generation fails with `error_code:
  stale_generation` instead of running against the new demonstration. Clients must drop any job result,
  payload or state whose `generation_id` differs from the latest snapshot's `generation.id`.
* `POST /api/recheck {rollout_id, spec_version?, spec_version_id?}`: an integer version resolves inside
  the rollout's own generation; `spec_version_id` may name another generation (`cross_generation: true`).
* `POST /api/jobs/{id}/cancel`: a queued job is cancelled at once; a running one at its next step
  boundary (finished simulation stays). Status `cancelled`. Jobs past `MIRROR_JOB_TIMEOUT_S` (900) are
  reported `error_code: timeout` and their late result is discarded.

## 3. Provenance

Two fields. `provenance` (string, unchanged from v0.2): `recorded | fresh_model_call |
offline_interpreter | replayed_cassette | fixture | simulated`.

`step_provenance` (object) is on every step response of the test flow: interpret, correct, answer,
run, recheck items, challenge create (`preparation[]`, `before`), challenge correction, review rechecks,
challenge execute (`after`), video process (`stages[]`), voice transcripts, manual edits, capture
proposals, and every Models-panel entry.

```json
{"kind": "fresh_model|replayed_cassette|recorded_model|offline_fallback|new_simulation|recorded_playback|new_check|fixture|manual",
 "label": "Fresh model inference (called now)",
 "model": "muse-spark-1.3|null", "interpreter": "muse-spark-1.3|offline-rules|manual-form|null",
 "interpreter_version": {"prompt_revision": "3", "code_version": "..."},
 "request_status": "ok|fallback|error",
 "latency_s": 1.23, "latency_source": "measured_now|recorded_with_cassette|null",
 "tokens": 1234, "cost_usd": null, "cost_note": null,
 "fallback_reason": "why the offline interpreter was used, or null",
 "simulator": "MuJoCo (simulation steps only)", "checker": "(recheck steps only)", "rollout_provenance": "(recheck)"}
```

Rules: `fresh_model` only when a model request was made in this request; a replayed cassette never is.
`offline_fallback` always has a `fallback_reason` (configured offline mode, missing recording, budget,
or the model error). Latency is `measured_now` only for work done now; replayed responses report the
latency recorded with the cassette or null. `cost_usd` is null unless `MIRROR_LLM_USD_PER_1K_TOKENS` is
set (then labelled an estimate). Static exports and the tour are `recorded_model` / `recorded_playback`.

## 4. Challenge mode

Challenge scenes come only from development scene families: seeds 0-9999 that no frozen evaluation names
(every `results/*/freeze.json` plus registration `design.json`/`inputs.json`; seeds 1 and 7 are fenced
conservatively because a frozen design names them). Anything else: 403 `evaluation_fenced`.

* `GET /api/challenge/options` → families (`mixed`, `shortcut`, `blocked`, `reference` presets), edit
  schema, `max_edits` 8, `correction.supported` (the bounded rule language), fence count and sources,
  `tour`.
* `POST /api/challenge {family, seed | preset, edits}` → job. Loads/interprets the reference
  demonstration if needed (`preparation[]` with provenance), builds the scene, executes the current
  specification there. Result `{challenge_id, before_rollout_id, status, step_provenance, preparation}`.
* `GET /api/challenge/{cid}` → `{id, state, generation_id, scene {family, split, seed, edits, scene_id,
  content_sha256, scene}, base_spec_version_id, before, proposal, revised_spec_version_id, rechecks,
  after, record_id}`. States: `preparing → scene_ready → proposed → reviewed → executed → published`.
* `POST /api/challenge/{cid}/correction {text | transcript_id}` → job; nothing is committed. Result
  `proposal {text, input typed|spoken|spoken_edited, changed, ops, diff {added, changed, removed},
  scope {objects, phases, kinds, items, unaffected_objects}, question, options, rejected, provenance,
  step_provenance, fallback_reason, trial_id}`.
* `POST /api/challenge/{cid}/review {accept, note}` → accept commits the version (409 if the spec moved)
  and re-checks the prior behaviour and every rollout of the generation from step logs
  (`rechecks[]` with `is_before`, `was`, `status`, `violations`, `step_provenance` kind `new_check`).
* `POST /api/challenge/{cid}/execute` → job; result `{after_rollout_id, status, step_provenance}`.
* `POST /api/challenge/{cid}/publish` → `{record_id, share_path: "?challenge=<record_id>"}`.
* `GET /api/records/{record_id}` and `/api/records/{record_id}/rollouts/{rollout_id}` need no session.
  `record_id` = first 16 hex of the SHA-256 of the canonical record, so a URL resolves only to that
  exact evidence. Records survive session expiry and restarts (bounded: `MIRROR_RECORDS_MAX` 200,
  `MIRROR_RECORDS_MAX_MB` 500; evicted ids answer 410, never reused).
* Recorded tour: `GET /api/tour`, `GET /api/tour/rollouts/{id}` (static export: `data/tour/record.json`,
  `data/tour/rollouts/<id>.json`). `provenance.recorded = true`, `label` says nothing runs when watched.
  v1: shortcut dev seed 4242, offline interpreter, MuJoCo; before tilted the cup 89.6° through the slot
  and kept 5/8 beads; recheck under the correction: constraint violated; after: around the wall, 8/8;
  transfer seeds 4243-4245: base routes violate under the revised rule, revised runs succeed.

## 5. Study mode and trial export (consented trials only)

* `POST /api/study/start {participant P01-P99|DEV, context dev_smoke|usability_pilot, task, task_origin
  prepared|developer_written|independently_authored, condition mirror|manual, consent {recorded, text,
  audio, quote}, task_packet_sha256}`. Refused without `consent.recorded`; `frozen_benchmark` never runs
  in the studio; `DEV` only in `dev_smoke`.
* `POST /api/study/task {task, task_origin, condition}`, `POST /api/study/end {reason}`,
  `POST /api/study/comprehension {trial_id, answer fresh|recorded|unsure}`,
  `POST /api/study/clarification {trial_id, answered, useful 1-5}`.
* Each challenge correction (and each manual edit) is one attempt, written to
  `MIRROR_TRIAL_STORE/<context>/<trial_id>.json` in schema `mirror-trial/1` (study/TRIAL_EXPORT.md),
  updated through review, recheck and execution. Events (`mirror-study-events/1`) go to
  `MIRROR_STUDY_LOG` when set. No names, addresses or user agents; text only with `consent.text`;
  audio not stored unless `consent.audio` and `MIRROR_STUDY_AUDIO_RETENTION_DAYS > 0`.
* Export: `python -m mirror.studio.trials export --store <dir> --out <dir> [--context ...] [--keep-text]`
  → `trials.jsonl`, `trials.csv`, `summary.md`; `purge-audio` deletes audio past its retention.
* Manual condition: `POST /api/spec/manual {edits, note}` (actions `set_rule`, `set_status`,
  `remove_rule`, `add_ordering`, `remove_ordering`; see mirror/studio/manual.py) → one new version,
  `step_provenance.kind = manual`; invalid edits answer 409 and are recorded as `outcome: error`.

## 6. Real-phone capture

Uploads accept `view` = `top | side | calibration | single` (same validation as section 7).

* `POST /api/capture {mode two_view|single_view, notes}` → capture (`observability` states what a single
  view cannot observe).
* `POST /api/capture/{cid}/clips {clips {role: upload id}, sync {method clap|flash|visual_marker|manual,
  top_t, side_t}}`: two_view needs top + side + calibration and a declared sync event inside both clips;
  clips whose lengths differ by more than 3 s (beyond the sync offset) are refused as separate takes.
  `sync.status` is `declared`; the studio never calls it verified.
* `POST /api/capture/{cid}/calibrate`, `/track` → jobs calling `mirror.perception.phone.calibrate` /
  `.track` (phone track). A failed calibration returns the capture to `clips_ready` with the reason.
  Missing module → 409 "not available on this server".
* `POST /api/capture/{cid}/review {correspondence [{track_id, proxy cup|block|marker|null, label,
  dimensions_m, dimensions_source, unsupported_features, assumed {mass_kg, friction, contents}}],
  events [{track_id, type grasp|release|carry_start|carry_end|place, t_start, t_end, status
  confirmed|rejected|uncertain|edited}]}`: append-only history; events are labelled human-hand
  inferred intervals, not robot contact.
* `POST /api/capture/{cid}/propose` → job: `reconstruct(capture, review)` → new generation
  (`reason: phone_capture`, source media with roles and hashes, calibration attached) → proposal.

## 7. Upload bounds (defaults; environment variables in deploy/mirror.env.example)

Accepted only after ffprobe reads a known, bounded video **and** ffmpeg decodes every frame without error:
40 MB per file, 120 MB and 6 files per session, 1 GB across the server, 0.5-90 s, sides 64-1920 px,
at most 1920x1080 pixels per frame, 1-60 fps, at most 5400 frames, probe 15 s, decode 60 s,
1.5 GB address space per tool, unused uploads expire after 30 min. Rejected files are deleted at once.

## 8. Admission limits per client address (defaults)

12 new sessions/hour, 2 live sessions at once (a third replaces that network's own idle session),
12 uploads and 200 MB/hour, 120 jobs/hour, 24 model requests/hour, 300 API requests/minute.
A newcomer may displace only a session idle for 5 minutes (`MIRROR_EVICT_MIN_IDLE_S`).
`MIRROR_TRUST_PROXY=1` takes the address from the right-most `X-Forwarded-For` entry (only behind your
own proxy). An address is a coarse identity; global session, job and model budgets remain the ceiling.
These are controlled-demo settings, not measured public-load capacity.
