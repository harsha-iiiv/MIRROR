import os
import tempfile

os.environ.setdefault("MUJOCO_GL", "osmesa")
os.environ.setdefault("PYOPENGL_PLATFORM", os.environ["MUJOCO_GL"])
# Never touch the repository's evaluation ledger from tests.
os.environ["MIRROR_EVAL_LEDGER"] = os.path.join(tempfile.mkdtemp(prefix="ledger-"), "ledger.jsonl")

import pytest  # noqa: E402

from mirror.evaluate.specs import reference_specs  # noqa: E402
from mirror.scenes import reference_scene  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: long-running arm tests (physics rollouts)")


@pytest.fixture(scope="session")
def specs():
    return reference_specs()


@pytest.fixture(scope="session")
def scenes():
    return {n: reference_scene(n) for n in ("demo", "shortcut", "blocked")}


@pytest.fixture(scope="session")
def arm_backend():
    from mirror.arm.backend import ArmBackend
    return ArmBackend()
