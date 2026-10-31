"""HTTP client for the Meta Model API with one cassette mechanism.

Configuration (environment):
  MODEL_API_KEY            bearer token (never written anywhere)
  MIRROR_META_MODE         live | record | replay | offline. Falls back to
                           MIRROR_LLM_MODE; default live if a key is set,
                           otherwise replay.
  MIRROR_META_BASE_URL     default https://api.meta.ai/v1
  MIRROR_META_CASSETTE_DIR default <repo>/data/cassettes_meta
  MIRROR_META_USAGE_LOG    default <repo>/results/meta_usage.jsonl; written only
                           for network calls (live/record), never in replay.

Modes:
  live     call the API; nothing recorded.
  record   serve recorded responses; call the API for missing ones and record
           them. File uploads always go to the API in record mode (uploads are
           free and server file ids expire), and their cassettes are refreshed.
  replay   recorded responses only; a missing one raises MetaCassetteMiss.
  offline  no model: every call raises MetaOffline.

Errors: 401/403 raise MetaAuthError and 402 MetaQuotaError at once; 429 is
retried a few times (honouring Retry-After) and then raises MetaQuotaError.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Union

import httpx

from mirror.meta.cassette import CassetteStore, MetaCassetteMiss, normalize, request_key, sha256_bytes

DEFAULT_BASE_URL = "https://api.meta.ai/v1"
MODES = ("live", "record", "replay", "offline")
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASSETTE_DIR = REPO_ROOT / "data" / "cassettes_meta"
DEFAULT_USAGE_LOG = REPO_ROOT / "results" / "meta_usage.jsonl"
VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v")

Media = Union[str, Path, bytes]


class MetaError(RuntimeError):
    """The Meta endpoint failed or returned unusable output."""


class MetaAuthError(MetaError):
    pass


class MetaQuotaError(MetaError):
    pass


class MetaOffline(MetaError):
    """Offline mode: no model is available."""


def _read(src: Media) -> bytes:
    return src if isinstance(src, (bytes, bytearray)) else Path(src).read_bytes()


def _mime(src: Media, default: str) -> str:
    if isinstance(src, (bytes, bytearray)):
        return default
    return mimetypes.guess_type(str(src))[0] or default


def text_part(text: str) -> dict:
    return {"type": "input_text", "text": text}


def image_part(src: Media, mime: Optional[str] = None) -> dict:
    """An image as an inline data URL (or an http(s) URL passed through)."""
    if isinstance(src, str) and src.startswith(("http://", "https://", "data:")):
        return {"type": "input_image", "image_url": src}
    data = _read(src)
    mime = mime or _mime(src, "image/png")
    return {"type": "input_image", "image_url": f"data:{mime};base64," + base64.b64encode(data).decode()}


def file_part(file_id: str, kind: str = "file") -> dict:
    """A Files API upload. kind 'file' (Muse video/audio understanding: input_file)
    or 'video' (SAM: input_video)."""
    if kind == "video":
        return {"type": "input_video", "file_id": file_id}
    return {"type": "input_file", "file_id": file_id}


def audio_part(src: Media, fmt: str = "wav") -> dict:
    return {"type": "input_audio", "input_audio": {"data": base64.b64encode(_read(src)).decode(), "format": fmt}}


@dataclass
class MetaResult:
    endpoint: str
    model: str                       # model id the API reported (or requested, if absent)
    text: str = ""
    data: dict = field(default_factory=dict)
    images: list[bytes] = field(default_factory=list, repr=False)
    usage: dict = field(default_factory=dict)
    latency_s: float = 0.0
    from_cassette: bool = False
    key: str = ""

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except ValueError as e:
            raise MetaError(f"{self.endpoint} ({self.model}) did not return JSON: {self.text[:200]!r}") from e


@dataclass
class MetaClient:
    mode: str = "replay"
    api_key: Optional[str] = field(default=None, repr=False)
    base_url: str = DEFAULT_BASE_URL
    cassette_dir: Path = DEFAULT_CASSETTE_DIR
    usage_log: Optional[Path] = None
    timeout_s: float = 180.0
    max_retries: int = 3
    backoff_s: float = 2.0
    transport: Optional[httpx.BaseTransport] = field(default=None, repr=False)
    usage: list[dict] = field(default_factory=list, repr=False)
    _files: dict[str, str] = field(default_factory=dict, repr=False)  # file id -> "sha256:<hex>"

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {self.mode!r}")
        self.cassette_dir = Path(self.cassette_dir)
        self._store = CassetteStore(self.cassette_dir)

    @classmethod
    def from_env(cls, env: Optional[Any] = None, **overrides: Any) -> "MetaClient":
        env = os.environ if env is None else env
        key = env.get("MODEL_API_KEY") or None
        mode = env.get("MIRROR_META_MODE") or env.get("MIRROR_LLM_MODE") or ("live" if key else "replay")
        kw: dict[str, Any] = dict(
            mode=mode, api_key=key,
            base_url=env.get("MIRROR_META_BASE_URL") or DEFAULT_BASE_URL,
            cassette_dir=Path(env.get("MIRROR_META_CASSETTE_DIR") or DEFAULT_CASSETTE_DIR),
        )
        if mode in ("live", "record"):
            kw["usage_log"] = Path(env.get("MIRROR_META_USAGE_LOG") or DEFAULT_USAGE_LOG)
        kw.update(overrides)
        return cls(**kw)

    # ------------------------------------------------------------------ core
    def _cached(self, model: str, endpoint: str, request: Any, network: Callable[[], tuple[dict, list, dict]],
                *, name: str, reuse: bool = True) -> tuple[dict, list[bytes], bool, float, str]:
        norm = normalize(request, self._files)
        key = request_key(model, endpoint, norm)
        if self.mode == "offline":
            raise MetaOffline(f"{endpoint} ({model}): offline mode, no model available")
        if self.mode in ("replay", "record") and (reuse or self.mode == "replay"):
            cas = self._store.load(model, key)
            if cas is not None:
                blobs = [self._store.blob(model, b["file"]) for b in cas.get("blobs", [])]
                self._log(name, endpoint, model, cas["response"].get("usage") or {}, cas.get("latency_s", 0.0),
                          from_cassette=True, key=key)
                return cas["response"], blobs, True, float(cas.get("latency_s", 0.0)), key
            if self.mode == "replay":
                raise MetaCassetteMiss(f"no recording for {endpoint} with {model} (key {key[:12]}) in "
                                       f"{self.cassette_dir}")
        if not self.api_key:
            raise MetaAuthError("MODEL_API_KEY is not set (needed for live or record mode)")
        t0 = time.monotonic()
        response, blobs, units = network()
        latency = time.monotonic() - t0
        if self.mode == "record":
            self._store.save(model, key, endpoint, norm, response, latency_s=latency,
                             blobs=[(ext, b) for ext, b in blobs])
        self._log(name, endpoint, model, {**(response.get("usage") or {}), **units}, latency,
                  from_cassette=False, key=key)
        return response, [b for _, b in blobs], False, latency, key

    def _log(self, name: str, endpoint: str, model: str, usage: dict, latency: float, *,
             from_cassette: bool, key: str) -> None:
        rec = {"ts": round(time.time(), 3), "call": name, "endpoint": endpoint, "model": model,
               "mode": self.mode, "from_cassette": from_cassette, "latency_s": round(latency, 3),
               "key": key[:16], "usage": usage}
        self.usage.append(rec)
        if self.usage_log is not None and not from_cassette:
            self.usage_log.parent.mkdir(parents=True, exist_ok=True)
            with open(self.usage_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")

    def _http(self) -> httpx.Client:
        return httpx.Client(base_url=self.base_url.rstrip("/"), timeout=self.timeout_s, transport=self.transport,
                            headers={"Authorization": f"Bearer {self.api_key}"})

    def _check(self, r: httpx.Response, attempt: int) -> Optional[float]:
        """None if OK; a delay to retry after; raises otherwise."""
        if r.status_code < 300:
            return None
        text = r.text[:500]
        if r.status_code in (401, 403):
            raise MetaAuthError(f"authentication failed ({r.status_code}); check MODEL_API_KEY")
        if r.status_code == 402:
            raise MetaQuotaError(f"payment required (402): {text}")
        if r.status_code == 429 or r.status_code >= 500:
            if attempt >= self.max_retries:
                cls = MetaQuotaError if r.status_code == 429 else MetaError
                raise cls(f"HTTP {r.status_code} after {attempt + 1} attempts: {text}")
            ra = r.headers.get("retry-after")
            return max(self.backoff_s * (2 ** attempt), float(ra) if ra and ra.replace(".", "", 1).isdigit() else 0)
        raise MetaError(f"HTTP {r.status_code}: {text}")

    def _post(self, path: str, **kw: Any) -> httpx.Response:
        for attempt in range(self.max_retries + 1):
            try:
                with self._http() as http:
                    r = http.post(path, **kw)
                    r.read()
            except (httpx.TimeoutException, httpx.TransportError) as e:
                if attempt >= self.max_retries:
                    raise MetaError(f"{path}: {type(e).__name__}: {e}") from e
                time.sleep(self.backoff_s * (2 ** attempt))
                continue
            delay = self._check(r, attempt)
            if delay is None:
                return r
            time.sleep(delay)
        raise MetaError(f"{path}: retries exhausted")

    # ------------------------------------------------------------- Responses
    def responses(self, model: str, content: list[dict], *, schema: Optional[dict] = None,
                  schema_name: str = "result", instructions: Optional[str] = None,
                  reasoning_effort: Optional[str] = None, metadata: Optional[dict] = None,
                  extra: Optional[dict] = None, name: str = "responses") -> MetaResult:
        """One non-streamed Responses API call. With ``schema`` the output is
        constrained to that JSON schema (text.format json_schema, strict)."""
        body: dict[str, Any] = {"model": model,
                                "input": [{"type": "message", "role": "user", "content": content}]}
        if instructions:
            body["instructions"] = instructions
        if schema is not None:
            body["text"] = {"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}}
        if reasoning_effort:
            body["reasoning"] = {"effort": reasoning_effort}
        if metadata:
            body["metadata"] = metadata
        if extra:
            body.update(extra)

        def net():
            r = self._post("/responses", json=body)
            d = r.json()
            if d.get("status") not in (None, "completed"):
                raise MetaError(f"response status {d.get('status')}: {d.get('incomplete_details') or d.get('error')}")
            text = "".join(c.get("text", "") for item in d.get("output", []) if item.get("type") == "message"
                           for c in item.get("content", []) if c.get("type") == "output_text")
            return ({"text": text, "model": d.get("model") or model, "usage": d.get("usage") or {},
                     "status": d.get("status")}, [], {})

        resp, _, cached, lat, key = self._cached(model, "/responses", body, net, name=name)
        return MetaResult("/responses", resp.get("model") or model, text=resp.get("text", ""), data=resp,
                          usage=resp.get("usage") or {}, latency_s=lat, from_cassette=cached, key=key)

    def responses_stream_text(self, model: str, content: list[dict], *, metadata: Optional[dict] = None,
                              name: str = "responses_stream") -> MetaResult:
        """A streamed Responses call; returns the accumulated output_text lane."""
        body: dict[str, Any] = {"model": model, "stream": True,
                                "input": [{"type": "message", "role": "user", "content": content}]}
        if metadata:
            body["metadata"] = metadata

        def net():
            for attempt in range(self.max_retries + 1):
                chunks: list[str] = []
                final: dict = {}
                lines = 0
                try:
                    with self._http() as http, http.stream("POST", "/responses", json=body,
                                                            headers={"Accept": "text/event-stream"}) as r:
                        if r.status_code >= 300:
                            r.read()
                            delay = self._check(r, attempt)
                            time.sleep(delay or 0)
                            continue
                        for line in r.iter_lines():
                            if not line.startswith("data: "):
                                continue
                            payload = line[6:]
                            if payload == "[DONE]":
                                break
                            ev = json.loads(payload)
                            t = ev.get("type")
                            if t == "response.output_text.delta":
                                chunks.append(ev.get("delta", ""))
                                lines += 1
                            elif t in ("response.completed", "response.incomplete"):
                                final = ev.get("response") or {}
                            elif t in ("response.failed", "error"):
                                raise MetaError(f"{model} stream failed: {json.dumps(ev)[:400]}")
                except (httpx.TimeoutException, httpx.TransportError) as e:
                    if attempt >= self.max_retries:
                        raise MetaError(f"/responses stream: {type(e).__name__}: {e}") from e
                    time.sleep(self.backoff_s * (2 ** attempt))
                    continue
                status = final.get("status", "eof")
                if status != "completed":
                    raise MetaError(f"{model} stream ended with status {status}")
                return ({"text": "".join(chunks), "model": final.get("model") or model,
                         "usage": final.get("usage") or {}, "status": status}, [], {"lines": lines})
            raise MetaError("/responses stream: retries exhausted")

        resp, _, cached, lat, key = self._cached(model, "/responses#stream", body, net, name=name)
        return MetaResult("/responses", resp.get("model") or model, text=resp.get("text", ""), data=resp,
                          usage=resp.get("usage") or {}, latency_s=lat, from_cassette=cached, key=key)

    # ----------------------------------------------------------------- Files
    def upload_file(self, src: Media, *, filename: Optional[str] = None, purpose: str = "user_data",
                    mime: Optional[str] = None) -> str:
        """Upload media through the Files API; returns the file id."""
        data = _read(src)
        sha = sha256_bytes(data)
        filename = filename or (Path(src).name if not isinstance(src, (bytes, bytearray)) else "upload.bin")
        mime = mime or _mime(src, "application/octet-stream")
        request = {"purpose": purpose, "file": f"sha256:{sha}"}

        def net():
            r = self._post("/files", data={"purpose": purpose}, files={"file": (filename, data, mime)})
            d = r.json()
            return ({"id": d["id"], "bytes": d.get("bytes"), "status": d.get("status"),
                     "filename": d.get("filename")}, [], {"bytes": len(data)})

        resp, _, _, _, _ = self._cached("files", "/files", request, net, name="upload_file", reuse=False)
        self._files[resp["id"]] = f"sha256:{sha}"
        return resp["id"]

    # ------------------------------------------------------------- Speech
    def asr_transcribe(self, audio: Media, *, model: str = "muse-voice-transcribe-1.0",
                       mode: str = "PUSH_TO_TALK", options: Optional[dict] = None) -> MetaResult:
        """Transcribe a WAV recording (mono 16-bit PCM, 16 or 24 kHz, at most
        10 minutes / 32 MB) with /v1/asr/transcribe."""
        data = _read(audio)
        req = {"model": model, "audioEncoding": "WAV", "mode": mode, **(options or {})}
        request = {"request": req, "audio": f"sha256:{sha256_bytes(data)}"}

        def net():
            r = self._post("/asr/transcribe", headers={"Accept": "application/json"},
                           files={"request": (None, json.dumps(req), "application/json"),
                                  "audio": ("audio.wav", data, "audio/wav")})
            return (r.json(), [], {"audio_bytes": len(data)})

        resp, _, cached, lat, key = self._cached(model, "/asr/transcribe", request, net, name="asr_transcribe")
        return MetaResult("/asr/transcribe", model, text=transcript_text(resp), data=resp,
                          latency_s=lat, from_cassette=cached, key=key)

    # ------------------------------------------------------------- Images
    def images_edit(self, images: list[Media], prompt: str, *, model: str = "muse-image-1.0", n: int = 1,
                    output_format: str = "png", extra: Optional[dict] = None) -> MetaResult:
        body = {"model": model, "prompt": prompt, "n": n, "output_format": output_format,
                "images": [{"image_url": image_part(i)["image_url"]} for i in images], **(extra or {})}
        return self._images("/images/edits", model, body, output_format)

    def images_generate(self, prompt: str, *, model: str = "muse-image-1.0", n: int = 1,
                        output_format: str = "png", size: Optional[str] = None,
                        extra: Optional[dict] = None) -> MetaResult:
        body = {"model": model, "prompt": prompt, "n": n, "output_format": output_format, **(extra or {})}
        if size:
            body["size"] = size
        return self._images("/images/generations", model, body, output_format)

    def _images(self, endpoint: str, model: str, body: dict, ext: str) -> MetaResult:
        def net():
            r = self._post(endpoint, json=body)
            d = r.json()
            blobs = [(ext, base64.b64decode(x["b64_json"])) for x in d.get("data", []) if x.get("b64_json")]
            meta = {k: v for k, v in d.items() if k != "data"}
            return ({**meta, "n_images": len(blobs)}, blobs, {"images": len(blobs)})

        resp, blobs, cached, lat, key = self._cached(model, endpoint, body, net, name=endpoint.strip("/"))
        return MetaResult(endpoint, model, data=resp, images=blobs, usage=resp.get("usage") or {},
                          latency_s=lat, from_cassette=cached, key=key)

    # ---------------------------------------------------------------- SAM
    def sam_segment(self, media: Media, prompt: str, *, video: Optional[bool] = None,
                    model: str = "sam-3.1", mask_encoding: str = "one_bit", include_confidence: bool = True,
                    decode_masks: bool = True):
        """Segment ``prompt`` (a short noun phrase, or a box/point prompt in SAM's
        token grammar) in an image or a video. Videos are uploaded through the
        Files API. Returns a mirror.meta.sam.SamResult."""
        from mirror.meta.sam import parse_sam_output
        if video is None:
            video = not isinstance(media, (bytes, bytearray)) and str(media).lower().endswith(VIDEO_SUFFIXES)
        if video:
            part = file_part(self.upload_file(media, mime="video/mp4"), kind="video")
        else:
            part = image_part(media)
        meta = {"mask_encoding": mask_encoding}
        if include_confidence:
            meta["include_confidence"] = "true"
        res = self.responses_stream_text(model, [text_part(prompt), part], metadata=meta, name="sam_segment")
        out = parse_sam_output(res.text, prompt=prompt, media="video" if video else "image",
                               decode_masks=decode_masks)
        out.model, out.latency_s, out.from_cassette = res.model, res.latency_s, res.from_cassette
        return out


def transcript_text(resp: dict) -> str:
    """The transcript in a /asr/transcribe JSON response (all turns joined)."""
    if isinstance(resp.get("transcript"), str):
        return resp["transcript"].strip()
    turns = resp.get("turns") or []
    parts = [t.get("transcript", "") for t in turns if isinstance(t, dict)]
    return " ".join(p.strip() for p in parts if p).strip()
