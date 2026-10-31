# Correction-compiler benchmark (muse)

Cases: 44 (cases.json sha256 `4eaa228264add77b`), written before any interpreter was run on them. Base specification: offline proposal for the reference demonstration after review; scene: shortcut.

| interpreter | model id returned | exact | apply exact | refusal exact | refused (any type) | false accepts | false refusals | wrong rule | scope-error cases | errors |
|---|---|---|---|---|---|---|---|---|---|---|
| muse-spark-1.3 (record) | muse-spark-1.3 | 44/44 (100%) | 32/32 (100%) | 12/12 (100%) | 12/12 (100%) | 0 | 0 | 0 | 0 | 0 |

## muse-spark-1.3 (record)

| category | exact |
|---|---|
| absent | 3/3 (100%) |
| ambiguous | 4/4 (100%) |
| contact | 2/2 (100%) |
| contradiction | 1/1 (100%) |
| negation | 5/5 (100%) |
| ordering | 3/3 (100%) |
| paraphrase | 8/8 (100%) |
| phase | 4/4 (100%) |
| scope | 6/6 (100%) |
| tolerance | 4/4 (100%) |
| unsupported | 4/4 (100%) |

Failures (0):

none
