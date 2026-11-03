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
  MIRROR_MODEL_CALLS_PER_HOUR across the server (default 80, sliding hour) and
  MIRROR_MODEL_CALLS_PER_SESSION per session (default 16; a video demonstration
  alone may need 11: up to 10 grounding requests plus the proposal). Past either limit,
  or on any model error, the offline rule-based interpreter is used and the
  interface says so.
* other Meta models, counted per request the studio makes, each with a server
  hour budget and a session budget:
    sam   (video tracking)        MIRROR_SAM_CALLS_PER_HOUR / _PER_SESSION     (24 / 8)
    video (video understanding)   MIRROR_VIDEO_CALLS_PER_HOUR / _PER_SESSION   (12 / 2)
    voice (speech to text)        MIRROR_VOICE_CALLS_PER_HOUR / _PER_SESSION   (60 / 10)
  Past a limit the request is refused with a clear message; recordings keep working.
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
        self.per_hour = per_hour if per_hour is not None else _env_int("MIRROR_MODEL_CALLS_PER_HOUR", 80)
        self.per_session = per_session if per_session is not None else _env_int("MIRROR_MODEL_CALLS_PER_SESSION", 16)
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

    def reserve(self, session_id: str, n: int) -> Optional[str]:
        """Take ``n`` requests at once, or none. Returns None if allowed, else why not."""
        if n <= 0:
            return None
        with self._lock:
            self._trim()
            used = self._by_session.get(session_id, 0)
            if used + n > self.per_session:
                return (f"this session's model budget cannot cover it ({n} requests needed, "
                        f"{max(0, self.per_session - used)} of {self.per_session} left)")
            if len(self._calls) + n > self.per_hour:
                return (f"the server's model budget for this hour cannot cover it ({n} requests needed, "
                        f"{max(0, self.per_hour - len(self._calls))} of {self.per_hour} left)")
            now = self.clock()
            self._calls.extend([now] * n)
            self._by_session[session_id] = used + n
            return None

    def release(self, session_id: str, n: int) -> None:
        """Give back ``n`` reserved requests that were not used (newest first)."""
        if n <= 0:
            return
        with self._lock:
            for _ in range(min(n, len(self._calls))):
                self._calls.pop()
            self._by_session[session_id] = max(0, self._by_session.get(session_id, 0) - n)

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


META_BUDGETS = {
    # kind: (hour env, default, session env, default, what it pays for)
    "sam": ("MIRROR_SAM_CALLS_PER_HOUR", 24, "MIRROR_SAM_CALLS_PER_SESSION", 8, "video tracking (SAM)"),
    "video": ("MIRROR_VIDEO_CALLS_PER_HOUR", 12, "MIRROR_VIDEO_CALLS_PER_SESSION", 2, "video understanding"),
    "voice": ("MIRROR_VOICE_CALLS_PER_HOUR", 60, "MIRROR_VOICE_CALLS_PER_SESSION", 10, "speech to text"),
}


def meta_budgets(clock: Callable[[], float] = time.monotonic) -> dict[str, "ModelBudget"]:
    out = {}
    for kind, (h_env, hour, s_env, sess, _what) in META_BUDGETS.items():
        out[kind] = ModelBudget(per_hour=_env_int(h_env, hour), per_session=_env_int(s_env, sess), clock=clock)
    return out


