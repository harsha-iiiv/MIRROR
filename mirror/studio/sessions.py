"""Per-visitor sessions, the simulation job gate and the model-call budget.

A public deployment runs one StudioService per visitor. Three limits keep a
small machine responsive:

* sessions: at most MIRROR_MAX_SESSIONS (default 6) at once; a session idle
  for MIRROR_SESSION_IDLE_S seconds (default 1800) is closed. When the server
  is full, the least recently used idle session makes room; if every session
  is busy, new visitors get a clear "server busy" answer.
* simulation jobs: at most MIRROR_MAX_JOBS (default 2) run at once across all
  sessions; the rest wait in arrival order and see their place in the queue.
  Each session runs one job at a time.
* model calls: live model requests only when MIRROR_LLM_MODE=live, limited to
  MIRROR_MODEL_CALLS_PER_HOUR across the server (default 40, sliding hour) and
  MIRROR_MODEL_CALLS_PER_SESSION per session (default 8). Past either limit,
  or on any model error, the offline rule-based interpreter is used and the
  interface says so.
"""
from __future__ import annotations

import os
import secrets
import shutil
import threading
import time
from collections import deque
from typing import Any, Callable, Optional


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


class ServerBusy(RuntimeError):
    """Every session slot is taken by a visitor with work in progress."""


class SessionBusy(RuntimeError):
    """This session already has a job queued or running."""


class SessionExpired(KeyError):
    """Unknown or expired session id."""


# --------------------------------------------------------------- job gate ---


class JobGate:
    """At most ``max_jobs`` simulation jobs run at once; others wait in FIFO order."""

    def __init__(self, max_jobs: Optional[int] = None):
        self.max_jobs = max(1, max_jobs if max_jobs is not None else _env_int("MIRROR_MAX_JOBS", 2))
        self._cond = threading.Condition()
        self.running = 0
        self._waiting: list[tuple[Any, Callable[[Any], None]]] = []

    def _renumber(self) -> None:
        for i, (job, notify) in enumerate(self._waiting):
            if getattr(job, "queue_position", None) != i + 1:
                job.queue_position = i + 1
                try:
                    notify(job)
                except Exception:  # noqa: BLE001
                    pass

    def acquire(self, job: Any, notify: Callable[[Any], None] = lambda j: None,
                cancelled: Callable[[], bool] = lambda: False) -> bool:
        with self._cond:
            self._waiting.append((job, notify))
            self._renumber()
            while self.running >= self.max_jobs or self._waiting[0][0] is not job:
                if cancelled():
                    self._waiting = [w for w in self._waiting if w[0] is not job]
                    self._renumber()
                    self._cond.notify_all()
                    return False
                self._cond.wait(timeout=0.5)
            self._waiting.pop(0)
            self.running += 1
            job.queue_position = 0
            self._renumber()
            return True

    def release(self) -> None:
        with self._cond:
            self.running = max(0, self.running - 1)
            self._cond.notify_all()

    def stats(self) -> dict:
        with self._cond:
            return {"running": self.running, "queued": len(self._waiting), "max": self.max_jobs}


# ------------------------------------------------------------ model budget ---


class ModelBudget:
    """Sliding one-hour budget for the server plus a per-session budget."""

    def __init__(self, per_hour: Optional[int] = None, per_session: Optional[int] = None,
                 clock: Callable[[], float] = time.monotonic):
        self.per_hour = per_hour if per_hour is not None else _env_int("MIRROR_MODEL_CALLS_PER_HOUR", 40)
        self.per_session = per_session if per_session is not None else _env_int("MIRROR_MODEL_CALLS_PER_SESSION", 8)
        self.clock = clock
        self._calls: deque = deque()
        self._by_session: dict[str, int] = {}
        self._lock = threading.Lock()

    def _trim(self) -> None:
        cutoff = self.clock() - 3600.0
        while self._calls and self._calls[0] < cutoff:
            self._calls.popleft()

    def take(self, session_id: str) -> Optional[str]:
        """Reserve one model request. Returns None if allowed, else the reason it is not."""
        with self._lock:
            self._trim()
            if self._by_session.get(session_id, 0) >= self.per_session:
                return f"this session's model budget is used up ({self.per_session} requests)"
            if len(self._calls) >= self.per_hour:
                return f"the server's model budget for this hour is used up ({self.per_hour} requests per hour)"
            self._calls.append(self.clock())
            self._by_session[session_id] = self._by_session.get(session_id, 0) + 1
            return None

    def remaining_hour(self) -> int:
        with self._lock:
            self._trim()
            return max(0, self.per_hour - len(self._calls))

    def remaining_session(self, session_id: str) -> int:
        with self._lock:
            return max(0, self.per_session - self._by_session.get(session_id, 0))

    def forget(self, session_id: str) -> None:
        with self._lock:
            self._by_session.pop(session_id, None)


class BudgetExceeded(Exception):
    """Raised by BudgetedClient instead of calling the model."""


def budget_error_class():
    """BudgetExceeded as a subclass of the interpreter's LLMError when available,
    so callers that catch model errors catch it too."""
    try:
        from ..interpret import LLMError
    except Exception:  # noqa: BLE001
        return BudgetExceeded

    class _Exceeded(LLMError, BudgetExceeded):
        pass
    return _Exceeded


