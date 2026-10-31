# mirror.plan

Task and motion planning for the kit-packing task family.

```python
from mirror.plan import ground, plan, candidate_plans
g = ground(spec, scene)                    # roles, goals, constraints -> instance ids, tray
r = plan(spec, scene)                      # PlanResult
r = plan(spec, scene, prefer="cup_0:around")   # pick among feasible routes
alts = candidate_plans(spec, scene, k=2)   # distinct valid behaviours
```

## What a plan contains

Per object, in order: `move` (empty jaw, from the previous pose), `approach`,
`grasp`, `lift`, `transport`, `place`, `release`, `retreat`. Waypoints are
TCP poses in the gripper frame of `mirror/geometry.py`. The transport note
reads `route=<label>; mode=<mode>; max_tilt_deg=<deg>`; the move note reads
`route=transit:<label>`.

`PlanResult.routes` lists every candidate considered, for transports and for
transits (labels prefixed `transit:`), with feasibility, cost and the reason
for rejection. `chosen` is True on exactly the transport and transit route each
object's plan uses (never on a `no_plan` result). Rejection reasons look like e.g. `violates c_cup_upright_transport (object tilt 90 deg >
max_tilt_deg 15 during transport)` or `envelope height 0.170 m plus 2 x 0.010 m
margin exceeds slot opening 0.100 m`.

## Route labels

| label | meaning |
|---|---|
| `direct` | straight horizontal moves between the start and goal columns |
| `around:<wall>:+y` / `-y` | past that end of a wall (wall-local y) |
| `through_slot:<wall>:<k>:<mode>` | through slot k in mode `upright` or `pitched±30/45/60/90` |
| `detour` | around box obstacles when no wall is in the way |
| `rrt:<modes>` | sampling-based fallback, only when no structured route is feasible |

Modes pitch the gripper about its finger closing axis by 0, ±30, ±45, ±60 or
±90 degrees; the held object's tilt equals the pitch. A mode passes a slot only
if the envelope's exact vertical extent at that orientation (computed from the
palm, finger and object boxes), plus the safety margin on both sides, fits the
opening; for a straight horizontal pass at a fixed orientation that test is
exact. With the reference slot (0.10 m) only ±90 fits the cup (envelope heights:
upright 0.170, 30 deg 0.175, 45 deg 0.159, 60 deg 0.133, 90 deg 0.060 m). In front of a slot the gripper turns its jaw along the wall,
pitches, passes straight through, then turns upright again behind it (at the
lowest level where the turn is collision-free).

Cost = TCP path length + 0.05 m per radian of gripper rotation. The cheapest
feasible candidate is used; ties break on the label. Because rotation costs,
the least-tilted mode that fits a given slot is cheapest, so a rule such as
"within 45 degrees" yields the least-tilted feasible route rather than no plan
whenever such a route exists.

## Grounding

A role covers every scene object of its bound kind and a selector covers every
matching object (decision D5); `ObjectBinding.count` is descriptive and a
mismatch is only a grounding warning. All matching objects are packed.

Rejection reasons name an obstacle only when it is decisive: removing it
alone lets the same search succeed. Around-routes blocked by a wall that
reaches past the workspace say so explicitly.

## Constraints

Only `spec.enforced_constraints()` (status `confirmed`) restrict planning:

* `upright`, phase `transport` or `always`: removes, for the matching objects
  only, every mode whose tilt exceeds `max_tilt_deg`.
* `upright`, phase `final`: objects are always released upright.
* `keep_clearance`: raises the safety margin to `min_m` for that object's transport.
* `forbidden_contact`: planned motions keep a safety margin to obstacles and
  other objects; recorded as a note.
* Confirmed ordering constraints fix the object order; otherwise cup, block,
  marker. If an object's route is blocked by an object that has not been moved
  yet, other admissible orders are tried.

## Collision model

Oriented boxes (separating axis test, vectorised). Obstacles: wall pieces
around slots, box obstacles, the tray floor slab and walls, unmoved objects
at their start poses and placed objects at their planned spots. The moving
body is the palm, both fingers and the held object. Margins: 10 mm in free
space, 3 mm for approach, grasp, place, release and retreat. Segments are
sampled so no body point moves more than the margin between samples.

## No plan, and infeasibility records

`status="no_plan"` lists every rejected candidate. `infeasibility` carries one
of two records (`certificate.py`):

* `scope: any_motion`, `proven: true` (`separating_wall_slot_clearance`).
  An upright rule bounds the object's tilt by alpha, a wall separates the
  object from the tray with no way around or over it inside the workspace,
  and any two points of the gripper and object in the wall's mid-plane span
  more height than all of the wall's openings, for every tilt in [0, alpha]
  (analytic bound, minimum at alpha). Holds for any motion within the
  planner's model of rigid top grasps in a stated depth range. For the cup it
  applies up to about 24 degrees.
* `scope: planner_model`, `proven: false` (`slot_height_over_tilt_interval`).
  Any motion must carry the object's bottom through an opening (proven), and
  over every orientation within alpha the envelope is taller than the
  tallest opening, so no straight horizontal pass at a fixed orientation (the
  planner's primitive) fits. The minimum height is a dense sweep over tilt and
  spin (0.25 deg grid) minus a stated bound of 2 x reach x grid half-spacing
  (about 1 mm). Passes that rotate or move diagonally inside the opening are
  not covered, so the reason still says this is not a proof.

Without either record the reason states that the planner found no plan and
that this is not a proof.

## Determinism

Identical inputs give identical plans. The sampling fallback is seeded from
the query and bounded by an iteration count; a wall-time safety cap exists
and its triggering is reported in the rejection reason.

## Simplifications

* Top grasps only, of upright objects; the grasp is treated as rigid.
* The marker is represented by its square bounding prism (conservative).
* Free-space motion is upright at a few fixed heights; orientation changes
  happen in place (in front of and behind slots, above the release spot).
* Orientation modes are discrete: upright and pitched by 30, 45, 60 or 90 degrees either way; slots are crossed by a straight horizontal pass at a fixed orientation.
* Planning uses exact object and obstacle poses from the scene description.
