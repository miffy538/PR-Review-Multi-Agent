"""Test doubles: a scripted LLM and Gemini-shaped HTTP responses."""
import json

import httpx

from app.llm.base import LLMError, LLMResponse


class FakeLLM:
    """Returns scripted replies in order (a str is the reply text; an Exception is raised)."""

    def __init__(self, replies, model="fake-model"):
        self.replies = list(replies)
        self.model = model
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def generate(self, *, system, user, json_mode=True):
        self.calls.append({"system": system, "user": user})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return LLMResponse(text=reply, model=self.model, tokens_in=1000, tokens_out=200,
                           latency_ms=5, cost_usd=0.0008)


def findings_json(*items) -> str:
    return json.dumps({"findings": list(items)})


def gemini_ok(text: str, tin=100, tout=20, thoughts=0, finish="STOP", model="gemini-test-001"):
    return httpx.Response(200, json={
        "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}],
        "usageMetadata": {"promptTokenCount": tin, "candidatesTokenCount": tout,
                          "thoughtsTokenCount": thoughts},
        "modelVersion": model})


def gemini_error(status: int, message="boom", retry_delay: str | None = None, headers=None):
    err: dict = {"code": status, "message": message}
    if retry_delay:
        err["details"] = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}]
    return httpx.Response(status, json={"error": err}, headers=headers or {})


__all__ = ["FakeLLM", "findings_json", "gemini_ok", "gemini_error", "LLMError"]
