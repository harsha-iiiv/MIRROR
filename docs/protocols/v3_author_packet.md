# Protocol v3: author packet

For independent case authors, editors and adjudicators. Send everything to the
custodian named in your invitation, not to MIRROR's developers.

## What MIRROR does

A simulated gripper packs a cup (with beads), a block and a marker into a tray,
around or through a wall. A task specification lists goals (which objects go in
the tray), rules (an object upright, an object not touching something, an
object keeping a distance from obstacles, each during carrying, after placing,
or always), and packing orders. You write what a person would say to correct
the robot's understanding.

Supported, and nothing else: upright rules with a tilt limit; no contact with
obstacles, the table outside the tray, or other objects; clearance from
obstacles in metres; packing order between two objects; dropping an object from
the task. Anything else (speed, cleaning, new objects) should be refused, and a
good case may test that.

## Scenes

Pick one per case: the reference shortcut scene (a slot in the wall is the
short way), the reference blocked scene (the slot is the only opening), or a
sampled layout of family "mixed" or "shortcut" (the custodian assigns the seed).
Development pictures of each family are in the studio's showcase layouts.

## Writing a case

Per case, fill in:

| Field | Meaning |
|---|---|
| `id` | your initials + number, e.g. `A03-07` (pseudonymous; no names) |
| `category` | positive, negative, ambiguous, unsupported or infeasible |
| `scene` | as above |
| `turns` | one or more things you would say, in order; later turns may refine, reverse or add exceptions |
| `expect_execution` | compliant_completion, proven_infeasible, or not_executed (for questions and refusals) |

Write in your own words. Do not copy sentences from MIRROR's README, demo,
prompts or earlier benchmarks. Do not run your sentences through MIRROR before
sending them.

## Gold (adjudicators)

For every turn, two adjudicators who did not write the case record the intended
outcome (apply, asks, rejects) and, for apply, the structured edit it should
produce. Record disagreements and their resolution.

## Manual edits (editors)

An editor who did not write the case and has not seen the gold reads each turn
and the scene and enters the structured edit they would make in a spec editor,
timing themselves. Leave the edit empty if you would not change the
specification.

## Speech (optional, real speakers only)

Record each spoken turn as WAV, 16 kHz mono, in a quiet room, phone or laptop
microphone at arm's length, saying the turn once as you would to a colleague.
Do not read punctuation aloud. Each speaker signs the consent form
(`study/consent_form.md`) first and gets a pseudonymous speaker id. Recordings
are used only for this evaluation and deleted after the period stated in the
form.

## Sending

Send the case file (format: `mirror/evaluate/protocol_v3/schema.py`, example:
the development cases) and any recordings to the custodian. The custodian
publishes only a hash and counts before the freeze. Your cases are opened only
for the frozen run.
