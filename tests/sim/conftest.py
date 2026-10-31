import os

# Offscreen rendering backend for the optional render test; must be chosen
# before mujoco is imported.
os.environ.setdefault("MUJOCO_GL", "osmesa")
os.environ.setdefault("PYOPENGL_PLATFORM", os.environ["MUJOCO_GL"])

import pytest

from mirror.scenes import reference_scene
from mirror.sim import SimEnv
from tests.sim.plans import demo_pack_plan, slot_plan


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: long-running simulation tests")


@pytest.fixture(scope="session")
def demo_scene():
    return reference_scene("demo")


@pytest.fixture(scope="session")
def shortcut_scene():
    return reference_scene("shortcut")


@pytest.fixture(scope="session")
def demo_pack_run(demo_scene):
    env = SimEnv(demo_scene)
    res = env.execute(demo_pack_plan(demo_scene))
    return env, res


@pytest.fixture(scope="session")
def tilted_slot_run(shortcut_scene):
    env = SimEnv(shortcut_scene)
    return env, env.execute(slot_plan(shortcut_scene, tilt_deg=90))


@pytest.fixture(scope="session")
def upright_slot_run(shortcut_scene):
    env = SimEnv(shortcut_scene)
    return env, env.execute(slot_plan(shortcut_scene, tilt_deg=0))
