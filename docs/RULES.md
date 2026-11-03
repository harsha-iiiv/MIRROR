# Rule semantics and support

MIRROR accepts a bounded rule language. Every goal, constraint, phase and ordering it accepts is listed
below with how the correction compiler, the planner and the independent checker treat it. The table is
generated from `mirror/rules_support.py` (`python -m mirror.rules_support`, or `--json` for the web
interface); a test fails if the two drift apart, or if the compiler can produce a rule the planner or the
checker cannot honour.

## Requirement status

| Status | Planner | Checker | Success |
|---|---|---|---|
| confirmed (constraint, ordering) | restricts plans | evaluated; can fail the rollout | gates |
| proposed (constraint, ordering) | ignored | evaluated and reported in `rule_results` (`enforced: false`) | never |
| rejected | ignored | recorded as `not_evaluated` | never |
| goal, proposed or confirmed | packs it | required | gates |
| goal, rejected | ignored | recorded as `not_evaluated` | never |

Goals are the one deliberate exception to "only confirmed items gate success": a proposed goal is the
interpreter's reading of what to pack, and a fresh proposal must be executable and judged. Rejecting a goal
removes it.

## Verdicts

The checker (`mirror.evaluate.check/2`) returns one `RuleResult` per goal, constraint and ordering rule:

- `satisfied`: the evidence shows the rule held. The detail says when it held vacuously (for example, the
  constrained object was never carried).
- `violated`: the evidence shows it did not.
- `unknown`: the evidence cannot settle it: an object is missing from the evidence, a signal is not
  recorded (contacts on playback frames), an event is unresolved, or two events fall within the timing
  margin. An enforced `unknown` makes an otherwise complete rollout `unverified`, never `success`.
- `not_applicable`: the selector matches no object in this scene.
- `not_evaluated`: rejected rules.

Rollout status precedence: execution failure or timeout, then `constraint_violated`, then
`task_incomplete`, then `unverified`, then `success`.

## Phases

- `transport`: from the first step the object is held (both fingers in contact, as detected by the
  simulator, or lifted off its support between held steps) until it is released.
- `final`: the last 0.3 s of the rollout, after the object's last release.
- `always`: every recorded step, including before the grasp.

## Ordering

`OrderingConstraint` (contract 1.2) relates two events: every object matched by `before` reaches
`before_event` strictly before any object matched by `after` reaches `after_event`.

- `grasp_start`: the first step of the object's first carried interval (held label, or lift-off between
  held steps). Held blips shorter than 0.05 s without lift-off are not grasps. On playback frames there is
  no held label; the first lift-off is used, an upper bound on the grasp.
- `placement_complete`: after its last release, the first step from which the object stays inside the
  destination tray and below the settle speed for 0.2 s. If it does not end inside the tray, the placement
  never completed. If it ends inside but is not seen at rest for 0.2 s, the event is unresolved.

Defaults: `before_event = placement_complete`, `after_event = grasp_start` (strict sequencing: pack the
earlier objects before touching the later ones). That is what an ordering meant before the fields existed;
older specifications load with these defaults and serialize unchanged.

Every pair (a, b) with a in `before`, b in `after`, a != b, is judged:

| after event | before event | verdict |
|---|---|---|
| never happened | any | holds (vacuously) |
| happened | never happened | violated |
| happened | happened more than the margin earlier | holds |
| happened | happened more than the margin later | violated |
| happened | within the margin | unknown |
| object missing or event unresolved | | unknown |

The margin is two evidence samples (2 physics steps on the dense log; two frames on playback). Any violated
pair violates the rule; otherwise any unknown pair makes it unknown. Events come from object motion and the
held label only. The plan's declared object order, step notes and the executor's phase labels are never read.

The compiler maps wording to events: "pack the marker before the cup" uses the default; "start with the
block" or "pick up the marker before the cup" use `grasp_start`/`grasp_start`; "the block has to be in the
tray before the cup is placed" uses `placement_complete`/`placement_complete`.

## Clearance

`keep_clearance` measures the exact distance between the object's solid shape (a box; a capsule for the
marker) and every obstacle box. Checker 1 used a bounding-sphere estimate, which reported clearances up to
several centimetres smaller than they were; no historical result enforced a clearance rule. The planner uses
`min_m` as the obstacle margin for free-space transport, proves the rule infeasible when the object already
rests closer than `min_m` (the transport phase begins at that resting pose), and checks the chosen tray spot.
Clearance from another object or from a region is not supported; the compiler asks a question instead. A
clearance without a distance is also answered with a question, never a default.

