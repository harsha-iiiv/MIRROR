# Protocol objective_v1: planner objective, rotation-cost sweep and a never-tilt baseline

Status: design document of a new registration. It changes no earlier result.
Earlier registrations (`results/eval`, `results/eval_muse`) are untouched; this
registration writes only under `results/eval_objective_v1/`.

## Why

The planner chooses the cheapest feasible route with cost = TCP path length +
`rotation_penalty` x gripper rotation, and `rotation_penalty` was 0.05 m/rad in
both earlier registrations. An audit found that the original specification's
tilted cup routes depend on that price: a plan-only sweep over the 90 frozen
layouts of the Muse registration gives tilted cup routes on 54/90 layouts at
0.05, 32/90 at 0.10, 2/90 at 0.20 and 0/90 at 0.40 and 1.0; and in 110 of the
113 paired trials where only the corrected specification was violation-free,
the corrected (upright) run also finished sooner. Both counts are reproduced
exactly in `results/eval_objective_v1/audit_repro/` (layouts regenerated from
their seeds, all 90 scene hashes equal to the frozen ones; nothing executed).

So part of the reported benefit of the correction may come from a rotation
price that undervalues slow wrist rotations, not from the correction itself.
This registration measures how much.

## Step 1: calibrate the objective on development layouts only

`python scripts/run_objective_eval.py calibrate` executes, on development
layouts (split `dev`, seeds 8000-8039 of both layout families; never an
evaluation seed), every feasible cup transport route under the original
specification, at rotation prices 0.05 and 1.0, with the standard controller.
Each executed route segment is a sample: transports (lift + transport + place,
exactly what the planner costs as one transport candidate) and transits (the
empty gripper's move). Translation length L and rotation R are computed from
the plan's poses as the planner's cost reads them.

Model A, `T = c0 + a*L + b*R`, is fitted per segment kind; the rotation price
of record is `b / a` from the transport fit (transport routes decide whether a
carried object is tilted). Model B adds a per-segment term as a diagnostic;
the planner cannot express it. Reported: coefficients, R^2, residual
quantiles, leave-one-layout-out error, a bootstrap over layouts for `b / a`,
and a decision check (how often each price picks the cup route that executed
fastest on development layouts, and the seconds lost when it does not).

## Step 2: registration (frozen before any of its layouts exists)

Strata: primary (eval, mixed family, seeds 85000-85079), shortcut (eval,
shortcut family, seeds 86000-86039), blocked (blocked split, seeds
85000-85009; the slot is the only opening, so it shows what a never-tilt
policy gives up). The freeze refuses overlap with every recorded or reserved
range and any ledger or output file naming these seeds.

Conditions, all executed with the same controller (`SimEnv`, seed 0), checker
and scene per seed:

| condition | planning |
|---|---|
| `original@<cal>` | original specification, calibrated rotation price (objective of record) |
| `original@0.05/0.10/0.20/0.40/1.0` | original specification, sweep values |
| `never_tilt@<cal>` | every carried object (cup, block, marker) upright in transport whatever the specification says; empty-gripper transits unrestricted; checked against the unmodified original specification |
| `corrected@<cal>` | corrected specification at the calibrated price |
| `corrected@0.05` | corrected specification at 0.05 (the earlier registrations' setting, on fresh layouts) |

The never-tilt baseline is a planner wrapper (rules added to a copy of the
specification given to the planner only). No condition is tuned; no baseline
parameter is chosen after seeing any layout of this registration.

Metrics: completion, success, violation-free success under the corrected rule
(primary endpoint), cup tilted on its planned route, corrected-rule
violations, retained beads, path length, rotation and path cost at the
calibrated price, simulated execution time, planning failures (and whether
infeasibility was proven).

Pre-stated analysis (per stratum; every seed and attempt reported):

1. corrected@cal vs original@cal on violation-free success: exact two-sided
   McNemar, Wilson intervals, paired seed-bootstrap 95% CI.
2. Retained benefit = (VFS corrected@cal − VFS original@cal) /
   (VFS corrected@0.05 − VFS original@0.05), seed-bootstrap 95% CI.
3. The sweep: every metric for original@lambda at each lambda.
4. Paired execution time and calibrated path cost: corrected@cal vs
   original@cal, never_tilt@cal vs corrected@cal and vs original@cal.
5. Trade-offs: layouts where one condition completes and another does not;
   the blocked stratum per condition.

Hypotheses (stated before the run, reported whatever the outcome): H1
original@cal tilts the cup on fewer layouts than original@0.05; H2 the
corrected-minus-original gap is smaller at the calibrated price than at 0.05;
H3 never-tilt completes no blocked layout and is never faster on average than
corrected@cal on the other strata.

## Running

    python scripts/run_objective_eval.py freeze      # writes freeze.json; commit it
    # from a clean checkout of that commit:
    python scripts/run_objective_eval.py run --attempt attempt_1 --workers 2
    python scripts/run_objective_eval.py report --attempt attempt_1

The runner refuses a dirty checkout, an uncommitted manifest, or any change
to the code under `mirror/`, the objective modules, the calibration file, the
specification pair or the runtime versions since the freeze. Layout accesses
go to `results/eval_objective_v1/ledger.jsonl`.

## Limits

* One controller and one physics model; times are simulated seconds under the
  executor's trapezoidal timing, not a real arm.
* The rotation price is calibrated to execution time only; it does not price
  risk. A time-calibrated objective is a fairer baseline, not the only
  reasonable one; the sweep shows the whole range.
* One specification pair (the Muse registration's replayed pair).
