"""Serve the web frontend as a static replay site for browser checks.

The site is web/ with a static config.js and a data/ folder taken from an
exported studio session (MIRROR_SITE_DATA, or a folder passed explicitly).
"""
from __future__ import annotations

import functools
import http.server
import os
import shutil
import socket
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"

STATIC_CONFIG = ('window.MIRROR_CONFIG = {"static": true, "base": "data/", "apiBase": "", "wsBase": "", '
                 '"repo": "https://github.com/harsha-iiiv/MIRROR"};\n')


def build_site(out: Path, data: Path | None, config_js: str = STATIC_CONFIG) -> Path:
    """Copy web/ to out, write a static config and link the recorded data folder."""
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(WEB, out)
    (out / "config.js").write_text(config_js)
    if data is not None:
        os.symlink(Path(data).resolve(), out / "data")
    return out


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):  # noqa: D401
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class StaticServer:
    """A background HTTP server for a folder; use as a context manager."""

    def __init__(self, folder: Path, handler=_Quiet):
        self.folder = Path(folder)
        self.port = free_port()
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", self.port),
                                                     functools.partial(handler, directory=str(self.folder)))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
