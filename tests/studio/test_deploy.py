"""deploy/: the service unit, the settings template and the Netlify configuration."""
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import export_static as ex  # noqa: E402

DEPLOY = ROOT / "deploy"


def test_netlify_toml_matches_the_page():
    """deploy/netlify.toml is what the exporter generates; the policy hashes the current import map."""
    rules = ex.header_rules((ROOT / "web" / "index.html").read_text(), generic=True)
    assert (DEPLOY / "netlify.toml").read_text() == ex.netlify_toml(rules, generic=True)
    conf = tomllib.loads((DEPLOY / "netlify.toml").read_text())
    by_path = {h["for"]: h["values"] for h in conf["headers"]}
    csp = by_path["/*"]["Content-Security-Policy"]
    assert all(h in csp for h in ex.inline_script_hashes((ROOT / "web" / "index.html").read_text()))
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
    assert "frame-ancestors 'none'" in csp and by_path["/*"]["X-Content-Type-Options"] == "nosniff"
    assert by_path["/data/rollouts/*"]["Cache-Control"].endswith("immutable")
    for p in ("/", "/index.html", "/config.js", "/data/session.json"):
        assert by_path[p]["Cache-Control"] == "no-cache"


def test_site_headers_limit_connections_to_the_configured_server(tmp_path):
    (tmp_path / "config.js").write_text(ex.config_js({"static": True, "base": "data/", "apiBase": "", "wsBase": ""}))
    ex.write_config(tmp_path, "https://studio.example.org")
    conf = tomllib.loads((tmp_path / "netlify.toml").read_text())
    csp = next(h for h in conf["headers"] if h["for"] == "/*")["values"]["Content-Security-Policy"]
    assert "connect-src 'self' https://studio.example.org wss://studio.example.org;" in csp
    hdr = (tmp_path / "_headers").read_text()
    assert "wss://studio.example.org" in hdr and "/data/rollouts/*\n  Cache-Control: public, max-age=31536000, immutable" in hdr


def test_service_unit():
    unit = (DEPLOY / "mirror-studio.service").read_text()
    assert "EnvironmentFile=/etc/mirror/mirror.env" in unit
    assert "Restart=always" in unit
    assert re.search(r"--host 127\.0\.0\.1 --port 8000 --workers 1", unit)


def test_env_template_has_placeholders_only_and_known_names():
    env = (DEPLOY / "mirror.env.example").read_text()
    pairs = dict(re.findall(r"^([A-Z_]+)=(.*)$", env, re.M))
    assert pairs["MODEL_API_KEY"].startswith("REPLACE")
    assert "REPLACE" in pairs["MIRROR_CORS_ORIGINS"]
    assert not re.search(r"sk-[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{8,}", env)
    code = "\n".join(p.read_text() for p in (ROOT / "mirror").rglob("*.py"))
    for name in pairs:
        assert name in code, f"{name} is not read by the code"
    readme = (DEPLOY / "README.md").read_text()
    for name in pairs:
        assert f"`{name}`" in readme, f"{name} missing from deploy/README.md"
