# Correction-compiler benchmark (after_prompt_revision)

Cases: 44 (cases.json sha256 `4eaa228264add77b`), written before any interpreter was run on them. Base specification: offline proposal for the reference demonstration after review; scene: shortcut.

| interpreter | model id returned | exact | apply exact | refusal exact | refused (any type) | false accepts | false refusals | wrong rule | scope-error cases | errors |
|---|---|---|---|---|---|---|---|---|---|---|
| offline-rules (offline) | offline-rules | 44/44 (100%) | 32/32 (100%) | 12/12 (100%) | 12/12 (100%) | 0 | 0 | 0 | 0 | 0 |
| gpt-5.5 (record) | gpt-5.5-2026-04-23 | 42/44 (95%) | 31/32 (97%) | 11/12 (92%) | 11/12 (92%) | 1 | 0 | 2 | 2 | 0 |

## offline-rules (offline)

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

## gpt-5.5 (record)

| category | exact |
|---|---|
| absent | 2/3 (67%) |
| ambiguous | 4/4 (100%) |
| contact | 2/2 (100%) |
| contradiction | 1/1 (100%) |
| negation | 5/5 (100%) |
| ordering | 3/3 (100%) |
| paraphrase | 8/8 (100%) |
| phase | 4/4 (100%) |
| scope | 5/6 (83%) |
| tolerance | 4/4 (100%) |
| unsupported | 4/4 (100%) |

Failures (2):

- **s01** (scope) "Only the cup matters, the marker can lie down."  
  gold: `apply {"enforced": {"c_upright_final_cup": {"max_tilt_deg": 15.0}, "c_upright_transport_cup": {"max_tilt_deg": 15.0}}, "inactive_goals": [], "ordering": []}`  
  got: `apply {"enforced": {"c_upright_final_cup": {"max_tilt_deg": 15.0}}, "inactive_goals": ["g_inside_block", "g_inside_marker"], "ordering": []}`  
  flags: scope error on block
- **x01** (absent) "The bowl must stay upright."  
  gold: `asks`  
  got: `apply {"enforced": {"c_upright_final_cup": {"max_tilt_deg": 15.0}, "c_upright_transport_cup": {"max_tilt_deg": 15.0}}, "inactive_goals": [], "ordering": []}`  
  flags: false_accept; scope error on cup
