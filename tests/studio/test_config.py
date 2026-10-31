"""config.js: what the exporter writes, and how the page reads it."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import export_static as ex  # noqa: E402


def test_config_round_trip_and_write_config(tmp_path):
    cfg = {"static": True, "base": "data/", "apiBase": "", "wsBase": "", "repo": "https://example.org/repo"}
    (tmp_path / "config.js").write_text(ex.config_js(cfg))
    assert ex.parse_config_js((tmp_path / "config.js").read_text()) == cfg
    out = ex.write_config(tmp_path, "https://studio.example.org/", "")
    assert out == {**cfg, "apiBase": "https://studio.example.org"}
    assert ex.parse_config_js((tmp_path / "config.js").read_text())["apiBase"] == "https://studio.example.org"
    ex.write_config(tmp_path, "", "")
    assert ex.parse_config_js((tmp_path / "config.js").read_text())["apiBase"] == ""


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "ftp://x.example", "https://user:pw@x.example",
                                 "https://x.example/?q=1", "studio.example.org"])
def test_write_config_rejects_bad_origins(tmp_path, bad):
    (tmp_path / "config.js").write_text(ex.config_js({"static": True}))
    with pytest.raises(ValueError):
        ex.write_config(tmp_path, bad)


@pytest.mark.parametrize("text", ["alert(1)", "window.MIRROR_CONFIG = [1];", "window.MIRROR_CONFIG = {bad};",
                                  "window.MIRROR_CONFIG = {}; alert(1);"])
def test_parse_config_rejects_anything_else(text):
    with pytest.raises(ValueError):
        ex.parse_config_js(text)


def test_cli_write_config(tmp_path):
    (tmp_path / "config.js").write_text(ex.config_js({"static": True, "base": "data/"}))
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "export_static.py"), "--write-config", str(tmp_path),
                        "--api-base", "https://studio.example.org"], capture_output=True, text=True,
                       timeout=60, cwd=ROOT, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    assert ex.parse_config_js((tmp_path / "config.js").read_text())["apiBase"] == "https://studio.example.org"


NODE = shutil.which("node")
CASES = r"""
import { resolveConfig, apiUrl, wsUrl, withLive } from '%s';
const loc = (search = '', extra = {}) => ({ search, protocol: 'https:', host: 'site.example', pathname: '/MIRROR/', ...extra });
const out = {
  server: resolveConfig({}, loc()),
  undefinedCfg: resolveConfig(undefined, loc()),
  recorded: resolveConfig({ static: true, base: 'data/', apiBase: '' }, loc('?live=1')),
  withApiOff: resolveConfig({ static: true, apiBase: 'https://studio.example.org/' }, loc()),
  withApiOn: resolveConfig({ static: true, apiBase: 'https://studio.example.org', wsBase: '' }, loc('?live=1&x=2')),
  badApi: resolveConfig({ static: true, apiBase: 'javascript:alert(1)' }, loc('?live=1')),
  badWs: resolveConfig({ static: true, apiBase: 'http://127.0.0.1:8000', wsBase: 'https://nope' }, loc('?live=1')),
  badBase: resolveConfig({ static: true, base: 'https://evil.example/' }, loc()),
  arr: resolveConfig([1, 2], loc()),
  urls: [
    apiUrl(resolveConfig({ static: true, apiBase: 'https://studio.example.org' }, loc()), 'state'),
    apiUrl(resolveConfig({}, loc()), 'state'),
    wsUrl(resolveConfig({ static: true, apiBase: 'https://studio.example.org' }, loc()), loc(), 'a b'),
    wsUrl(resolveConfig({}, loc()), loc(), 's1'),
    withLive('https://site.example/MIRROR/?x=2', true),
    withLive('https://site.example/MIRROR/?live=1&x=2', false),
  ],
};
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_browser_config_resolution():
    src = (ROOT / "web" / "js" / "config.js").as_uri()
    r = subprocess.run([NODE, "--input-type=module", "-e", CASES % src], capture_output=True, text=True,
                       timeout=60, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout)
    assert o["server"]["recordings"] is False and o["server"]["wantLive"] is True and o["server"]["apiBase"] == ""
    assert o["undefinedCfg"]["wantLive"] is True
    assert o["recorded"]["wantLive"] is False and o["recorded"]["liveAvailable"] is False
    assert o["withApiOff"]["liveAvailable"] is True and o["withApiOff"]["wantLive"] is False
    assert o["withApiOff"]["apiBase"] == "https://studio.example.org"
    assert o["withApiOn"]["wantLive"] is True and o["withApiOn"]["wsBase"] == "wss://studio.example.org"
    assert o["badApi"]["apiBase"] == "" and o["badApi"]["wantLive"] is False
    assert o["badWs"]["wsBase"] == "ws://127.0.0.1:8000"
    assert o["badBase"]["base"] == "data/"
    assert o["arr"]["recordings"] is False
    assert o["urls"] == ["https://studio.example.org/api/state", "api/state",
                         "wss://studio.example.org/ws?session=a%20b", "wss://site.example/MIRROR/ws?session=s1",
                         "https://site.example/MIRROR/?x=2&live=1", "https://site.example/MIRROR/?x=2"]
