# Study event log, schema `mirror-study-events/1`

One JSON object per line. The studio writes it only when started with
`MIRROR_STUDY_LOG=<path>` (requested from the studio track; not yet emitted by
the studio). `tools/studykit.py validate` checks a log against this schema.

## Fields on every event

| Field | Type | Notes |
|---|---|---|
| `schema` | `"mirror-study-events/1"` | |
| `synthetic` | bool | true only for fixtures |
| `participant` | string `P\d\d` (or `SYN\d\d` for fixtures) | pseudonymous |
| `session` | string | random id per session |
| `seq` | int | strictly increasing within a session |
| `t` | float | seconds, monotonic clock of the studio |
| `event` | string | one of the types below |
| `task` | string or null | `T0`...`T7` |
| `condition` | `mirror` \| `manual` \| `reteach` \| null | |

## Event types

| `event` | Extra fields |
|---|---|
| `session_start` | `app_commit`, `task_packet_sha256`, `consent` {`text`, `audio`, `quote`} |
| `task_start` | `scene_key`, `spec_version_id`, `spec_state` |
| `correction_submitted` | `input` (`text`\|`speech`), `chars`, `text` (only if consent.text) |
| `interpretation_returned` | `outcome` (`apply`\|`asks`\|`rejects`\|`error`), `provenance` (studio values: `recorded`\|`fresh_model_call`\|`offline_interpreter`\|`replayed_cassette`\|`fixture`\|`simulated`), `fallback_reason`, `latency_s` |
| `clarification_shown` | `question_chars` |
| `clarification_answered` | `chars` |
| `manual_edit_saved` | `valid` (bool), `errors` (int) |
| `spec_version_created` | `spec_version_id`, `spec_state`, `source` (`correction`\|`manual`\|`reteach`\|`revert`) |
| `demo_recorded` | `duration_s` |
| `recheck_run` | `rollout_id`, `verdict` |
| `execution_run` | `rollout_id`, `verdict`, `scene_key` |
| `task_end` | `reason` (`participant_done`\|`time_limit`\|`gave_up`\|`technical_problem`) |
| `rating` | `confidence` (1-7), `effort` (1-7), `comment` (only if consent.quote) |
| `facilitator_note` | `kind` (`explanation`\|`technical`\|`other`) |
| `session_end` | |

`spec_state` is the specification's semantic state as defined by
`mirror.evaluate.protocol_v3.edits.semantic_state` (enforced rules with
parameters, confirmed orderings, rejected goals). `spec_version_id` is the
studio's immutable, generation-scoped version id.
