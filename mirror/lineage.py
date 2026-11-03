"""Immutable identities and lineage for demonstrations, specifications and rollouts.

A *generation* is one demonstration and everything derived from it: the
specification versions interpreted and corrected from it and the rollouts
executed under those versions. Replacing the demonstration, or interpreting
it again from scratch, starts a new generation; the previous one is archived
unchanged so its artifacts stay resolvable against their original
specifications.

Identities (never reused, also across server restarts):

* generation id    ``g<hex milliseconds><hex4>``
* spec version id  ``<generation>.v<n>``                 (n restarts per generation)
* rollout id       ``<generation>.v<n>.<scene key>.<kind><seq>.<hash8>``
* demo playback id ``demo.<generation>.<demo hash8>``

``scene key`` is ``s<seed>`` / ``b<seed>`` / ``e<seed>`` for sampled
development / blocked / evaluation layouts and ``h<scene hash8>`` otherwise;
``seq`` counts rollouts within the generation; ``hash8`` is the start of the
rollout content hash.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

LINEAGE_FORMAT = "mirror-lineage/1"

_ID_LOCK = threading.Lock()
_LAST_MS = 0

GEN_RE = re.compile(r"^g[0-9a-f]{11,}[0-9a-f]{4}$")
ROLLOUT_RE = re.compile(r"^(?P<gen>g[0-9a-f]+)\.v(?P<version>\d+)\.(?P<scene>[sbeh][0-9a-z]+)\."
                        r"(?P<kind>[rc])(?P<seq>\d{3,})\.(?P<hash>[0-9a-f]{8})$")


def _plain(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


def canonical_json(obj: Any) -> str:
    return json.dumps(_plain(obj), sort_keys=True, separators=(",", ":"), default=str)


def sha256_of(obj: Any) -> str:
    """SHA-256 of the canonical JSON form of a model, dict or list (bytes are hashed as is)."""
    if isinstance(obj, (bytes, bytearray)):
        return hashlib.sha256(obj).hexdigest()
    return hashlib.sha256(canonical_json(obj).encode()).hexdigest()


def file_sha256(path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def new_generation_id() -> str:
    """Strictly increasing within a process; time plus randomness across processes."""
    global _LAST_MS
    with _ID_LOCK:
        ms = max(int(time.time() * 1000), _LAST_MS + 1)
        _LAST_MS = ms
    return f"g{ms:011x}{secrets.token_hex(2)}"


def spec_version_id(generation_id: str, version: int) -> str:
    return f"{generation_id}.v{int(version)}"


def scene_sha256(scene) -> str:
    return sha256_of(scene)


def scene_key(scene) -> str:
    split = getattr(scene, "split", None)
    seed = getattr(scene, "seed", None)
    prefix = {"dev": "s", "blocked": "b", "eval": "e"}.get(split or "")
    if seed is not None and prefix:
        return f"{prefix}{int(seed)}"
    return "h" + scene_sha256(scene)[:8]


_VOLATILE_REPORT = ("rollout_id", "wall_time_s", "planning_time_s", "condition")


def rollout_content_sha256(rollout, *, spec_sha: str = "", scene_sha: str = "", plan=None) -> str:
    """Hash of what a rollout *is*: its checked outcome, frames, plan, spec and scene.

    Timing and labels are excluded, so the same behaviour always hashes the same."""
    h = hashlib.sha256()
    rep = _plain(rollout.report) if rollout is not None else {}
    rep = {k: v for k, v in (rep or {}).items() if k not in _VOLATILE_REPORT}
    h.update(canonical_json(rep).encode())
    h.update(spec_sha.encode())
    h.update(scene_sha.encode())
    if plan is not None:
        p = _plain(plan)
        if isinstance(p, dict):
            p = {k: v for k, v in p.items() if k != "planning_time_s"}
        h.update(canonical_json(p).encode())
    for f in (getattr(rollout, "frames", None) or []):
        h.update(f"{f.t:.4f}|{f.gripper_width:.5f}|{f.phase}".encode())
        for name in sorted(f.bodies):
            p = f.bodies[name]
            h.update(name.encode())
            h.update(",".join(f"{v:.5f}" for v in (*p.pos, *p.quat)).encode())
    return h.hexdigest()


def make_rollout_id(generation_id: str, version: int, scene, kind: str, seq: int, content_sha: str) -> str:
    if kind not in ("r", "c"):
        raise ValueError("rollout kind is 'r' (run) or 'c' (clarification candidate)")
    return f"{generation_id}.v{int(version)}.{scene_key(scene)}.{kind}{int(seq):03d}.{content_sha[:8]}"


def parse_rollout_id(rid: str) -> Optional[dict]:
    m = ROLLOUT_RE.match(rid or "")
    if not m:
        return None
    d = m.groupdict()
    d["version"] = int(d["version"])
    d["seq"] = int(d["seq"])
    d["label"] = f"{d['kind']}{d['seq']:03d}"
    return d


def demo_playback_id(generation_id: str, demo_sha: str) -> str:
    return f"demo.{generation_id}.{(demo_sha or '0' * 8)[:8]}"


@dataclass
class Generation:
    """One demonstration and everything derived from it."""

    id: str = field(default_factory=new_generation_id)
    created_at: float = field(default_factory=time.time)
    reason: str = "started"
    parent: Optional[str] = None
    demo_id: Optional[str] = None
    demo_sha256: Optional[str] = None
    demo_source: Optional[str] = None
    source_media: list = field(default_factory=list)     # [{id, view, sha256, bytes, duration, ...}]
    calibration: Optional[dict] = None                    # camera / table calibration the trace relied on
    seq: int = 0                                          # rollouts executed in this generation
    archived_at: Optional[float] = None
    archived_reason: str = ""

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def bind_demo(self, demo, source_media: Optional[list] = None, calibration: Optional[dict] = None) -> None:
        self.demo_id = getattr(demo, "demo_id", None) if demo is not None else None
        self.demo_sha256 = sha256_of(demo) if demo is not None else None
        self.demo_source = getattr(demo, "source", None) if demo is not None else None
        if source_media is not None:
            self.source_media = list(source_media)
        if calibration is not None:
            self.calibration = calibration

    @property
    def demo_playback_id(self) -> Optional[str]:
        return demo_playback_id(self.id, self.demo_sha256) if self.demo_sha256 else None

    def public(self) -> dict:
        return {"id": self.id, "created_at": self.created_at, "reason": self.reason, "parent": self.parent,
                "demo": {"demo_id": self.demo_id, "content_sha256": self.demo_sha256, "source": self.demo_source,
                         "playback_id": self.demo_playback_id},
                "source_media": list(self.source_media), "calibration": self.calibration,
                "rollouts_executed": self.seq, "archived_at": self.archived_at,
                "archived_reason": self.archived_reason or None}

    def to_json(self) -> dict:
        return {"format": LINEAGE_FORMAT, **{k: getattr(self, k) for k in self.__dataclass_fields__}}

    @classmethod
    def from_json(cls, d: dict) -> "Generation":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})
