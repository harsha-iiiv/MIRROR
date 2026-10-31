import os

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: long-running evaluation tests (simulation in the loop)")


def pytest_collection_modifyitems(config, items):
    """Slow tests run only when selected with -m slow or MIRROR_RUN_SLOW=1."""
    if os.environ.get("MIRROR_RUN_SLOW") == "1" or "slow" in (config.getoption("-m") or ""):
        return
    skip = pytest.mark.skip(reason="slow; run with -m slow or MIRROR_RUN_SLOW=1")
    for item in items:
        if "slow" in item.keywords and "tests/evaluate" in str(item.fspath).replace(os.sep, "/"):
            item.add_marker(skip)
