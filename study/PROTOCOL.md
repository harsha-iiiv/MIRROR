# Impact study protocol (pilot)

**Status: not yet run.**

## Beneficiary and question

Initial beneficiary: robotics engineers and integrators who revise handling
constraints for kitting tasks (what must stay upright, what must not touch
what, what goes in first). The pilot asks whether correcting MIRROR in natural
language gets them to a valid specification faster, with fewer wrong-scope
edits and iterations, than editing the structured specification directly, and
whether either reaches the intended behaviour on layouts they never saw.

## Participants

* 5 to 8 external task authors: people who program, integrate or maintain
  robot cells and have revised a handling or packing requirement in the last
  year. Not MIRROR developers, not people who wrote its benchmarks or cases.
* Recruited by the owner (no one is contacted without the owner's approval).
  Each participant gets a pseudonymous id (P01, P02, ...) at consent. No
  compensation is promised in this kit; the owner decides.
* Results are reported per person, including every failure and drop-out. Five
  to eight people is a pilot: it can show problems and effect directions, not
  population rates.

## Conditions

| Condition | What the participant does | Same for all |
|---|---|---|
| **MIRROR** | Types (or speaks) corrections in the studio, reviews the interpreted scope, answers clarifying questions, re-checks the old run, executes | scene, starting specification, planner, simulator, checker, time limit |
| **Manual** | Edits the structured specification in the studio's rule editor (rule type, object or every object, phase, limit, status; orderings; goals), re-checks, executes | as above |
| **Reteach** (exploratory, one task) | Records a new demonstration by teleoperation and lets MIRROR re-interpret it, then reviews | as above |

## Tasks and counterbalancing

The packet (`task_packet/tasks.json`) has six main tasks in two matched sets
(A: T1-T3, B: T4-T6) and one reteach task (T7). Each task is a work order in
prose ("the quality team reports...") that never contains the sentence a
participant would type. Participants have not seen any task before the session.

Each participant does one set with MIRROR and the other with Manual. Order of
conditions and assignment of sets rotate over four orders
(MIRROR-A then Manual-B, Manual-B then MIRROR-A, MIRROR-B then Manual-A,
Manual-A then MIRROR-B), produced by `studykit.py assign`. Within a set, task
order follows a 3x3 Latin square. T7 comes last, with Reteach and MIRROR in
alternating order across participants.

The task packet's sha256 and the studio commit are recorded before the first
session. A compiler or prompt change during the study is recorded with the
session it affects and its results are reported separately.

## Training

Before the timed tasks: a 5-minute walkthrough of the reference story (the
existing recorded tour) and one practice task with each condition (T0 in the
packet, not scored).

## Measures (per participant x task)

| Measure | Definition |
|---|---|
| Time to valid specification | seconds from `task_start` to the first specification version whose semantic state equals the task's acceptance state |
| Task completion | final specification equals the acceptance state and its execution passes the checker under the acceptance specification on the task's held-back transfer layouts |
| Wrong-scope edits | specification versions that differ from the starting specification on an object kind the acceptance state does not change (a later version that undoes the mistake is not counted again) |
| Clarification turns | clarifying questions shown |
| Iterations | specification versions created |
| Invalid edits | manual edits that failed validation; corrections that errored |
| Gave up / timed out | task ended by the participant or by the 8-minute limit |

Plus, after each condition, a two-question rating (confidence that the robot
will do what you meant, 1-7; effort, 1-7) and one open comment, recorded only
with consent.

## Analysis

* Per-person table of every measure and every failure (`studykit.py analyze`).
* Per condition: medians and ranges; tasks not reaching a valid specification
  count as the time limit and are also listed separately (never dropped).
* Paired comparison of time to valid specification (MIRROR vs Manual, per
  participant, set-matched): Wilcoxon signed-rank, exact, reported with the
  number of pairs; with 5 to 8 people this is descriptive.
* Wrong-scope edits and completion as counts per condition.
* Reteach (T7) reported descriptively only.

## What this study cannot show

Real-world deployment, physical robots, long-term use, people outside the
recruited group, or tasks outside the kit-packing family.
