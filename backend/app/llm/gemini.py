"""Gemini client over plain REST (httpx).

Why REST and not an SDK: one fewer dependency, and retry/timeout/backoff behaviour is explicit
and easy to unit-test with httpx.MockTransport. Handles: retries with exponential backoff and
jitter, Retry-After / RetryInfo hints, quota-exhausted detection (fail fast instead of sleeping
for an hour), truncated and safety-blocked responses, token + cost accounting.
"""
import asyncio
import random
import re
import time
from collections.abc import Awaitable, Callable

import httpx

from app.config import settings
from app.llm.base import LLMConfigError, LLMError, LLMResponse, estimate_cost

RETRYABLE_STATUS = {500, 502, 503, 504}


def _retry_delay_from(r: httpx.Response) -> float | None:
    """Server hint for how long to wait: Retry-After header, or Google's RetryInfo detail."""
    ra = r.headers.get("retry-after")
    if ra:
        try:
            return float(ra)
        except ValueError:
            pass
    try:
        for d in r.json().get("error", {}).get("details", []):
            if str(d.get("@type", "")).endswith("RetryInfo"):
                m = re.match(r"^([\d.]+)s$", str(d.get("retryDelay", "")))
                if m:
                    return float(m.group(1))
    except Exception:  # noqa: BLE001 - body is best-effort
        pass
    return None


def _api_message(r: httpx.Response) -> str:
    try:
        return str(r.json().get("error", {}).get("message") or r.text)[:300]
    except Exception:  # noqa: BLE001
        return r.text[:300]


class GeminiClient:
    def __init__(self, api_key: str | None = None, model: str | None = None, *,
                 base_url: str | None = None, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float | None = None, max_retries: int | None = None,
                 max_backoff: float | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.api_key = api_key if api_key is not None else settings.gemini_api_key
        if not self.api_key:
            raise LLMConfigError("GEMINI_API_KEY is not set. Add it to .env and restart the backend.")
        self.model = model or settings.gemini_model
        self.max_retries = settings.llm_max_retries if max_retries is None else max_retries
        self.max_backoff = settings.llm_max_backoff_s if max_backoff is None else max_backoff
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            base_url=(base_url or settings.gemini_api_url).rstrip("/"),
            headers={"x-goog-api-key": self.api_key},   # header, not ?key=, so it never lands in logs
            transport=transport,
            timeout=timeout if timeout is not None else settings.llm_timeout_s)

    async def __aenter__(self) -> "GeminiClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._http.aclose()

    # ---- request ----
    def _body(self, system: str, user: str, json_mode: bool) -> dict:
        cfg: dict = {"maxOutputTokens": settings.llm_max_output_tokens}
        if json_mode:
            cfg["responseMimeType"] = "application/json"
        if settings.llm_temperature is not None:
            cfg["temperature"] = settings.llm_temperature
        if settings.gemini_thinking_level:
            cfg["thinkingConfig"] = {"thinkingLevel": settings.gemini_thinking_level}
        return {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": cfg}

    async def generate(self, *, system: str, user: str, json_mode: bool = True) -> LLMResponse:
        body = self._body(system, user, json_mode)
        path = f"/models/{self.model}:generateContent"
        started = time.perf_counter()
        total_attempts = self.max_retries + 1
        for attempt in range(1, total_attempts + 1):
            err: LLMError
            try:
                r = await self._http.post(path, json=body)
            except httpx.TimeoutException:
                err = LLMError("LLM request timed out", retryable=True)
            except httpx.TransportError as e:
                err = LLMError(f"Network error talking to Gemini: {type(e).__name__}", retryable=True)
            else:
                if r.status_code == 200:
                    return self._parse(r, attempt, started)
                err = self._http_error(r)

            err.attempts = attempt
            if not err.retryable or attempt == total_attempts:
                raise err
            await self._sleep(self._backoff(attempt, err.retry_after))
        raise AssertionError("unreachable")  # pragma: no cover

    def _backoff(self, attempt: int, hint: float | None) -> float:
        if hint is not None:
            return min(hint + 0.5, self.max_backoff)
        base = min(self.max_backoff, 2.0 * 2 ** (attempt - 1))
        return base + random.uniform(0, base * 0.25)

    # ---- response handling ----
    def _http_error(self, r: httpx.Response) -> LLMError:
        code, msg = r.status_code, _api_message(r)
        if code == 429:
            delay = _retry_delay_from(r)
            if delay is not None and delay > self.max_backoff:
                return LLMError(
                    f"Gemini quota exhausted (429); the API says retry in ~{int(delay)}s. This "
                    "usually means the free-tier daily limit. Wait, switch to a lighter model "
                    "(GEMINI_MODEL) or enable billing.", status=429, retry_after=delay)
            return LLMError(f"Gemini rate limit (429): {msg}", retryable=True, status=429,
                            retry_after=delay)
        if code in RETRYABLE_STATUS:
            return LLMError(f"Gemini server error {code}: {msg}", retryable=True, status=code,
                            retry_after=_retry_delay_from(r))
        if code in (401, 403) or "API key" in msg:
            return LLMError(f"Gemini rejected the API key ({code}). Check GEMINI_API_KEY. "
                            f"Details: {msg}", status=code)
        if code == 404:
            return LLMError(f"Gemini model '{self.model}' not found. Run "
                            "`python -m scripts.smoke_llm` to list models your key can use, then set "
                            "GEMINI_MODEL.", status=404)
        return LLMError(f"Gemini request failed ({code}): {msg}", status=code)

    def _parse(self, r: httpx.Response, attempts: int, started: float) -> LLMResponse:
        data = r.json()
        latency_ms = int((time.perf_counter() - started) * 1000)
        block = (data.get("promptFeedback") or {}).get("blockReason")
        if block:
            raise LLMError(f"Gemini blocked the prompt ({block}).", attempts=attempts)
        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMError("Gemini returned no candidates.", attempts=attempts)
        cand = candidates[0]
        finish = cand.get("finishReason")
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if finish == "MAX_TOKENS":
            raise LLMError("Gemini output was cut off (MAX_TOKENS). Raise LLM_MAX_OUTPUT_TOKENS "
                           "or set GEMINI_THINKING_LEVEL=low.", attempts=attempts)
        if finish in ("SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST"):
            raise LLMError(f"Gemini stopped the response ({finish}).", attempts=attempts)
        if not text.strip():
            raise LLMError(f"Gemini returned an empty response (finishReason={finish}).",
                           attempts=attempts)
        u = data.get("usageMetadata") or {}
        tin = int(u.get("promptTokenCount") or 0)
        tout = int(u.get("candidatesTokenCount") or 0) + int(u.get("thoughtsTokenCount") or 0)
        return LLMResponse(text=text, model=data.get("modelVersion") or self.model,
                           tokens_in=tin, tokens_out=tout, latency_ms=latency_ms,
                           cost_usd=estimate_cost(tin, tout), attempts=attempts, finish_reason=finish)
