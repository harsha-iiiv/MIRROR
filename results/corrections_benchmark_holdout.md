# Correction-compiler benchmark (holdout)

Cases: 28 (cases_holdout.json sha256 `37d903dd470fc45a`), written before any interpreter was run on them. Base specification: offline proposal for the reference demonstration after review; scene: shortcut.

| interpreter | model id returned | exact | apply exact | refusal exact | refused (any type) | false accepts | false refusals | wrong rule | scope-error cases | errors |
|---|---|---|---|---|---|---|---|---|---|---|
| offline-rules (offline) | offline-rules | 21/28 (75%) | 13/20 (65%) | 8/8 (100%) | 8/8 (100%) | 0 | 5 | 2 | 0 | 0 |
| gpt-5.5 (record) | gpt-5.5-2026-04-23 | 27/28 (96%) | 19/20 (95%) | 8/8 (100%) | 8/8 (100%) | 0 | 0 | 1 | 0 | 0 |

## offline-rules (offline)

| category | exact |
|---|---|
| absent | 2/2 (100%) |
| ambiguous | 2/2 (100%) |
| contact | 1/1 (100%) |
| contradiction | 1/1 (100%) |
| negation | 1/3 (33%) |
| ordering | 1/1 (100%) |
| paraphrase | 2/5 (40%) |
| phase | 2/3 (67%) |
| scope | 3/4 (75%) |
| tolerance | 3/3 (100%) |
| unsupported | 3/3 (100%) |

Failures (7):

- **hp03** (paraphrase) "When you carry the cup, its opening has to face up the entire way."  
  gold: `apply {"enforced": {"c_upright_final_cup": {"max_tilt_deg": 15.0}, "c_upright_transport_cup": {"max_tilt_deg": 15.0}}, "inactive_goals": [], "ordering": []}`  
  got: `rejects: This does not describe a supported change: object orientation (upright), contact with obstacles, clearance, packing order, or which objects go in the tray.`  
  flags: false_refusal
- **hp04** (paraphrase) "You tilted the cup and the beads fell out; it has to stay level while moving."  
  gold: `apply {"enforced": {"c_upright_final_cup": {"max_tilt_deg": 15.0}, "c_upright_transport_cup": {"max_tilt_deg": 15.0}}, "inactive_goals": [], "ordering": []}`  
  got: `asks: I could not tell whether "you tilted the cup and the beads fell out" adds a requirement or removes one. Should this be required?`  
  flags: false_refusal
- **hp05** (paraphrase) "Upright during transport too, for the cup, not merely at the end."  
  gold: `apply {"enforced": {"c_upright_final_cup": {"max_tilt_deg": 15.0}, "c_upright_transport_cup": {"max_tilt_deg": 15.0}}, "inactive_goals": [], "ordering": []}`  
  got: `asks: Which object does this rule refer to: the cup, the block, the marker?`  
  flags: false_refusal
- **hs02** (scope) "I only care about the cup staying upright when it is carried; tilt the others as you like."  
  gold: `apply {"enforced": {"c_upright_final_cup": {"max_tilt_deg": 15.0}, "c_upright_transport_cup": {"max_tilt_deg": 15.0}}, "inactive_goals": [], "ordering": []}`  
  got: `asks: I could not tell whether "tilt the others as you like" adds a requirement or removes one. Should this be required?`  
  flags: false_refusal
- **hh02** (phase) "The block must stay upright the whole time, from start to finish."  
  gold: `apply {"enforced": {"c_upright_always_block": {"max_tilt_deg": 15.0}, "c_upright_final_cup": {"max_tilt_deg": 15.0}}, "inactive_goals": [], "ordering": []}`  
  got: `apply {"enforced": {"c_upright_final_cup": {"max_tilt_deg": 15.0}, "c_upright_transport_block": {"max_tilt_deg": 15.0}}, "inactive_goals": [], "ordering": []}`
- **hn02** (negation) "Forget about keeping the marker upright."  
  gold: `apply {"enforced": {"c_upright_final_cup": {"max_tilt_deg": 15.0}}, "inactive_goals": [], "ordering": []}`  
  got: `apply {"enforced": {"c_upright_final_cup": {"max_tilt_deg": 15.0}, "c_upright_transport_marker": {"max_tilt_deg": 15.0}}, "inactive_goals": [], "ordering": []}`
- **hn03** (negation) "Pack the objects in whatever order you like."  
  gold: `apply {"enforced": {"c_upright_final_cup": {"max_tilt_deg": 15.0}}, "inactive_goals": [], "ordering": []}`  
  got: `asks: Which order do you want? Name the object that should go first.`  
  flags: false_refusal

## gpt-5.5 (record)

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

- **hs02** (scope) "I only care about the cup staying upright when it is carried; tilt the others as you like."  
  gold: `apply {"enforced": {"c_upright_final_cup": {"max_tilt_deg": 15.0}, "c_upright_transport_cup": {"max_tilt_deg": 15.0}}, "inactive_goals": [], "ordering": []}`  
  got: `apply {"enforced": {"c_upright_transport_cup": {"max_tilt_deg": 15.0}}, "inactive_goals": [], "ordering": []}`
