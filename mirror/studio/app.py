"""MIRROR studio web application.

Run:  python -m uvicorn mirror.studio.app:app --host 127.0.0.1 --port 8000
Then open http://127.0.0.1:8000/ . The same frontend also runs without this
server from a static export (scripts/export_static.py), and a static export can
connect to this server as its live backend (see deploy/README.md).

Every visitor gets their own session: POST /api/session returns an id, which
the browser sends as the X-Mirror-Session header (and as ?session= on the
websocket). Limits are set through environment variables, documented in
mirror/studio/sessions.py and deploy/mirror.env.example.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .session_adapter import (MAX_UPLOAD_MB, EvaluationFenceError, StudioService, max_eval_seeds,
                              packages_available)
from .sessions import ServerBusy, SessionBusy, SessionExpired, SessionManager, budgets_public, llm_public

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
SESSION_HEADER = "X-Mirror-Session"
VERSION = "0.2"


class CorrectionIn(BaseModel):
    text: str
    transcript_id: Optional[str] = None


class VideoIn(BaseModel):
    top: str = "bundled"
    side: Optional[str] = None


class DecisionsIn(BaseModel):
    decisions: dict[str, str]
    note: str = ""


class RunIn(BaseModel):
    spec_version: Optional[int] = None
    condition: str = ""
    tags: list[str] = []


class SceneIn(BaseModel):
    preset: Optional[str] = None
    edit: Optional[dict[str, Any]] = None


class RecheckIn(BaseModel):
    rollout_id: str
    spec_version: Optional[int] = None


class EvalIn(BaseModel):
    seeds: list[int]
    spec_a: Optional[int] = None
    spec_b: Optional[int] = None
    split: str = "dev"


class StudioError(Exception):
    def __init__(self, status: int, code: str, detail: str, headers: Optional[dict] = None):
        super().__init__(detail)
        self.status, self.code, self.detail, self.headers = status, code, detail, headers or {}


def allowed_origins(raw: Optional[str] = None) -> list[str]:
    """MIRROR_CORS_ORIGINS: comma-separated origins such as https://example.netlify.app."""
    raw = os.environ.get("MIRROR_CORS_ORIGINS", "") if raw is None else raw
    out = []
    for part in raw.split(","):
        o = part.strip().rstrip("/")
        if o:
            out.append(o)
    return out


def origin_ok(origin: Optional[str], host: Optional[str], allowed: list[str]) -> bool:
    """No Origin header (not a browser request), the same host, or an allowed origin."""
    if not origin:
        return True
    origin = origin.rstrip("/")
    if origin in allowed:
        return True
    try:
        return bool(host) and urlparse(origin).netloc.lower() == host.lower()
    except ValueError:
        return False