def budgets_public(budgets: Optional[dict], session_id: Optional[str] = None) -> dict:
    out = {}
    for kind, b in (budgets or {}).items():
        d = {"per_hour": b.per_hour, "remaining_hour": b.remaining_hour(), "per_session": b.per_session,
             "pays_for": META_BUDGETS.get(kind, ("", 0, "", 0, kind))[4]}
        if session_id:
            d["remaining_session"] = b.remaining_session(session_id)
        out[kind] = d
    return out


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
    unit of budget first (and, when set, one unit of the client address's hourly
    allowance). Everything else is delegated unchanged."""

    def __init__(self, inner: Any, budget: ModelBudget, session_id: str, admit: Optional[Callable[[], None]] = None):
        self._inner = inner
        self._budget = budget
        self._sid = session_id
        self._exc = budget_error_class()
        self.admit = admit

    def complete_json(self, *a, **k):
        if self.admit is not None:
            try:
                self.admit()
            except Exception as exc:  # noqa: BLE001 - surfaced as a budget refusal (offline fallback)
                raise self._exc(str(exc))
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
    """Sessions are bearer capabilities: an unguessable public id plus a secret token.

    Only the token's SHA-256 is kept. With MIRROR_STUDIO_STATE set, every session is
    persisted there and restored on first use after a server restart (same id and
    token); an idle-expired session's state is deleted. Admission is limited per
    client address (mirror/studio/admission.py) and globally (max sessions, job gate,
    model budgets). A newcomer may only displace a session idle for at least
    MIRROR_EVICT_MIN_IDLE_S seconds (default 300), never one with work in progress."""

    def __init__(self, factory: Optional[Callable[[str, "SessionManager"], Any]] = None, *,
                 max_sessions: Optional[int] = None, idle_s: Optional[float] = None,
                 gate: Optional[JobGate] = None, budget: Optional[ModelBudget] = None,
                 clock: Callable[[], float] = time.monotonic, admission=None, state_dir=None,
                 records=None, media_quota=None, require_token: Optional[bool] = None,
                 evict_min_idle_s: Optional[float] = None):
        from pathlib import Path
        from .admission import Admission
        from .uploads import MediaQuota, UploadLimits
        self.max_sessions = max(1, max_sessions if max_sessions is not None else _env_int("MIRROR_MAX_SESSIONS", 6))
        self.idle_s = float(idle_s if idle_s is not None else _env_int("MIRROR_SESSION_IDLE_S", 1800))
        self.evict_min_idle_s = float(evict_min_idle_s if evict_min_idle_s is not None
                                      else _env_int("MIRROR_EVICT_MIN_IDLE_S", 300))
        self.gate = gate or JobGate()
        self.budget = budget or ModelBudget()
        self.meta_budgets = meta_budgets(clock)
        self.clock = clock
        self.factory = factory or _default_factory
        self.admission = admission if admission is not None else Admission(clock=clock)
        self.require_token = (os.environ.get("MIRROR_REQUIRE_TOKEN", "1") != "0") if require_token is None else require_token
        raw_state = state_dir if state_dir is not None else (os.environ.get("MIRROR_STUDIO_STATE") or None)
        self.state_dir = Path(raw_state) if raw_state else None
        if self.state_dir is not None:
            (self.state_dir / "sessions").mkdir(parents=True, exist_ok=True)
        self.media_quota = media_quota or MediaQuota(UploadLimits.from_env().max_global_bytes)
        self._records = records
        self._lock = threading.RLock()
        self._sessions: dict[str, Any] = {}
        self._last: dict[str, float] = {}
        self._tokens: dict[str, str] = {}      # sid -> sha256(token)
        self._addr: dict[str, str] = {}
        self.started = time.time()
        self.restored = 0

    # -- shared stores
    @property
    def records(self):
        if self._records is None:
            import tempfile
            from pathlib import Path
            from .records import RecordStore
            raw = os.environ.get("MIRROR_RECORD_STORE")
            root = Path(raw) if raw else (self.state_dir / "records" if self.state_dir is not None
                                          else Path(tempfile.mkdtemp(prefix="mirror-records-")))
            self._records = RecordStore(root)
        return self._records

    def _session_dir(self, sid: str):
        return self.state_dir / "sessions" / sid if self.state_dir is not None else None

    @staticmethod
    def _hash(token: str) -> str:
        import hashlib
        return hashlib.sha256(token.encode()).hexdigest()

    def _wire(self, sid: str, svc: Any, addr: str) -> Any:
        """Give a service the manager's shared limits and stores (works with any factory)."""
        for name, val in (("admission", self.admission), ("client_addr", addr), ("records", self.records)):
            if hasattr(svc, name) or name in ("admission", "client_addr", "records"):
                try:
                    setattr(svc, name, val)
                except AttributeError:
                    pass
        media = getattr(svc, "media", None)
        if media is not None and getattr(media, "quota", None) is None:
            media.quota = self.media_quota
        client = getattr(getattr(svc, "session", None), "client", None)
        if isinstance(client, BudgetedClient):
            adm = self.admission
            client.admit = lambda: adm.check("model", addr)
        d = self._session_dir(sid)
        if d is not None and hasattr(svc, "state_dir"):
            svc.state_dir = d
        return svc

    # -- lifecycle
    def create(self, addr: str = "local") -> tuple:
        """New session for a client address. Returns (sid, token, service)."""
        import json
        with self._lock:
            self.sweep()
            self.admission.check("session", addr)
            mine = [s for s in sorted(self._last, key=self._last.get) if self._addr.get(s) == addr]
            from .admission import LOOPBACK
            if addr != LOOPBACK and self.admission.live_per_addr > 0 and len(mine) >= self.admission.live_per_addr:
                idle = [s for s in mine if not self._busy(s)]
                if not idle:
                    self.admission.give_back("session", addr)
                    from .admission import RateLimited
                    raise RateLimited(f"your network already has {len(mine)} live sessions with work in progress", 60)
                self.close(idle[0], reason="replaced by a newer session from the same network")
            if len(self._sessions) >= self.max_sessions:
                now = self.clock()
                idle = [sid for sid in sorted(self._last, key=self._last.get)
                        if not self._busy(sid) and now - self._last[sid] >= self.evict_min_idle_s]
                if not idle:
                    self.admission.give_back("session", addr)
                    raise ServerBusy(f"all {self.max_sessions} live sessions are in use")
                self.close(idle[0], reason="made room for a new visitor")
            sid = secrets.token_urlsafe(18)
            token = secrets.token_urlsafe(24)
            svc = self._wire(sid, self.factory(sid, self), addr)
            self._sessions[sid] = svc
            self._last[sid] = self.clock()
            self._tokens[sid] = self._hash(token)
            self._addr[sid] = addr
            d = self._session_dir(sid)
            if d is not None:
                d.mkdir(parents=True, exist_ok=True)
                (d / "auth.json").write_text(json.dumps({"token_sha256": self._tokens[sid], "addr": addr,
                                                         "created": time.time(), "last_seen": time.time()}))
            return sid, token, svc

    def _check_token(self, sid: str, token: Optional[str]) -> None:
        import hmac
        if not self.require_token:
            return
        if not token:
            raise TokenRequired("this session needs its token (X-Mirror-Token)")
        if not hmac.compare_digest(self._tokens.get(sid, ""), self._hash(token)):
            raise TokenInvalid("wrong session token")

    def _restore(self, sid: str, token: Optional[str]) -> Any:
        """Bring back a persisted session after a restart (caller holds the lock)."""
        import json
        d = self._session_dir(sid)
        if d is None or not (d / "auth.json").exists() or not (d / "service.json").exists():
            raise SessionExpired(sid)
        auth = json.loads((d / "auth.json").read_text())
        self._tokens[sid] = auth.get("token_sha256", "")
        try:
            self._check_token(sid, token)
        except (TokenRequired, TokenInvalid):
            self._tokens.pop(sid, None)
            raise
        if time.time() - float(auth.get("last_seen") or 0) > self.idle_s:
            self._tokens.pop(sid, None)
            import shutil
            shutil.rmtree(d, ignore_errors=True)
            raise SessionExpired(sid)
        if len(self._sessions) >= self.max_sessions:
            now = self.clock()
            idle = [s for s in sorted(self._last, key=self._last.get)
                    if not self._busy(s) and now - self._last[s] >= self.evict_min_idle_s]
            if not idle:
                self._tokens.pop(sid, None)
                raise ServerBusy(f"all {self.max_sessions} live sessions are in use")
            self.close(idle[0], reason="made room for a returning visitor")
        addr = auth.get("addr") or "unknown"
        svc = self._wire(sid, self.factory(sid, self), addr)
        try:
            svc.restore(d)
        except Exception as exc:  # noqa: BLE001 - an unreadable state cannot be trusted
            self._tokens.pop(sid, None)
            try:
                svc.close("restore failed")
            except Exception:  # noqa: BLE001
                pass
            raise SessionExpired(f"{sid} (could not restore: {type(exc).__name__})")
        self._sessions[sid] = svc
        self._last[sid] = self.clock()
        self._addr[sid] = addr
        self.restored += 1
        return svc

    def get(self, sid: Optional[str], token: Optional[str] = None) -> Any:
        with self._lock:
            self.sweep()
            if not sid:
                raise SessionExpired("")
            if sid not in self._sessions:
                return self._restore(sid, token)
            self._check_token(sid, token)
            self._last[sid] = self.clock()
            self._touch_disk(sid)
            return self._sessions[sid]

    def _touch_disk(self, sid: str) -> None:
        import json
        d = self._session_dir(sid)
        if d is None or not (d / "auth.json").exists():
            return
        try:
            auth = json.loads((d / "auth.json").read_text())
            if time.time() - float(auth.get("last_seen") or 0) > 30:
                auth["last_seen"] = time.time()
                (d / "auth.json").write_text(json.dumps(auth))
        except (OSError, ValueError):
            pass

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
            for svc in self._sessions.values():
                media = getattr(svc, "media", None)
                if media is not None:
                    media.sweep()
        self.admission.sweep()
        return gone

    def close(self, sid: str, reason: str = "", keep_state: bool = False) -> None:
        with self._lock:
            svc = self._sessions.pop(sid, None)
            self._last.pop(sid, None)
            self._tokens.pop(sid, None)
            self._addr.pop(sid, None)
        self.budget.forget(sid)
        for b in self.meta_budgets.values():
            b.forget(sid)
        if svc is not None and hasattr(svc, "close"):
            try:
                svc.close(reason, keep_files=keep_state)
            except TypeError:
                svc.close(reason)
        d = self._session_dir(sid)
        if d is not None and not keep_state:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def close_all(self) -> None:
        """Server stopping: release resources but keep persisted state for a restart."""
        for sid in list(self._sessions):
            svc = self._sessions.get(sid)
            if svc is not None and hasattr(svc, "persist"):
                svc.persist()
            self.close(sid, reason="server stopping", keep_state=self.state_dir is not None)

    def stats(self) -> dict:
        with self._lock:
            return {"active": len(self._sessions), "max": self.max_sessions, "idle_expiry_s": int(self.idle_s),
                    "busy": sum(1 for s in self._sessions if self._busy(s)), "restored": self.restored,
                    "persistent": self.state_dir is not None, "tokens_required": self.require_token,
                    "evict_min_idle_s": int(self.evict_min_idle_s)}


class TokenRequired(PermissionError):
    pass


class TokenInvalid(PermissionError):
    pass


def _default_factory(sid: str, mgr: SessionManager):
    from .session_adapter import StudioService
    return StudioService(session_id=sid, gate=mgr.gate, budget=mgr.budget, meta_budgets=mgr.meta_budgets,
                         one_job=True, private_runtime=True)
