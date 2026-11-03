"""A local stand-in for the studio server's challenge API (docs/CHALLENGE_API.md), for
browser tests of the web frontend only.

Everything it returns is synthetic: snapshots and playback come from a fixture export
(key-framed placeholder motion), and every response is labelled provenance "fixture" or
"offline_interpreter". It runs no simulation and calls no model.
"""
from __future__ import annotations

import functools
import hashlib
import http.server
import json
import threading
import time
import urllib.parse
from pathlib import Path

from tests.web.harness import WEB, free_port

FENCED = set(range(30000, 30100)) | set(range(50000, 50100)) | {1234}   # 1234: a synthetic fenced seed inside the dev range


def _h(*parts) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()


class FakeStudio:
    def __init__(self, data: Path, job_delay: float = 0.6):
        self.data = Path(data)
        self.job_delay = job_delay
        self.base = json.loads((self.data / "session.json").read_text())
        self.generation = "g18f3aa01" + "0001"
        self.jobs: dict[str, dict] = {}
        self.challenges: dict[str, dict] = {}
        self.records: dict[str, dict] = {}
        self.payload_generation_override: dict[str, str] = {}   # rollout id -> generation to report
        self.seq = 0
        self.stale_challenge_payloads = False
        self.lock = threading.Lock()
        rs = {r["id"]: r for r in self.base["rollouts"]}
        tags = lambda t: next(r for r in self.base["rollouts"] if t in r["tags"])  # noqa: E731
        self.before_src = tags("world")["id"]
        self.after_src = tags("corrected")["id"]
        self.rs = rs

    # ---------------------------------------------------------------- state
    def snapshot(self):
        s = dict(self.base)
        s.pop("static", None)
        s.pop("export_id", None)
        s["mode"] = "fixture"
        s["generation"] = {"id": self.generation, "reason": "demo_loaded",
                           "demo": {"demo_id": s["demo"]["demo_id"], "content_sha256": _h("demo", self.generation),
                                    "playback_id": f"demo.{self.generation}.{_h('demo', self.generation)[:8]}"}}
        s["capabilities"] = {**(s.get("capabilities") or {}), "voice": False, "video": False}
        return s

    def rollout_payload(self, rid: str):
        src = rid
        if rid.startswith("demo."):
            src = "demo"
        elif "." in rid:
            src = self.before_src if ".r" in rid.rsplit(".", 2)[-2][:2] else self.after_src
        p = json.loads((self.data / "rollouts" / f"{src}.json").read_text())
        p["generation_id"] = self.payload_generation_override.get(rid, self.generation)
        if self.stale_challenge_payloads and "." in rid and not rid.startswith("demo."):
            p["generation_id"] = "g18f2bb0000ff"
        return p

    # ----------------------------------------------------------------- jobs
    def job(self, kind, result, delay=None):
        with self.lock:
            self.seq += 1
            jid = f"j{self.seq}"
        self.jobs[jid] = {"id": jid, "kind": kind, "status": "running", "progress": 0.1, "message": f"{kind} (fixture)",
                          "generation_id": self.generation, "t_done": time.time() + (self.job_delay if delay is None else delay),
                          "_result": result}
        return self.job_view(jid)

    def job_view(self, jid):
        j = self.jobs[jid]
        if j["status"] == "running" and time.time() >= j["t_done"]:
            j["status"], j["progress"] = "done", 1.0
            j["result"] = j["_result"]() if callable(j["_result"]) else j["_result"]
        return {k: v for k, v in j.items() if not k.startswith("_") and k != "t_done"}

    # ------------------------------------------------------------ challenge
    def options(self):
        return {"families": [{"id": "mixed", "split": "dev", "seed_range": [0, 8999]},
                             {"id": "shortcut", "split": "dev", "seed_range": [0, 8999]},
                             {"id": "blocked", "split": "blocked", "seed_range": [0, 8999]},
                             {"id": "reference", "presets": ["demo", "shortcut", "blocked"]}],
                "edits": {}, "max_edits": 8,
                "correction": {"max_chars": 600, "audio": False,
                               "supported": [{"kind": "upright", "phases": ["transport", "final", "always"],
                                              "example": "Keep the cup upright while carrying it."}]},
                "fenced_seed_count": len(FENCED), "tour": None}

    def create(self, body):
        seed = body.get("seed")
        if seed is not None and (seed in FENCED or not (0 <= seed <= 8999)):
            return 403, {"error": "evaluation_fenced", "detail": f"seed {seed} is fenced"}
        with self.lock:
            self.seq += 1
            cid = f"ch{self.seq:04d}"
        n = len(self.challenges) + 1
        before = f"{self.generation}.v1.s{seed or 0}.r{n:03d}.{_h(cid)[:8]}"
        self.challenges[cid] = {"id": cid, "state": "scene_ready", "generation_id": self.generation,
                                "scene": {"family": body.get("family"), "split": "dev", "seed": seed, "edits": body.get("edits", []),
                                          "scene_id": f"dev_{seed}", "content_sha256": _h("scene", seed, json.dumps(body.get("edits", [])))},
                                "base_spec_version_id": f"{self.generation}.v1",
                                "before": {"rollout_id": before, "status": "success", "violations": []},
                                "proposal": None, "revised_spec_version_id": None, "rechecks": [], "after": None, "record_id": None}
        return 200, self.job("challenge", {"challenge_id": cid, "before_rollout_id": before, "status": "success"})

    def correction(self, cid, body):
        ch = self.challenges[cid]
        t = (body.get("text") or "").lower()
        prop = {"text": body.get("text"), "input": "typed", "changed": False, "ops": [], "scope": None,
                "diff": {"added": [], "changed": [], "removed": []}, "question": None, "options": [], "rejected": None,
                "provenance": "offline_interpreter", "fallback_reason": None}
        if "fly" in t:
            prop["rejected"] = "Flying is not a supported rule (fixture)."
        elif "careful" in t:
            prop["question"] = "Careful how: keep the cup upright while carried, or only when placed?"
            prop["options"] = ["Keep the cup upright while carrying it.", "Keep the cup upright when placed."]
        elif "upright" in t:
            prop.update(changed=True, ops=[{"op": "set_status", "target_id": "c_upright_transport_cup", "value": {"status": "confirmed"}}],
                        scope={"objects": ["cup_0"], "phases": ["transport"], "items": ["c_upright_transport_cup"], "kinds": ["constraint"]},
                        diff={"added": [], "changed": ["c_upright_transport_cup"], "removed": []})
        ch["proposal"] = prop
        ch["state"] = "proposed"
        return 200, self.job("challenge_correction", {"proposal": prop})

    def review(self, cid, body):
        ch = self.challenges[cid]
        if not body.get("accept"):
            ch["proposal"], ch["state"] = None, "scene_ready"
            return 200, {"discarded": True}
        ch["revised_spec_version_id"] = f"{self.generation}.v2"
        ch["state"] = "reviewed"
        ch["rechecks"] = [{"rollout_id": ch["before"]["rollout_id"], "status": "constraint_violated",
                           "violations": [{"constraint_id": "c_upright_transport_cup", "object_id": "cup_0", "peak": 90.0, "t_start": 6.0, "t_end": 10.0}]}]
        return 200, {"revised_spec_version_id": ch["revised_spec_version_id"], "rechecks": ch["rechecks"]}

    def execute(self, cid):
        ch = self.challenges[cid]
        after = ch["before"]["rollout_id"].replace(".v1.", ".v2.").replace(".r", ".c")

        def done():
            ch["after"] = {"rollout_id": after, "status": "success", "violations": []}
            ch["state"] = "executed"
            return {"after_rollout_id": after, "status": "success"}
        return 200, self.job("challenge_execute", done)

    def publish(self, cid):
        ch = self.challenges[cid]
        rid = _h("record", cid)[:16]
        ch["record_id"], ch["state"] = rid, "published"
        self.records[rid] = self.record_for(ch, rid, recorded=False)
        return 200, {"record_id": rid, "share_path": f"?challenge={rid}"}

    def record_for(self, ch, rid, recorded):
        return {"format": "mirror-challenge-record/1", "record_id": rid, "created_at": time.time(),
                "scene": ch["scene"], "generation": {"id": ch["generation_id"], "demo": {"demo_id": self.base["demo"]["demo_id"], "content_sha256": _h("demo"), "source": "video_grounded"}},
                "spec_versions": {"base": {"version_id": ch["base_spec_version_id"], "content_sha256": _h("v1")},
                                  "revised": {"version_id": ch["revised_spec_version_id"], "content_sha256": _h("v2")}},
                "correction": {"text": (ch["proposal"] or {}).get("text"), "input": "typed", "ops": (ch["proposal"] or {}).get("ops", []),
                               "scope": (ch["proposal"] or {}).get("scope"), "provenance": "offline_interpreter", "fallback_reason": None},
                "rechecks": ch["rechecks"],
                "rollouts": {"before": {**ch["before"], "content_sha256": _h("b", ch["id"]), "spec_version_id": ch["base_spec_version_id"]},
                             "after": {**(ch["after"] or {}), "content_sha256": _h("a", ch["id"]), "spec_version_id": ch["revised_spec_version_id"]}},
                "provenance": {"interpreter": "offline-rules", "recorded": recorded, "simulation": "fixture", "server_version": "fake"}}


