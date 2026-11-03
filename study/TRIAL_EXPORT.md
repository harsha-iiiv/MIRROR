# Trial export, schema `mirror-trial/1`

One JSON object per **attempt** (one submitted correction or one saved manual
edit), written as JSON lines, plus a CSV flattening and a readable summary.
It covers every field the owner's milestone M2 section 4 asks to retain. The
studio emits it (requested; field names follow the studio API: generation,
spec version and rollout ids from CHALLENGE_API section 2, provenance values
from section 3). `study/tools/pilotkit.py validate` checks it; the pilot
analysis reads only this format.

Records are written only for consented sessions (`consent.recorded` true).
No secrets, no IP addresses, no user agents, no names. Correction text is kept
only when `consent.text` is true; audio only when `consent.audio` is true, under
the retention in `study/DATA.md`.

## Fields

| Field | Type | Notes |
|---|---|---|
| `format` | `"mirror-trial/1"` | |
| `trial_id` | string | random, one per attempt |
| `participant` | `P\d\d` \| `DEV` \| `SYN\d\d` | anonymous; `DEV` = developer smoke test; `SYN` = synthetic fixture |
| `synthetic` | bool | true only for fixtures |
| `context` | `dev_smoke` \| `usability_pilot` \| `frozen_benchmark` | kept separate in every analysis |
| `task_origin` | `prepared` \| `developer_written` \| `independently_authored` | who wrote the task the attempt answers |
| `task` | string \| null | task card id (`C1`...) |
| `condition` | `mirror` \| `manual` | |
| `attempt` | int | 1-based, per participant x task |
| `started_at`, `ended_at` | float | seconds (monotonic clock of the session) |
| `task_started_at` | float | when the task card was opened |
| `versions` | object | `code_commit`, `server_version`, `prompt_revision`, `model`, `interpreter` |
| `scene` | object | `family`, `split`, `seed`, `edits`, `scene_id`, `content_sha256` |
| `lineage` | object | `generation_id`, `demo_id`, `demo_sha256`, `base_spec_version_id`, `revised_spec_version_id` (null if nothing was committed) |
| `input` | object | `kind` (`typed`\|`spoken`\|`spoken_edited`\|`manual_form`), `chars`, `text` (consent only), `transcript_approved` (spoken only) |
| `proposal` | object | `outcome` (`apply`\|`asks`\|`rejects`\|`error`), `ops`, `scope`, `question`, `rejected`, `validation` {`passed`, `errors`}, `provenance`, `fallback_reason`, `latency_s`, `cost_usd` (null if unknown) |
| `clarification` | object \| null | `question`, `answered` (bool), `useful` (participant rating 1-5 or null) |
| `review` | object | `accepted` (bool), `edited_before_accept` (bool) |
| `spec_state_before`, `spec_state_after` | object | `mirror.evaluate.protocol_v3.edits.semantic_state` of the spec before and after this attempt |
| `recheck` | object \| null | old behaviour under the revised spec: `rollout_id`, `status`, `violations`, `provenance` |
| `execution` | object \| null | new run: `rollout_id`, `status`, `violations`, `proven_infeasible` (bool, only with a valid certificate), `provenance` (`simulated`\|`recorded`\|`fixture`) |
| `failure_reason` | string \| null | timeout, error, budget, participant gave up |
| `comprehension` | object \| null | answer to "was this result computed now or recorded?": `answer` (`fresh`\|`recorded`\|`unsure`), `truth` (the provenance shown) |

## Readable summary

`pilotkit.py analyze` writes the per-person table and failures in Markdown;
`pilotkit.py export-csv` flattens records to CSV (one row per attempt, text
columns dropped unless `--keep-text`).
