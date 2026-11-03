"""Planner-objective fairness study (registration objective_v1).

Separate from the frozen evaluation modules: it reads mirror.plan only
through PlannerConfig overrides and the public plan() entry point.

  sweep      plan-only rotation-cost sweep on the 90 layouts of the Muse
             registration, and a recount of its saved paired outcomes
  calibrate  route time model fitted on development layouts only
  design     the registration (seeds, conditions, analysis), frozen first
  run        executes every condition with the same controller and physics
  report     paired comparisons and the written report
"""
