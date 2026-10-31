"""Recorded responses for every Meta endpoint.

A cassette is keyed by SHA-256 over (model, endpoint, normalized request).
Normalization replaces inline media (data URLs, raw uploads) by the SHA-256 of
their bytes and replaces file ids by the SHA-256 of the file that was
uploaded, so the key depends on content, never on server-assigned ids.
Cassettes live in <dir>/<model>/<key[:24]>.json; binary outputs (images) sit
next to them as <key[:24]>_<n>.<ext>. No credentials are ever stored.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Optional


class MetaCassetteMiss(RuntimeError):
    """Replay mode found no recorded response for this request."""


_DATA_URL = re.compile(r"^data:([^;,]+)?(;base64)?,", re.I)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def normalize(obj: Any, file_map: dict[str, str]) -> Any:
    """Copy of ``obj`` with media replaced by content hashes."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "file_id" and isinstance(v, str):
                out[k] = file_map.get(v, v)
            elif k == "data" and isinstance(v, str) and len(v) > 256:  # inline base64 media (input_audio)
                try:
                    out[k] = "sha256:" + sha256_bytes(base64.b64decode(v, validate=True))
                except ValueError:
                    out[k] = "sha256:" + sha256_bytes(v.encode())
            else:
                out[k] = normalize(v, file_map)
        return out
    if isinstance(obj, list):
        return [normalize(v, file_map) for v in obj]
    if isinstance(obj, str) and _DATA_URL.match(obj):
        head, _, payload = obj.partition(",")
        raw = base64.b64decode(payload) if ";base64" in head.lower() else payload.encode()
        return f"{head},sha256:{sha256_bytes(raw)}"
    return obj


def request_key(model: str, endpoint: str, normalized_request: Any) -> str:
    blob = json.dumps({"model": model, "endpoint": endpoint, "request": normalized_request},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def _safe(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", model)


class CassetteStore:
    def __init__(self, directory: Path):
        self.dir = Path(directory)

    def path(self, model: str, key: str) -> Path:
        return self.dir / _safe(model) / f"{key[:24]}.json"

    def load(self, model: str, key: str) -> Optional[dict]:
        p = self.path(model, key)
        if not p.exists():
            return None
        cas = json.loads(p.read_text(encoding="utf-8"))
        if cas.get("key") != key:
            return None
        return cas

    def blob(self, model: str, name: str) -> bytes:
        return (self.dir / _safe(model) / name).read_bytes()

    def save(self, model: str, key: str, endpoint: str, normalized_request: Any, response: dict, *,
             latency_s: float, blobs: Optional[list[tuple[str, bytes]]] = None) -> dict:
        p = self.path(model, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        files = []
        for i, (ext, data) in enumerate(blobs or []):
            name = f"{key[:24]}_{i}.{ext}"
            (p.parent / name).write_bytes(data)
            files.append({"file": name, "sha256": sha256_bytes(data), "bytes": len(data)})
        cas = {"key": key, "model": model, "endpoint": endpoint, "request": normalized_request,
               "response": response, "blobs": files, "latency_s": round(latency_s, 3),
               "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        p.write_text(json.dumps(cas, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        return cas
