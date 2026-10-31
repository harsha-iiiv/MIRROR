# Correction-compiler benchmark (holdout_muse)

Cases: 28 (cases_holdout.json sha256 `37d903dd470fc45a`), written before any interpreter was run on them. Base specification: offline proposal for the reference demonstration after review; scene: shortcut.

| interpreter | model id returned | exact | apply exact | refusal exact | refused (any type) | false accepts | false refusals | wrong rule | scope-error cases | errors |
|---|---|---|---|---|---|---|---|---|---|---|
| muse-spark-1.3 (record) | muse-spark-1.3 | 27/28 (96%) | 19/20 (95%) | 8/8 (100%) | 8/8 (100%) | 0 | 0 | 1 | 1 | 0 |

## muse-spark-1.3 (record)

| category | exact |
|---|---|
| absent | 2/2 (100%) |
| ambiguous | 2/2 (100%) |
| contact | 1/1 (100%) |
| contradiction | 1/1 (100%) |
| negation | 3/3 (100%) |
| ordering | 1/1 (100%) |
| paraphrase | 5/5 (100%) |
| phase | 3/3 (100%) |
| scope | 3/4 (75%) |
| tolerance | 3/3 (100%) |
| unsupported | 3/3 (100%) |

Failures (1):

- **hs04** (scope) "All three objects have to stay upright at all times."  
  gold: `apply {"enforced": {"c_upright_always_all": {"max_tilt_deg": 15.0}, "c_upright_final_cup": {"max_tilt_deg": 15.0}}, "inactive_goals": [], "ordering": []}`  
  got: `apply {"enforced": {"c_upright_always_block": {"max_tilt_deg": 15.0}, "c_upright_always_cup": {"max_tilt_deg": 15.0}, "c_upright_always_marker": {"max_tilt_deg": 15.0}, "c_upright_final_cup": {"max_tilt_deg": 15.0}}, "inactive_goals": [], "ordering": []}`  
  flags: scope error on block, cup, marker
