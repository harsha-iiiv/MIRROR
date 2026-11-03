def pytest_configure(config):
    config.addinivalue_line("markers", "slow: long-running simulation tests")
