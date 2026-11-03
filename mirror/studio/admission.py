"""Per-visitor admission control for the public studio server.

Origin checks only stop other web sites from driving a visitor's browser; any
HTTP client can omit the Origin header. Sessions are therefore bearer
capabilities (an unguessable id plus a secret token), and everything a client
can make the server do is bounded per client address and globally:

  MIRROR_SESSIONS_PER_ADDR_HOUR [12]   new sessions per address per hour
  MIRROR_LIVE_SESSIONS_PER_ADDR [2]    sessions one address holds at once
  MIRROR_UPLOADS_PER_ADDR_HOUR [12]    video uploads per address per hour
  MIRROR_UPLOAD_MB_PER_ADDR_HOUR [200] uploaded megabytes per address per hour
  MIRROR_JOBS_PER_ADDR_HOUR [120]      simulation and interpretation jobs per address per hour
  MIRROR_MODEL_CALLS_PER_ADDR_HOUR [24] model requests per address per hour
  MIRROR_REQUESTS_PER_ADDR_MIN [300]   API requests per address per minute
  MIRROR_TRUST_PROXY [0]               1: take the address from X-Forwarded-For (only behind your own proxy)

An address is a coarse identity (shared networks share it, a determined
client can rotate it); the global session, job and model budgets in
mirror/studio/sessions.py remain the hard ceiling. Requests from the loopback
address without a trusted X-Forwarded-For (a local tunnel) are exempt from the
per-address limits, because they cannot be told apart.
"""
from __future__ import annotations

import ipaddress
import os
import threading
import time
from collections import deque
from typing import Callable, Optional

WINDOWS = {
    # kind: (env, default, window seconds, what)
    "session": ("MIRROR_SESSIONS_PER_ADDR_HOUR", 12, 3600.0, "new sessions"),
    "upload": ("MIRROR_UPLOADS_PER_ADDR_HOUR", 12, 3600.0, "video uploads"),
    "upload_bytes": ("MIRROR_UPLOAD_MB_PER_ADDR_HOUR", 200, 3600.0, "uploaded megabytes"),
    "job": ("MIRROR_JOBS_PER_ADDR_HOUR", 120, 3600.0, "simulation and interpretation jobs"),
    "model": ("MIRROR_MODEL_CALLS_PER_ADDR_HOUR", 24, 3600.0, "model requests"),
    "request": ("MIRROR_REQUESTS_PER_ADDR_MIN", 300, 60.0, "requests"),
}


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, "") or default))
    except ValueError:
        return default


class RateLimited(RuntimeError):
    def __init__(self, detail: str, retry_after: int):
        super().__init__(detail)
        self.detail, self.retry_after = detail, max(1, int(retry_after))


def normalise_address(addr: Optional[str]) -> str:
    """IPv4 as is; IPv6 grouped by /64 (one host usually owns a whole /64)."""
    if not addr:
        return "unknown"
    try:
        ip = ipaddress.ip_address(addr.strip())
    except ValueError:
        return "unknown"
    if ip.version == 6:
        if ip.ipv4_mapped:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


LOOPBACK = "loopback"


def client_address(peer: Optional[str], forwarded_for: Optional[str], trust_proxy: Optional[bool] = None) -> str:
    """The client address limits are counted against.

    Behind a local tunnel that adds no X-Forwarded-For, every visitor arrives from the loopback
    address; per-address limits cannot tell them apart, so they are returned as LOOPBACK, which
    Admission exempts (only the global limits apply). Counting them together would let one
    visitor push the others out."""
    if trust_proxy is None:
        trust_proxy = os.environ.get("MIRROR_TRUST_PROXY", "0") == "1"
    if trust_proxy and forwarded_for:
        # The proxy appends the peer it saw; the right-most entry is the one our proxy wrote.
        return normalise_address(forwarded_for.split(",")[-1])
    try:
        if peer and ipaddress.ip_address(peer.strip()).is_loopback:
            return LOOPBACK
    except ValueError:
        pass
    return normalise_address(peer)


class Admission:
    def __init__(self, limits: Optional[dict] = None, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.limits = {k: (limits or {}).get(k, _env_int(env, d)) for k, (env, d, _w, _what) in WINDOWS.items()}
        self.live_per_addr = (limits or {}).get("live_sessions", _env_int("MIRROR_LIVE_SESSIONS_PER_ADDR", 2))
        self._events: dict[tuple[str, str], deque] = {}
        self._lock = threading.Lock()
        self.refusals = 0

    def _trim(self, q: deque, window: float, now: float) -> None:
        while q and q[0][0] < now - window:
            q.popleft()

    def check(self, kind: str, addr: str, n: int = 1, take: bool = True) -> None:
        """Count ``n`` units of ``kind`` for ``addr`` or raise RateLimited (nothing counted)."""
        env, _d, window, what = WINDOWS[kind]
        limit = self.limits[kind]
        if limit <= 0 or addr == LOOPBACK:
            return
        now = self.clock()
        with self._lock:
            q = self._events.setdefault((kind, addr), deque())
            self._trim(q, window, now)
            used = sum(x[1] for x in q)
            if used + n > limit:
                self.refusals += 1
                retry = int(q[0][0] + window - now) + 1 if q else int(window)
                unit = "per minute" if window <= 60 else "per hour"
                raise RateLimited(f"too many {what} from your network ({limit} {unit}); "
                                  f"try again in about {max(1, retry)} s", retry)
            if take:
                q.append((now, n))

    def give_back(self, kind: str, addr: str, n: int = 1) -> None:
        with self._lock:
            q = self._events.get((kind, addr))
            if q:
                for i in range(len(q) - 1, -1, -1):
                    if q[i][1] == n:
                        del q[i]
                        return

    def sweep(self) -> None:
        now = self.clock()
        with self._lock:
            for key in list(self._events):
                window = WINDOWS[key[0]][2]
                self._trim(self._events[key], window, now)
                if not self._events[key]:
                    del self._events[key]

    def stats(self) -> dict:
        with self._lock:
            return {"limits": dict(self.limits), "live_sessions_per_address": self.live_per_addr,
                    "tracked": len(self._events), "refusals": self.refusals}