def make_handler(studio: FakeStudio):
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(WEB), **k)

        def log_message(self, *a):
            pass

        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            try:
                return json.loads(raw or b"{}")
            except ValueError:
                return {}

        def _gen_ok(self):
            g = self.headers.get("X-Mirror-Generation")
            if g and g != studio.generation:
                self._json(409, {"error": "stale_generation", "detail": "generation changed", "current_generation": studio.generation})
                return False
            return True

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path == "/config.js":
                body = b"window.MIRROR_CONFIG = {};\n"
                self.send_response(200); self.send_header("Content-Type", "text/javascript")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                return
            if not path.startswith("/api/"):
                if path == "/ws":
                    self.send_error(404)
                    return
                return super().do_GET()
            p = path[5:]
            if p == "health":
                return self._json(200, {"ok": True, "mode": "fixture"})
            if p == "state":
                return self._json(200, studio.snapshot())
            if p.startswith("rollouts/"):
                return self._json(200, studio.rollout_payload(urllib.parse.unquote(p[9:])))
            if p.startswith("jobs/"):
                return self._json(200, studio.job_view(p[5:]))
            if p == "challenge/options":
                return self._json(200, studio.options())
            if p.startswith("challenge/"):
                cid = p.split("/")[1]
                return self._json(200, studio.challenges[cid]) if cid in studio.challenges else self._json(404, {"error": "not_found", "detail": cid})
            if p.startswith("records/"):
                parts = p.split("/")
                rec = studio.records.get(parts[1])
                if not rec:
                    return self._json(410, {"error": "record_expired", "detail": parts[1]})
                if len(parts) == 4 and parts[2] == "rollouts":
                    return self._json(200, studio.rollout_payload(urllib.parse.unquote(parts[3])))
                return self._json(200, rec)
            if p == "tour":
                return self._json(404, {"error": "not_found", "detail": "no tour"})
            return self._json(404, {"error": "not_found", "detail": p})

        def do_POST(self):
            path = urllib.parse.urlparse(self.path).path
            p = path[5:] if path.startswith("/api/") else ""
            body = self._body()
            if p == "session":
                return self._json(200, {"session": "fake-session", "token": "fake-token", "mode": "fixture"})
            if self.headers.get("X-Mirror-Token") != "fake-token":
                return self._json(401, {"error": "token_required", "detail": "missing token"})
            if not self._gen_ok():
                return
            if p == "challenge":
                return self._json(*studio.create(body))
            parts = p.split("/")
            if len(parts) == 3 and parts[0] == "challenge":
                cid, act = parts[1], parts[2]
                if act == "correction":
                    return self._json(*studio.correction(cid, body))
                if act == "review":
                    return self._json(*studio.review(cid, body))
                if act == "execute":
                    return self._json(*studio.execute(cid))
                if act == "publish":
                    return self._json(*studio.publish(cid))
            if p == "_test/bump_generation":
                studio.generation = "g18f3aa02" + "0002"
                return self._json(200, {"generation": studio.generation})
            return self._json(404, {"error": "not_found", "detail": p})
    return H


class FakeServer:
    def __init__(self, data: Path, **kw):
        self.studio = FakeStudio(data, **kw)
        self.port = free_port()
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), make_handler(self.studio))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
