"""A static handler with HTTP Range support (browsers need it to seek video)."""

import os, re, http.server
class RangeHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
    def end_headers(self):
        self.send_header("Cache-Control", "no-store"); self.send_header("Accept-Ranges", "bytes"); super().end_headers()
    def send_head(self):
        rng = self.headers.get("Range")
        path = self.translate_path(self.path)
        if not rng or os.path.isdir(path) or not os.path.exists(path):
            return super().send_head()
        m = re.match(r"bytes=(\d*)-(\d*)", rng); size = os.path.getsize(path)
        a = int(m.group(1)) if m.group(1) else 0; b = int(m.group(2)) if m.group(2) else size - 1; b = min(b, size - 1)
        f = open(path, "rb"); f.seek(a)
        self.send_response(206); self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {a}-{b}/{size}"); self.send_header("Content-Length", str(b - a + 1)); self.end_headers()
        self._remain = b - a + 1
        return _Limited(f, b - a + 1)
class _Limited:
    def __init__(s, f, n): s.f, s.n = f, n
    def read(s, k=-1):
        if s.n <= 0: return b""
        k = s.n if k < 0 else min(k, s.n); d = s.f.read(k); s.n -= len(d); return d
    def close(s): s.f.close()
