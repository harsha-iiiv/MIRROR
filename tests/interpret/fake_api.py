"""In-process fake of an OpenAI-compatible chat-completions endpoint."""
from __future__ import annotations

import json
from typing import Any, Callable, Union

import httpx

Reply = Union[dict, str, tuple, Callable[[dict], Any]]


class FakeAPI:
    def __init__(self, replies: list[Reply], model: str = "fake-model-2026"):
        self.replies = list(replies)
        self.requests: list[dict] = []
        self.model = model

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append({"body": body, "headers": dict(request.headers)})
        if not self.replies:
            return httpx.Response(500, json={"error": "no more replies"})
        r = self.replies.pop(0)
        if callable(r):
            r = r(body)
        if isinstance(r, tuple):
            status, payload = r
            return httpx.Response(status, json=payload if isinstance(payload, dict) else {"error": payload})
        content = r if isinstance(r, str) else json.dumps(r)
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "model": self.model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})
