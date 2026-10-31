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
