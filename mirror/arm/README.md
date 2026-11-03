# Arm backend (`mirror.arm`)

An articulated-arm execution backend for MIRROR's simulated kit-packing task.
It runs the same scenes, task specifications, task planner and independent
checker as the default backend. The default backend stays the free-flying
gripper and is labelled the baseline everywhere.

```
mirror run --scene demo --backend arm           # CLI
```
```python
from mirror.backends import get_backend
from mirror.evaluate.trial import execute_trial
arm = get_backend("arm")
rollout, steplog, plan = execute_trial(scene, spec, condition="v2",
                                       planner=arm.plan, env_factory=arm.env)
```

## Robot

* **Arm:** Universal Robots UR10e (default) or UR5e, from MuJoCo Menagerie
  (BSD-3-Clause, Copyright 2018 ROS Industrial Consortium; see
  `mirror/assets/NOTICE`). Kinematics, inertias, joint ranges and collision
  capsules are the vendored model's; visual meshes were removed, so the arm is
  drawn with its collision shapes.
* **Mount:** hanging from a ceiling plate 0.95 m above the table, 0.62 m past
  the table centre in +y (`mirror.arm.model.DEFAULT_MOUNT`). This mount was
  chosen on development seeds 100-107 (they are excluded from the report).
* **Tool:** the MIRROR parallel-jaw gripper, geometrically identical to the
  baseline's, on a 0.30 m tool tube (radius 0.02 m, inside the palm's
  footprint). The gripper frame is the TCP, so planner waypoints mean the same
  thing for both backends.
* **Control:** one PD position servo per joint with velocity feed-forward
  (`ctrl = q_ref + kv/kp * qd_ref`), the vendored model's force limits, and
  gravity compensation of the arm and gripper links routed through the
  actuators (MuJoCo `gravcomp` + `actuatorgravcomp`, so it counts against the
  force limits; the payload is not compensated). The jaw uses the baseline's
  finger actuators unchanged. Object poses are never written during execution.

## From plan to joint motion

1. **Planner (unchanged).** `mirror.plan` decides object order, grasps,
   placements, admissible routes and orientation modes under the spec's
   confirmed rules.
2. **Validation** (`motion.validate`). Every TCP segment of the plan is
   sampled (<= 3 mm, <= 0.02 rad) and solved by damped-least-squares inverse
   kinematics, warm-started along one continuous branch. A segment is rejected
   on IK failure, joint-range violation, a joint jump (singularity/branch
   flip), an arm link within 10 mm of the table, a tray, a wall, a box or an
   object, self-collision, or (on free-space legs) the gripper or the held
   object within 3 mm of anything. Up to four start configurations are tried.
3. **Search** (`backend.ArmBackend`), within a fixed budget (40 validation
   attempts, 8 route switches): the plan as is; its free-space legs carried at
   0.40 m (same xy path and orientations; walls are taller than the workspace
   so this only adds clearance); the tool turned by pi about its axis (the jaw
   is symmetric); then the planner's next-cheapest admissible route for the
   motion that failed. After a collision passing a wall slot, other candidates
   through that slot are skipped (a search heuristic, recorded as "not
   attempted").
4. **Execution** (`env.ArmEnv`). The validated joint path is followed with a
   trapezoidal time scaling that respects the baseline executor's TCP limits
   and the arm's joint speed (rated) and acceleration limits. Grasp, drop and
   settle detection and the per-step `StateLog` are the baseline's, so the
   checker reads arm runs exactly as gripper runs. A task-space tracking
   monitor stops execution if the TCP strays > 20 mm / 0.25 rad for > 0.3 s.

## Failure labels

| outcome | `PlanResult` | meaning |
|---|---|---|
| planner finds no plan | passed through (`planner (before arm validation): ...`) | includes the planner's proven certificate when it has one; a certificate about the gripper and the carried object also holds for the arm, which carries the same gripper and object along planner TCP paths |
| planner plans, arm cannot execute any | `no_plan`, `infeasibility.proven = false`, `kind = "arm_motion_planning_failure"`, `failure_kind` (`ik_failed`, `joint_limit`, `discontinuity`, `collision`, `self_collision`), `search_budget_exhausted` | a planning failure of this backend, never a proof |
| executes, checker decides | `RolloutReport.status` from `mirror.evaluate.check` | the checker never reads planner or backend claims |

## Limitations (simulation only)

* No hardware. Nothing here is a physical demonstration or a sim-to-real result.
* Grasps are the planner's top grasps; the arm adds no grasp planning.
  Contact is MuJoCo's soft contact model (friction pads, bead spheres); grasp
  forces and slip are not calibrated against a real gripper.
* Gravity compensation is ideal (exact model); a real controller's model error
  and joint friction are not modelled. Servo gains are chosen here, not
  identified from a robot.
* The 0.30 m tool tube is a design assumption that keeps the wrist above the
  0.5 m walls during carries. Without it most layouts fail for the arm (wrist
  against the wall).
* A fixed-base arm cannot pass the 0.10 m wall slots with the tool
  horizontal: every through-slot route collides, so the arm never takes the
  slot shortcut. On layouts whose only opening is a slot the arm backend
  reports a planning failure (not proven).
* `keep_clearance` rules are planned at exactly `min_m`; the arm's few
  millimetres of tracking lag can then dip just below the bound (observed:
  0.0396 m against 0.040 m on the reference scene), and the checker
  correctly reports a violation.
