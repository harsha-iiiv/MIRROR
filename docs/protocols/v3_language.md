# Protocol v3, language-understanding track

**Status: not yet run.** Prepared and dry-run on 14 team-written development
cases only. No independent author, adjudicator or judge has taken part, and no
number from this track is a result.

## Why a separate track

The two pre-registered layout evaluations compile one specification pair and
require the replayed model specification to equal the offline-rule
specification before anything runs (`mirror/evaluate/specs.py`). That
requirement is a **deterministic regression check**: it makes sure the
recorded responses still produce the rules the offline interpreter produces.
Because the check forces the two to agree, those evaluations would give the
same layout results with the offline rules alone. They show the effect of the
corrected rule, not what Muse Spark contributes. The correction-compiler
benchmark's Muse Spark rows are a post-hoc model swap on cases seen during
development. Neither measures language understanding on fresh input.

This track does: fresh corrections written by independent authors, scored
against an independently adjudicated gold, with the model and the rule baseline
free to disagree.

## Cases

* Written by the independent authors of the authoring track
  ([v3_authoring.md](v3_authoring.md)), in separate case files with
  `"track": "language"`. No development, benchmark or prompt sentence is reused;
  overlap is checked before the freeze.
* **Bases vary:** the reviewed reference specification, the same proposal
  before review, and bases that already confirm a rule or an ordering (cup
  upright while carried; block within 10 degrees; cup before block; every
  object upright while carried). A correction can therefore add, tighten,
  relax, reverse or except a rule that already exists.
* **Phenomena,** at least 15 turns each: negation and reversal; scope (only,
  except, every object, pairs); ambiguity (missing or unclear referent, vague
  tolerance); rule types and phases not in the base; multi-turn refinement;
  unsupported requests; statements that should change nothing.
* No execution: the score is whether the specification means what the author
  meant. Execution of authored specifications is the authoring track's job.

## Conditions

`mirror_model` (Muse Spark through the correction compiler), `offline_rules`
and `manual_structured`, as in the authoring track. There is no agreement gate:
each condition is scored on its own against the gold.

## Scoring

1. **Gold.** Two adjudicators who wrote neither the case nor MIRROR each record,
   independently, the outcome (apply, asks, rejects) and the structured edit for
   every turn, and any other edits they would accept as meaning the same (for
   example 10 to 20 degrees for "roughly level").
   `adjudication-agreement` reports Cohen's kappa on outcomes and exact edit
   agreement; a third adjudicator resolves every disagreement before the freeze,
   and the resolution is kept.
2. **Automatic score (primary).** A turn is correct when its outcome and its
   semantic enforced state equal the gold or an accepted alternative.
   Accepted alternatives are counted separately.
3. **Blind judgement (secondary).** After the run, `adjudication-pack` writes one
   sheet per turn: the sentence, the rules in force before it, and every
   condition's result as a plain change list under shuffled labels. The label
   key goes only to the custodian. Two judges (not the gold adjudicators) label
   each output: correct, acceptable alternative, wrong scope, over-applied,
   under-applied, wrong rule, wrong refusal, wrong acceptance, correct refusal,
   unclear. `adjudication-unblind` counts labels per condition. Every turn where
   the automatic score and the judges disagree is listed.

## Analysis

Per phenomenon and per base: exact rate per condition with Wilson intervals,
and paired exact McNemar per turn (Muse Spark vs offline rules, Muse Spark vs
manual). Every attempt is counted as in the authoring track.

## Running it (development only)

```bash
python -m mirror.evaluate.protocol_v3 dry-run --language --out <dir> --mirror-mode offline --no-execute
python -m mirror.evaluate.protocol_v3 adjudication-pack <dir> --out <pack> --seed 1
```

On the development cases the manual condition uses team fixtures equal to the
gold, so its score is perfect by construction and means nothing; the offline
rules and the stand-in for the model differ from the gold on several negation,
scope and new-rule cases. These cases exist to test the harness.

## Needs budget (not approved)

J2: at most 300 turns, each at most 3 `compile_correction` requests to
`muse-spark-1.3`; expected about $3.50, cap $6. It also needs the independent
authors, two gold adjudicators, two blind judges and the custodian.
