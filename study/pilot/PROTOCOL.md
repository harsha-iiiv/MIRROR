# MIRROR usability pilot (3-5 people)

**Status: prepared, not run.** No one has been recruited or contacted.
Recruiting needs the owner's approval. Everything under `fixtures/` is
synthetic.

**What it can show.** A pilot with 3 to 5 people is preliminary usability
evidence: where people get stuck, which interpretations go wrong, whether the
labels are understood. It is not proof of demand, of a general productivity
gain, or of language understanding in general (that is
[protocol v3](../../docs/protocols/v3_language.md)). The owner's own testing is
a developer smoke test (`context: dev_smoke`), not independent validation, and
is never pooled with pilot sessions.

## Participants

3 to 5 people who have not read MIRROR's prompts, code, benchmark cases or
task cards, and did not build it. Robotics or automation background preferred
but not required. Pseudonymous ids (P01...). Consent:
[consent form](../consent_form.md) (same form; this session is about 40
minutes).

## Session (about 40 minutes)

1. **Consent and setup** (3 min). Studio in study mode with trial export on.
2. **Recorded tour** (3 min). Then the first comprehension check: "Was what you
   just saw computed now or played back from a recording?" (fresh, recorded,
   unsure).
3. **Practice** (3 min). Card C0 in each condition.
4. **Two blocks of three cards** (2 x 10 min): one block with MIRROR (type or
   speak a correction in your own words, review the interpreted object, phase and
   rule, recheck the old run, run the new specification), one with manual
   editing (the studio's structured rule form). Card sets A (C1-C3) and B
   (C4-C6) are matched: a tolerance change, an ordering or clearance rule, a
   phase-specific or multi-turn change. After the first fresh run, the second
   comprehension check.
5. **Probes, MIRROR only** (8 min): C7 "be careful" (should ask which
   measurable requirement matters, not silently add one), C8 an object that is
   not in the scene (should ask, not substitute), C9 an unsupported request
   (should refuse), C10 a blocked layout (should stop and say why; "proven
   infeasible under these stated assumptions" only with a valid certificate,
   otherwise "planner found no compliant route").
6. **Two questions and a comment** (3 min).

Each card has a 7-minute limit. Cards describe outcomes, never sentences to
type ([CARDS.md](CARDS.md)).

## Counterbalancing

`python study/tools/pilotkit.py assign --participants N` rotates four orders of
(condition, set), rotates card order within each set, and rotates the four
probes. With 3 to 5 people the balance is partial; the order each person had
is reported with their results.

## Measures (all from the trial export)

| Measure | Definition |
|---|---|
| First-attempt correct interpretation | the first attempt's outcome matches the card (apply, ask or refuse) and, for apply, its specification equals the card's outcome |
| Wrong-object / wrong-phase changes | attempts whose resulting specification changes a rule on an object the card does not concern, or the right object in the wrong phase |
| Clarification usefulness | participant rating (1-5) of each question, and whether the next attempt reached the card's outcome |
| Time to a valid specification | from opening the card to the first accepted specification equal to the card's outcome |
| Completed tasks | final specification equals the outcome and its run succeeds (C10: a proven stop; C8, C9: nothing changed) |
| Unresolved failures | every card not completed, with the reason |
| Recorded vs fresh understood | answers to the two comprehension checks against the provenance actually shown |

Every attempt is a record, including errors, timeouts and gave-ups. Nothing
is excluded.

## Instrumentation

The studio's trial export ([schema](../TRIAL_EXPORT.md), requested from the
studio track; not yet emitted). Validate and analyse:

```bash
python study/tools/pilotkit.py validate trials.jsonl
python study/tools/pilotkit.py analyze trials.jsonl --context usability_pilot --out report.md
python study/tools/pilotkit.py export-csv trials.jsonl --out trials.csv
```

Raw exports stay in the private store (`study/DATA.md`); only the report and
the CSV without text columns are published.

## Facilitator rules

Say: "We are testing the software, not you." Do not suggest wording, do not
reveal scoring, do not fix the specification. Log any explanation you give
(`facilitator_note`). Stop a card at the time limit.
