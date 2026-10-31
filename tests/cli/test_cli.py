import io
import json

import pytest

from mirror import cli
from mirror.contracts import TaskSpec
from mirror.interpret import compile_correction, confirm_items, propose_spec, resolve_client
from tests.interpret.fixtures import make_demo


def run(*argv):
    buf = io.StringIO()
    code = cli.main(list(argv), stream=buf)
    return code, buf.getvalue()


@pytest.fixture
def demo_file(tmp_path):
    p = tmp_path / "demo.json"
    p.write_text(make_demo().model_dump_json())
    return p


@pytest.fixture
def tilted_demo_file(tmp_path):
    p = tmp_path / "tilted.json"
    p.write_text(make_demo(tilt_kind="marker", demo_id="tilted").model_dump_json())
    return p


def test_doctor_never_prints_the_key(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "zz-secret-qq")
    code, out = run("doctor")
    assert code == 0
    assert "zz-secret-qq" not in out
    assert "MODEL_API_KEY" in out and "set" in out
    assert "mujoco" in out and "recordings" in out


def test_doctor_flags_missing_key_for_live(monkeypatch):
    monkeypatch.setenv("MIRROR_LLM_MODE", "live")
    code, out = run("doctor")
    assert code == 1 and "FAIL" in out


def test_demo_summary(demo_file):
    code, out = run("demo", "--demo", str(demo_file))
    assert code == 0
    assert "cup_0" in out and "Regularities" in out and "around_+y" in out


def test_demo_json(demo_file):
    code, out = run("demo", "--demo", str(demo_file), "--json")
    assert code == 0 and json.loads(out)["order"] == ["cup_0", "block_0", "marker_0"]


def test_interpret_replays_matching_recording(demo_file):
    code, out = run("interpret", "--demo", str(demo_file))
    assert code == 0
    assert "Interpreter: recorded responses from" in out and "replay" in out
    assert "c_upright_transport_cup" in out and "Open questions" in out


def test_interpret_falls_back_to_offline_without_recording(tilted_demo_file):
    code, out = run("interpret", "--demo", str(tilted_demo_file))
    assert code == 0
    assert "Interpreter: offline rules" in out and "no recording matches" in out
    assert "interpreter: offline-rules" in out


def test_interpret_respects_mode_and_json(monkeypatch, demo_file, capsys):
    monkeypatch.setenv("MIRROR_LLM_MODE", "offline")
    code, out = run("interpret", "--demo", str(demo_file), "--json")
    assert code == 0
    spec = TaskSpec.model_validate_json(out)
    assert spec.interpreter == "offline-rules"
    assert "MIRROR_LLM_MODE=offline" in capsys.readouterr().err


def test_resolve_client_rules(monkeypatch):
    trace = make_demo()
    client, reason = resolve_client(trace)
    assert client.mode == "replay" and "match" in reason
    client, _ = resolve_client(make_demo(tilt_kind="cup", demo_id="x"))
    assert client.offline
    client, reason = resolve_client(trace, env={"MIRROR_LLM_MODE": "offline"})
    assert client.offline and reason == "MIRROR_LLM_MODE=offline"


def _corrected_spec_file(tmp_path, as_list=False):
    s1 = propose_spec(make_demo())
    s2, _ = confirm_items(s1, confirm=[g.id for g in s1.goals] + ["c_upright_final_cup"])
    s3, _ = compile_correction(s2, cli.REFERENCE_CORRECTION)
    p = tmp_path / ("specs.json" if as_list else "spec.json")
    if as_list:
        p.write_text(json.dumps([s.model_dump(mode="json") for s in (s1, s2, s3)]))
    else:
        p.write_text(s3.model_dump_json())
    return p


def test_load_spec_from_session_export(tmp_path):
    p = _corrected_spec_file(tmp_path, as_list=True)
    assert cli.load_spec(p).version == 3
    assert cli.load_spec(p, 2).version == 2
    with pytest.raises(SystemExit):
        cli.load_spec(p, 9)


def test_run_blocked_with_corrected_spec(tmp_path, demo_file):
    spec = _corrected_spec_file(tmp_path)
    code, out = run("run", "--scene", "blocked", "--spec", str(spec), "--demo", str(demo_file),
                    "--out", str(tmp_path / "session"))
    assert code == 0
    assert "PLANNING_FAILED" in out and "infeasibility PROVEN" in out
    assert (tmp_path / "session" / "specs.json").exists()


def test_eval_delegates(monkeypatch, tmp_path):
    script = tmp_path / "run_eval.py"
    script.write_text("CALLS = []\ndef main(argv=None):\n    print('ARGV', argv)\n    return 0\n")
    monkeypatch.setattr(cli, "RUN_EVAL", script)
    code, _ = run("eval", "dev", "--seeds", "0-1")
    assert code == 0


def test_eval_passes_arguments(monkeypatch, tmp_path, capsys):
    script = tmp_path / "run_eval.py"
    script.write_text("def main(argv=None):\n    print('ARGV', argv)\n    return 3\n")
    monkeypatch.setattr(cli, "RUN_EVAL", script)
    code, _ = run("eval", "dev", "--seeds", "0-1", "--out", "x")
    assert code == 3
    assert "ARGV ['dev', '--seeds', '0-1', '--out', 'x']" in capsys.readouterr().out


def test_studio_launches_uvicorn(monkeypatch):
    import uvicorn
    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(app=app, **kw))
    code, out = run("studio", "--port", "8123")
    assert code == 0 and seen["app"] == "mirror.studio.app:app" and seen["port"] == 8123


def test_table_alignment():
    t = cli.table([("a", 1), ("bbb", None)], ["x", "y"])
    lines = t.splitlines()
    assert lines[0].startswith("  x") and set(lines[1].strip()) <= {"-", " "}
    assert len(lines) == 4
