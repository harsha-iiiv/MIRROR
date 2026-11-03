"""keep_clearance planning margin (PlannerConfig.clearance_tracking_margin)."""
from dataclasses import replace

import pytest

from mirror.contracts import Constraint, ObjectSelector, Provenance, Status
from mirror.evaluate.specs import reference_specs
from mirror.plan import plan
from mirror.plan.config import DEFAULT_CONFIG
from mirror.scenes import reference_scene


def clearance_spec(m=0.04, phase="transport"):
    base = reference_specs()[1]
    s = base.model_copy(deep=True)
    s.version, s.parent_version = base.version + 1, base.version
    s.constraints.append(Constraint(id="c_cup_clearance", type="keep_clearance", applies_to=ObjectSelector(kind="cup"),
                                    phase=phase, params={"min_m": m}, status=Status.confirmed,
                                    provenance=Provenance(source="manual", version_introduced=s.version)))
    return s


def _dump(p):
    return p.model_dump_json(exclude={"planning_time_s"})


def test_default_is_zero_and_plans_are_unchanged():
    assert DEFAULT_CONFIG.clearance_tracking_margin == 0.0
    zero = replace(DEFAULT_CONFIG, clearance_tracking_margin=0.0)
    for name in ("demo", "shortcut"):
        sc = reference_scene(name)
        for spec in (*reference_specs(), clearance_spec()):
            assert _dump(plan(spec, sc)) == _dump(plan(spec, sc, config=zero))


def test_margin_is_noted_and_only_affects_clearance_rules():
    sc = reference_scene("demo")
    wide = replace(DEFAULT_CONFIG, clearance_tracking_margin=0.005)
    for spec in reference_specs():           # no clearance rule: identical plans
        assert _dump(plan(spec, sc)) == _dump(plan(spec, sc, config=wide))
    p = plan(clearance_spec(), sc, config=wide)
    assert p.status == "ok"
    assert "planned 0.005 m wider than its 0.04 m" in p.reason


def test_tray_spot_check_includes_the_margin():
    sc = reference_scene("demo")
    ok = plan(clearance_spec(0.25, "final"), sc)
    assert ok.status == "ok"
    wide = replace(DEFAULT_CONFIG, clearance_tracking_margin=0.05)
    p = plan(clearance_spec(0.25, "final"), sc, config=wide)
    assert p.status != "ok" and "planning margin" in p.reason
