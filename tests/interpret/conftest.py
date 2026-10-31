import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: long-running test (deselect with -m 'not slow')")
    config.addinivalue_line("markers", "live: calls a real model endpoint (needs MIRROR_LLM_* env)")


@pytest.fixture(autouse=True)
def _no_ambient_llm_env(monkeypatch):
    for k in ("MIRROR_LLM_MODE", "MIRROR_LLM_BASE_URL", "MIRROR_LLM_MODEL", "MODEL_API_KEY",
              "MIRROR_CASSETTE_DIR", "MIRROR_LLM_USAGE_LOG"):
        monkeypatch.delenv(k, raising=False)
