# Spoken corrections: muse-voice-transcribe-1.0, then muse-spark-1.3, against the text baseline

Status: completed. Design `42740c196986b517`, inputs `6fe77f5df640ab24`, code `24fef070c1fb5c3a`.

Speech used: Test audio synthesised by a non-Meta speech engine (ElevenLabs eleven_v4_turbo, two premade voices). It is clean synthetic speech, not human recordings; results on real speakers may differ.

## What was measured

Each recorded sentence was transcribed once by the speech model. The transcript then went through the same correction compiler as the sentence typed as text, and both results were scored against the expected change to the task. Word error rate compares the transcript with the sentence after the same normalisation (case, punctuation, small numbers spelled out).

| condition / voice | recordings | word error rate (corpus) | median per sentence | right from audio | right from text | text right, audio wrong | audio right, text wrong | McNemar p | transcription failures |
|---|---|---|---|---|---|---|---|---|---|
| clean/all | 144 | 0.030 | 0.000 | 141 | 142 | 2 | 1 | 1 | 0 |
| clean/voice_a | 72 | 0.033 | 0.000 | 72 | 71 | 0 | 1 | 1 | 0 |
| clean/voice_b | 72 | 0.027 | 0.000 | 69 | 71 | 2 | 0 | 0.5 | 0 |
| noise15db/all | 144 | 0.022 | 0.000 | 140 | 142 | 2 | 0 | 0.5 | 0 |
| noise15db/voice_a | 72 | 0.022 | 0.000 | 70 | 71 | 1 | 0 | 1 | 0 |
| noise15db/voice_b | 72 | 0.022 | 0.000 | 70 | 71 | 1 | 0 | 1 | 0 |

## Pre-stated checks

- corpus WER clean <= 0.05: met
- audio exact >= text exact - 5 pp (clean): met
- corpus WER noise15db <= 0.1: met
- audio exact >= text exact - 10 pp (noise15db): met

Hypotheses (written in the design before any transcription):

- Clean synthetic speech: corpus WER <= 5% and exact operation match from audio within 5 percentage points of the text baseline.
- Pink noise at 15 dB SNR: corpus WER <= 10% and exact match within 10 points of the text baseline.

## Right as text, wrong from audio (4)

- s05, voice_b, clean: word error rate 0.00. Said: "Every object must stay upright while carried." Heard: "Every object must stay upright while carried.." Result: apply.
- h02, voice_b, clean: word error rate 0.00. Said: "The cup must be upright at all times." Heard: "The cup must be upright at all times.." Result: apply.
- hs01, voice_a, noise15db: word error rate 0.00. Said: "The pen should stay vertical while you carry it, the cup too." Heard: "The pen should stay vertical while you carry it. The cup too." Result: asks.
- hs01, voice_b, noise15db: word error rate 0.00. Said: "The pen should stay vertical while you carry it, the cup too." Heard: "The pen should stay vertical while you carry it. The cup too." Result: asks.

## Spend

| model | network calls | replayed | input tokens | output tokens | median latency | cost |
|---|---|---|---|---|---|---|
| muse-voice-transcribe-1.0 | 288 | 0 | 0 | 0 | 1.0 s | $0.047 |
| muse-spark-1.3 | 148 | 212 | 325118 | 292459 | 6.4 s | $1.649 |

Total $1.696 at list prices (dev.meta.ai/docs/pricing-rate-limits and dev.meta.ai/products/meta-model-api, read 2026-10-31).

## Every recording

