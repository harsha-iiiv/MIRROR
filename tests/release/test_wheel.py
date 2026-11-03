"""A built wheel ships the vendored robot models, their licences and NOTICE,
and the JSON data the package reads at run time."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow          # builds a wheel (about 30 s); CI runs it in the semantics job

ROOT = Path(__file__).resolve().parents[2]
MODELS = ["universal_robots_ur10e/ur10e.xml", "universal_robots_ur10e/LICENSE",
          "universal_robots_ur5e/ur5e.xml", "universal_robots_ur5e/LICENSE"]
DATA = ["mirror/evaluate/protocol_v3/design.json", "mirror/evaluate/protocol_v3/dev_cases.json",
        "mirror/interpret/benchmark/cases.json", "mirror/interpret/benchmark/base_spec.json"]


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> Path:
    have_pip = subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True).returncode == 0
    if shutil.which("uv") is None and not have_pip:
        pytest.skip("neither uv nor pip is available to build a wheel")
    out = tmp_path_factory.mktemp("wheel")
    src = tmp_path_factory.mktemp("src") / "repo"
    # Build from a copy of the tracked files so the check sees exactly what a release would.
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    for f in files:
        if (ROOT / f).is_file():
            (src / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / f, src / f)
    cmd = (["uv", "build", "--wheel", "--out-dir", str(out)] if shutil.which("uv")
           else [sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "-w", str(out)])
    p = subprocess.run(cmd, cwd=src, capture_output=True, text=True, timeout=600)
    if p.returncode != 0:
        if "failed to fetch" in p.stderr.lower() or "network" in p.stderr.lower():
            pytest.skip("build backend could not be fetched (offline)")
        raise AssertionError(p.stderr[-2000:])
    (whl,) = out.glob("*.whl")
    return whl


def test_wheel_contains_vendored_models_licences_and_notice(wheel):
    names = set(zipfile.ZipFile(wheel).namelist())
    for m in MODELS:
        assert f"mirror/assets/{m}" in names, m
    assert "mirror/assets/NOTICE" in names
    notice = zipfile.ZipFile(wheel).read("mirror/assets/NOTICE").decode()
    assert "BSD-3-Clause" in notice and "MuJoCo Menagerie" in notice
    for lic in ("universal_robots_ur10e/LICENSE", "universal_robots_ur5e/LICENSE"):
        assert zipfile.ZipFile(wheel).read(f"mirror/assets/{lic}") == (ROOT / "mirror" / "assets" / lic).read_bytes()


def test_wheel_contains_runtime_data(wheel):
    names = set(zipfile.ZipFile(wheel).namelist())
    for d in DATA:
        assert d in names, d


def test_installed_package_finds_its_assets(wheel, tmp_path):
    zipfile.ZipFile(wheel).extractall(tmp_path)
    code = ("import mirror.arm.model as m, mirror.evaluate.protocol_v3.access as a; "
            "assert str(m.ASSETS).startswith(%r), m.ASSETS; "
            "assert (m.ASSETS / 'universal_robots_ur10e' / 'ur10e.xml').is_file(); "
            "assert a.DEV_CASES.is_file()") % str(tmp_path)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(tmp_path)
    p = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True,
                       timeout=120)
    assert p.returncode == 0, p.stderr[-2000:]
