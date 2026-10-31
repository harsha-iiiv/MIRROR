import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _cli():
    spec = importlib.util.spec_from_file_location("run_eval", ROOT / "scripts" / "run_eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_seeds():
    cli = _cli()
    assert cli.parse_seeds("0-3,7, 9") == [0, 1, 2, 3, 7, 9]
    assert cli.parse_seeds("5") == [5]


def test_reference_specs_differ_by_one_scoped_rule():
    from mirror.evaluate import corrected_rules
    from mirror.evaluate.specs import reference_specs
    a, b = reference_specs()
    rules = corrected_rules(a, b)
    assert len(rules) == 1 and rules[0].phase == "transport"
    assert rules[0].applies_to.role == "container"
    assert b.version == a.version + 1
