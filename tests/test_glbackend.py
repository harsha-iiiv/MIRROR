"""Offscreen OpenGL backend choice (mirror/glbackend.py)."""
import subprocess
import sys

from mirror import glbackend as gl


def test_explicit_mujoco_gl_wins():
    for plat in ("linux", "darwin"):
        assert gl.offscreen_backend({"MUJOCO_GL": "egl"}, plat) == "egl"
        assert gl.default_backend({"MUJOCO_GL": "egl", "DISPLAY": ":0"}, plat) == "egl"


def test_macos_defaults_to_cgl():
    assert gl.offscreen_backend({}, "darwin") == "cgl"
    assert gl.default_backend({}, "darwin") == "cgl"
    assert gl.default_backend({"DISPLAY": ":0"}, "darwin") == "cgl"   # XQuartz DISPLAY does not change it


def test_linux_defaults():
    assert gl.offscreen_backend({}, "linux") == "osmesa"
    assert gl.default_backend({}, "linux") == "osmesa"
    assert gl.default_backend({"DISPLAY": ":0"}, "linux") is None     # MuJoCo chooses with a display
    assert gl.offscreen_backend({"DISPLAY": ":0"}, "linux") == "osmesa"


def test_apply_sets_pyopengl_platform_only_for_osmesa_and_egl():
    env = {}
    assert gl.apply("osmesa", env) == "osmesa" and env["PYOPENGL_PLATFORM"] == "osmesa"
    env = {}
    assert gl.apply("cgl", env) == "cgl" and "PYOPENGL_PLATFORM" not in env
    env = {"MUJOCO_GL": "egl"}
    assert gl.apply("cgl", env) == "egl"


def test_describe_names_backend():
    assert gl.describe({}, "darwin").startswith("cgl (default for darwin)")
    assert gl.describe({"MUJOCO_GL": "egl"}, "linux") == "egl (MUJOCO_GL set)"


def test_import_on_this_platform_picks_the_default():
    code = ("import os; os.environ.pop('MUJOCO_GL', None); os.environ.pop('DISPLAY', None);"
            "import mirror.sim, sys; print(os.environ['MUJOCO_GL'])")
    env = {k: v for k, v in __import__("os").environ.items() if k not in ("MUJOCO_GL", "DISPLAY")}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120)
    assert out.stdout.strip() == ("cgl" if sys.platform == "darwin" else "osmesa"), out.stderr[-500:]


def test_doctor_reports_backend(capsys):
    from mirror.cli import main
    main(["doctor"])
    assert "rendering backend" in capsys.readouterr().out