class BudgetedClient:
    """Wraps an interpreter client: every structured model request takes one
    unit of budget first. Everything else is delegated unchanged."""

    def __init__(self, inner: Any, budget: ModelBudget, session_id: str):
        self._inner = inner
        self._budget = budget
        self._sid = session_id
        self._exc = budget_error_class()

    def complete_json(self, *a, **k):
        why = self._budget.take(self._sid)
        if why:
            raise self._exc(why)
        return self._inner.complete_json(*a, **k)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def llm_mode() -> str:
    """live | replay | offline. A key alone never enables live calls."""
    m = (os.environ.get("MIRROR_LLM_MODE") or "offline").strip().lower()
    return m if m in ("live", "replay", "offline") else "offline"


def make_client(budget: Optional[ModelBudget], session_id: str):
    """The interpreter client for one session, or None when the interpreter package is absent."""
    try:
        from ..interpret import LLMClient
    except Exception:  # noqa: BLE001
        return None
    mode = llm_mode()
    if mode == "offline":
        return LLMClient(mode="offline")
    client = LLMClient.from_env(mode=mode)
    if mode == "live" and budget is not None:
        return BudgetedClient(client, budget, session_id)
    return client


def llm_public(budget: Optional[ModelBudget], session_id: Optional[str] = None) -> dict:
    """What the browser may know about the model: never the key."""
    from urllib.parse import urlparse
    mode = llm_mode()
    out: dict[str, Any] = {"mode": mode}
    if mode != "offline":
        out["model"] = os.environ.get("MIRROR_LLM_MODEL") or _default_model()
    if mode == "live":
        out["endpoint_host"] = urlparse(os.environ.get("MIRROR_LLM_BASE_URL") or _default_base()).hostname
        if budget is not None:
            out["calls_per_hour"] = budget.per_hour
            out["calls_remaining_hour"] = budget.remaining_hour()
            out["calls_per_session"] = budget.per_session
            if session_id:
                out["calls_remaining_session"] = budget.remaining_session(session_id)
    return out


def _default_model() -> str:
    try:
        from ..interpret.llm import DEFAULT_MODEL
        return DEFAULT_MODEL
    except Exception:  # noqa: BLE001
        return ""


def _default_base() -> str:
    try:
        from ..interpret.llm import DEFAULT_BASE_URL
        return DEFAULT_BASE_URL
    except Exception:  # noqa: BLE001
        return ""


# --------------------------------------------------------- session manager ---


class SessionManager:
    def __init__(self, factory: Optional[Callable[[str, "SessionManager"], Any]] = None, *,
                 max_sessions: Optional[int] = None, idle_s: Optional[float] = None,
                 gate: Optional[JobGate] = None, budget: Optional[ModelBudget] = None,
                 clock: Callable[[], float] = time.monotonic):
        self.max_sessions = max(1, max_sessions if max_sessions is not None else _env_int("MIRROR_MAX_SESSIONS", 6))
        self.idle_s = float(idle_s if idle_s is not None else _env_int("MIRROR_SESSION_IDLE_S", 1800))
        self.gate = gate or JobGate()
        self.budget = budget or ModelBudget()
        self.clock = clock
        self.factory = factory or _default_factory
        self._lock = threading.RLock()
        self._sessions: dict[str, Any] = {}
        self._last: dict[str, float] = {}
        self.started = time.time()

    # -- lifecycle
    def create(self) -> tuple[str, Any]:
        with self._lock:
            self.sweep()
            if len(self._sessions) >= self.max_sessions:
                idle = [sid for sid in sorted(self._last, key=self._last.get) if not self._busy(sid)]
                if not idle:
                    raise ServerBusy(f"all {self.max_sessions} live sessions are in use")
                self.close(idle[0], reason="made room for a new visitor")
            sid = secrets.token_urlsafe(18)
            svc = self.factory(sid, self)
            self._sessions[sid] = svc
            self._last[sid] = self.clock()
            return sid, svc

    def get(self, sid: Optional[str]) -> Any:
        with self._lock:
            self.sweep()
            if not sid or sid not in self._sessions:
                raise SessionExpired(sid or "")
            self._last[sid] = self.clock()
            return self._sessions[sid]

    def touch(self, sid: str) -> None:
        with self._lock:
            if sid in self._last:
                self._last[sid] = self.clock()

    def _busy(self, sid: str) -> bool:
        svc = self._sessions.get(sid)
        return bool(svc is not None and getattr(svc, "busy", lambda: False)())

    def sweep(self) -> list[str]:
        now = self.clock()
        gone = []
        with self._lock:
            for sid in list(self._sessions):
                if now - self._last.get(sid, now) > self.idle_s and not self._busy(sid):
                    self.close(sid, reason="idle for too long")
                    gone.append(sid)
        return gone

    def close(self, sid: str, reason: str = "") -> None:
        with self._lock:
            svc = self._sessions.pop(sid, None)
            self._last.pop(sid, None)
        self.budget.forget(sid)
        if svc is not None and hasattr(svc, "close"):
            svc.close(reason)

    def close_all(self) -> None:
        for sid in list(self._sessions):
            self.close(sid, reason="server stopping")

    def stats(self) -> dict:
        with self._lock:
            return {"active": len(self._sessions), "max": self.max_sessions, "idle_expiry_s": int(self.idle_s),
                    "busy": sum(1 for s in self._sessions if self._busy(s))}


def _default_factory(sid: str, mgr: SessionManager):
    from .session_adapter import StudioService
    return StudioService(session_id=sid, gate=mgr.gate, budget=mgr.budget, one_job=True, private_runtime=True)