## Evidence sources

- Dense step log (`dense_state_log`): every physics step; all monitors.
- Playback frames (`playback_frames`): historical rollouts can be re-checked from their stored frames with
  `mirror.evaluate.evidence.log_from_rollout_frames` and `scene_from_rollout`. Positions are rounded to 1 mm
  and sampled at the frame rate; the held label is derived from lift-off; contacts are not available, so
  contact rules are `unknown`. Re-checking all 266 stored eval rollouts that have frames under their own
  rules reproduces every recorded status.

## Support matrix

| Rule | Phase | Variant | Compiler | Planner | Checker | Notes |
|---|---|---|---|---|---|---|
| goal: inside | - | - | yes | enforced | monitored | Proposed goals are pursued and required (a fresh proposal must be executable and judged); rejected goals are ignored. This is the one place a proposed item gates success. |
| upright | final | - | yes | by_construction | monitored | max_tilt_deg in (0, 90] from the compiler, (0, 180] in a spec |
| upright | transport | - | yes | enforced | monitored | max_tilt_deg in (0, 90] from the compiler, (0, 180] in a spec |
| upright | always | - | yes | enforced | monitored | max_tilt_deg in (0, 90] from the compiler, (0, 180] in a spec |
| forbidden_contact | final | obstacle | yes | by_construction | monitored_dense | Contacts are only in the dense step log; on playback frames the verdict is unknown. |
| forbidden_contact | final | table_outside_tray | yes | by_construction | monitored_dense | Contacts are only in the dense step log; on playback frames the verdict is unknown. |
| forbidden_contact | final | other_objects | yes | by_construction | monitored_dense | Contacts are only in the dense step log; on playback frames the verdict is unknown. |
| forbidden_contact | transport | obstacle | yes | enforced | monitored_dense | Contacts are only in the dense step log; on playback frames the verdict is unknown. |
| forbidden_contact | transport | table_outside_tray | no | none | monitored_dense | Unsatisfiable as defined: the object rests on the table when its grasp begins (and before it), so the checker would always report a contact. The compiler asks whether the user means 'must not end up on the table' (final). |
| forbidden_contact | transport | other_objects | yes | by_construction | monitored_dense | Contacts are only in the dense step log; on playback frames the verdict is unknown. |
| forbidden_contact | always | obstacle | yes | enforced | monitored_dense | Contacts are only in the dense step log; on playback frames the verdict is unknown. |
| forbidden_contact | always | table_outside_tray | no | none | monitored_dense | Unsatisfiable as defined: the object rests on the table when its grasp begins (and before it), so the checker would always report a contact. The compiler asks whether the user means 'must not end up on the table' (final). |
| forbidden_contact | always | other_objects | yes | by_construction | monitored_dense | Contacts are only in the dense step log; on playback frames the verdict is unknown. |
| keep_clearance | final | obstacle | yes | enforced | monitored | Clearance is measured to obstacles (walls, boxes) only. Clearance from another object or an arbitrary region is not supported. |
| keep_clearance | transport | obstacle | yes | enforced | monitored | Clearance is measured to obstacles (walls, boxes) only. Clearance from another object or an arbitrary region is not supported. |
| keep_clearance | always | obstacle | yes | enforced | monitored | Clearance is measured to obstacles (walls, boxes) only. Clearance from another object or an arbitrary region is not supported. |
| keep_clearance | - | other_objects | no | none | none | Not supported: the planner does not keep a distance from these. The compiler asks a clarifying question instead. |
| keep_clearance | - | region | no | none | none | Not supported: the planner does not keep a distance from these. The compiler asks a clarifying question instead. |
| ordering | - | placement_complete/grasp_start | yes | enforced | monitored | pack every 'before' object before touching any 'after' object (default). A confirmed cycle is an invalid specification. |
| ordering | - | grasp_start/grasp_start | yes | by_construction | monitored | start with the 'before' objects. A confirmed cycle is an invalid specification. |
| ordering | - | placement_complete/placement_complete | yes | by_construction | monitored | the 'before' objects are in the tray first. A confirmed cycle is an invalid specification. |
| ordering | - | grasp_start/placement_complete | no | by_construction | monitored | start the 'before' objects before the 'after' ones are placed. A confirmed cycle is an invalid specification. |

Planner levels: `enforced` restricts the search or reports a proven infeasibility; `by_construction` holds
for every plan the planner can produce. Checker levels: `monitored` works on dense logs and playback frames;
`monitored_dense` needs the dense step log. The `ui` field in the JSON is filled in by the interface.
