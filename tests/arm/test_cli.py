"""CLI: the arm is opt-in; the gripper stays the default baseline."""
from mirror.cli import build_parser


def test_run_backend_flag_defaults_to_the_gripper():
    p = build_parser()
    assert p.parse_args(["run", "--scene", "demo"]).backend == "gripper"
    assert p.parse_args(["run", "--scene", "demo", "--backend", "arm"]).backend == "arm"
