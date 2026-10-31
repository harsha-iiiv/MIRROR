"""OpenAI-compatible chat-completions client with structured output.

Works with the Meta Model API (https://api.meta.ai/v1) and any endpoint that
speaks the same protocol. Configuration comes from the environment:

  MIRROR_LLM_BASE_URL   default https://api.meta.ai/v1
  MIRROR_LLM_MODEL      default muse-spark-1.3
  MODEL_API_KEY         bearer token (never written anywhere)
  MIRROR_LLM_MODE       live | record | replay | offline   (default: live if a
                        key is set, otherwise offline)
  MIRROR_CASSETTE_DIR   default <repo>/data/cassettes
  MIRROR_LLM_USAGE_LOG  optional JSON-lines file for per-call usage records
  MIRROR_LLM_TIMEOUT    seconds per request, default 90

Modes:
  live     call the API.
  record   serve existing cassettes; call the API for missing ones and save
           each validated response as a new cassette.
  replay   serve responses from cassettes only; a missing cassette raises
           CassetteMiss (no network access).
  offline  no model at all; callers use the deterministic rule-based
           interpreter in mirror.interpret.offline.

Cassettes are keyed by a SHA-256 of (requested model, messages, schema) and
store the model id the API reported.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from mirror.interpret.types import InterpretError

DEFAULT_BASE_URL = "https://api.meta.ai/v1"
DEFAULT_MODEL = "muse-spark-1.3"
MODES = ("live", "record", "replay", "offline")
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASSETTE_DIR = REPO_ROOT / "data" / "cassettes"

T = TypeVar("T", bound=BaseModel)
Validator = Callable[[Any], list[str]]


class LLMError(InterpretError):
    """The model endpoint failed or returned unusable output."""


class CassetteMiss(LLMError):
    """Replay mode found no recorded response for this request."""


@dataclass
class LLMResult:
    data: Any
    model: str                 # model id reported by the API (or the cassette)
    content: str
    usage: dict[str, Any]
    from_cassette: bool
    key: str
    attempts: int = 1
    format_used: str = ""


def strict_schema(model: Type[BaseModel]) -> dict[str, Any]:
    """JSON schema for strict structured output: every object closed, every
    property required (optional ones are nullable), no defaults or titles."""
    schema = copy.deepcopy(model.model_json_schema())

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("title", None)
            node.pop("default", None)
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    return schema


def request_key(model: str, messages: list[dict], schema: dict) -> str:
    blob = json.dumps({"model": model, "messages": messages, "schema": schema},
                      sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


@dataclass
class LLMClient:
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key: Optional[str] = field(default=None, repr=False)
    mode: str = "live"
    cassette_dir: Path = DEFAULT_CASSETTE_DIR
    timeout_s: float = 90.0
    max_retries: int = 3
    backoff_s: float = 1.5
    usage_log_path: Optional[Path] = None
    temperature: Optional[float] = None
    transport: Optional[httpx.BaseTransport] = field(default=None, repr=False)
    usage: list[dict[str, Any]] = field(default_factory=list)
    _json_schema_supported: Optional[bool] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"MIRROR_LLM_MODE must be one of {MODES}, got {self.mode!r}")
        self.cassette_dir = Path(self.cassette_dir)
        self.base_url = self.base_url.rstrip("/")

    # ------------------------------------------------------------ setup ---
    @classmethod
    def from_env(cls, env: Optional[Any] = None, **overrides: Any) -> "LLMClient":
        env = os.environ if env is None else env
        key = env.get("MODEL_API_KEY") or None
        mode = env.get("MIRROR_LLM_MODE") or ("live" if key else "offline")
        kw: dict[str, Any] = dict(
            base_url=env.get("MIRROR_LLM_BASE_URL") or DEFAULT_BASE_URL,
            model=env.get("MIRROR_LLM_MODEL") or DEFAULT_MODEL,
            api_key=key, mode=mode,
            cassette_dir=Path(env.get("MIRROR_CASSETTE_DIR") or DEFAULT_CASSETTE_DIR),
            timeout_s=float(env.get("MIRROR_LLM_TIMEOUT") or 90.0),
            usage_log_path=Path(env["MIRROR_LLM_USAGE_LOG"]) if env.get("MIRROR_LLM_USAGE_LOG") else None,
        )
        kw.update(overrides)
        return cls(**kw)

    @classmethod
    def offline_client(cls) -> "LLMClient":
        return cls(mode="offline")

    @property
    def offline(self) -> bool:
        return self.mode == "offline"

    @property
    def label(self) -> str:
        return "offline-rules" if self.offline else self.model

    # ----------------------------------------------------------- public ---
    def complete_json(self, messages: list[dict[str, str]], schema_model: Type[T], *,
                      name: str, validate: Optional[Validator] = None) -> LLMResult:
        """Ask for a JSON object matching ``schema_model``.

        The output is parsed and validated with pydantic and then with
        ``validate`` (semantic checks returning a list of problems). On failure
        the model gets one repair round with the problems listed. Invalid
        output after the repair round raises LLMError; nothing is patched up
        silently."""
        if self.offline:
            raise LLMError("offline mode has no model; use the rule-based interpreter")
        schema = strict_schema(schema_model)
        key = request_key(self.model, messages, schema)
        cas = self._load_cassette(key) if self.mode in ("replay", "record") else None
        if cas is None and self.mode == "replay":
            raise CassetteMiss(
                f"no cassette for request {key[:16]} ({name}, model {self.model}) in {self.cassette_dir}")
        if cas is not None:  # record mode reuses existing recordings and only adds missing ones
            data, errs = self._parse(cas["response"]["content"], schema_model, validate)
            if errs:
                raise LLMError(f"cassette {key[:16]} no longer validates: {errs}")
            self._log(name, key, cas["response"].get("model", self.model),
                      cas["response"].get("usage", {}), 0.0, from_cassette=True)
            return LLMResult(data=data, model=cas["response"].get("model", self.model),
                             content=cas["response"]["content"], usage=cas["response"].get("usage", {}),
                             from_cassette=True, key=key, attempts=cas.get("attempts", 1),
                             format_used=cas.get("format_used", ""))

        convo = list(messages)
        total_usage: dict[str, int] = {}
        returned_model = self.model
        content = ""
        fmt = ""
        errs: list[str] = []
        for attempt in (1, 2):
            t0 = time.monotonic()
            content, returned_model, usage, fmt = self._chat(convo, schema, name)
            for k, v in usage.items():
                if isinstance(v, int):
                    total_usage[k] = total_usage.get(k, 0) + v
            self._log(name, key, returned_model, usage, time.monotonic() - t0,
                      from_cassette=False, attempt=attempt)
            data, errs = self._parse(content, schema_model, validate)
            if not errs:
                result = LLMResult(data=data, model=returned_model, content=content,
                                   usage=total_usage, from_cassette=False, key=key,
                                   attempts=attempt, format_used=fmt)
                if self.mode == "record":
                    self._save_cassette(key, name, messages, schema, result)
                return result
            convo = convo + [
                {"role": "assistant", "content": content},
                {"role": "user", "content": (
                    "Your previous output was rejected by the validator:\n- "
                    + "\n- ".join(errs[:20])
                    + "\nReturn the complete corrected JSON object only, matching the schema.")},
            ]
        raise LLMError(f"{name}: model output invalid after one repair round: {errs}")

    # -------------------------------------------------------- internals ---
    def _parse(self, content: str, schema_model: Type[T], validate: Optional[Validator]):
        try:
            raw = _extract_json(content)
        except (json.JSONDecodeError, ValueError) as e:
            return None, [f"output is not valid JSON: {e}"]
        try:
            data = schema_model.model_validate(raw)
        except ValidationError as e:
            return None, [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]
        if validate is not None:
            errs = validate(data)
            if errs:
                return None, list(errs)
        return data, []

    def _http(self) -> httpx.Client:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return httpx.Client(timeout=self.timeout_s, headers=headers, transport=self.transport)

    def _chat(self, messages: list[dict], schema: dict, name: str) -> tuple[str, str, dict, str]:
        formats = []
        if self._json_schema_supported is not False:
            formats.append("json_schema")
        formats += ["json_object", "plain"]
        last_err: Optional[Exception] = None
        for fmt in formats:
            body: dict[str, Any] = {"model": self.model, "messages": list(messages)}
            if self.temperature is not None:
                body["temperature"] = self.temperature
            if fmt == "json_schema":
                body["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": name, "strict": True, "schema": schema}}
            else:
                body["messages"] = list(messages) + [{"role": "user", "content": (
                    "Respond with one JSON object that validates against this JSON schema, "
                    "and nothing else:\n" + json.dumps(schema, sort_keys=True))}]
                if fmt == "json_object":
                    body["response_format"] = {"type": "json_object"}
            try:
                resp = self._post(body)
            except _FormatRejected as e:
                last_err = e
                if fmt == "json_schema":
                    self._json_schema_supported = False
                if fmt == "plain":
                    break
                continue
            if fmt == "json_schema":
                self._json_schema_supported = True
            try:
                msg = resp["choices"][0]["message"]
                content = msg.get("content") or ""
                if isinstance(content, list):  # some APIs return content parts
                    content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
            except (KeyError, IndexError, TypeError) as e:
                raise LLMError(f"unexpected response shape: {str(resp)[:300]}") from e
            return content, str(resp.get("model") or self.model), resp.get("usage") or {}, fmt
        raise LLMError(f"endpoint rejected every structured-output format: {last_err}")

    def _post(self, body: dict) -> dict:
        url = f"{self.base_url}/chat/completions"
        delay = self.backoff_s
        last: Optional[str] = None
        for attempt in range(self.max_retries + 1):
            try:
                with self._http() as http:
                    r = http.post(url, json=body)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last = f"{type(e).__name__}: {e}"
            else:
                if r.status_code == 200:
                    return r.json()
                text = r.text[:500]
                if r.status_code in (400, 422) and "response_format" in body and (
                        "response_format" in text or "json_schema" in text or "schema" in text):
                    raise _FormatRejected(f"{r.status_code}: {text}")
                if r.status_code in (401, 403):
                    raise LLMError(f"authentication failed ({r.status_code}); check MODEL_API_KEY")
                if r.status_code not in (408, 409, 429) and r.status_code < 500:
                    raise LLMError(f"HTTP {r.status_code}: {text}")
                last = f"HTTP {r.status_code}: {text}"
                ra = r.headers.get("retry-after")
                if ra and ra.replace(".", "", 1).isdigit():
                    delay = max(delay, float(ra))
            if attempt < self.max_retries:
                time.sleep(delay)
                delay *= 2
        raise LLMError(f"request failed after {self.max_retries + 1} attempts: {last}")

    def _cassette_path(self, key: str) -> Path:
        return self.cassette_dir / f"{key[:24]}.json"

    def _load_cassette(self, key: str) -> Optional[dict]:
        p = self._cassette_path(key)
        if not p.exists():
            return None
        cas = json.loads(p.read_text(encoding="utf-8"))
        if cas.get("key") != key:
            return None
        return cas

    def _save_cassette(self, key: str, name: str, messages: list[dict], schema: dict,
                       result: LLMResult) -> None:
        self.cassette_dir.mkdir(parents=True, exist_ok=True)
        cas = {
            "key": key, "name": name,
            "request": {"model": self.model, "messages": messages, "schema": schema},
            "response": {"content": result.content, "model": result.model, "usage": result.usage},
            "attempts": result.attempts, "format_used": result.format_used,
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._cassette_path(key).write_text(json.dumps(cas, indent=2, ensure_ascii=False) + "\n",
                                            encoding="utf-8")

    def _log(self, name: str, key: str, model: str, usage: dict, latency: float, *,
             from_cassette: bool, attempt: int = 1) -> None:
        rec = {"ts": round(time.time(), 3), "call": name, "key": key[:16], "mode": self.mode,
               "requested_model": self.model, "model": model, "attempt": attempt,
               "from_cassette": from_cassette, "latency_s": round(latency, 3),
               "prompt_tokens": usage.get("prompt_tokens"),
               "completion_tokens": usage.get("completion_tokens"),
               "total_tokens": usage.get("total_tokens")}
        self.usage.append(rec)
        if self.usage_log_path:
            self.usage_log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.usage_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")


class _FormatRejected(Exception):
    pass
