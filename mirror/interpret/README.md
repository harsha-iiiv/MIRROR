# mirror.interpret

Turns a demonstration into a proposed task specification, and natural-language
corrections and clarification answers into new specification versions.

```python
from mirror.interpret import (LLMClient, propose_spec, confirm_items, compile_correction,
                              make_clarification, apply_answer, spec_diff, NeedsClarification)

client = LLMClient.from_env()            # live / record / replay / offline (see llm.py)
spec = propose_spec(trace, client)       # everything inferred is "proposed"
spec, _ = confirm_items(spec, confirm=["g_inside_cup", "c_upright_final_cup"])
try:
    spec, record = compile_correction(spec, "This container must remain upright throughout "
                                      "transport, not just at the destination.", client,
                                      context={"scene": scene})
except NeedsClarification as q:
    show(q.question, q.options)          # spec unchanged; ask the user
```

## How it works

1. `summarize_demo` computes deterministic features from the trace: grasp and
   release times, tilt while carried, end pose and tray, wall crossings,
   obstacle distance, pick order, and a numbered list of *regularities*
   (R1, R2, ...): things the demonstration did consistently.
2. The interpreter (model or offline rules) fills a small closed draft schema.
   It must cite or explicitly dismiss every regularity, separate what the
   demonstration shows from what it assumes, and ask one question per inferred
   rule. Checks in `drafts.py` reject drafts that contradict the measurements
   (for example an upright rule the demonstration itself violated), name
   objects that are not in the scene, or leave regularities unaddressed. The
   model gets one repair round with the problems listed; after that the call
   fails instead of being patched.
3. Identifiers, statuses and provenance are assigned by code. An inferred item
   is always `proposed`; only a human statement makes it `confirmed`.
4. Corrections are compiled into intent-level operations (require / relax a
   rule, confirm / reject an item, ordering, goals) and then into `SpecOp`s
   that `specops.apply_ops` applies to produce version + 1 with a
   `CorrectionRecord`. A rule that applies to every object is accepted only
   when the text says so (all, every, ...). Requiring and relaxing the same rule
   in one correction is reported as a contradiction.
5. `make_clarification` compares candidate plans (planned waypoints, rigid
   grasp assumed), picks the pair that best targets an open question, and
   precomputes the ops for a "yes" and a "no" answer. `apply_answer` maps the
   answer; free-text answers go through the correction compiler.

## Offline rules

`LLMClient(mode="offline")` (the default when no key is set) uses
`offline.py`: the same draft structures from deterministic rules. It covers the
supported rule types, object synonyms (container, mug, pen, cube, ...), phases,
negation and permission, explicit degrees and distances, simple ordering
phrases, and "only the X matters". Anything else is answered with a question or
a rejection. Its output is labelled `interpreter="offline-rules"`.

## Recordings

`data/cassettes/` holds recorded model responses for the scenarios in
`tests/interpret/scenarios.py`, keyed by a hash of (model, messages, schema),
with the model id the API reported. The replay tests use a pinned copy of the
reference demonstration (`data/cassettes/inputs/`). Re-record with
`MIRROR_LLM_MODE=record ... python -m tests.interpret.scenarios --prune`.

## Limits

* Interpretation reads simulator trace data (poses and grasp events), not video.
* Planned tilt in a clarification is computed from plan waypoints, not measured.
* Obstacle distances in the summary ignore wall slots.
* Rules are per object kind; several objects of one kind share their rules.

## Correction benchmark

`benchmark/cases.json` (44 cases) and `benchmark/cases_holdout.json` (28 cases)
give, for each correction text, the exact enforced constraints, confirmed
orderings and rejected goals expected after applying it to `benchmark/base_spec.json`
in the shortcut scene, or that the interpreter must ask or reject. The frozen set
was written before any interpreter ran; the held-out set was written after the
offline rules were fixed on the frozen set and before any run on it. Cases are
not edited after results. Run `python scripts/bench_corrections.py`; results go
to `results/corrections_benchmark*.{json,md}` (summary in
`corrections_benchmark_summary.md`). Model recordings:
`data/cassettes/corrections_benchmark/`.

## Rendered keyframes (optional)

`propose_spec(trace, client, keyframes=frames)` attaches a few frames rendered by
the simulator from the recorded trace to the proposal prompt as images
(`mirror interpret --keyframes 4`, or `--keyframes-dir` for saved frames). They
are rendered simulator frames, not real video or camera footage, and are off by
default. The measurements stay authoritative: the same checks reject any draft
they contradict. Rendering needs `MUJOCO_GL=osmesa` (or egl). Stored recordings
replace image data with its hash; replay needs the same images, so a pinned set
is kept in `data/cassettes/inputs/keyframes_reference/`.

## Prompt revisions

`prompts.PROMPT_REVISION` records the revision in use. Revision 1 contained the
reference correction's own phrasing as examples ("this container" is the cup; "not
just at the destination" keeps the destination rule). Revision 2 replaced every
example with neutral wording that appears in no demonstration, story or benchmark
text (a test checks this) and made contact rules without a stated phase default to
transport. Revision 3 rewords revision 2's object-resolution rule, which had led the
model to map objects absent from the scene onto the cup. Revisions 2 and 3 were made
after the benchmark results were seen; their benchmark rows are labelled as such.

Note on the reference story: the reference correction does not create a new rule.
It confirms `c_upright_transport_cup`, which the interpreter had already proposed from
the demonstration (the cup stayed upright while carried) and left open as a question.

## Offline lean

For a cup with loose beads the offline rules lean "required" on the transport rule.
That lean comes from the scene description (bead count), not from the demonstration,
and the question and assumption text say so. Leans never change status: every
inferred item is proposed until a person decides.

## Validation layer (absent objects)

After the interpreter (model or offline rules) and the schema and specification
checks, `guard.absent_object_question` runs before anything is applied. If the
correction names an object noun from a fixed tabletop lexicon that matches no
scene object, kind or role, and names no object that is present, the correction
is not applied and the user is asked which scene object they meant. It is on by
default; `context={"validation_layer": False}` turns it off (this key is never
sent to the model). It was added after the benchmark showed the model mapping
"bowl" and "jar" onto the cup, so the benchmark reports it as separate,
post-hoc rows computed from the same stored outputs. Nouns outside the lexicon
are not detected.
