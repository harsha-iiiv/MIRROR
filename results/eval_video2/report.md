# Video to specification: sam-3.1 tracking, muse-wonder-1.0 vs muse-spark-1.3 observations, muse-spark-1.3 specification (registration 2)

Status: stopped early: spend cap $14.00: $8.50 spent and $6.80 reserved by running seeds before seed 71016; a seed is projected at $0.66 (reservation x1.5). Design `a41be6f9737fe0b3`, inputs `b0794e98b4eef716`, code `f174076be48d0b84`. Seeds: 71000, 71001, 71002, 71003, 71004, 71005, 71006, 71007, 71008, 71009, 71010, 71011, 71012, 71013, 71014, 71015, 71016, 71017, 71018, 71019. Tracks from: sam-3.1.

## What was measured

Each rendered demonstration was tracked and reconstructed into object positions, grasp and release times and cup tilt, then compared with the simulator's ground truth frame by frame. Both video models watched the same clip once. Muse Spark then proposed a task specification from the video and from the ground-truth recording, and the two were compared.

| measure | result |
|---|---|
| seeds completed | 16 of 16 |
| object found (within 5 cm, visible frames) | 0.886 |
| median position error | 0.72 cm |
| grasp/release detected within 1 s | 0.635 |
| median timing error | 0.65 s |
| median cup tilt error (in-image tilt vs true tilt) | 2.00 deg |
| same proposed specification as from ground truth (pass/fail measure) | 8 of 16 |
| same structure ignoring numeric limits (reported only) | 8 of 16 |

## Muse Wonder vs Muse Spark (video observations)

| model | clips | errors | mean event F1 (0.5 s) | events within 2 s | mean timing error (within 2 s) | relevant objects right | care cue for the cup (expected) | validator issues | median latency | cost |
|---|---|---|---|---|---|---|---|---|---|---|
| muse-wonder-1.0 | 16 | 0 | 0.062 | 24/96 | 0.99 s | 14 | 5 (16) | 0 | 79.0 s | $3.857 |
| muse-spark-1.3 | 16 | 0 | 0.115 | 59/96 | 1.00 s | 16 | 5 (16) | 0 | 41.8 s | $0.763 |

On the 16 seed(s) where both models ran: mean event F1 Wonder 0.062, Spark 0.115; relevant objects right Wonder 14, Spark 16.

Decision rule, stated in advance: use muse-wonder-1.0 for observations only if its mean event F1 is at least 0.05 above muse-spark-1.3 and it gets the relevant-object set right at least as often; otherwise keep muse-spark-1.3. Outcome: keep muse-spark-1.3.

## Pre-stated checks

- found rate >= 0.95: not met
- median centroid error <= 1.5 cm: met
- grasp/release detected within 1 s >= 0.9: not met
- median timing error <= 0.3 s: not met
- median cup tilt error <= 5.0 deg: met
- same proposal signature on >= 18 of 20 seeds: not met

## Every seed

| seed | outcome | found | median cm | events matched | Wonder F1 | Spark F1 | same spec |
|---|---|---|---|---|---|---|---|
| 71000 | completed | 0.983 | 1.09 | 6/6 | 0.00 | 0.00 | no |
| 71001 | completed | 0.933 | 2.13 | 3/6 | 0.00 | 0.17 | yes |
| 71002 | completed | 0.701 | 1.95 | 1/6 | 0.00 | 0.00 | no |
| 71003 | completed | 0.959 | 0.27 | 4/6 | 0.00 | 0.00 | yes |
| 71004 | completed | 0.929 | 0.73 | 2/6 | 0.00 | 0.00 | yes |
| 71005 | completed | 0.971 | 1.54 | 5/6 | 0.00 | 0.00 | yes |
| 71006 | completed | 0.986 | 0.84 | 5/6 | 0.00 | 0.17 | yes |
| 71007 | completed | 0.976 | 0.26 | 5/6 | 0.00 | 0.33 | no |
| 71008 | completed | 0.962 | 1.21 | 4/6 | 0.00 | 0.33 | no |
| 71009 | completed | 0.984 | 0.22 | 4/6 | 0.33 | 0.00 | no |
| 71010 | completed | 0.000 | n/a | 0/6 | 0.00 | 0.00 | no |
| 71011 | completed | 0.978 | 1.50 | 5/6 | 0.00 | 0.33 | no |
| 71012 | completed | 0.985 | 0.26 | 5/6 | 0.00 | 0.17 | yes |
| 71013 | completed | 0.842 | 0.99 | 2/6 | 0.33 | 0.00 | yes |
| 71014 | completed | 0.990 | 0.18 | 5/6 | 0.00 | 0.17 | yes |
| 71015 | completed | 0.991 | 0.39 | 5/6 | 0.33 | 0.17 | no |

## How the run went

- Seeds in parallel: 8; spend reservation per running seed: x1.5 of the projected seed cost.
- Attempts of this run: 1 (restarts: 0). Seeds taken from checkpoints of earlier attempts: none. Seeds that had started without finishing and were run again: none (their earlier partial calls are included in the spend).
- Arguments passed to observe(): {"strategy": "video"}.
- HTTP responses retried by the client (429 = rate limit): {}.

**Stopped early.** spend cap $14.00: $8.50 spent and $6.80 reserved by running seeds before seed 71016; a seed is projected at $0.66 (reservation x1.5). Seeds not run: 71016, 71017, 71018, 71019.

## Every numeric difference between the two proposals (reported only)

- seed 71007: upright (transport) on {"kind": "cup"}: max_tilt_deg = 20.0 from video vs 15.0 from the ground-truth trace

## Spend

| model | network calls | replayed | input tokens | output tokens | median latency | cost |
|---|---|---|---|---|---|---|
| muse-spark-1.3 | 172 | 0 | 620301 | 457151 | 8.3 s | $2.705 |
| files | 94 | 0 | 0 | 0 | 3.5 s | $0.000 |
| sam-3.1 | 94 | 0 | 0 | 0 | 13.1 s | $3.879 |
| muse-wonder-1.0 | 16 | 0 | 263344 | 140188 | 75.2 s | $3.857 |

Total $10.441 at list prices (dev.meta.ai/docs/pricing-rate-limits and dev.meta.ai/products/meta-model-api, read 2026-10-31). Budget lines: {"muse-spark-1.3:grounding": {"used": 124, "cap": null}, "muse-spark-1.3:video": {"used": 16, "cap": null}, "muse-wonder-1.0": {"used": 16, "cap": null}, "sam-3.1": {"used": 94, "cap": null}}. Every attempt is in ledger.jsonl (119 entries).
