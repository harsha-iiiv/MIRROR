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
for rejection, e.g. `violates c_cup_upright_transport (object tilt 90 deg >
max_tilt_deg 15 during transport)` or `envelope height 0.170 m plus 2 x 0.010 m
margin exceeds slot opening 0.100 m`.

## Route labels

| label | meaning |
|---|---|
| `direct` | straight horizontal moves between the start and goal columns |
| `around:<wall>:+y` / `-y` | past that end of a wall (wall-local y) |
| `through_slot:<wall>:<k>:<mode>` | through slot k in mode `upright`, `pitched+90` or `pitched-90` |
| `detour` | around box obstacles when no wall is in the way |
| `rrt:<modes>` | sampling-based fallback, only when no structured route is feasible |

Modes pitch the gripper about its finger closing axis; the held object's tilt
equals the pitch. In front of a slot the gripper turns its jaw along the wall,
pitches, passes straight through, then turns upright again behind it (at the
lowest level where the turn is collision-free).

Cost = TCP path length + 0.05 m per radian of gripper rotation. The cheapest
feasible candidate is used; ties break on the label.

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

## No plan, and infeasibility certificates

`status="no_plan"` lists every rejected candidate. `infeasibility` is set only
when impossibility is proven (`proven: true`); the reason otherwise states
that the planner found no plan and that this is not a proof. The one proof
implemented is `separating_wall_slot_clearance` (see `certificate.py`): an
upright rule bounds the object's tilt, a wall separates the object from the
tray with no way around or over it inside the workspace, and any two points
of the gripper and object in the wall's mid-plane span more height than all
of the wall's openings. It holds within the planner's model (rigid top grasps
in a stated depth range); its assumptions are listed in the certificate.

## Determinism

Identical inputs give identical plans. The sampling fallback is seeded from
the query and bounded by an iteration count; a wall-time safety cap exists
and its triggering is reported in the rejection reason.

## Simplifications

* Top grasps only, of upright objects; the grasp is treated as rigid.
* The marker is represented by its square bounding prism (conservative).
* Free-space motion is upright at a few fixed heights; orientation changes
  happen in place (in front of and behind slots, above the release spot).
* Orientation modes are discrete: upright and pitched by 90 degrees either way.
* Planning uses exact object and obstacle poses from the scene description.
