"""Immutable, content-addressed challenge records (share and replay).

A record is frozen JSON plus the playback payload of every rollout it names.
Its id is the first 16 hex digits of the SHA-256 of its canonical JSON, so a
share URL can only ever resolve to exactly the evidence it was made from.
Records outlive the session that made them and server restarts. The store is
bounded (MIRROR_RECORDS_MAX [200], MIRROR_RECORDS_MAX_MB [500]); the oldest
records are evicted first and leave a tombstone, so an evicted id answers
"expired" rather than "not found" and is never reused.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from ..lineage import canonical_json, sha256_of

RECORD_FORMAT = "mirror-challenge-record/1"


class RecordExpired(KeyError):
    pass


def record_id_of(record: dict) -> str:
    body = {k: v for k, v in record.items() if k not in ("record_id",)}
    return sha256_of(body)[:16]


def _safe_id(x: str) -> bool:
    return bool(x) and len(x) <= 160 and all(c.isalnum() or c in "._-@" for c in x)


class RecordStore:
    def __init__(self, root: Path, max_records: Optional[int] = None, max_bytes: Optional[int] = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_records = max_records if max_records is not None else int(os.environ.get("MIRROR_RECORDS_MAX") or 200)
        self.max_bytes = max_bytes if max_bytes is not None else int(float(os.environ.get("MIRROR_RECORDS_MAX_MB") or 500) * 2**20)
        self._lock = threading.Lock()
        self._tomb = self.root / "tombstones.jsonl"

    def _dir(self, rid: str) -> Path:
        return self.root / rid

    def put(self, record: dict, payloads: dict[str, dict]) -> str:
        """Store a record and its rollout payloads; returns its id (idempotent)."""
        record = json.loads(canonical_json(record))
        record["format"] = record.get("format") or RECORD_FORMAT
        rid = record_id_of(record)
        record["record_id"] = rid
        with self._lock:
            d = self._dir(rid)
            if (d / "record.json").exists():
                return rid
            tmp = self.root / f".tmp-{uuid.uuid4().hex[:8]}"
            (tmp / "rollouts").mkdir(parents=True)
            for pid, payload in payloads.items():
                if not _safe_id(pid):
                    raise ValueError(f"bad rollout id {pid!r}")
                (tmp / "rollouts" / f"{pid}.json").write_text(json.dumps(payload, separators=(",", ":")))
            (tmp / "record.json").write_text(json.dumps(record, indent=1))
            tmp.replace(d)
            self._evict()
        return rid

    def get(self, rid: str) -> dict:
        if not _safe_id(rid):
            raise KeyError(rid)
        p = self._dir(rid) / "record.json"
        if p.exists():
            return json.loads(p.read_text())
        if self._tombstoned(rid):
            raise RecordExpired(rid)
        raise KeyError(rid)

    def rollout(self, rid: str, rollout_id: str) -> dict:
        rec = self.get(rid)
        if not _safe_id(rollout_id):
            raise KeyError(rollout_id)
        p = self._dir(rid) / "rollouts" / f"{rollout_id}.json"
        if not p.exists():
            raise KeyError(rollout_id)
        if rollout_id not in json.dumps(rec.get("rollouts", {})) and rollout_id not in json.dumps(rec.get("rechecks", [])):
            raise KeyError(rollout_id)
        return json.loads(p.read_text())

    def _tombstoned(self, rid: str) -> bool:
        if not self._tomb.exists():
            return False
        return any(line.strip() == rid for line in self._tomb.read_text().splitlines())

    def _size(self, d: Path) -> int:
        return sum(f.stat().st_size for f in d.rglob("*") if f.is_file())

    def _evict(self) -> None:
        dirs = sorted((d for d in self.root.iterdir() if d.is_dir() and not d.name.startswith(".")),
                      key=lambda d: (d / "record.json").stat().st_mtime if (d / "record.json").exists() else 0)
        sizes = {d: self._size(d) for d in dirs}
        total = sum(sizes.values())
        while dirs and (len(dirs) > self.max_records or total > self.max_bytes):
            old = dirs.pop(0)
            total -= sizes[old]
            with self._tomb.open("a") as fh:
                fh.write(old.name + "\n")
            shutil.rmtree(old, ignore_errors=True)

    def stats(self) -> dict:
        n = sum(1 for d in self.root.iterdir() if d.is_dir() and not d.name.startswith("."))
        return {"records": n, "max_records": self.max_records, "max_mb": round(self.max_bytes / 2**20, 1)}
