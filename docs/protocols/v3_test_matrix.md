# Protocol v3 test matrix: understanding, not familiarity

**Status: design; development coverage only.** Independently authored cases
do not exist yet. Machine-readable rows: `mirror/evaluate/protocol_v3/matrix.py`
(`ROWS`); coverage is computed from the case files, never typed by hand.

Every row is covered twice and the two are never pooled:

* **Development** (developer-written): `dev_cases.json` and
  `dev_language_cases.json` in `mirror/evaluate/protocol_v3/`, and the
  compiler's own paraphrase tests. They build and debug the harness. They are
  not evidence of understanding, because the people who wrote them also wrote
  the compiler and its prompts.
* **Independent** (held-out): written by authors who have not seen the prompts,
  code or development cases, through the author packet and custodian of
  [v3_authoring.md](v3_authoring.md), scored by the adjudication protocol of
  [v3_language.md](v3_language.md). Rows with execution go through the authoring
  track (identical planner and checker); rows without go through the
  language-understanding track.

| Row | What | Scored as | Track |
|---|---|---|---|
| paraphrase | unprompted paraphrase of a supported requirement | semantic state equals gold | both |
| scope | object-specific versus every-object scope | semantic state; wrong-scope edits counted | both |
| phase | transport versus final-placement requirements | semantic state; wrong-phase changes counted | both |
| ordering | packing order | semantic state; execution unknown unless the checker reports the ordering id | authoring |
| clearance | distance from obstacles | semantic state and execution | authoring |
| multi-turn | refinements, reversals, exceptions | per turn and final state | both |
| vague | "be careful" and similar | gold **asks** which measurable requirement matters; adding a rule unasked is a false accept | language |
| absent object | an object the scene does not contain | gold **asks**; substituting a present object is a false accept | language |
| alias vs absent | "the coffee" for a mug's contents | apply only with explicit context or after a clarification | language |
| unsupported | requirements outside the rule language | gold **rejects** | both |
| infeasible | supported rules no motion can satisfy here | "proven infeasible under these stated assumptions" only with a valid certificate, otherwise "planner found no compliant route"; never a safety guarantee | authoring |

## Absent-object terms

"jar" and "bowl" appeared in development failures and shaped the validation
layer. They stay as **regression** cases and are never counted as fresh
generalisation. Before choosing a term for a fresh case, check it against the
live lexicons: `matrix.absent_term_status(term)` returns `kind_alias` (a word
the compiler maps to a scene object), `regression`, `lexicon` (known to the
compiler or the validation layer: tests that code path, not generalisation) or
`unseen`. On 2 November 2026, for example, "vase" and "flask" are `lexicon`,
"thermos" and "canteen" are `unseen`, "mug" is `kind_alias`. Held-out cases are
checked with `absent_terms_in` at freeze, and each absent-object case records
its status as a tag (`absent:unseen`, `absent:lexicon`, `absent:regression`).
An `unseen` term is outside the validation layer by definition, so a failure
there is expected to come from the interpreter, and is reported as such.

## Where the pilot fits

The usability pilot (`study/pilot/`) uses a few of these rows as task cards
(vague, absent object, unsupported, infeasible probes). Its results are
usability observations from 3 to 5 people, not coverage of this matrix.
