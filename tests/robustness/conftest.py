import os

os.environ.setdefault("MUJOCO_GL", "osmesa")
os.environ.setdefault("PYOPENGL_PLATFORM", os.environ["MUJOCO_GL"])


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: long-running test (rendering, physics)")