def create_app(manager: Optional[SessionManager] = None, *, cors_origins: Optional[list[str]] = None,
               serve_web: bool = True) -> FastAPI:
    mgr = manager or SessionManager()
    origins = allowed_origins() if cors_origins is None else list(cors_origins)
    started = time.time()

    @asynccontextmanager
    async def lifespan(_app):
        yield
        mgr.close_all()

    app = FastAPI(title="MIRROR studio", version=VERSION, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.manager = mgr
    app.state.cors_origins = origins

    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "OPTIONS"],
                           allow_headers=["Content-Type", SESSION_HEADER], expose_headers=["Retry-After"],
                           allow_credentials=False, max_age=600)

    @app.middleware("http")
    async def reject_foreign_origins(request: Request, call_next):
        # CORS only stops the browser reading a response; also refuse to act for other sites.
        if request.method != "OPTIONS" and not origin_ok(request.headers.get("origin"),
                                                          request.headers.get("host"), origins):
            return JSONResponse({"error": "origin_not_allowed", "detail": "this origin may not use the server"},
                                status_code=403)
        return await call_next(request)

    @app.exception_handler(StudioError)
    async def studio_error(_req, exc: StudioError):
        return JSONResponse({"error": exc.code, "detail": exc.detail}, status_code=exc.status, headers=exc.headers)

    def session(request: Request) -> StudioService:
        sid = request.headers.get(SESSION_HEADER) or request.query_params.get("session")
        if not sid:
            raise StudioError(400, "no_session", "start a session first (POST /api/session)")
        try:
            return mgr.get(sid)
        except SessionExpired:
            raise StudioError(404, "session_expired", "this session has ended (idle too long or the server restarted)")

    def guard(fn, *a, **k):
        try:
            return fn(*a, **k)
        except SessionBusy as exc:
            raise StudioError(409, "session_busy", str(exc))
        except EvaluationFenceError as exc:
            raise StudioError(403, "evaluation_fenced", str(exc))
        except KeyError as exc:
            raise StudioError(404, "not_found", f"not found: {exc}")
        except (ValueError, RuntimeError) as exc:
            raise StudioError(409, "conflict", str(exc))

    @app.get("/api/health")
    def health():
        ok, missing = packages_available()
        return {"ok": True, "service": "mirror-studio", "version": VERSION,
                "backend": "fixture" if (not ok or os.environ.get("MIRROR_STUDIO_BACKEND", "").lower() == "fixture") else "live",
                "llm": llm_public(mgr.budget),
                "meta_budgets": budgets_public(mgr.meta_budgets),
                "sessions": mgr.stats(), "jobs": mgr.gate.stats(),
                "eval": {"max_seeds": max_eval_seeds(), "showcase_seeds": [9000, 9099]},
                "uptime_s": round(time.time() - started, 1)}

    @app.post("/api/session")
    def new_session():
        try:
            sid, svc = mgr.create()
        except ServerBusy as exc:
            raise StudioError(503, "server_busy", f"{exc}; try again shortly or watch the recordings",
                              headers={"Retry-After": "30"})
        return {"session": sid, "mode": svc.mode, "idle_expiry_s": int(mgr.idle_s)}

    @app.get("/api/state")
    def state(svc: StudioService = Depends(session)):
        return svc.snapshot()

    @app.get("/api/rollouts/{rollout_id}")
    def rollout(rollout_id: str, svc: StudioService = Depends(session)):
        return guard(svc.rollout_payload, rollout_id)

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str, svc: StudioService = Depends(session)):
        j = svc.jobs.get(job_id)
        if j is None:
            raise StudioError(404, "not_found", "no such job")
        return j.public()

    @app.post("/api/demo/load")
    def demo_load(svc: StudioService = Depends(session)):
        return guard(svc.load_demo)

    @app.post("/api/interpret")
    def interpret(svc: StudioService = Depends(session)):
        return guard(svc.interpret).public()

    @app.post("/api/spec/decisions")
    def decisions(body: DecisionsIn, svc: StudioService = Depends(session)):
        return guard(svc.commit_decisions, body.decisions, body.note)

    @app.post("/api/correct")
    def correct(body: CorrectionIn, svc: StudioService = Depends(session)):
        text = body.text.strip()
        if not text:
            raise StudioError(422, "invalid", "empty correction")
        if len(text) > 600:
            raise StudioError(422, "invalid", "a correction is one or two sentences (600 characters at most)")
        return guard(svc.correct, text, body.transcript_id).public()

    @app.post("/api/run")
    def run(body: RunIn, svc: StudioService = Depends(session)):
        if not svc.session.specs:
            raise StudioError(409, "conflict", "interpret the demonstration first")
        return guard(svc.run, body.spec_version, body.condition[:120], body.tags[:6]).public()

    @app.post("/api/recheck")
    def recheck(body: RecheckIn, svc: StudioService = Depends(session)):
        return guard(svc.recheck, body.rollout_id, body.spec_version)

    @app.post("/api/scene")
    def scene(body: SceneIn, svc: StudioService = Depends(session)):
        if body.preset:
            return guard(svc.set_scene_preset, body.preset)
        if body.edit:
            return guard(svc.edit_scene, body.edit)
        raise StudioError(422, "invalid", "give a preset or an edit")

    @app.post("/api/clarify")
    def clarify(svc: StudioService = Depends(session)):
        if not svc.session.specs:
            raise StudioError(409, "conflict", "interpret the demonstration first")
        return guard(svc.clarify).public()

    @app.post("/api/clarify/answer")
    def answer(body: CorrectionIn, svc: StudioService = Depends(session)):
        return guard(svc.answer, body.text[:600]).public()

    @app.post("/api/evaluate")
    def evaluate(body: EvalIn, svc: StudioService = Depends(session)):
        if len(svc.session.specs) < 2 and body.spec_a is None:
            raise StudioError(409, "conflict", "need two specification versions")
        cap = max_eval_seeds()
        if not body.seeds or len(body.seeds) > cap:
            raise StudioError(422, "too_many_seeds", f"between 1 and {cap} showcase layouts per request")
        return guard(svc.evaluate, body.seeds, body.spec_a, body.spec_b, body.split).public()

    async def read_body(request: Request, limit: int) -> bytes:
        n = request.headers.get("content-length")
        if n is not None and n.isdigit() and int(n) > limit:
            raise StudioError(413, "too_large", f"upload larger than {limit // (1024 * 1024)} MB")
        data = b""
        async for chunk in request.stream():
            data += chunk
            if len(data) > limit:
                raise StudioError(413, "too_large", f"upload larger than {limit // (1024 * 1024)} MB")
        return data

    @app.post("/api/video/upload")
    async def video_upload(request: Request, view: str = "top", svc: StudioService = Depends(session)):
        data = await read_body(request, MAX_UPLOAD_MB * 1024 * 1024)
        try:
            return await asyncio.to_thread(svc.upload_video, data, view)
        except ValueError as exc:
            raise StudioError(422, "invalid_video", str(exc))

    @app.post("/api/video/process")
    def video_process(body: VideoIn, svc: StudioService = Depends(session)):
        return guard(svc.process_video, body.top, body.side).public()

    @app.get("/api/video/file/{vid}")
    def video_file(vid: str, svc: StudioService = Depends(session)):
        path = guard(svc.video_file, vid)
        return FileResponse(path, media_type="video/mp4")

    @app.post("/api/voice")
    async def voice(request: Request, svc: StudioService = Depends(session)):
        data = await read_body(request, 2 * 1024 * 1024)
        from .session_adapter import check_wav
        try:
            check_wav(data)
        except ValueError as exc:
            raise StudioError(422, "invalid_audio", str(exc))
        return guard(svc.transcribe, data).public()

    @app.get("/api/models")
    def models(svc: StudioService = Depends(session)):
        return svc.models_public()

    @app.post("/api/teleop/start")
    def teleop_start(svc: StudioService = Depends(session)):
        return guard(svc.teleop_start)

    @app.post("/api/teleop/cancel")
    def teleop_cancel(svc: StudioService = Depends(session)):
        svc.teleop_cancel()
        return {"ok": True}

    @app.post("/api/teleop/finish")
    def teleop_finish(svc: StudioService = Depends(session)):
        return guard(svc.teleop_finish)

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        if not origin_ok(socket.headers.get("origin"), socket.headers.get("host"), origins):
            await socket.close(code=1008)
            return
        sid = socket.query_params.get("session")
        try:
            svc = mgr.get(sid)
        except SessionExpired:
            await socket.close(code=4404)
            return
        await socket.accept()
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def push(msg):
            loop.call_soon_threadsafe(queue.put_nowait, msg)

        unsubscribe = svc.subscribe(push)

        async def sender():
            while True:
                msg = await queue.get()
                await socket.send_text(json.dumps(msg, default=str))
                if msg.get("type") == "expired":
                    await socket.close(code=4404)
                    return

        task = asyncio.create_task(sender())
        try:
            await socket.send_text(json.dumps({"type": "hello", "mode": svc.mode, "revision": svc.revision}))
            while True:
                raw = await socket.receive_text()
                mgr.touch(sid)
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") == "teleop":
                    try:
                        frame = await asyncio.to_thread(svc.teleop_step, msg.get("dpos", [0, 0, 0]),
                                                        msg.get("drot", [0, 0, 0]), msg.get("grip"))
                        await queue.put({"type": "teleop_frame", "frame": frame})
                    except Exception as exc:  # noqa: BLE001
                        await queue.put({"type": "error", "error": str(exc)})
                elif msg.get("type") == "ping":
                    await queue.put({"type": "pong"})
        except WebSocketDisconnect:
            pass
        finally:
            unsubscribe()
            task.cancel()

    if serve_web and WEB_DIR.exists():
        @app.get("/")
        def index():
            return FileResponse(WEB_DIR / "index.html")

        app.mount("/", StaticFiles(directory=str(WEB_DIR)), name="web")
    return app


app = create_app()
