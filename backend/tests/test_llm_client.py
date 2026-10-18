import asyncio
import json

import httpx
import pytest

from app.llm.base import LLMConfigError, LLMError
from app.llm.gemini import GeminiClient
from tests.fakes import gemini_error, gemini_ok


def make_client(handler, **kw):
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    c = GeminiClient("test-key", "gemini-test", transport=httpx.MockTransport(handler),
                     sleep=fake_sleep, max_retries=kw.pop("max_retries", 3),
                     max_backoff=kw.pop("max_backoff", 30), **kw)
    return c, sleeps


def run(coro):
    return asyncio.run(coro)


async def call(client):
    async with client as c:
        return await c.generate(system="sys", user="usr")


def test_success_parses_text_tokens_and_cost():
    seen = {}

    def handler(req):
        seen["key"] = req.headers.get("x-goog-api-key")
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        return gemini_ok('{"findings": []}', tin=1000, tout=200, thoughts=300)

    c, _ = make_client(handler)
    r = run(call(c))
    assert r.text == '{"findings": []}'
    assert r.tokens_in == 1000 and r.tokens_out == 500          # thinking tokens count as output
    assert r.cost_usd > 0 and r.attempts == 1 and r.model == "gemini-test-001"
    assert seen["key"] == "test-key" and "key=" not in seen["url"]   # key in header, never in the URL
    assert seen["url"].endswith("/models/gemini-test:generateContent")
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "sys"
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"


def test_thought_parts_are_not_returned_as_text():
    def handler(req):
        return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": "secret reasoning", "thought": True}, {"text": "answer"}]}}]})
    c, _ = make_client(handler)
    assert run(call(c)).text == "answer"


def test_retries_503_then_succeeds():
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        return gemini_error(503, "overloaded") if n["i"] < 3 else gemini_ok("{}")

    c, sleeps = make_client(handler)
    r = run(call(c))
    assert r.attempts == 3 and len(sleeps) == 2
    assert sleeps[1] > sleeps[0]                                  # exponential backoff


def test_gives_up_after_max_retries():
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        return gemini_error(500)

    c, sleeps = make_client(handler, max_retries=2)
    with pytest.raises(LLMError) as e:
        run(call(c))
    assert n["i"] == 3 and e.value.attempts == 3 and e.value.retryable and len(sleeps) == 2


def test_429_honours_server_retry_hint():
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        return gemini_error(429, "slow down", retry_delay="7s") if n["i"] == 1 else gemini_ok("{}")

    c, sleeps = make_client(handler)
    assert run(call(c)).attempts == 2
    assert 7 <= sleeps[0] <= 8


def test_429_with_huge_delay_fails_fast_as_quota():
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        return gemini_error(429, "quota", retry_delay="3600s")

    c, sleeps = make_client(handler)
    with pytest.raises(LLMError) as e:
        run(call(c))
    assert n["i"] == 1 and sleeps == [] and "quota" in str(e.value).lower()


@pytest.mark.parametrize("status,needle", [(400, "400"), (403, "API key"), (404, "not found")])
def test_client_errors_are_not_retried(status, needle):
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        return gemini_error(status, "nope")

    c, sleeps = make_client(handler)
    with pytest.raises(LLMError) as e:
        run(call(c))
    assert n["i"] == 1 and not sleeps and needle in str(e.value)


def test_timeout_is_retried():
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        if n["i"] == 1:
            raise httpx.ReadTimeout("slow", request=req)
        return gemini_ok("{}")

    c, _ = make_client(handler)
    assert run(call(c)).attempts == 2


def test_truncated_output_is_an_error_with_hint():
    c, _ = make_client(lambda req: gemini_ok('{"findings": [', finish="MAX_TOKENS"))
    with pytest.raises(LLMError, match="MAX_TOKENS"):
        run(call(c))


def test_blocked_prompt_and_empty_reply():
    c, _ = make_client(lambda req: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}))
    with pytest.raises(LLMError, match="blocked"):
        run(call(c))
    c, _ = make_client(lambda req: httpx.Response(200, json={"candidates": [
        {"finishReason": "STOP", "content": {"parts": []}}]}))
    with pytest.raises(LLMError, match="empty"):
        run(call(c))


def test_missing_key_is_a_clear_config_error():
    with pytest.raises(LLMConfigError, match="GEMINI_API_KEY"):
        GeminiClient(api_key="")
