import os

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: long-running studio test (simulation or browser); deselect with -m 'not slow'")


@pytest.fixture(autouse=True)
def _offline_interpreter(monkeypatch):
    """Studio tests never call a model endpoint."""
    for k in ("MIRROR_LLM_BASE_URL", "MIRROR_LLM_MODEL", "MODEL_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MIRROR_LLM_MODE", "offline")


def pytest_collection_modifyitems(config, items):
    """Slow studio tests run only with -m slow or MIRROR_RUN_SLOW=1."""
    if os.environ.get("MIRROR_RUN_SLOW") == "1" or "slow" in (config.getoption("-m") or ""):
        return
    skip = pytest.mark.skip(reason="slow; run with -m slow or MIRROR_RUN_SLOW=1")
    for item in items:
        if "slow" in item.keywords and "tests/studio" in str(item.fspath).replace(os.sep, "/"):
            item.add_marker(skip)
