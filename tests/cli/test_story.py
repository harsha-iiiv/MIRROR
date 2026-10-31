"""The full reference story through the real simulator (slow, about a minute)."""
import io

import pytest

from mirror import cli

pytestmark = pytest.mark.slow


def test_story(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRROR_LLM_MODE", "offline")
    buf = io.StringIO()
    code = cli.main(["story", "--out", str(tmp_path / "story")], stream=buf)
    out = buf.getvalue()
    assert code == 0, out
    assert "Interpreter: offline rules" in out
    for n in range(1, 10):
        assert f"\n{n}. " in out
    assert "c_upright_transport_cup.status" in out
    assert "CONSTRAINT_VIOLATED" in out          # old rollout under the corrected spec
    assert "infeasibility PROVEN" in out          # blocked scene
    assert (tmp_path / "story" / "specs.json").exists()
    assert (tmp_path / "story" / "events.json").exists()
