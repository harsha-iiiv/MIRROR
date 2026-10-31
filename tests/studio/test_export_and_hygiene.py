"""Static export and repository hygiene for the studio's files."""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
OWNED = [ROOT / "mirror" / "studio", ROOT / "web", ROOT / "tests" / "studio", ROOT / "scripts" / "export_static.py"]


def _files():
    for p in OWNED:
        if p.is_file():
            yield p
        else:
            yield from (f for f in p.rglob("*") if f.is_file() and "__pycache__" not in f.parts)


def test_owned_files_contain_no_environment_details():
    banned = [re.compile(r"\b" + w + r"\b", re.I) for w in ("ma" + "na", "fire" + "base", "cloud" + "space", "sand" + "box")]
    banned.append(re.compile("/" + "work" + "space"))
    banned.append(re.compile(r"127\.0\.0\.1:8790"))
    hits = []
    for f in _files():
        text = f.read_text(errors="ignore")
        for b in banned:
            if b.search(text):
                hits.append(f"{f.relative_to(ROOT)}: {b.pattern}")
    assert not hits, hits


def test_export_refuses_fixture_without_flag(tmp_path):
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "export_static.py"), "--backend", "fixture",
                        "--out", str(tmp_path / "s")], capture_output=True, text=True, timeout=60, cwd=ROOT,
                       stdin=subprocess.DEVNULL)
    assert r.returncode != 0 and "allow-fixture" in r.stderr


def test_export_refuses_seeds_outside_the_showcase_range(tmp_path):
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "export_static.py"), "--backend", "fixture",
                        "--allow-fixture", "--seed-base", "10000", "--out", str(tmp_path / "s")],
                       capture_output=True, text=True, timeout=60, cwd=ROOT, stdin=subprocess.DEVNULL)
    assert r.returncode != 0 and "showcase" in r.stderr
    assert not (tmp_path / "s").exists()


def test_studio_sources_never_sample_evaluation_layouts():
    """Structural fence: no studio file asks the layout sampler for the evaluation split."""
    pat = re.compile(r"sample_layout\([^)]*['\"](eval|blocked)['\"]")
    for f in _files():
        if f.suffix == ".py":
            assert not pat.search(f.read_text()), f


def test_export_fixture_site(tmp_path):
    out = tmp_path / "site"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "export_static.py"), "--backend", "fixture",
                        "--allow-fixture", "--seeds", "2", "--out", str(out)], capture_output=True, text=True,
                       timeout=90, cwd=ROOT, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    assert '"static": true' in (out / "config.js").read_text()
    snap = json.loads((out / "data" / "session.json").read_text())
    assert snap["mode"] == "fixture" and snap["static"] is True
    for rr in snap["rollouts"]:
        assert (out / "data" / "rollouts" / f"{rr['id']}.json").exists()
    assert (out / "data" / "rollouts" / "demo.json").exists()
    prov = json.loads((out / "data" / "provenance.json").read_text())
    assert prov["backend"] == "fixture" and prov["showcase_seeds"] == [9000, 9001]
    assert (out / "index.html").exists() and (out / "js" / "viewer.js").exists()