| sentence | voice | condition | WER | heard | from audio | from text |
|---|---|---|---|---|---|---|
| p01 | voice_a | clean | 0.08 | This container container must remain upright throughout transport, not just at the destination. | apply (right) | apply (right) |
| p01 | voice_a | noise15db | 0.00 | this container must remain upright throughout transport. Not just at the destination.. | apply (right) | apply (right) |
| p01 | voice_b | clean | 0.00 | This container must remain upright throughout transport, not just at the destination. | apply (right) | apply (right) |
| p01 | voice_b | noise15db | 0.00 | This container must remain upright throughout transport. Not just at the destination. | apply (right) | apply (right) |
| p02 | voice_a | clean | 0.00 | Keep the cup upright the whole time you are carrying it,. | apply (right) | apply (right) |
| p02 | voice_a | noise15db | 0.00 | Keep the cup upright the whole time you are carrying it.. | apply (right) | apply (right) |
| p02 | voice_b | clean | 0.00 | Keep the cup upright the whole time you are carrying it.. | apply (right) | apply (right) |
| p02 | voice_b | noise15db | 0.00 | Keep the cup upright the whole time you are carrying it.. | apply (right) | apply (right) |
| p03 | voice_a | clean | 0.00 | The cup has to stay vertical while it's being moved, not only when it's put down.. | apply (right) | apply (right) |
| p03 | voice_a | noise15db | 0.00 | The cup has to stay vertical while it's being moved, not only when it's put down. | apply (right) | apply (right) |
| p03 | voice_b | clean | 0.06 | The cup has to stay vertical while it's being moved, moved, not only when it's put down.. | apply (right) | apply (right) |
| p03 | voice_b | noise15db | 0.06 | The cup has to to stay vertical while it's being moved. not only when it's put down. | apply (right) | apply (right) |
| p04 | voice_a | clean | 0.10 | Don't tilt the the cup on the way to the tray... | apply (right) | apply (right) |
| p04 | voice_a | noise15db | 0.00 | Don't tilt the cup on the way to the tray. | apply (right) | apply (right) |
| p04 | voice_b | clean | 0.00 | don't tilt the cup on the way to the tray.. | apply (right) | apply (right) |
| p04 | voice_b | noise15db | 0.00 | Don't tilt the cup on the way to the tray. | apply (right) | apply (right) |
| p05 | voice_a | clean | 0.10 | The beads must not spill while the cup is carried.ed. | apply (right) | apply (right) |
| p05 | voice_a | noise15db | 0.00 | The beads must not spill while the cup is carried. | apply (right) | apply (right) |
| p05 | voice_b | clean | 0.00 | The beads must not spill while the cup is carried. | apply (right) | apply (right) |
| p05 | voice_b | noise15db | 0.10 | The beads must must not spill while the cup is carried. | apply (right) | apply (right) |
| p06 | voice_a | clean | 0.00 | While carrying the mug, keep it upright. | apply (right) | apply (right) |
| p06 | voice_a | noise15db | 0.00 | While carrying the mug, keep it upright | apply (right) | apply (right) |
| p06 | voice_b | clean | 0.00 | While carrying the mug, keep it upright.. | apply (right) | apply (right) |
| p06 | voice_b | noise15db | 0.00 | While carrying the mug, keep it upright. | apply (right) | apply (right) |
| p07 | voice_a | clean | 0.11 | Carrying the cup on its side is not okay.. | apply (right) | apply (right) |
| p07 | voice_a | noise15db | 0.11 | carrying the cup on its side is not okay. | apply (right) | apply (right) |
| p07 | voice_b | clean | 0.11 | Carrying the cup on its side is not okay.. | apply (right) | apply (right) |
| p07 | voice_b | noise15db | 0.11 | Carrying the cup on its side is not okay. | apply (right) | apply (right) |
| p08 | voice_a | clean | 0.00 | The cup should never be on its side during transport. | apply (right) | apply (right) |
| p08 | voice_a | noise15db | 0.00 | The cup should never be on its side during transport. | apply (right) | apply (right) |
| p08 | voice_b | clean | 0.00 | The cup should never be on its side during transport. | apply (right) | apply (right) |
| p08 | voice_b | noise15db | 0.00 | The cup should never be on its side during transport. | apply (right) | apply (right) |
| s01 | voice_a | clean | 0.00 | Only the cup matters, the marker can lie down. | apply (right) | apply (right) |
| s01 | voice_a | noise15db | 0.11 | only the cup cup matters. The marker can lie down. | apply (right) | apply (right) |
| s01 | voice_b | clean | 0.11 | Only the cup cup matters. The marker can lie down. | apply (right) | apply (right) |
| s01 | voice_b | noise15db | 0.00 | Only the cup matters. The marker can lie down.. | apply (right) | apply (right) |
| s02 | voice_a | clean | 0.00 | The cup must stay upright while carried. The block and the marker can tilt.. | apply (right) | apply (right) |
| s02 | voice_a | noise15db | 0.00 | The cup must stay upright while carried. The block and the marker can tilt. | apply (right) | apply (right) |
| s02 | voice_b | clean | 0.07 | The cup must must stay upright while carried. The block and the marker can tilt.. | apply (right) | apply (right) |
| s02 | voice_b | noise15db | 0.00 | The cup must stay upright while carried. The block and the marker can tilt. | apply (right) | apply (right) |
| s03 | voice_a | clean | 0.14 | Keep the marker marker upright while carrying it. | apply (right) | apply (right) |
| s03 | voice_a | noise15db | 0.14 | Keep the marker marker upright while carrying it.. | apply (right) | apply (right) |
| s03 | voice_b | clean | 0.00 | Keep the marker upright while carrying it.. | apply (right) | apply (right) |
| s03 | voice_b | noise15db | 0.00 | Keep the marker upright while carrying it.. | apply (right) | apply (right) |
| s04 | voice_a | clean | 0.00 | The block and the cup both need to stay upright during transport. | apply (right) | apply (right) |
| s04 | voice_a | noise15db | 0.00 | The block and the cup both need to stay upright during transport. | apply (right) | apply (right) |
| s04 | voice_b | clean | 0.00 | The block and the cup both need to stay upright during transport. | apply (right) | apply (right) |
| s04 | voice_b | noise15db | 0.00 | The block and the cup both need to stay upright during transport. | apply (right) | apply (right) |
| s05 | voice_a | clean | 0.00 | Every object must stay upright while carried. | apply (right) | apply (right) |
| s05 | voice_a | noise15db | 0.00 | Every object must stay upright while carried. | apply (right) | apply (right) |
| s05 | voice_b | clean | 0.00 | Every object must stay upright while carried.. | apply | apply (right) |
| s05 | voice_b | noise15db | 0.00 | Every object must stay upright while carried. | apply (right) | apply (right) |
| s06 | voice_a | clean | 0.00 | The marker must end standing up in the tray. | apply (right) | apply (right) |
| s06 | voice_a | noise15db | 0.00 | The marker must end standing up in the tray..: | apply (right) | apply (right) |
| s06 | voice_b | clean | 0.11 | The marker must must end standing up in the tray. | apply (right) | apply (right) |
| s06 | voice_b | noise15db | 0.11 | The marker must must end standing up in the tray. | apply (right) | apply (right) |
| h01 | voice_a | clean | 0.00 | The cup only needs to be upright when you set it down.. | apply (right) | apply (right) |
| h01 | voice_a | noise15db | 0.00 | The cup only needs to be upright when you set it down.. | apply (right) | apply (right) |
| h01 | voice_b | clean | 0.08 | The cup only needs to be upright when you set it down. down. | apply (right) | apply (right) |
| h01 | voice_b | noise15db | 0.00 | The cup only needs to be upright when you set it down. | apply (right) | apply (right) |
| h02 | voice_a | clean | 0.00 | The cup must be upright at all times. | apply (right) | apply (right) |
| h02 | voice_a | noise15db | 0.00 | The cup must be upright at all times. | apply (right) | apply (right) |
| h02 | voice_b | clean | 0.00 | The cup must be upright at all times.. | apply | apply (right) |
| h02 | voice_b | noise15db | 0.00 | The cup must be upright at all times. | apply (right) | apply (right) |
| h03 | voice_a | clean | 0.00 | Keep the marker upright when you set it down. | apply (right) | apply (right) |
| h03 | voice_a | noise15db | 0.00 | Keep the marker upright when you set it down. | apply (right) | apply (right) |
| h03 | voice_b | clean | 0.00 | Keep the marker upright when you set it down.. | apply (right) | apply (right) |
| h03 | voice_b | noise15db | 0.00 | Keep the marker upright when you set it down.. | apply (right) | apply (right) |
| h04 | voice_a | clean | 0.00 | The cup must stay upright while carrying it and when placing it. | apply (right) | apply (right) |
| h04 | voice_a | noise15db | 0.08 | The cup must must stay upright while carrying it and when placing it. | apply (right) | apply (right) |
| h04 | voice_b | clean | 0.00 | The cup must stay upright while carrying it and when placing it.. | apply (right) | apply (right) |
| h04 | voice_b | noise15db | 0.00 | The cup must stay upright while carrying it and when placing it.. | apply (right) | apply (right) |
| t01 | voice_a | clean | 0.00 | Keep the cup within 30 degrees of upright while carrying it. | apply (right) | apply (right) |
| t01 | voice_a | noise15db | 0.00 | Keep the cup within 30 degrees of upright while carrying it.. | apply (right) | apply (right) |
| t01 | voice_b | clean | 0.00 | Keep the cup within 30 degrees of upright while carrying it.. | apply (right) | apply (right) |
| t01 | voice_b | noise15db | 0.00 | keep the cup within 30 degrees of upright while carrying it. | apply (right) | apply (right) |
| t02 | voice_a | clean | 0.00 | The cup may tilt at most five degrees during transport. | apply (right) | apply (right) |
| t02 | voice_a | noise15db | 0.00 | The cup may tilt at most five degrees during transport. | apply (right) | apply (right) |
| t02 | voice_b | clean | 0.00 | The cup may tilt at most five degrees during transport. | apply (right) | apply (right) |
| t02 | voice_b | noise15db | 0.00 | The cup may tilt at most five degrees during transport. | apply (right) | apply (right) |
| t03 | voice_a | clean | 0.00 | While the cup is carried, it can lean up to 20 degrees. No more. | apply (right) | apply (right) |
| t03 | voice_a | noise15db | 0.07 | While the cup is is carried, it can lean up to 20 degrees. No more. | apply (right) | apply (right) |
| t03 | voice_b | clean | 0.00 | While the cup is carried it can lean up to 20 degrees, no more.. | apply (right) | apply (right) |
| t03 | voice_b | noise15db | 0.00 | While the cup is carried, it can lean up to 20 degrees, no more. | apply (right) | apply (right) |
| t04 | voice_a | clean | 0.00 | Once placed, the cup must be within 10 degrees of vertical. | apply (right) | apply (right) |
| t04 | voice_a | noise15db | 0.00 | Once placed, the cup must be within 10 degrees of vertical.. | apply (right) | apply (right) |
| t04 | voice_b | clean | 0.00 | Once placed, the cup must be within 10 degrees of vertical. | apply (right) | apply (right) |
| t04 | voice_b | noise15db | 0.09 | Once placed, placed, the cup must be within 10 degrees of vertical.. | apply (right) | apply (right) |
| n01 | voice_a | clean | 0.09 | The cup doesn't doesn't need to stay upright while it is carried... | apply (right) | apply (right) |
| n01 | voice_a | noise15db | 0.00 | The cup doesn't need to stay upright while it is carried. | apply (right) | apply (right) |
| n01 | voice_b | clean | 0.00 | The cup doesn't need to stay upright while it is carried. | apply (right) | apply (right) |
| n01 | voice_b | noise15db | 0.09 | The cup doesn't need to stay upright while it is carried.ed. | apply (right) | apply (right) |
| n02 | voice_a | clean | 0.00 | It doesn't matter whether the cup ends upright.. | apply (right) | apply (right) |
| n02 | voice_a | noise15db | 0.00 | It doesn't matter whether the cup ends upright.. | apply (right) | apply (right) |
| n02 | voice_b | clean | 0.00 | It doesn't matter whether the cup ends upright.. | apply (right) | apply (right) |
| n02 | voice_b | noise15db | 0.00 | It doesn't matter whether the cup ends upright.. | apply (right) | apply (right) |
| n03 | voice_a | clean | 0.00 | The marker is allowed to tilt. | apply (right) | apply (right) |
| n03 | voice_a | noise15db | 0.00 | The marker is allowed to tilt. | apply (right) | apply (right) |
| n03 | voice_b | clean | 0.00 | The marker is allowed to tilt., | apply (right) | apply (right) |
| n03 | voice_b | noise15db | 0.00 | The marker is allowed to tilt. | apply (right) | apply (right) |
| n04 | voice_a | clean | 0.00 | The order doesn't matter. | apply (right) | apply (right) |
| n04 | voice_a | noise15db | 0.00 | The order doesn't matter.. | apply (right) | apply (right) |
| n04 | voice_b | clean | 0.00 | The order doesn't matter. | apply (right) | apply (right) |
| n04 | voice_b | noise15db | 0.00 | The order doesn't matter. | apply (right) | apply (right) |
| n05 | voice_a | clean | 0.14 | You don't have to pack pack the marker. | apply (right) | apply (right) |
| n05 | voice_a | noise15db | 0.00 | You don't have to pack the marker. | apply (right) | apply (right) |
| n05 | voice_b | clean | 0.00 | You don't have to pack the marker. | apply (right) | apply (right) |
| n05 | voice_b | noise15db | 0.00 | You don't have to pack the marker.. | apply (right) | apply (right) |
| o01 | voice_a | clean | 0.00 | Pack the block before the cup.. | apply (right) | apply (right) |
| o01 | voice_a | noise15db | 0.00 | Pack the block before the cup.. | apply (right) | apply (right) |
| o01 | voice_b | clean | 0.00 | Pack the block before the cup. | apply (right) | apply (right) |
| o01 | voice_b | noise15db | 0.00 | pack the block before the cup.. | apply (right) | apply (right) |
| o02 | voice_a | clean | 0.00 | The cup has to go in first. | apply (right) | apply (right) |
| o02 | voice_a | noise15db | 0.00 | The cup has to go in first., | apply (right) | apply (right) |
| o02 | voice_b | clean | 0.00 | The cup has to go in first. | apply (right) | apply (right) |
| o02 | voice_b | noise15db | 0.43 | The cup has to go has to go in first. | apply (right) | apply (right) |
| o03 | voice_a | clean | 0.00 | Put the marker in last. | apply (right) | apply (right) |
| o03 | voice_a | noise15db | 0.00 | Put the marker in last. | apply (right) | apply (right) |
| o03 | voice_b | clean | 0.00 | Put the marker in last. | apply (right) | apply (right) |
| o03 | voice_b | noise15db | 0.00 | Put the marker in last. | apply (right) | apply (right) |
| c01 | voice_a | clean | 0.00 | The Cup must not touch the wall. | apply (right) | apply (right) |
| c01 | voice_a | noise15db | 0.14 | The cup must must not touch the wall. | apply (right) | apply (right) |
| c01 | voice_b | clean | 0.00 | The cup must not touch the wall. | apply (right) | apply (right) |
| c01 | voice_b | noise15db | 0.00 | The cup must not touch the wall. | apply (right) | apply (right) |
| c02 | voice_a | clean | 0.07 | Keep the cup at at least three centimeters away from the wall while carrying it.. | apply (right) | apply (right) |
| c02 | voice_a | noise15db | 0.14 | keep the cup at least three centimeters away from the from the wall while carrying it.. | apply (right) | apply (right) |
| c02 | voice_b | clean | 0.00 | Keep the cup at least three centimeters away from the wall while carrying it. | apply (right) | apply (right) |
| c02 | voice_b | noise15db | 0.07 | Keep the cup at least three centimeters away from the the wall while carrying it. | apply (right) | apply (right) |
| a01 | voice_a | clean | 0.00 | It must stay upright the whole time.. | asks (right) | asks (right) |
| a01 | voice_a | noise15db | 0.00 | It must stay upright the whole time. | asks (right) | asks (right) |
| a01 | voice_b | clean | 0.00 | it must stay upright the whole time. | asks (right) | asks (right) |
| a01 | voice_b | noise15db | 0.00 | It must stay upright the whole time. | asks (right) | asks (right) |
| a02 | voice_a | clean | 0.00 | Keep that one upright. | asks (right) | asks (right) |
| a02 | voice_a | noise15db | 0.00 | Keep that one upright.. | asks (right) | asks (right) |
| a02 | voice_b | clean | 0.00 | Keep that one upright.. | asks (right) | asks (right) |
| a02 | voice_b | noise15db | 0.00 | keep that one upright.. | asks (right) | asks (right) |
| a03 | voice_a | clean | 0.00 | this one can tilt but that one can't. | asks (right) | asks (right) |
| a03 | voice_a | noise15db | 0.00 | This one can tilt but that one can't. | asks (right) | asks (right) |
| a03 | voice_b | clean | 0.00 | This one can tilt but that one can't. | asks (right) | asks (right) |
| a03 | voice_b | noise15db | 0.00 | this one can tilt, but that one can't. | asks (right) | asks (right) |
| a04 | voice_a | clean | 0.00 | The object must not tilt while carried. | asks (right) | asks (right) |
| a04 | voice_a | noise15db | 0.00 | The object must not tilt while carried.. | asks (right) | asks (right) |
| a04 | voice_b | clean | 0.00 | The object must not tilt while carried. | asks (right) | asks (right) |
| a04 | voice_b | noise15db | 0.00 | The object must not tilt while carried. | asks (right) | asks (right) |
| a05 | voice_a | clean | 0.00 | The cup must stay upright while carried, and it may tilt while carried. | asks (right) | asks (right) |
| a05 | voice_a | noise15db | 0.00 | The cup must stay upright while carried and it may tilt while carried.. | asks (right) | asks (right) |
| a05 | voice_b | clean | 0.08 | The cup must must stay upright while carried. and it may tilt while carried.. | asks (right) | asks (right) |
| a05 | voice_b | noise15db | 0.00 | The cup must stay upright while carried, and it may tilt while carried. | asks (right) | asks (right) |
| x01 | voice_a | clean | 0.40 | The bull must must stay upright.. | asks (right) | asks (right) |
| x01 | voice_a | noise15db | 0.20 | The ball must stay upright. | asks (right) | asks (right) |
| x01 | voice_b | clean | 0.20 | The bowl must must stay upright. | asks (right) | asks (right) |
| x01 | voice_b | noise15db | 0.20 | The bowl must must stay upright.. | asks (right) | asks (right) |
| x02 | voice_a | clean | 0.00 | Keep the bottle upright during transport.. | asks (right) | asks (right) |
| x02 | voice_a | noise15db | 0.00 | Keep the bottle upright during transport.. | asks (right) | asks (right) |
| x02 | voice_b | clean | 0.00 | Keep the bottle upright during transport.. | asks (right) | asks (right) |
| x02 | voice_b | noise15db | 0.00 | Keep the bottle upright during transport., | asks (right) | asks (right) |
| x03 | voice_a | clean | 0.00 | The lid must go in first.. | asks (right) | asks (right) |
| x03 | voice_a | noise15db | 0.00 | The lid must go in first. | asks (right) | asks (right) |
| x03 | voice_b | clean | 0.17 | The lid must go go in first.. | asks (right) | asks (right) |
| x03 | voice_b | noise15db | 0.00 | The lid must go in first. | asks (right) | asks (right) |
| u01 | voice_a | clean | 0.00 | move faster. | rejects (right) | rejects (right) |
| u01 | voice_a | noise15db | 0.00 | Move faster. | rejects (right) | rejects (right) |
| u01 | voice_b | clean | 0.00 | Move faster.: | rejects (right) | rejects (right) |
| u01 | voice_b | noise15db | 0.00 | Move faster. | rejects (right) | rejects (right) |
| u02 | voice_a | clean | 0.00 | Use your left hand. | rejects (right) | rejects (right) |
| u02 | voice_a | noise15db | 0.00 | Use your left hand. | rejects (right) | rejects (right) |
| u02 | voice_b | clean | 0.00 | Use your left hand. | rejects (right) | rejects (right) |
| u02 | voice_b | noise15db | 0.00 | Use your left hand. | rejects (right) | rejects (right) |
| u03 | voice_a | clean | 0.00 | Make the robot quieter.. | rejects (right) | rejects (right) |
| u03 | voice_a | noise15db | 0.00 | Make the robot quieter. | rejects (right) | rejects (right) |
| u03 | voice_b | clean | 0.00 | Make the robot quieter. | rejects (right) | rejects (right) |
| u03 | voice_b | noise15db | 0.25 | Make the robot robot quieter. | rejects (right) | rejects (right) |
| u04 | voice_a | clean | 0.00 | Close the gripper more gently. | rejects (right) | rejects (right) |
| u04 | voice_a | noise15db | 0.00 | Close the gripper more gently. | rejects (right) | rejects (right) |
| u04 | voice_b | clean | 0.20 | Close the gripper gripper more gently. | rejects (right) | rejects (right) |
| u04 | voice_b | noise15db | 0.00 | Close the gripper more gently.. | rejects (right) | rejects (right) |
| hp01 | voice_a | clean | 0.00 | please make sure the cup stays upright as you move it over to the tray. | apply (right) | apply (right) |
| hp01 | voice_a | noise15db | 0.00 | Please make sure the cup stays upright as you move it over to the tray., | apply (right) | apply (right) |
| hp01 | voice_b | clean | 0.00 | Please make sure the cup stays upright as you move it over to the tray. | apply (right) | apply (right) |
| hp01 | voice_b | noise15db | 0.07 | please make sure the the cup stays upright as you move it over to the tray. | apply (right) | apply (right) |
| hp02 | voice_a | clean | 0.00 | No tipping the container while it's in the gripper. | apply (right) | apply (right) |
| hp02 | voice_a | noise15db | 0.22 | No tipping the tipping the container while it's in the gripper. | apply (right) | apply (right) |
| hp02 | voice_b | clean | 0.00 | No tipping the container while it's in the gripper. | apply (right) | apply (right) |
| hp02 | voice_b | noise15db | 0.00 | No tipping the container while it's in the gripper. | apply (right) | apply (right) |
| hp03 | voice_a | clean | 0.00 | When you carry the cup, it's opening has to face up the entire way. | apply (right) | apply (right) |
| hp03 | voice_a | noise15db | 0.00 | When you carry the cup its opening has to face up the entire way. | apply (right) | apply (right) |
| hp03 | voice_b | clean | 0.00 | When you carry the cup, its opening has to face up the entire way. | apply (right) | apply (right) |
| hp03 | voice_b | noise15db | 0.00 | When you carry the cup, it's opening has to face up the entire way. | apply (right) | apply (right) |
| hp04 | voice_a | clean | 0.06 | You tilted the cup and the beads fell out, it it has to stay level while moving. | apply (right) | apply (right) |
| hp04 | voice_a | noise15db | 0.00 | You tilted the cup and the beads fell out. It has to stay level while moving. | apply (right) | apply (right) |
| hp04 | voice_b | clean | 0.00 | you tilted the cup and the beads fell out. It has to stay level while moving. | apply (right) | apply (right) |
| hp04 | voice_b | noise15db | 0.00 | You tilted the cup and the beads fell out. It has to stay level while moving.. | apply (right) | apply (right) |
| hp05 | voice_a | clean | 0.00 | Upright during transport too, for the cup. Not merely at the end.. | apply (right) | apply (right) |
| hp05 | voice_a | noise15db | 0.00 | Upright during transport too, for the cup, not merely at the end. | apply (right) | apply (right) |
| hp05 | voice_b | clean | 0.00 | Upright during transport too. For the cup. Not merely at the end. | apply (right) | apply (right) |
| hp05 | voice_b | noise15db | 0.00 | upright during transport too. For the cup, not merely at the end,. | apply (right) | apply (right) |
| hs01 | voice_a | clean | 0.00 | The pen should stay vertical while you carry it. The cup too.: | apply (right) | apply (right) |
| hs01 | voice_a | noise15db | 0.00 | The pen should stay vertical while you carry it. The cup too. | asks | apply (right) |
| hs01 | voice_b | clean | 0.00 | The pen should stay vertical while you carry it. The cup too.. | apply (right) | apply (right) |
| hs01 | voice_b | noise15db | 0.00 | The pen should stay vertical while you carry it. The cup too. | asks | apply (right) |
| hs02 | voice_a | clean | 0.00 | I only care about the cup staying upright when it is carried. Tilt the others as you like. | apply (right) | apply (right) |
| hs02 | voice_a | noise15db | 0.00 | I only care about the cup staying upright when it is carried. Tilt the others as you like. | apply (right) | apply (right) |
| hs02 | voice_b | clean | 0.00 | I only care about the cup staying upright when it is carried. Tilt the others as you like. | apply (right) | apply (right) |
| hs02 | voice_b | noise15db | 0.00 | I only care about the cup staying upright when it is carried. Tilt the others as you like.. | apply (right) | apply (right) |
| hs03 | voice_a | clean | 0.00 | Keep the cube level while carrying it.. | apply (right) | apply (right) |
| hs03 | voice_a | noise15db | 0.00 | Keep the cube level while carrying it. | apply (right) | apply (right) |
| hs03 | voice_b | clean | 0.00 | Keep the cube level while carrying it.. | apply (right) | apply (right) |
| hs03 | voice_b | noise15db | 0.00 | Keep the cube level while carrying it.. | apply (right) | apply (right) |
| hs04 | voice_a | clean | 0.00 | all three objects have to stay upright at all times., | apply (right) | apply |
| hs04 | voice_a | noise15db | 0.00 | All three objects have to stay upright at all times. | apply | apply |
| hs04 | voice_b | clean | 0.00 | All three objects have to stay upright at all times. | apply | apply |
| hs04 | voice_b | noise15db | 0.00 | All three objects have to stay upright at all times. | apply | apply |
| hh01 | voice_a | clean | 0.08 | The marker has has to be standing upright once it is in the tray.. | apply (right) | apply (right) |
| hh01 | voice_a | noise15db | 0.00 | The marker has to be standing upright once it is in the tray.. | apply (right) | apply (right) |
| hh01 | voice_b | clean | 0.15 | The marker has to be standing upright once it is it is in the tray.. | apply (right) | apply (right) |
| hh01 | voice_b | noise15db | 0.08 | The marker has to be standing upright once it is is in the tray.: | apply (right) | apply (right) |
| hh02 | voice_a | clean | 0.00 | The block must stay upright the whole time, from start to finish. | apply (right) | apply (right) |
| hh02 | voice_a | noise15db | 0.08 | The block must stay upright the whole time. From From start to finish.. | apply (right) | apply (right) |
| hh02 | voice_b | clean | 0.08 | The block must must stay upright the whole time. From start to finish. | apply (right) | apply (right) |
| hh02 | voice_b | noise15db | 0.00 | The block must stay upright the whole time, from start to finish. | apply (right) | apply (right) |
| hh03 | voice_a | clean | 0.20 | For the cup, being upright only matters at the at the end. | apply (right) | apply (right) |
| hh03 | voice_a | noise15db | 0.20 | For the cup, cup, being upright only matters at at the end. | apply (right) | apply (right) |
| hh03 | voice_b | clean | 0.20 | For the cup, cup, being upright only matters at at the end.. | apply (right) | apply (right) |
| hh03 | voice_b | noise15db | 0.00 | for the cup being upright only matters at the end.. | apply (right) | apply (right) |
| ht01 | voice_a | clean | 0.00 | A tilt of up to 25 degrees is acceptable for the cup while you carry it, but not more. | apply (right) | apply (right) |
| ht01 | voice_a | noise15db | 0.00 | A tilt of up to 25 degrees is acceptable for the cup while you carry it, but not more.. | apply (right) | apply (right) |
| ht01 | voice_b | clean | 0.00 | A tilt of up to 25 degrees is acceptable for the cup while you carry it. but not more., | apply (right) | apply (right) |
| ht01 | voice_b | noise15db | 0.00 | A tilt of up to 25 degrees is acceptable for the cup while you carry it. But not more. | apply (right) | apply (right) |
| ht02 | voice_a | clean | 0.18 | During transport, the cup must not lean more than more than eight degrees.. | apply (right) | apply (right) |
| ht02 | voice_a | noise15db | 0.09 | During transport, transport, the cup must not lean more than eight degrees.. | apply (right) | apply (right) |
| ht02 | voice_b | clean | 0.00 | During transport, the cup must not lean more than eight degrees. | apply (right) | apply (right) |
| ht02 | voice_b | noise15db | 0.00 | During transport, the cup must not lean more than eight degrees. | apply (right) | apply (right) |
| ht03 | voice_a | clean | 0.00 | Relax the cup's final upright tolerance to 40 degrees. | apply (right) | apply (right) |
| ht03 | voice_a | noise15db | 0.00 | Relax the cup's final upright tolerance to 40 degrees. | apply (right) | apply (right) |
| ht03 | voice_b | clean | 0.11 | Relax the cup's final upright tolerance to 40 40 degrees. | apply (right) | apply (right) |
| ht03 | voice_b | noise15db | 0.00 | Relax the cup's final upright tolerance to 40 degrees.. | apply (right) | apply (right) |
| hn01 | voice_a | clean | 0.00 | The cup can end up on its side in the tray. That's fine.. | apply (right) | apply (right) |
| hn01 | voice_a | noise15db | 0.00 | The cup can end up on its side in the tray. That's fine.. | apply (right) | apply (right) |
| hn01 | voice_b | clean | 0.00 | The cup can end up on its side in the tray. That's fine. | apply (right) | apply (right) |
| hn01 | voice_b | noise15db | 0.00 | The cup can end up on its side in the tray, that's fine. | apply (right) | apply (right) |
| hn02 | voice_a | clean | 0.00 | Forget about keeping the marker upright. | apply (right) | apply (right) |
| hn02 | voice_a | noise15db | 0.00 | Forget about keeping the marker upright.. | apply (right) | apply (right) |
| hn02 | voice_b | clean | 0.17 | Forget about keeping keeping the marker upright. | apply (right) | apply (right) |
| hn02 | voice_b | noise15db | 0.17 | Forget about keeping keeping the marker upright. | apply (right) | apply (right) |
| hn03 | voice_a | clean | 0.00 | Pack the objects in whatever order you like.. | apply (right) | apply (right) |
| hn03 | voice_a | noise15db | 0.00 | Pack the objects in whatever order you like.. | apply (right) | apply (right) |
| hn03 | voice_b | clean | 0.00 | Pack the objects in whatever order you like. | apply (right) | apply (right) |
| hn03 | voice_b | noise15db | 0.00 | Pack the objects in whatever order you like.. | apply (right) | apply (right) |
| ho01 | voice_a | clean | 0.10 | The marker should go go into the tray before the block.. | apply (right) | apply (right) |
| ho01 | voice_a | noise15db | 0.00 | The marker should go into the tray before the block. | apply (right) | apply (right) |
| ho01 | voice_b | clean | 0.00 | The marker should go into the tray before the block.. | apply (right) | apply (right) |
| ho01 | voice_b | noise15db | 0.00 | The marker should go into the tray before the block.. | apply (right) | apply (right) |
| hc01 | voice_a | clean | 0.20 | Avoid bumping bumping the cup into the wall while carrying carrying it.. | apply (right) | apply (right) |
| hc01 | voice_a | noise15db | 0.00 | Avoid bumping the cup into the wall while carrying it.. | apply (right) | apply (right) |
| hc01 | voice_b | clean | 0.00 | Avoid bumping the cup into the wall while carrying it. | apply (right) | apply (right) |
| hc01 | voice_b | noise15db | 0.00 | Avoid bumping the cup into the wall while carrying it. | apply (right) | apply (right) |
| ha01 | voice_a | clean | 0.00 | Don't let it tip over. | asks (right) | asks (right) |
| ha01 | voice_a | noise15db | 0.00 | don't let it tip over | asks (right) | asks (right) |
| ha01 | voice_b | clean | 0.00 | Don't let it tip over. | asks (right) | asks (right) |
| ha01 | voice_b | noise15db | 0.00 | Don't let it tip over. | asks (right) | asks (right) |
| ha02 | voice_a | clean | 0.00 | That thing has to stay upright while carried. | asks (right) | asks (right) |
| ha02 | voice_a | noise15db | 0.00 | That thing has to stay upright while carried. | asks (right) | asks (right) |
| ha02 | voice_b | clean | 0.00 | that thing has to stay upright while carried. | asks (right) | asks (right) |
| ha02 | voice_b | noise15db | 0.00 | That thing has to stay upright while carried..: | asks (right) | asks (right) |
| ha03 | voice_a | clean | 0.06 | Keep the marker marker upright while carrying it. But the marker doesn't need to be upright while carried. | asks (right) | asks (right) |
| ha03 | voice_a | noise15db | 0.00 | Keep the marker upright while carrying it. But the marker doesn't need to be upright while carried. | asks (right) | asks (right) |
| ha03 | voice_b | clean | 0.06 | Keep the marker upright while carrying it but the marker doesn't need to be upright while while carried. | asks (right) | asks (right) |
| ha03 | voice_b | noise15db | 0.00 | Keep the marker upright while carrying it. But the marker doesn't need to be upright while carried.: | asks (right) | asks (right) |
| hx01 | voice_a | clean | 0.00 | The jar must not be tilted.. | asks (right) | asks (right) |
| hx01 | voice_a | noise15db | 0.00 | The jar must not be tilted. | asks (right) | asks (right) |
| hx01 | voice_b | clean | 0.00 | The jar must not be tilted. | asks (right) | asks (right) |
| hx01 | voice_b | noise15db | 0.00 | the jar must not be tilted. | asks (right) | asks (right) |
| hx02 | voice_a | clean | 0.00 | Pack the phone before the cup. | asks (right) | asks (right) |
| hx02 | voice_a | noise15db | 0.00 | Pack the phone before the cup. | asks (right) | asks (right) |
| hx02 | voice_b | clean | 0.00 | Pack the phone before the cup. | asks (right) | asks (right) |
| hx02 | voice_b | noise15db | 0.00 | pack the phone before the cup. | asks (right) | asks (right) |
| hu01 | voice_a | clean | 0.20 | Take a photo of photo of the tray when you are done.. | rejects (right) | rejects (right) |
| hu01 | voice_a | noise15db | 0.00 | Take a photo of the tray when you are done.. | rejects (right) | rejects (right) |
| hu01 | voice_b | clean | 0.00 | Take a photo of the tray when you are done. | rejects (right) | rejects (right) |
| hu01 | voice_b | noise15db | 0.00 | Take a photo of the tray when you are done. | rejects (right) | rejects (right) |
| hu02 | voice_a | clean | 0.00 | grip harder. | rejects (right) | rejects (right) |
| hu02 | voice_a | noise15db | 0.00 | Grip harder. | rejects (right) | rejects (right) |
| hu02 | voice_b | clean | 0.00 | grip harder, | rejects (right) | rejects (right) |
| hu02 | voice_b | noise15db | 0.00 | grip harder. | rejects (right) | rejects (right) |
| hu03 | voice_a | clean | 0.00 | wait five seconds before starting. | rejects (right) | rejects (right) |
| hu03 | voice_a | noise15db | 0.00 | Wait five seconds before starting.. | rejects (right) | rejects (right) |
| hu03 | voice_b | clean | 0.00 | Wait five seconds before starting. | rejects (right) | rejects (right) |
| hu03 | voice_b | noise15db | 0.00 | Wait five seconds before starting.. | rejects (right) | rejects (right) |

Budget lines: {"muse-voice-transcribe-1.0": {"used": 288, "cap": 300}}. Every attempt is in ledger.jsonl (362 entries). No recording was excluded.
