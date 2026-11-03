# Protocol v3 dry run on development cases (not evidence)

Development cases written by the project team; manual edits are team fixtures; mirror_model mode: offline. Run dev-20261102T163609. Not an evaluation result.

| | manual_structured | mirror_model | offline_rules |
|---|---|---|---|
| Attempts by status | completed 18 | completed 18 | completed 18 |
| Turns exact (not measured) | 16/20 (0) | 15/20 (0) | 15/20 (0) |
| Apply turns exact | 16/16 | 11/16 | 11/16 |
| Refusal turns exact (any refusal) | 0/4 (4) | 4/4 (4) | 4/4 (4) |
| False accepts | 0 | 0 | 0 |
| False refusals | 0 | 2 | 2 |
| Wrong-scope turns | 0 | 0 | 0 |
| Clarification turns | 0 | 3 | 3 |
| Invalid or error turns | 0 | 1 | 1 |
| Final specification exact (cases measured) | 18/18 | 13/18 | 13/18 |
| End-to-end success (known; unknown) | 16/17; 1 | 16/17; 1 | 16/17; 1 |
| Executed / violations under gold / proven-infeasible stops | 14 / 1 / 1 | 14 / 1 / 1 | 14 / 1 / 1 |
| Median turn latency (s) | 0.0 | 0.001 | 0.001 |
| Median planning / sim time (s) | 0.22 / 57.152 | 0.228 / 57.792 | 0.228 / 57.792 |
| Median path length (m) | 4.712 | 4.686 | 4.686 |
| Network calls / cost (USD) | 0 / 0 | 0 / 0 | 0 / 0 |

Paired end-to-end success (exact McNemar; unknown cases excluded and counted):

- mirror_model vs offline_rules: both 16, only mirror_model 0, only offline_rules 0, neither 1, unknown 1, p = None
- mirror_model vs manual_structured: both 16, only mirror_model 0, only manual_structured 0, neither 1, unknown 1, p = None
- offline_rules vs manual_structured: both 16, only offline_rules 0, only manual_structured 0, neither 1, unknown 1, p = None

By category (cases, end-to-end success, final spec exact):

- manual_structured: ambiguous 2/2/2; infeasible 1/1/1; negative 1/1/1; positive 12/10/12; unsupported 2/2/2
- mirror_model: ambiguous 2/2/2; infeasible 1/1/1; negative 1/1/1; positive 12/10/7; unsupported 2/2/2
- offline_rules: ambiguous 2/2/2; infeasible 1/1/1; negative 1/1/1; positive 12/10/7; unsupported 2/2/2

Failures (every non-exact turn):

- refused (other type): manual_structured d05 turn 1: got abstain, gold asks (editor team-fixture made no change)
- refused (other type): manual_structured d06 turn 1: got abstain, gold rejects (editor team-fixture made no change)
- refused (other type): manual_structured d07 turn 1: got abstain, gold rejects (editor team-fixture made no change)
- refused (other type): manual_structured d16 turn 1: got abstain, gold asks (editor team-fixture made no change)
- failed: mirror_model d02 turn 1: got apply, gold apply
- failed: mirror_model d03 turn 1: got rejects, gold apply (This does not describe a supported change: object orientation (upright), contact with obstacles, clearance, packing order, or which objects go in the tray.)
- failed: mirror_model d10 turn 2: got apply, gold apply
- failed: mirror_model d12 turn 1: got error, gold apply (InterpretError: add_constraint: id c_keep_clearance_transport_cup already exists)
- failed: mirror_model d15 turn 1: got asks, gold apply (Your correction both requires and relaxes the same rule (upright transport for the block). Which do you mean?)
- failed: offline_rules d02 turn 1: got apply, gold apply
- failed: offline_rules d03 turn 1: got rejects, gold apply (This does not describe a supported change: object orientation (upright), contact with obstacles, clearance, packing order, or which objects go in the tray.)
- failed: offline_rules d10 turn 2: got apply, gold apply
- failed: offline_rules d12 turn 1: got error, gold apply (InterpretError: add_constraint: id c_keep_clearance_transport_cup already exists)
- failed: offline_rules d15 turn 1: got asks, gold apply (Your correction both requires and relaxes the same rule (upright transport for the block). Which do you mean?)

Not measured (replay had no recording for the request; excluded, never replaced): 0 case(s)

Old behaviour re-checked under the gold specification (base-spec rollout on the same scene):

- cases whose gold changes the enforced specification: 12; base rollout passed under its own spec and fails under gold: 6; passes under both (the change did not matter in this scene): 5
- d01: base success, under gold constraint_violated, violated ['c_upright_transport_cup']
- d02: base success, under gold constraint_violated, violated ['c_upright_transport_cup']
- d03: base success, under gold success
- d08: base success, under gold constraint_violated, violated ['c_upright_transport_cup']
- d10: base success, under gold success
- d11: base success, under gold success (unverified: ['o_marker_before_cup'])
- d12: base success, under gold constraint_violated, violated ['c_keep_clearance_transport_cup']
- d13: base success, under gold success
- d14: base success, under gold success
- d15: base success, under gold success
- d17: base success, under gold constraint_violated, violated ['c_upright_transport_cup']
- d18: base success, under gold constraint_violated, violated ['c_upright_transport_cup']

Attempts that did not complete: 0

