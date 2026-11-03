import pytest


def test_capture_extra_installed():
    pytest.importorskip("cv2", reason="phone-capture tests need the capture extra: pip install 'mirror-robot[capture]'")
