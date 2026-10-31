"""MIRROR studio web application.

Run:  python -m uvicorn mirror.studio.app:app --port 8000
Then open http://127.0.0.1:8000/ . The same frontend also runs without this
server from a static export (scripts/export_static.py).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .session_adapter import StudioService

WEB_DIR = Path(__file__).resolve().parents[2] / "web"


class CorrectionIn(BaseModel):
    text: str


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
    split: str = "eval"


def create_app(service: Optional[StudioService] = None) -> FastAPI:
    app = FastAPI(title="MIRROR studio", version="0.1")
    app.state.service = service or StudioService()
    svc: StudioService = app.state.service

    def guard(fn, *a, **k):
        try:
            return fn(*a, **k)
        except KeyError as exc:
            raise HTTPException(404, f"not found: {exc}") from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/health")
    def health():
        return {"ok": True, "mode": svc.mode, "revision": svc.revision}

    @app.get("/api/state")
    def state():
        return svc.snapshot()

    @app.get("/api/rollouts/{rollout_id}")
    def rollout(rollout_id: str):
        return guard(svc.rollout_payload, rollout_id)

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        j = svc.jobs.get(job_id)
        if j is None:
            raise HTTPException(404, "no such job")
        return j.public()

    @app.post("/api/demo/load")
    def demo_load():
        return guard(svc.load_demo)

    @app.post("/api/interpret")
    def interpret():
        return svc.interpret().public()

    @app.post("/api/spec/decisions")
    def decisions(body: DecisionsIn):
        return guard(svc.commit_decisions, body.decisions, body.note)

    @app.post("/api/correct")
    def correct(body: CorrectionIn):
        if not body.text.strip():
            raise HTTPException(422, "empty correction")
        return svc.correct(body.text.strip()).public()

    @app.post("/api/run")
    def run(body: RunIn):
        if not svc.session.specs:
            raise HTTPException(409, "interpret the demonstration first")
        return svc.run(body.spec_version, body.condition, body.tags).public()

    @app.post("/api/recheck")
    def recheck(body: RecheckIn):
        return guard(svc.recheck, body.rollout_id, body.spec_version)

    @app.post("/api/scene")
    def scene(body: SceneIn):
        if body.preset:
            return guard(svc.set_scene_preset, body.preset)
        if body.edit:
            return guard(svc.edit_scene, body.edit)
        raise HTTPException(422, "give a preset or an edit")

    @app.post("/api/clarify")
    def clarify():
        if not svc.session.specs:
            raise HTTPException(409, "interpret the demonstration first")
        return svc.clarify().public()

    @app.post("/api/clarify/answer")
    def answer(body: CorrectionIn):
        return svc.answer(body.text).public()

    @app.post("/api/evaluate")
    def evaluate(body: EvalIn):
        if len(svc.session.specs) < 2 and body.spec_a is None:
            raise HTTPException(409, "need two specification versions")
        if not body.seeds or len(body.seeds) > 64:
            raise HTTPException(422, "between 1 and 64 seeds")
        return svc.evaluate(body.seeds, body.spec_a, body.spec_b, body.split).public()

    @app.post("/api/teleop/start")
    def teleop_start():
        return guard(svc.teleop_start)

    @app.post("/api/teleop/finish")
    def teleop_finish():
        return guard(svc.teleop_finish)

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
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

        task = asyncio.create_task(sender())
        try:
            await socket.send_text(json.dumps({"type": "hello", "mode": svc.mode, "revision": svc.revision}))
            while True:
                raw = await socket.receive_text()
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

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html")

    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=str(WEB_DIR)), name="web")
    return app


app = create_app()
