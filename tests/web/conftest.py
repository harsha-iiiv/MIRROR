def pytest_configure(config):
    config.addinivalue_line("markers", "slow: needs a headless browser or a full export")
